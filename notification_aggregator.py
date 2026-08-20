"""
notification_aggregator.py — Windows toast notification capture and summary.

Stores notifications in SQLite, deduplicates, detects priority,
and provides voice-queryable summaries.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Callable

logger = logging.getLogger(__name__)

PRIORITY_KEYWORDS = ["error", "failed", "urgent", "otp", "payment", "warning",
                     "critical", "alert", "security", "expired", "deadline"]


class NotificationAggregator:
    """Captures and summarizes Windows notifications."""

    def __init__(self):
        self._db = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._recent_hashes: set = set()  # dedup within 60s
        logger.info("NotificationAggregator: initialized")

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def start(self):
        """Start notification monitoring."""
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._monitor_loop,
                                        name="NotificationThread", daemon=True)
        self._thread.start()
        logger.info("NotificationAggregator: started")

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)

    def log_notification(self, source_app: str, title: str, body: str = ""):
        """Manually log a notification (used by other components)."""
        # Dedup
        dedup_key = f"{source_app}:{title}"
        if dedup_key in self._recent_hashes:
            return
        self._recent_hashes.add(dedup_key)

        # Auto-expire dedup entries after 60s
        threading.Timer(60.0, lambda: self._recent_hashes.discard(dedup_key)).start()

        # Priority detection
        combined = (title + " " + body).lower()
        is_priority = any(kw in combined for kw in PRIORITY_KEYWORDS)

        try:
            self._get_db().save_notification(source_app, title, body, is_priority)
        except Exception as e:
            logger.debug(f"NotificationAggregator: save failed: {e}")

    def get_summary(self, hours: int = 1) -> str:
        """Get TTS-ready summary of recent notifications."""
        try:
            notifs = self._get_db().get_recent_notifications(hours=hours)
        except Exception:
            return "I couldn't check your notifications."

        if not notifs:
            return f"No notifications in the last {'hour' if hours == 1 else f'{hours} hours'}."

        count = len(notifs)
        priority = [n for n in notifs if n.get("priority_flag")]
        apps = set(n.get("source_app", "") for n in notifs)

        parts = [f"You have {count} notification{'s' if count != 1 else ''} "
                 f"from {len(apps)} app{'s' if len(apps) != 1 else ''}."]

        if priority:
            parts.append(f"{len(priority)} marked as important.")
            for p in priority[:3]:
                parts.append(f"{p.get('source_app', 'Unknown')}: {p.get('title', '')}")

        # Top 3 non-priority
        regular = [n for n in notifs if not n.get("priority_flag")][:3]
        if regular:
            for r in regular:
                parts.append(f"{r.get('source_app', 'Unknown')}: {r.get('title', '')}")

        return " ".join(parts)

    def get_urgent_summary(self) -> str:
        """Get only urgent/priority notifications."""
        try:
            notifs = self._get_db().get_recent_notifications(hours=24, priority_only=True)
        except Exception:
            return "Couldn't check urgent notifications."

        if not notifs:
            return "No urgent notifications."

        parts = [f"{len(notifs)} urgent notification{'s' if len(notifs) != 1 else ''}:"]
        for n in notifs[:5]:
            parts.append(f"{n.get('source_app', '')}: {n.get('title', '')}")
        return " ".join(parts)

    def get_by_app(self, app_name: str) -> str:
        """Get notifications filtered by app."""
        try:
            all_notifs = self._get_db().get_recent_notifications(hours=24)
            filtered = [n for n in all_notifs
                        if app_name.lower() in n.get("source_app", "").lower()]
        except Exception:
            return f"Couldn't check notifications from {app_name}."

        if not filtered:
            return f"No notifications from {app_name} in the last 24 hours."

        parts = [f"{len(filtered)} notification{'s' if len(filtered) != 1 else ''} from {app_name}:"]
        for n in filtered[:5]:
            parts.append(n.get("title", ""))
        return " ".join(parts)

    def _monitor_loop(self):
        """Monitor for Windows toast notifications."""
        # Try to use windows-toasts for capture
        try:
            self._monitor_toasts()
        except ImportError:
            logger.info("NotificationAggregator: windows-toasts not installed, passive mode only")
        except Exception as e:
            logger.debug(f"NotificationAggregator: toast monitoring failed: {e}")

    def _monitor_toasts(self):
        """Monitor using Action Center history via PowerShell."""
        import subprocess
        while self._running.is_set():
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "[Windows.UI.Notifications.Management.UserNotificationListener,"
                     "Windows.UI.Notifications.Management,ContentType=WindowsRuntime] | Out-Null;"
                     "$listener = [Windows.UI.Notifications.Management.UserNotificationListener]::Current;"
                     "$notifs = $listener.GetNotificationsAsync("
                     "[Windows.UI.Notifications.Management.NotificationKinds]::Toast).GetAwaiter().GetResult();"
                     "foreach($n in $notifs){Write-Output \"$($n.AppInfo.DisplayInfo.DisplayName)|$($n.Notification.Visual.GetBinding("
                     "[Windows.UI.Notifications.KnownNotificationBindings]::ToastGeneric).GetTextElements()[0].Text)\"}"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.stdout:
                    for line in result.stdout.strip().split("\n"):
                        if "|" in line:
                            app, title = line.split("|", 1)
                            self.log_notification(app.strip(), title.strip())
            except Exception:
                pass

            for _ in range(60):  # Check every 30 seconds
                if not self._running.is_set():
                    return
                time.sleep(0.5)


_instance: Optional[NotificationAggregator] = None

def get_notification_aggregator() -> NotificationAggregator:
    global _instance
    if _instance is None:
        _instance = NotificationAggregator()
    return _instance
