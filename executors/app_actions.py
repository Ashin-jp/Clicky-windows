"""
executors/app_actions.py — Application lifecycle actions.

Registers: RUN, CLOSE_APP, SWITCH_TO_APP, LIST_OPEN_APPS, RESTART_APP, APP_VOLUME
Uses app_resolver.py for intelligent app name resolution.
"""

import logging
import subprocess
import time

from executors import register_action, ActionResult
from app_resolver import get_app_resolver

logger = logging.getLogger(__name__)


@register_action("RUN", "🚀 Launch", "Launch an application", "system")
def handle_run(params: str) -> ActionResult:
    """Launch an app using the intelligent app resolver."""
    resolver = get_app_resolver()
    result = resolver.resolve(params)
    
    if result.found:
        try:
            if result.is_uwp:
                import os
                os.startfile(result.exe_path)
            else:
                # Use Popen to launch and detach
                subprocess.Popen(result.exe_path, shell=True)
            resolver.record_launch(result.display_name, result.exe_path)
            return ActionResult(success=True, message=f"Opening {result.display_name}")
        except Exception as e:
            return ActionResult(success=False, message=f"Failed to launch {result.display_name}: {e}")
    else:
        if result.candidates:
            cands = ", ".join(result.candidates)
            return ActionResult(success=False, message=f"I couldn't find that app. Did you mean {cands}?")
        return ActionResult(success=False, message="I couldn't find that app. What's it called in your Start Menu?")


@register_action("CLOSE_APP", "❌ Close App", "Close a running application", "system")
def handle_close_app(params: str) -> ActionResult:
    """Close an app by name."""
    import psutil
    
    resolver = get_app_resolver()
    result = resolver.resolve(params)
    
    if not result.found:
        return ActionResult(success=False, message="I couldn't find that app to close.")
        
    closed = 0
    exe_name = result.exe_path.split('\\')[-1].lower() if '\\' in result.exe_path else result.exe_path.lower()
    
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == exe_name:
                proc.terminate()
                closed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
            
    if closed > 0:
        return ActionResult(success=True, message=f"Closed {result.display_name}")
    return ActionResult(success=False, message=f"{result.display_name} doesn't seem to be running.")


@register_action("SWITCH_TO_APP", "🔄 Switch", "Bring application to foreground", "system")
def handle_switch_to_app(params: str) -> ActionResult:
    """Bring an app's window to the foreground."""
    import win32gui
    import win32con
    
    resolver = get_app_resolver()
    result = resolver.resolve(params)
    
    if not result.found:
        return ActionResult(success=False, message="I couldn't find that app to switch to.")
        
    running = resolver.get_running_apps()
    exe_name = result.exe_path.split('\\')[-1].lower() if '\\' in result.exe_path else result.exe_path.lower()
    
    for app in running:
        if app["exe"] and app["exe"].lower() == exe_name:
            try:
                # Restore if minimized
                win32gui.ShowWindow(app["hwnd"], win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(app["hwnd"])
                return ActionResult(success=True, message=f"Switched to {result.display_name}")
            except Exception:
                pass
                
    return ActionResult(success=False, message=f"{result.display_name} doesn't seem to be running.")


@register_action("LIST_OPEN_APPS", "📋 List Apps", "List running applications", "system")
def handle_list_open_apps(params: str) -> ActionResult:
    """Read aloud the currently open apps."""
    resolver = get_app_resolver()
    running = resolver.get_running_apps()
    
    if not running:
        return ActionResult(success=True, message="No visible apps are currently running.")
        
    # Deduplicate titles/exes to make it sound natural
    seen_exes = set()
    clean_titles = []
    for app in running:
        if app["exe"] not in seen_exes:
            seen_exes.add(app["exe"])
            # Clean up generic titles
            title = app["title"].split('-')[-1].strip()
            clean_titles.append(title)
            
    if len(clean_titles) > 5:
        app_list = ", ".join(clean_titles[:5])
        message = f"You have {len(running)} apps open. Some of them are: {app_list}"
    else:
        app_list = ", ".join(clean_titles)
        message = f"You have these apps open: {app_list}"
        
    return ActionResult(success=True, message=message)


@register_action("RESTART_APP", "🔄 Restart", "Restart an application", "system")
def handle_restart_app(params: str) -> ActionResult:
    """Close and then re-launch an app."""
    close_res = handle_close_app(params)
    time.sleep(1.0)
    run_res = handle_run(params)
    
    if run_res.success:
        return ActionResult(success=True, message=f"Restarted {params}")
    return ActionResult(success=False, message=f"Failed to restart {params}")


@register_action("APP_VOLUME", "🔊 App Volume", "Adjust volume for a specific app", "system")
def handle_app_volume(params: str) -> ActionResult:
    """Adjust per-app volume using pycaw."""
    # Expected param format: "Chrome|mute" or "Spotify|lower" or just "Spotify" (defaults to mute/unmute toggle)
    parts = params.split("|", 1)
    app_name = parts[0]
    action = parts[1].lower() if len(parts) > 1 else "mute"
    
    try:
        from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
        
        resolver = get_app_resolver()
        result = resolver.resolve(app_name)
        
        if not result.found:
            return ActionResult(success=False, message="I couldn't find that app.")
            
        exe_name = result.exe_path.split('\\')[-1].lower() if '\\' in result.exe_path else result.exe_path.lower()
        
        sessions = AudioUtilities.GetAllSessions()
        found = False
        msg = ""
        for session in sessions:
            if session.Process and session.Process.name().lower() == exe_name:
                volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                
                if action == "mute":
                    volume.SetMute(1, None)
                    msg = f"Muted {result.display_name}"
                elif action == "unmute":
                    volume.SetMute(0, None)
                    msg = f"Unmuted {result.display_name}"
                elif action == "lower":
                    current = volume.GetMasterVolume()
                    volume.SetMasterVolume(max(0.0, current - 0.2), None)
                    msg = f"Lowered volume for {result.display_name}"
                elif action == "raise" or action == "increase":
                    current = volume.GetMasterVolume()
                    volume.SetMasterVolume(min(1.0, current + 0.2), None)
                    msg = f"Raised volume for {result.display_name}"
                else:
                    # Toggle mute
                    is_muted = volume.GetMute()
                    volume.SetMute(0 if is_muted else 1, None)
                    msg = f"{'Unmuted' if is_muted else 'Muted'} {result.display_name}"
                    
                found = True
                break
        
        if found:
            return ActionResult(success=True, message=msg)
        return ActionResult(success=False, message=f"{result.display_name} doesn't seem to be playing audio.")
        
    except ImportError:
        return ActionResult(success=False, message="I need the pycaw library to adjust app volumes.")
