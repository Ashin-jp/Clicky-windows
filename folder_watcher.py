"""
folder_watcher.py — Filesystem Monitoring

Watches directories for file changes and triggers notifications
or callback actions. Uses watchdog for cross-platform filesystem events.
"""

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer

from storage import get_db

logger = logging.getLogger(__name__)

# Try to import watchdog, fall back to polling if not available
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logger.warning("watchdog not installed — folder watching will use polling")


class FolderEvent:
    """A detected filesystem event."""
    def __init__(self, event_type: str, path: str, is_directory: bool = False):
        self.event_type = event_type  # created, modified, deleted, moved
        self.path = path
        self.is_directory = is_directory
        self.timestamp = time.time()

    def __repr__(self):
        return f"FolderEvent({self.event_type}, {self.path})"


if HAS_WATCHDOG:
    class _WatchdogHandler(FileSystemEventHandler):
        """Bridges watchdog events to Qt signals."""

        def __init__(self, callback):
            super().__init__()
            self._callback = callback

        def on_created(self, event):
            self._callback(FolderEvent("created", event.src_path, event.is_directory))

        def on_modified(self, event):
            self._callback(FolderEvent("modified", event.src_path, event.is_directory))

        def on_deleted(self, event):
            self._callback(FolderEvent("deleted", event.src_path, event.is_directory))

        def on_moved(self, event):
            self._callback(FolderEvent("moved", event.dest_path, event.is_directory))


class FolderWatcher(QObject):
    """
    Watches folders for filesystem changes and emits signals.

    Usage:
        watcher = FolderWatcher()
        watcher.file_event.connect(handle_event)
        watcher.watch("C:/Users/me/Downloads", "created")
    """

    file_event = Signal(object)  # FolderEvent

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watchers: dict[str, dict] = {}  # path → watcher info
        self._observer = None
        self._db = get_db()

        if HAS_WATCHDOG:
            self._observer = Observer()
            self._observer.start()

        # Restore active watchers from DB
        self._restore_watchers()

    def watch(self, path: str, event_filter: str = "all") -> bool:
        """
        Start watching a folder.

        Args:
            path: Directory path to watch
            event_filter: "all", "created", "modified", "deleted", or "moved"
        """
        resolved = Path(path).resolve()
        if not resolved.exists() or not resolved.is_dir():
            logger.error(f"FolderWatcher: path not found or not a directory: {path}")
            return False

        path_key = str(resolved)

        if path_key in self._watchers:
            logger.info(f"FolderWatcher: already watching {path_key}")
            return True

        if HAS_WATCHDOG and self._observer:
            handler = _WatchdogHandler(
                lambda evt: self._on_event(evt, event_filter)
            )
            watch = self._observer.schedule(handler, path_key, recursive=False)
            self._watchers[path_key] = {
                "watch": watch,
                "filter": event_filter,
                "handler": handler,
            }
        else:
            # Polling fallback
            self._watchers[path_key] = {
                "filter": event_filter,
                "last_snapshot": self._snapshot_dir(resolved),
            }
            if not hasattr(self, "_poll_timer"):
                self._poll_timer = QTimer(self)
                self._poll_timer.timeout.connect(self._poll_check)
                self._poll_timer.start(3000)  # Poll every 3s

        # Persist to DB
        self._db.save_watcher(path_key, event_filter)

        logger.info(f"FolderWatcher: watching {path_key} (filter={event_filter})")
        return True

    def unwatch(self, path: str):
        """Stop watching a folder."""
        resolved = str(Path(path).resolve())
        info = self._watchers.pop(resolved, None)
        if info and HAS_WATCHDOG and self._observer and "watch" in info:
            self._observer.unschedule(info["watch"])
        logger.info(f"FolderWatcher: stopped watching {resolved}")

    def get_watched_folders(self) -> list[str]:
        """Get list of currently watched folders."""
        return list(self._watchers.keys())

    def stop(self):
        """Stop all watchers and clean up."""
        if HAS_WATCHDOG and self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
        self._watchers.clear()

    def _on_event(self, event: FolderEvent, filter_type: str):
        """Handle a filesystem event, applying the filter."""
        if filter_type != "all" and event.event_type != filter_type:
            return
        logger.info(f"FolderWatcher: {event}")
        self.file_event.emit(event)

    def _snapshot_dir(self, path: Path) -> dict:
        """Take a snapshot of directory contents for polling."""
        try:
            return {
                str(f): f.stat().st_mtime
                for f in path.iterdir()
                if f.is_file()
            }
        except Exception:
            return {}

    def _poll_check(self):
        """Polling fallback: check for changes in watched directories."""
        for path_key, info in list(self._watchers.items()):
            if "last_snapshot" not in info:
                continue

            resolved = Path(path_key)
            if not resolved.exists():
                continue

            new_snapshot = self._snapshot_dir(resolved)
            old_snapshot = info["last_snapshot"]

            # Check for new files
            for f, mtime in new_snapshot.items():
                if f not in old_snapshot:
                    self._on_event(
                        FolderEvent("created", f), info["filter"]
                    )
                elif mtime != old_snapshot[f]:
                    self._on_event(
                        FolderEvent("modified", f), info["filter"]
                    )

            # Check for deleted files
            for f in old_snapshot:
                if f not in new_snapshot:
                    self._on_event(
                        FolderEvent("deleted", f), info["filter"]
                    )

            info["last_snapshot"] = new_snapshot

    def _restore_watchers(self):
        """Restore active watchers from the database."""
        try:
            saved = self._db.get_active_watchers()
            for w in saved:
                path = w["path"]
                if Path(path).exists():
                    self.watch(path, w["event_types"])
        except Exception as e:
            logger.warning(f"FolderWatcher: failed to restore watchers: {e}")
