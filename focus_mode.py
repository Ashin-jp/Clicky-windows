"""
focus_mode.py — Distraction monitoring and focus session tracking.

Monitors foreground window every 5 seconds during focus sessions.
Nudges user via TTS after 10 seconds on distraction apps.
Supports snooze and session summaries.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger(__name__)

DEFAULT_DISTRACTION_APPS = [
    "chrome.exe", "firefox.exe", "msedge.exe",
    "discord.exe", "slack.exe", "telegram.exe",
    "spotify.exe", "steam.exe", "epicgameslauncher.exe",
    "twitter.exe", "instagram.exe",
]


class FocusMode:
    """Distraction monitoring with TTS nudges and session tracking."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._tts_callback: Optional[Callable] = None
        self._db = None

        self._session_id: Optional[int] = None
        self._planned_minutes: int = 25
        self._start_time: float = 0
        self._focused_secs: int = 0
        self._distracted_secs: int = 0
        self._distraction_apps_seen: set = set()

        self._snooze_until: float = 0
        self._distraction_start: float = 0
        self._nudge_delay: float = 10.0  # seconds before nudge
        self._last_nudge: float = 0
        self._nudge_cooldown: float = 60.0

        self._distraction_list: list[str] = list(DEFAULT_DISTRACTION_APPS)

        logger.info("FocusMode: initialized")

    def set_tts_callback(self, callback: Callable):
        self._tts_callback = callback

    def set_distraction_apps(self, apps: list[str]):
        self._distraction_list = [a.lower() for a in apps]

    def is_active(self) -> bool:
        return self._running.is_set()

    def activate(self, minutes: int = 25):
        """Start a focus session."""
        if self._running.is_set():
            return

        self._planned_minutes = minutes
        self._start_time = time.monotonic()
        self._focused_secs = 0
        self._distracted_secs = 0
        self._distraction_apps_seen = set()
        self._distraction_start = 0
        self._snooze_until = 0

        # Create DB session
        try:
            self._get_db()
            self._session_id = self._db.start_focus_session(minutes)
        except Exception as e:
            logger.debug(f"FocusMode: DB error: {e}")
            self._session_id = None

        self._running.set()
        self._thread = threading.Thread(target=self._monitor_loop, name="FocusModeThread", daemon=True)
        self._thread.start()

        self._notify(f"Focus mode activated for {minutes} minutes. Stay focused!")
        logger.info(f"FocusMode: activated for {minutes} minutes")

    def deactivate(self):
        """End the focus session with summary."""
        if not self._running.is_set():
            return

        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)

        # Generate summary
        total = int(time.monotonic() - self._start_time)
        total_min = total // 60
        focused_min = self._focused_secs // 60

        summary = f"Focus session ended. You focused for {focused_min} of {total_min} minutes."
        if self._distraction_apps_seen:
            summary += f" Distractions: {', '.join(self._distraction_apps_seen)}."

        self._notify(summary)

        # Update DB
        if self._session_id:
            try:
                apps_str = ",".join(self._distraction_apps_seen)
                self._db.update_focus_session(self._session_id, self._focused_secs,
                                              self._distracted_secs, apps_str)
                self._db.end_focus_session(self._session_id)
            except Exception:
                pass

        logger.info(f"FocusMode: deactivated ({focused_min}/{total_min} min focused)")

    def snooze(self, seconds: int = 300):
        """Snooze nudges for N seconds."""
        self._snooze_until = time.monotonic() + seconds
        self._notify(f"Focus nudges snoozed for {seconds // 60} minutes.")

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def _monitor_loop(self):
        poll_interval = 5.0
        while self._running.is_set():
            try:
                self._check_focus()
            except Exception as e:
                logger.debug(f"FocusMode: monitor error: {e}")

            # Check if session time expired
            elapsed = time.monotonic() - self._start_time
            if elapsed >= self._planned_minutes * 60:
                self.deactivate()
                return

            for _ in range(int(poll_interval * 2)):
                if not self._running.is_set():
                    return
                time.sleep(0.5)

    def _check_focus(self):
        """Check if user is on a distraction app."""
        try:
            from uia_helper import get_foreground_app_name
            app = get_foreground_app_name()
        except Exception:
            app = None

        if not app:
            self._focused_secs += 5
            return

        app_lower = app.lower()
        is_distraction = app_lower in self._distraction_list

        if is_distraction:
            self._distracted_secs += 5
            self._distraction_apps_seen.add(app.replace(".exe", ""))

            now = time.monotonic()
            if self._distraction_start == 0:
                self._distraction_start = now

            in_distraction = now - self._distraction_start
            is_snoozed = now < self._snooze_until
            nudge_ready = (now - self._last_nudge) > self._nudge_cooldown

            if in_distraction > self._nudge_delay and not is_snoozed and nudge_ready:
                app_name = app.replace(".exe", "")
                self._notify(f"You've switched to {app_name}. Back to work?")
                self._last_nudge = now
        else:
            self._focused_secs += 5
            self._distraction_start = 0

        # Update DB periodically
        if self._session_id and (self._focused_secs + self._distracted_secs) % 30 == 0:
            try:
                apps_str = ",".join(self._distraction_apps_seen)
                self._db.update_focus_session(self._session_id, self._focused_secs,
                                              self._distracted_secs, apps_str)
            except Exception:
                pass

    def _notify(self, text: str):
        logger.info(f"FocusMode: {text}")
        if self._tts_callback:
            try:
                self._tts_callback(text)
            except Exception:
                pass


_instance: Optional[FocusMode] = None

def get_focus_mode() -> FocusMode:
    global _instance
    if _instance is None:
        _instance = FocusMode()
    return _instance
