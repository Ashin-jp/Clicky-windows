"""
ambient_context.py — Background context engine.

Daemon thread polling at near-zero CPU cost. Snapshots foreground app
every 30 seconds via UIA, maintains 2-hour ring buffer, and exposes
pre-assembled context for injection into AI calls.
"""

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContextSnapshot:
    """A single ambient context snapshot."""
    timestamp: str
    app_name: str
    window_title: str
    focused_element_type: str = ""
    clipboard_type: str = ""
    duration_in_app: float = 0.0  # seconds in current app
    previous_app: str = ""


@dataclass
class AssembledContext:
    """Pre-assembled context for AI calls."""
    active_app: str
    window_title: str
    time_in_app_seconds: float
    previous_app: str
    clipboard_type: str
    focused_element: str
    recent_conversation: list  # Last 3 turns
    timestamp: str
    uia_tree_text: str = ""  # Optional UIA tree for screen context

    def to_context_string(self) -> str:
        """Format as a string for injection into AI system prompt."""
        parts = [
            f"[Context] Time: {self.timestamp}",
            f"Active app: {self.active_app}",
            f"Window: {self.window_title}",
        ]
        if self.time_in_app_seconds > 60:
            mins = int(self.time_in_app_seconds / 60)
            parts.append(f"Time in app: {mins} minutes")
        if self.previous_app and self.previous_app != self.active_app:
            parts.append(f"Previous app: {self.previous_app}")
        if self.clipboard_type and self.clipboard_type != "empty":
            parts.append(f"Clipboard contains: {self.clipboard_type}")
        if self.focused_element:
            parts.append(f"Focused on: {self.focused_element}")
        if self.uia_tree_text:
            parts.append(f"Screen content:\n{self.uia_tree_text[:1000]}")
        return " | ".join(parts)


class AmbientContextEngine:
    """
    Background daemon that captures ambient context snapshots.
    Near-zero CPU cost — polls every 30 seconds.
    Memory cost: ~5MB for 2-hour ring buffer (240 entries max).
    """

    def __init__(self, poll_interval: float = 30.0, max_entries: int = 240):
        self._poll_interval = poll_interval
        self._buffer: deque[ContextSnapshot] = deque(maxlen=max_entries)
        self._current_app: str = ""
        self._current_app_start: float = 0.0
        self._previous_app: str = ""
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._recent_conversation: list = []  # Last 3 (role, content) pairs
        self._clipboard_type: str = "empty"
        self._db = None
        self._own_loop: Optional[asyncio.AbstractEventLoop] = None
        self._heartbeat = threading.Event()  # Set after each successful poll

        self.linux_mode_active = False
        self.linux_error_callback = None

        logger.info("AmbientContext: engine created")

    def start(self):
        """Start the background polling thread with its own independent event loop."""
        if self._thread and self._thread.is_alive():
            return

        self._running.set()
        self._heartbeat.set()  # Mark as alive immediately
        self._thread = threading.Thread(
            target=self._run_isolated_loop,
            name="AmbientContextThread",
            daemon=True,
        )
        self._thread.start()
        logger.info("AmbientContext: started (isolated loop)")

    def stop(self):
        """Stop the background polling thread."""
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._own_loop:
            self._own_loop.call_soon_threadsafe(self._own_loop.stop)
            self._own_loop = None
        logger.info("AmbientContext: stopped")

    def is_healthy(self) -> bool:
        """Check if the ambient context loop is still producing heartbeats.
        Used by the watchdog to avoid false-restarts."""
        alive = self._heartbeat.is_set()
        self._heartbeat.clear()  # Reset for next check cycle
        return alive

    def update_conversation(self, role: str, content: str):
        """Update recent conversation turns (called by companion_manager)."""
        with self._lock:
            self._recent_conversation.append((role, content))
            # Keep last 6 entries (3 exchanges)
            if len(self._recent_conversation) > 6:
                self._recent_conversation = self._recent_conversation[-6:]

    def update_clipboard_type(self, clip_type: str):
        """Update current clipboard content type (called by clipboard_monitor)."""
        with self._lock:
            self._clipboard_type = clip_type

    def get_current_context(self) -> AssembledContext:
        """
        Get pre-assembled context for AI call injection.
        This is the primary API — called by companion_manager before every AI call.
        Returns immediately from cached data, never blocks.
        """
        with self._lock:
            now = datetime.now()
            duration = time.monotonic() - self._current_app_start if self._current_app_start else 0

            # Get focused element info (fast, cached from last poll)
            focused_desc = ""
            if self._buffer:
                latest = self._buffer[-1]
                focused_desc = latest.focused_element_type

            return AssembledContext(
                active_app=self._current_app or "unknown",
                window_title=self._get_window_title() or "",
                time_in_app_seconds=duration,
                previous_app=self._previous_app,
                clipboard_type=self._clipboard_type,
                focused_element=focused_desc,
                recent_conversation=list(self._recent_conversation[-6:]),
                timestamp=now.strftime("%H:%M:%S"),
            )

    def get_context_with_screen(self) -> AssembledContext:
        """Get context WITH UIA tree (slightly slower, for screen-aware queries)."""
        ctx = self.get_current_context()
        try:
            from uia_helper import get_app_tree_as_text
            tree = get_app_tree_as_text(max_depth=3, max_chars=1500)
            if tree:
                ctx.uia_tree_text = tree
        except Exception as e:
            logger.debug(f"AmbientContext: UIA tree failed: {e}")
        return ctx

    def get_history(self, minutes: int = 30) -> list[ContextSnapshot]:
        """Get context history for the last N minutes."""
        with self._lock:
            cutoff = datetime.now().timestamp() - (minutes * 60)
            result = []
            for snap in self._buffer:
                try:
                    ts = datetime.fromisoformat(snap.timestamp).timestamp()
                    if ts >= cutoff:
                        result.append(snap)
                except (ValueError, TypeError):
                    result.append(snap)
            return result

    def _get_window_title(self) -> Optional[str]:
        """Get current window title (fast, no UIA)."""
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            return win32gui.GetWindowText(hwnd) if hwnd else None
        except Exception:
            return None

    def _run_isolated_loop(self):
        """Run the ambient context on its own independent asyncio event loop.
        This prevents any main loop disruption from affecting the heartbeat."""
        self._own_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._own_loop)
        try:
            self._own_loop.run_until_complete(self._async_poll_loop())
        except Exception as e:
            logger.error(f"AmbientContext: isolated loop crashed: {e}")
        finally:
            self._own_loop.close()
            self._own_loop = None

    async def _async_poll_loop(self):
        """Async polling loop running on the isolated event loop."""
        logger.debug("AmbientContext: async poll loop started")

        while self._running.is_set():
            try:
                self._capture_snapshot()
                self._heartbeat.set()  # Signal watchdog: still alive
            except Exception as e:
                logger.debug(f"AmbientContext: snapshot error: {e}")

            # Async sleep in small increments for clean shutdown
            for _ in range(int(self._poll_interval * 2)):
                if not self._running.is_set():
                    break
                await asyncio.sleep(0.5)

    def _poll_loop(self):
        """Legacy sync polling loop — kept as fallback."""
        logger.debug("AmbientContext: poll loop started")

        while self._running.is_set():
            try:
                self._capture_snapshot()
            except Exception as e:
                logger.debug(f"AmbientContext: snapshot error: {e}")

            # Sleep in small increments to allow clean shutdown
            for _ in range(int(self._poll_interval * 2)):
                if not self._running.is_set():
                    break
                time.sleep(0.5)

    def _capture_snapshot(self):
        """Capture a single ambient context snapshot."""
        try:
            from uia_helper import get_foreground_app_name, get_window_title, get_focused_element_info

            app_name = get_foreground_app_name() or "unknown"
            window_title = get_window_title() or ""

            # Track app transitions
            with self._lock:
                if app_name != self._current_app:
                    self._previous_app = self._current_app
                    self._current_app = app_name
                    self._current_app_start = time.monotonic()

                duration = time.monotonic() - self._current_app_start if self._current_app_start else 0

            # Get focused element type (fast)
            focused_type = ""
            try:
                elem = get_focused_element_info()
                if elem:
                    focused_type = elem.control_type
            except Exception:
                pass

            snapshot = ContextSnapshot(
                timestamp=datetime.now().isoformat(),
                app_name=app_name,
                window_title=window_title,
                focused_element_type=focused_type,
                clipboard_type=self._clipboard_type,
                duration_in_app=duration,
                previous_app=self._previous_app,
            )

            with self._lock:
                self._buffer.append(snapshot)

            # Log to database if available
            self._log_to_db(snapshot)

            # Linux error detection
            if self.linux_mode_active and app_name.lower() in ("cmd.exe", "windowsterminal.exe", "powershell.exe", "wsl.exe", "ubuntu.exe"):
                self._detect_linux_errors()

        except Exception as e:
            logger.debug(f"AmbientContext: capture failed: {e}")

    def _log_to_db(self, snapshot: ContextSnapshot):
        """Log snapshot to SQLite ambient_context_log table.
        Uses its own DB connection to avoid cross-thread SQLite issues."""
        try:
            if self._db is None:
                from storage import ClickyDatabase
                self._db = ClickyDatabase()  # Fresh connection for this thread

            self._db.log_ambient_context(
                app_name=snapshot.app_name,
                window_title=snapshot.window_title,
                focused_element=snapshot.focused_element_type,
                duration_in_app=snapshot.duration_in_app,
                previous_app=snapshot.previous_app,
            )
        except Exception as e:
            logger.debug(f"AmbientContext: DB log failed: {e}")

    def _detect_linux_errors(self):
        """Scan terminal output for common Linux error signatures."""
        try:
            from uia_helper import get_app_tree_as_text
            tree = get_app_tree_as_text(depth=3, max_chars=1000)
            if not tree:
                return
            
            tree_lower = tree.lower()
            error_signatures = [
                "command not found", "permission denied", "syntax error",
                "segmentation fault", "no such file or directory", "fatal error:"
            ]
            
            for sig in error_signatures:
                if sig in tree_lower:
                    if self.linux_error_callback:
                        self.linux_error_callback(sig)
                    break
        except Exception as e:
            logger.debug(f"AmbientContext: Linux error detection failed: {e}")


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[AmbientContextEngine] = None
_instance_lock = threading.Lock()


def get_ambient_context() -> AmbientContextEngine:
    """Get or create the singleton AmbientContextEngine."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AmbientContextEngine()
        return _instance
