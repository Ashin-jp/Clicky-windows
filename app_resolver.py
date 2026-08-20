"""
app_resolver.py — Intelligent app name resolution.

Scans Windows registry App Paths, Start Menu shortcuts, and UWP apps.
Builds a fuzzy-searchable dictionary in SQLite. Resolves natural language
app names to executable paths with confidence scoring.
"""

import logging
import os
import re
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AppEntry:
    """A discovered application."""
    display_name: str
    exe_path: str
    source: str  # "registry", "startmenu", "uwp", "alias", "recent"
    launch_count: int = 0
    last_launched: float = 0.0


@dataclass
class ResolveResult:
    """Result of app name resolution."""
    found: bool
    exe_path: str = ""
    display_name: str = ""
    confidence: float = 0.0
    candidates: list = field(default_factory=list)  # Top 3 alternatives
    is_uwp: bool = False


# ─── Hardcoded Aliases ────────────────────────────────────────────────
DEFAULT_ALIASES = {
    # Browsers
    "browser": ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"],
    "chrome": ["chrome.exe"],
    "google chrome": ["chrome.exe"],
    "firefox": ["firefox.exe"],
    "edge": ["msedge.exe"],
    "microsoft edge": ["msedge.exe"],
    "brave": ["brave.exe"],
    # Editors
    "vs code": ["Code.exe"],
    "vscode": ["Code.exe"],
    "visual studio code": ["Code.exe"],
    "visual studio": ["devenv.exe"],
    "notepad": ["notepad.exe"],
    "notepad++": ["notepad++.exe"],
    "sublime": ["sublime_text.exe"],
    # System
    "terminal": ["wt.exe", "WindowsTerminal.exe", "cmd.exe"],
    "windows terminal": ["wt.exe"],
    "command prompt": ["cmd.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "task manager": ["Taskmgr.exe"],
    "file explorer": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "settings": ["ms-settings:"],
    "control panel": ["control.exe"],
    "calculator": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "snipping tool": ["SnippingTool.exe"],
    # Office
    "word": ["WINWORD.EXE"],
    "excel": ["EXCEL.EXE"],
    "powerpoint": ["POWERPNT.EXE"],
    "outlook": ["OUTLOOK.EXE"],
    "onenote": ["ONENOTE.EXE"],
    # Communication
    "discord": ["Discord.exe", "Update.exe --processStart Discord.exe"],
    "slack": ["slack.exe"],
    "teams": ["ms-teams.exe", "Teams.exe"],
    "zoom": ["Zoom.exe"],
    "telegram": ["Telegram.exe"],
    "whatsapp": ["WhatsApp.exe"],
    # Media
    "spotify": ["Spotify.exe"],
    "vlc": ["vlc.exe"],
    "music": ["Spotify.exe"],
    # Development
    "github desktop": ["GitHubDesktop.exe"],
    "git bash": ["git-bash.exe"],
    "postman": ["Postman.exe"],
    "docker": ["Docker Desktop.exe"],
    # Gaming
    "steam": ["steam.exe"],
    "epic games": ["EpicGamesLauncher.exe"],
    # Misc
    "obs": ["obs64.exe", "obs32.exe"],
    "7zip": ["7zFM.exe"],
    "winrar": ["WinRAR.exe"],
}


class AppResolver:
    """Discovers and resolves app names to executable paths."""

    def __init__(self):
        self._apps: dict[str, AppEntry] = {}  # lowercase name -> AppEntry
        self._aliases: dict[str, list[str]] = dict(DEFAULT_ALIASES)
        self._lock = threading.Lock()
        self._db = None
        self._scan_done = False
        self._user_aliases: dict[str, str] = {}  # user-defined aliases

        # Start background scan
        threading.Thread(target=self._full_scan, name="AppScanThread", daemon=True).start()
        logger.info("AppResolver: initialized, scanning...")

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def resolve(self, name: str) -> ResolveResult:
        """
        Resolve a natural language app name to an executable path.
        Returns ResolveResult with confidence scoring.
        """
        if not name or not name.strip():
            return ResolveResult(found=False)

        query = name.strip().lower()

        # Step 1: Check user aliases (highest priority)
        if query in self._user_aliases:
            path = self._user_aliases[query]
            return ResolveResult(found=True, exe_path=path,
                                 display_name=query, confidence=1.0)

        # Step 2: Check hardcoded aliases
        if query in self._aliases:
            for exe_name in self._aliases[query]:
                # Handle special URIs (ms-settings:, etc.)
                if ":" in exe_name and not exe_name[1] == ":":
                    return ResolveResult(found=True, exe_path=exe_name,
                                         display_name=query, confidence=0.95, is_uwp=True)

                # Search discovered apps for this exe
                full_path = self._find_exe_path(exe_name)
                if full_path:
                    return ResolveResult(found=True, exe_path=full_path,
                                         display_name=query, confidence=0.95)

                # Try launching by name directly (works for PATH apps)
                if self._exe_exists_in_path(exe_name):
                    return ResolveResult(found=True, exe_path=exe_name,
                                         display_name=query, confidence=0.90)

        # Step 3: Exact match in discovered apps
        with self._lock:
            if query in self._apps:
                app = self._apps[query]
                return ResolveResult(found=True, exe_path=app.exe_path,
                                     display_name=app.display_name, confidence=0.95)

            # Try without ".exe"
            query_exe = query if query.endswith(".exe") else query + ".exe"
            for key, app in self._apps.items():
                if app.exe_path.lower().endswith(query_exe.lower()):
                    return ResolveResult(found=True, exe_path=app.exe_path,
                                         display_name=app.display_name, confidence=0.90)

        # Step 4: Fuzzy match
        candidates = self._fuzzy_search(query)
        if candidates:
            best = candidates[0]
            if best["score"] > 70:
                return ResolveResult(
                    found=True, exe_path=best["path"],
                    display_name=best["name"], confidence=best["score"] / 100.0,
                    candidates=[c["name"] for c in candidates[:3]],
                )
            else:
                return ResolveResult(
                    found=False,
                    candidates=[c["name"] for c in candidates[:3]],
                )

        return ResolveResult(found=False)

    def add_alias(self, alias: str, exe_path: str):
        """Add a user-defined alias."""
        self._user_aliases[alias.lower()] = exe_path
        try:
            db = self._get_db()
            db.set_config(f"app_alias:{alias.lower()}", exe_path)
        except Exception:
            pass

    def record_launch(self, name: str, exe_path: str):
        """Record an app launch for recent-app ranking."""
        key = name.lower()
        with self._lock:
            if key in self._apps:
                self._apps[key].launch_count += 1
                self._apps[key].last_launched = time.time()
            else:
                self._apps[key] = AppEntry(
                    display_name=name, exe_path=exe_path,
                    source="recent", launch_count=1, last_launched=time.time(),
                )

    def get_running_apps(self) -> list[dict]:
        """Get list of currently visible windows."""
        apps = []
        try:
            import win32gui
            import win32process
            import psutil

            def enum_cb(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd)
                if not title or title in ("", "Program Manager", "Windows Input Experience"):
                    return
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    proc = psutil.Process(pid)
                    apps.append({
                        "title": title,
                        "exe": proc.name(),
                        "pid": pid,
                        "hwnd": hwnd,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            win32gui.EnumWindows(enum_cb, None)
        except Exception as e:
            logger.debug(f"AppResolver: get_running_apps failed: {e}")
        return apps

    def _find_exe_path(self, exe_name: str) -> Optional[str]:
        """Find full path for an exe name in discovered apps."""
        exe_lower = exe_name.lower()
        with self._lock:
            for key, app in self._apps.items():
                basename = os.path.basename(app.exe_path).lower()
                if basename == exe_lower:
                    return app.exe_path
        return None

    def _exe_exists_in_path(self, exe_name: str) -> bool:
        """Check if an exe exists in system PATH."""
        try:
            result = subprocess.run(
                ["where", exe_name], capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _fuzzy_search(self, query: str) -> list[dict]:
        """Fuzzy search against all known app names."""
        with self._lock:
            all_names = list(self._apps.keys())
            all_entries = dict(self._apps)

        # Add alias names too
        for alias in self._aliases:
            if alias not in all_names:
                all_names.append(alias)

        if not all_names:
            return []

        # Try rapidfuzz first
        try:
            from rapidfuzz import fuzz, process
            results = process.extract(query, all_names, scorer=fuzz.WRatio, limit=5)
            candidates = []
            for name, score, _ in results:
                if name in all_entries:
                    path = all_entries[name].exe_path
                elif name in self._aliases:
                    path = self._aliases[name][0]
                else:
                    path = name
                candidates.append({"name": name, "score": score, "path": path})
            return candidates
        except ImportError:
            pass

        # Fallback: simple substring matching
        candidates = []
        for name in all_names:
            if query in name or name in query:
                score = 80
            elif any(w in name for w in query.split()):
                score = 60
            else:
                continue

            if name in all_entries:
                path = all_entries[name].exe_path
            elif name in self._aliases:
                path = self._aliases[name][0]
            else:
                path = name
            candidates.append({"name": name, "score": score, "path": path})

        return sorted(candidates, key=lambda x: x["score"], reverse=True)[:5]

    def _full_scan(self):
        """Full app discovery scan (runs in background thread)."""
        try:
            self._scan_registry()
            self._scan_start_menu()
            self._scan_uwp()
            self._load_user_aliases()
            self._scan_done = True
            logger.info(f"AppResolver: scan complete, {len(self._apps)} apps found")
        except Exception as e:
            logger.error(f"AppResolver: scan failed: {e}")

    def _scan_registry(self):
        """Scan Windows App Paths registry keys."""
        keys_to_scan = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        for hkey, subkey_path in keys_to_scan:
            try:
                with winreg.OpenKey(hkey, subkey_path) as key:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as app_key:
                                try:
                                    exe_path, _ = winreg.QueryValueEx(app_key, "")
                                    exe_path = exe_path.strip('"')
                                    if exe_path and os.path.exists(exe_path):
                                        name = subkey_name.replace(".exe", "").lower()
                                        with self._lock:
                                            self._apps[name] = AppEntry(
                                                display_name=subkey_name.replace(".exe", ""),
                                                exe_path=exe_path,
                                                source="registry",
                                            )
                                except FileNotFoundError:
                                    pass
                            i += 1
                        except OSError:
                            break
            except OSError:
                pass

    def _scan_start_menu(self):
        """Scan Start Menu shortcuts for app targets."""
        start_menu_dirs = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        for sm_dir in start_menu_dirs:
            if not sm_dir.exists():
                continue
            for lnk_file in sm_dir.rglob("*.lnk"):
                try:
                    target = self._resolve_shortcut(str(lnk_file))
                    if target and os.path.exists(target):
                        name = lnk_file.stem.lower()
                        # Skip uninstallers
                        if "uninstall" in name or "remove" in name:
                            continue
                        with self._lock:
                            if name not in self._apps:
                                self._apps[name] = AppEntry(
                                    display_name=lnk_file.stem,
                                    exe_path=target,
                                    source="startmenu",
                                )
                except Exception:
                    pass

    def _resolve_shortcut(self, lnk_path: str) -> Optional[str]:
        """Resolve a .lnk shortcut to its target path."""
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(lnk_path)
            target = shortcut.Targetpath
            return target if target else None
        except Exception:
            # Fallback: read binary .lnk
            try:
                with open(lnk_path, "rb") as f:
                    data = f.read()
                # Very basic .lnk parsing — look for .exe path
                matches = re.findall(rb'([A-Za-z]:\\[^\x00]+?\.exe)', data)
                if matches:
                    path = matches[0].decode("utf-8", errors="ignore")
                    return path if os.path.exists(path) else None
            except Exception:
                pass
        return None

    def _scan_uwp(self):
        """Discover UWP/Store apps via PowerShell."""
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-AppxPackage | Where-Object {$_.IsFramework -eq $false} | "
                 "Select-Object Name, PackageFamilyName | "
                 "ForEach-Object { $_.Name + '|' + $_.PackageFamilyName }"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if "|" in line:
                        name, pfn = line.split("|", 1)
                        display = name.split(".")[-1].lower()
                        if display and len(display) > 2:
                            uri = f"shell:AppsFolder\\{pfn}!App"
                            with self._lock:
                                if display not in self._apps:
                                    self._apps[display] = AppEntry(
                                        display_name=name.split(".")[-1],
                                        exe_path=uri,
                                        source="uwp",
                                    )
        except Exception as e:
            logger.debug(f"AppResolver: UWP scan failed: {e}")

    def _load_user_aliases(self):
        """Load user-defined aliases from database."""
        try:
            db = self._get_db()
            # Check for stored aliases
            from sqlite3 import OperationalError
            rows = db._conn.execute(
                "SELECT key, value FROM config WHERE key LIKE 'app_alias:%'"
            ).fetchall()
            for row in rows:
                alias = row["key"].replace("app_alias:", "")
                self._user_aliases[alias] = row["value"]
        except Exception:
            pass


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[AppResolver] = None
_instance_lock = threading.Lock()


def get_app_resolver() -> AppResolver:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AppResolver()
        return _instance
