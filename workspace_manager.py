import logging
import os
import json
import time
import threading
from typing import Optional, Callable
import win32gui
import win32process
import win32con
import psutil

from storage import get_db
from watchdog_system import get_watchdog

logger = logging.getLogger(__name__)


def get_browser_url_uia(hwnd) -> Optional[str]:
    """Attempt to extract URL from a browser window via UIA."""
    try:
        import comtypes
        import comtypes.client
        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=None)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return None

        # 1. Try Document control
        cond_doc = uia.CreatePropertyCondition(30003, 50030)  # DocumentControlTypeId
        doc = root.FindFirst(4, cond_doc)
        if doc:
            try:
                val = doc.GetCurrentPattern(10002)  # ValuePatternId
                if val and val.CurrentValue:
                    url = val.CurrentValue
                    if url.startswith("http"):
                        return url
            except Exception:
                pass

        # 2. Try Edit control (Address Bar)
        cond_edit = uia.CreatePropertyCondition(30003, 50004)  # EditControlTypeId
        edits = root.FindAll(4, cond_edit)
        if edits:
            for i in range(edits.Length):
                edit = edits.GetElement(i)
                try:
                    val = edit.GetCurrentPattern(10002)
                    if val and val.CurrentValue:
                        url = val.CurrentValue
                        if url.startswith("http") or "://" in url:
                            return url
                        # Sometimes Chrome leaves out https:// in the address bar
                        if "." in url and " " not in url:
                            return "https://" + url
                except Exception:
                    continue
        return None
    except Exception as e:
        logger.debug(f"WorkspaceManager: UIA URL extraction failed: {e}")
        return None


class WorkspaceManager:
    """Manages saving and restoring complex window layouts and browser tabs."""

    def __init__(self, tts_callback: Optional[Callable] = None):
        self._tts_callback = tts_callback
        self._status_callback = None
        self._thread = None
        self._running = threading.Event()
        get_watchdog().register(name="workspace_manager", heartbeat_interval=60.0)
        self._running.set()
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        logger.info("WorkspaceManager: initialized")

    def set_tts_callback(self, callback: Callable):
        self._tts_callback = callback

    def set_status_callback(self, callback: Callable):
        """Callback to update the floating panel UI progress."""
        self._status_callback = callback

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=2)
        get_watchdog().unregister("workspace_manager")

    def _heartbeat_loop(self):
        while self._running.is_set():
            get_watchdog().heartbeat("workspace_manager")
            time.sleep(30)

    def _notify_tts(self, message: str):
        if self._tts_callback:
            try:
                # Dispatch to asyncio loop if needed, assuming callback handles it
                self._tts_callback(message)
            except Exception as e:
                logger.debug(f"WorkspaceManager TTS failed: {e}")

    def _notify_status(self, message: str):
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                pass

    def save_workspace(self, name: str) -> str:
        """Capture the current workspace and save to SQLite."""
        self._notify_status("Saving workspace...")
        windows = []
        
        def enum_windows_callback(hwnd, win_list):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                rect = win32gui.GetWindowRect(hwnd)
                # Skip invalid/hidden windows
                if rect == (0, 0, 0, 0):
                    return
                # Skip Clicky overlays
                title = win32gui.GetWindowText(hwnd)
                if "Clicky" in title and "Overlay" in title:
                    return

                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    proc = psutil.Process(pid)
                    exe = proc.name().lower()
                    
                    # Exclude system/background exes
                    if exe in ["explorer.exe", "textinputhost.exe", "searchapp.exe"]:
                        return
                        
                    win_list.append({
                        "hwnd": hwnd,
                        "title": title,
                        "exe": exe,
                        "rect": rect,
                        "pid": pid
                    })
                except Exception:
                    pass

        win32gui.EnumWindows(enum_windows_callback, windows)

        # Sort by z-order
        z_ordered = []
        try:
            hwnd_top = win32gui.GetTopWindow(None)
            while hwnd_top:
                for w in windows:
                    if w["hwnd"] == hwnd_top:
                        z_ordered.append(w)
                        break
                hwnd_top = win32gui.GetWindow(hwnd_top, win32con.GW_HWNDNEXT)
        except Exception:
            # Fallback if z-order sorting fails
            z_ordered = windows

        snapshot_windows = []
        for i, w in enumerate(z_ordered):
            win_data = {
                "exe": w["exe"],
                "title": w["title"],
                "rect": w["rect"],
                "z_order": i
            }

            # 1. URL extraction for browsers
            if w["exe"] in ["chrome.exe", "msedge.exe", "firefox.exe"]:
                url = get_browser_url_uia(w["hwnd"])
                if url:
                    win_data["url"] = url
                elif w["exe"] == "firefox.exe":
                    # Firefox Fallback
                    title = w["title"]
                    if "— Mozilla Firefox" in title:
                        page_title = title.replace("— Mozilla Firefox", "").strip()
                    elif "- Mozilla Firefox" in title:
                        page_title = title.replace("- Mozilla Firefox", "").strip()
                    else:
                        page_title = title
                    win_data["fallback_title"] = page_title

            # 2. Generalized filename extraction
            title = w["title"]
            if "-" in title or "—" in title:
                sep = "—" if "—" in title else "-"
                parts = title.split(sep)
                if len(parts) > 1:
                    # Usually the first part or the part before the app name is the filename
                    # Let's try the first part first
                    candidate1 = parts[0].strip()
                    candidate2 = sep.join(parts[:-1]).strip() # Everything before the last separator
                    
                    filename_arg = None
                    if os.path.isabs(candidate1) and os.path.exists(candidate1):
                        filename_arg = candidate1
                    elif os.path.isabs(candidate2) and os.path.exists(candidate2):
                        filename_arg = candidate2
                    elif os.path.exists(candidate1):
                        filename_arg = candidate1
                    elif os.path.exists(candidate2):
                        filename_arg = candidate2
                        
                    if filename_arg:
                        win_data["filename_arg"] = filename_arg

            snapshot_windows.append(win_data)

        # Save to DB
        snapshot_json = json.dumps(snapshot_windows)
        get_db().save_workspace_snapshot(name, snapshot_json)
        
        msg = f"Saved workspace '{name}' with {len(snapshot_windows)} windows."
        self._notify_status("Workspace saved")
        self._notify_tts(msg)
        logger.info(msg)
        return msg

    def restore_workspace(self, name: str) -> str:
        """Restore a saved workspace layout."""
        self._notify_status(f"Loading workspace {name}...")
        snapshot = get_db().get_workspace_snapshot(name)
        if not snapshot:
            msg = f"Workspace '{name}' not found."
            self._notify_tts(msg)
            self._notify_status("Restore failed")
            return msg

        try:
            windows = json.loads(snapshot["snapshot_json"])
        except Exception as e:
            logger.error(f"Failed to parse workspace JSON: {e}")
            return "Failed to parse workspace data."

        if len(windows) > 10:
            logger.warning(f"Workspace {name} has >10 windows. Might need confirmation, but proceeding for now.")
            # Action Confirmation will handle the Confirm Once trust

        self._notify_tts(f"Restoring your workspace {name}.")
        
        # Determine primary screen bounds to reset lost windows
        import win32api
        monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))
        screen_rect = monitor_info.get("Monitor", (0, 0, 1920, 1080))
        screen_w = screen_rect[2] - screen_rect[0]
        screen_h = screen_rect[3] - screen_rect[1]

        skipped_apps = []
        restored_count = 0

        # Sort windows by reverse z-order so we bring the topmost to front last
        windows_to_restore = sorted(windows, key=lambda x: x.get("z_order", 0), reverse=True)

        for i, w in enumerate(windows_to_restore):
            exe = w["exe"]
            self._notify_status(f"Restoring {exe} ({i+1}/{len(windows_to_restore)})")
            
            # Check if running
            is_running = False
            target_pid = None
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'].lower() == exe:
                        is_running = True
                        target_pid = proc.info['pid']
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if not is_running:
                # Launch via existing actions logic
                from actions import execute_registered_action
                
                # Check if it's a browser with URL
                if "url" in w:
                    logger.info(f"Workspace: opening URL {w['url']} in {exe}")
                    execute_registered_action("OPEN", w["url"])
                    time.sleep(2.0)  # Give browser time to open
                else:
                    cmd = exe
                    if "filename_arg" in w:
                        cmd += f" \"{w['filename_arg']}\""
                    logger.info(f"Workspace: launching {cmd}")
                    
                    # Test if executable actually exists or is in PATH
                    import shutil
                    if shutil.which(exe) or os.path.exists(exe):
                        execute_registered_action("RUN", cmd)
                        time.sleep(1.5)  # Wait for window to appear
                    else:
                        skipped_apps.append(exe)
                        continue
            else:
                # App is running. If it's a browser and has URL, decide new window vs tab
                if "url" in w:
                    from actions import execute_registered_action
                    
                    # Count existing browser windows for this exe
                    browser_window_count = 0
                    def _count_browser_windows(hwnd, ctx):
                        if win32gui.IsWindowVisible(hwnd):
                            try:
                                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                                proc = psutil.Process(pid)
                                if proc.name().lower() == exe:
                                    ctx[0] += 1
                            except Exception:
                                pass
                    count_ctx = [0]
                    win32gui.EnumWindows(_count_browser_windows, count_ctx)
                    browser_window_count = count_ctx[0]
                    
                    if browser_window_count > 2:
                        # Active use — open in new window
                        logger.info(f"Workspace: {exe} has {browser_window_count} windows, opening URL in new window")
                        import subprocess
                        import shutil
                        browser_path = shutil.which(exe.replace(".exe", "")) or exe
                        try:
                            subprocess.Popen([browser_path, "--new-window", w["url"]])
                        except Exception:
                            execute_registered_action("OPEN", w["url"])
                    else:
                        logger.info(f"Workspace: opening URL {w['url']} in existing browser")
                        execute_registered_action("OPEN", w["url"])
                    time.sleep(1.0)

            # Attempt to find the window and move it
            # We must enumerate windows again because PIDs/HWnds change
            matched_hwnd = None
            def find_hwnd_callback(hwnd, ctx):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        proc = psutil.Process(pid)
                        if proc.name().lower() == exe:
                            # Heuristic: if we have filename_arg, match it in title
                            title = win32gui.GetWindowText(hwnd)
                            if "filename_arg" in w:
                                fname = os.path.basename(w["filename_arg"])
                                if fname.lower() in title.lower():
                                    ctx.append(hwnd)
                                    return
                            # Fallback Firefox title
                            if "fallback_title" in w:
                                if w["fallback_title"].lower() in title.lower():
                                    ctx.append(hwnd)
                                    return
                            # Otherwise just grab the first matching exe
                            if not ctx:
                                ctx.append(hwnd)
                    except Exception:
                        pass
                        
            hwnds = []
            win32gui.EnumWindows(find_hwnd_callback, hwnds)
            
            if hwnds:
                matched_hwnd = hwnds[-1] # take the most specific match
                rect = w["rect"]
                
                # Monitor bounds check
                # rect is (left, top, right, bottom)
                if rect[0] > screen_rect[2] or rect[2] < screen_rect[0] or rect[1] > screen_rect[3] or rect[3] < screen_rect[1]:
                    logger.warning(f"Workspace: window {exe} bounds out of screen. Resetting.")
                    rect = (100, 100, 900, 700) # Default safe bounds
                    
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                
                try:
                    # Un-minimize if minimized
                    placement = win32gui.GetWindowPlacement(matched_hwnd)
                    if placement[1] == win32con.SW_SHOWMINIMIZED:
                        win32gui.ShowWindow(matched_hwnd, win32con.SW_RESTORE)
                        
                    win32gui.SetForegroundWindow(matched_hwnd)
                    win32gui.MoveWindow(matched_hwnd, rect[0], rect[1], width, height, True)
                    restored_count += 1
                except Exception as e:
                    logger.debug(f"Workspace: failed to move {exe}: {e}")

        self._notify_status(f"Restored {restored_count} windows")
        
        msg = f"Workspace restoration complete."
        if skipped_apps:
            skipped_str = ", ".join(set(skipped_apps))
            msg += f" I skipped {skipped_str} because they are not installed."
            
        self._notify_tts(msg)
        return msg


# Singleton
_instance: Optional[WorkspaceManager] = None

def get_workspace_manager() -> WorkspaceManager:
    global _instance
    if _instance is None:
        _instance = WorkspaceManager()
    return _instance
