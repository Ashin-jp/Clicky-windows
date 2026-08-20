"""
clipboard_monitor.py — Background clipboard intelligence.

Polls clipboard every 500ms, detects content type via regex,
stores history in SQLite, and provides proactive suggestions.
"""

import logging
import re
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Content Type Detection Patterns ─────────────────────────────────
_PATTERNS = [
    ("url", re.compile(r"^https?://\S+$", re.MULTILINE)),
    ("email", re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")),
    ("file_path", re.compile(r"^[A-Za-z]:\\[\w\\\s.\-]+$|^/[\w/.\-]+$", re.MULTILINE)),
    ("hex_color", re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")),
    ("phone", re.compile(r"^[\+]?[\d\s\-\(\)]{7,15}$")),
    ("python_code", re.compile(r"(?:^(?:def |class |import |from |if |for |while |print\(|#)|    )", re.MULTILINE)),
    ("json", re.compile(r"^\s*[\{\[]", re.MULTILINE)),
]


def detect_content_type(text: str) -> str:
    """Detect the type of clipboard content."""
    if not text or not text.strip():
        return "empty"

    stripped = text.strip()

    for type_name, pattern in _PATTERNS:
        if pattern.search(stripped):
            return type_name

    if len(stripped) > 500:
        return "long_text"
    return "plain_text"


class ClipboardMonitor:
    """
    Background thread polling clipboard every 500ms.
    Detects content type, stores history, offers proactive suggestions.
    """

    def __init__(self, poll_interval: float = 0.5):
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._last_content: str = ""
        self._last_type: str = "empty"
        self._last_suggestion_time: dict[str, float] = {}
        self._suggestion_cooldown = 60.0  # seconds between same-type suggestions
        self._db = None
        self._on_suggestion_callback = None
        self._ambient_context = None

        logger.info("ClipboardMonitor: created")

    def start(self):
        """Start clipboard monitoring."""
        if self._thread and self._thread.is_alive():
            return

        self._running.set()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="ClipboardMonitorThread",
            daemon=True,
        )
        self._thread.start()
        logger.info("ClipboardMonitor: started")

    def stop(self):
        """Stop clipboard monitoring."""
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("ClipboardMonitor: stopped")

    def set_suggestion_callback(self, callback):
        """Set callback for proactive suggestions: callback(suggestion_text: str)."""
        self._on_suggestion_callback = callback

    def get_current_type(self) -> str:
        """Get the current clipboard content type."""
        return self._last_type

    def get_current_content(self) -> str:
        """Get the current clipboard content."""
        return self._last_content

    def get_recent(self, limit: int = 5) -> list[dict]:
        """Get recent clipboard entries from database."""
        try:
            db = self._get_db()
            return db.get_recent_clipboard(limit)
        except Exception as e:
            logger.debug(f"ClipboardMonitor: get_recent failed: {e}")
            return []

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def _poll_loop(self):
        """Main polling loop."""
        while self._running.is_set():
            try:
                self._check_clipboard()
            except Exception as e:
                logger.debug(f"ClipboardMonitor: poll error: {e}")

            # Sleep in small chunks for clean shutdown
            for _ in range(int(self._poll_interval * 10)):
                if not self._running.is_set():
                    return
                time.sleep(0.1)

    def _check_clipboard(self):
        """Check clipboard for changes."""
        try:
            import pyperclip
            content = pyperclip.paste()
        except Exception:
            return

        if not content or content == self._last_content:
            return

        # New content detected
        self._last_content = content
        content_type = detect_content_type(content)
        self._last_type = content_type

        # Update ambient context engine
        try:
            if self._ambient_context is None:
                from ambient_context import get_ambient_context
                self._ambient_context = get_ambient_context()
            self._ambient_context.update_clipboard_type(content_type)
        except Exception:
            pass

        # Store in database
        try:
            preview = content[:200].replace("\n", " ")
            self._get_db().save_clipboard_entry(preview, content_type, content)
        except Exception as e:
            logger.debug(f"ClipboardMonitor: DB save failed: {e}")

        logger.debug(f"ClipboardMonitor: new {content_type} ({len(content)} chars)")

        # Proactive suggestions
        self._maybe_suggest(content_type, content)

    def _maybe_suggest(self, content_type: str, content: str):
        """Generate proactive suggestion if appropriate."""
        if not self._on_suggestion_callback:
            return

        # Throttle: same type not re-suggested within cooldown
        now = time.monotonic()
        last = self._last_suggestion_time.get(content_type, 0)
        if now - last < self._suggestion_cooldown:
            return

        suggestion = None
        if content_type == "url":
            suggestion = "I see you copied a URL. Want me to summarize that page?"
        elif content_type == "python_code":
            suggestion = "I see you copied some Python code. Want me to explain or run it?"
        elif content_type == "file_path":
            suggestion = "I see you copied a file path. Want me to open that file?"
        elif content_type == "json":
            suggestion = "I see you copied some JSON. Want me to format or explain it?"

        if suggestion:
            self._last_suggestion_time[content_type] = now
            try:
                self._on_suggestion_callback(suggestion)
            except Exception as e:
                logger.debug(f"ClipboardMonitor: suggestion callback failed: {e}")


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[ClipboardMonitor] = None


def get_clipboard_monitor() -> ClipboardMonitor:
    global _instance
    if _instance is None:
        _instance = ClipboardMonitor()
    return _instance
