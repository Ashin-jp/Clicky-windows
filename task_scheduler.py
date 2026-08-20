"""
task_scheduler.py — Lightweight Scheduled Task Runner

Runs actions at specified times or intervals.
Uses QTimer for scheduling and persists tasks to SQLite.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer

from storage import get_db

logger = logging.getLogger(__name__)


class ScheduledTask:
    """A single scheduled task."""
    def __init__(
        self,
        task_id: int,
        name: str,
        action_type: str,
        action_params: str,
        run_at: datetime | None = None,
        interval_minutes: int | None = None,
    ):
        self.task_id = task_id
        self.name = name
        self.action_type = action_type
        self.action_params = action_params
        self.run_at = run_at
        self.interval_minutes = interval_minutes
        self._timer: QTimer | None = None


class TaskScheduler(QObject):
    """
    Lightweight task scheduler using Qt timers.

    Usage:
        scheduler = TaskScheduler()
        scheduler.task_triggered.connect(handle_task)
        scheduler.schedule_at("morning_routine", "09:00", "CHAIN", "RUN:notepad|OPEN:https://news.ycombinator.com")
        scheduler.schedule_interval("check_downloads", 30, "SEARCH_FILES", "C:/Users/me/Downloads|*.pdf")
    """

    task_triggered = Signal(str, str, str)  # task_name, action_type, action_params

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: dict[int, ScheduledTask] = {}
        self._db = get_db()

        # Check timer — runs every minute to check for due tasks
        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self._check_due_tasks)
        self._check_timer.start(60_000)  # Every 60 seconds

        # Restore saved tasks
        self._restore_tasks()

    def schedule_at(self, name: str, time_str: str, action_type: str, action_params: str) -> int:
        """
        Schedule an action at a specific time today (or tomorrow if time has passed).

        Args:
            name: Human-readable task name
            time_str: Time in HH:MM format (24-hour)
            action_type: Action type to execute
            action_params: Action parameters

        Returns:
            Task ID
        """
        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            logger.error(f"Invalid time format: {time_str}. Use HH:MM")
            return -1

        now = datetime.now()
        run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If the time has already passed today, schedule for tomorrow
        if run_at <= now:
            run_at += timedelta(days=1)

        action = {"type": action_type, "params": action_params}
        self._db.save_schedule(name, action, run_at=run_at.isoformat())

        # Get the ID from DB
        schedules = self._db.get_active_schedules()
        task_id = schedules[-1]["id"] if schedules else 0

        task = ScheduledTask(
            task_id=task_id,
            name=name,
            action_type=action_type,
            action_params=action_params,
            run_at=run_at,
        )
        self._tasks[task_id] = task

        logger.info(f"Scheduler: '{name}' scheduled at {run_at.strftime('%H:%M %Y-%m-%d')}")
        return task_id

    def schedule_interval(self, name: str, interval_minutes: int, action_type: str, action_params: str) -> int:
        """
        Schedule an action to repeat at a fixed interval.

        Args:
            name: Human-readable task name
            interval_minutes: Interval in minutes
            action_type: Action type to execute
            action_params: Action parameters

        Returns:
            Task ID
        """
        action = {"type": action_type, "params": action_params}
        self._db.save_schedule(
            name, action,
            cron_expr=f"every_{interval_minutes}m",
        )

        schedules = self._db.get_active_schedules()
        task_id = schedules[-1]["id"] if schedules else 0

        task = ScheduledTask(
            task_id=task_id,
            name=name,
            action_type=action_type,
            action_params=action_params,
            interval_minutes=interval_minutes,
        )

        # Create a repeating timer
        timer = QTimer(self)
        timer.timeout.connect(lambda: self._fire_task(task))
        timer.start(interval_minutes * 60_000)
        task._timer = timer

        self._tasks[task_id] = task
        logger.info(f"Scheduler: '{name}' repeating every {interval_minutes}m")
        return task_id

    def cancel_task(self, task_id: int):
        """Cancel a scheduled task."""
        task = self._tasks.pop(task_id, None)
        if task:
            if task._timer:
                task._timer.stop()
            self._db.deactivate_schedule(task_id)
            logger.info(f"Scheduler: cancelled task '{task.name}'")

    def list_tasks(self) -> list[dict]:
        """List all active tasks."""
        return [
            {
                "id": t.task_id,
                "name": t.name,
                "action_type": t.action_type,
                "run_at": t.run_at.isoformat() if t.run_at else None,
                "interval_minutes": t.interval_minutes,
            }
            for t in self._tasks.values()
        ]

    def stop(self):
        """Stop all timers and clean up."""
        self._check_timer.stop()
        for task in self._tasks.values():
            if task._timer:
                task._timer.stop()
        self._tasks.clear()

    def _check_due_tasks(self):
        """Check for one-time tasks that are due."""
        now = datetime.now()
        for task_id, task in list(self._tasks.items()):
            if task.run_at and task.run_at <= now and not task.interval_minutes:
                self._fire_task(task)
                # Remove one-time task after execution
                self._tasks.pop(task_id, None)
                self._db.deactivate_schedule(task_id)

    def _fire_task(self, task: ScheduledTask):
        """Execute a scheduled task."""
        logger.info(f"Scheduler: firing '{task.name}' → {task.action_type}:{task.action_params[:50]}")
        self.task_triggered.emit(task.name, task.action_type, task.action_params)

    def _restore_tasks(self):
        """Restore active tasks from the database."""
        try:
            saved = self._db.get_active_schedules()
            for s in saved:
                action = json.loads(s["action_json"])
                action_type = action.get("type", "")
                action_params = action.get("params", "")

                if s["cron_expr"] and s["cron_expr"].startswith("every_"):
                    # Interval task
                    try:
                        minutes = int(s["cron_expr"].replace("every_", "").replace("m", ""))
                        task = ScheduledTask(
                            task_id=s["id"],
                            name=s["name"],
                            action_type=action_type,
                            action_params=action_params,
                            interval_minutes=minutes,
                        )
                        timer = QTimer(self)
                        timer.timeout.connect(lambda t=task: self._fire_task(t))
                        timer.start(minutes * 60_000)
                        task._timer = timer
                        self._tasks[s["id"]] = task
                    except ValueError:
                        pass
                elif s["run_at"]:
                    # One-time task
                    run_at = datetime.fromisoformat(s["run_at"])
                    if run_at > datetime.now():
                        task = ScheduledTask(
                            task_id=s["id"],
                            name=s["name"],
                            action_type=action_type,
                            action_params=action_params,
                            run_at=run_at,
                        )
                        self._tasks[s["id"]] = task

            if self._tasks:
                logger.info(f"Scheduler: restored {len(self._tasks)} task(s)")
        except Exception as e:
            logger.warning(f"Scheduler: failed to restore tasks: {e}")
