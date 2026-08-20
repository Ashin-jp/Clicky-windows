"""
watchdog_system.py — Self-healing component monitor.

Each subsystem registers with a heartbeat interval. Background thread
checks all registrations every 5 seconds. Missed heartbeats trigger
warnings, restarts, and eventually graceful disable with TTS notification.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ComponentRegistration:
    name: str
    heartbeat_interval: float
    restart_fn: Optional[Callable] = None
    last_heartbeat: float = 0.0
    missed_count: int = 0
    restart_attempts: int = 0
    max_restarts: int = 3
    is_disabled: bool = False
    registered_at: float = 0.0


class WatchdogSystem:
    def __init__(self, check_interval: float = 5.0):
        self._check_interval = check_interval
        self._components: dict[str, ComponentRegistration] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._tts_callback = None
        self._macro_failures: dict[str, list[int]] = {}
        self._intentionally_stopped: set[str] = set()
        logger.info("Watchdog: created")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._check_loop, name="WatchdogThread", daemon=True)
        self._thread.start()
        logger.info("Watchdog: started")

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Watchdog: stopped")

    def set_tts_callback(self, callback):
        self._tts_callback = callback

    def register(self, name: str, heartbeat_interval: float = 10.0, restart_fn: Callable = None):
        with self._lock:
            self._components[name] = ComponentRegistration(
                name=name, heartbeat_interval=heartbeat_interval,
                restart_fn=restart_fn, last_heartbeat=time.monotonic(),
                registered_at=time.monotonic(),
            )
        logger.info(f"Watchdog: registered '{name}' (interval={heartbeat_interval}s)")

    def unregister(self, name: str):
        with self._lock:
            self._components.pop(name, None)
            self._intentionally_stopped.discard(name)

    def disable(self, component_name: str):
        """Mark a component as intentionally stopped. Watchdog will not restart it."""
        with self._lock:
            self._intentionally_stopped.add(component_name)
            comp = self._components.get(component_name)
            if comp:
                comp.is_disabled = True
        logger.info(f"Watchdog: '{component_name}' intentionally disabled")

    def enable(self, component_name: str):
        """Remove a component from the intentionally stopped set. Resume monitoring."""
        with self._lock:
            self._intentionally_stopped.discard(component_name)
            comp = self._components.get(component_name)
            if comp:
                comp.is_disabled = False
                comp.last_heartbeat = time.monotonic()
                comp.missed_count = 0
        logger.info(f"Watchdog: '{component_name}' re-enabled")

    def heartbeat(self, name: str):
        with self._lock:
            comp = self._components.get(name)
            if comp:
                comp.last_heartbeat = time.monotonic()
                comp.missed_count = 0

    def report_macro_failure(self, macro_name: str, step_index: int):
        with self._lock:
            if macro_name not in self._macro_failures:
                self._macro_failures[macro_name] = []
            self._macro_failures[macro_name].append(step_index)
            counts = {}
            for s in self._macro_failures[macro_name]:
                counts[s] = counts.get(s, 0) + 1
            for step, count in counts.items():
                if count >= 3:
                    self._flag_broken_macro(macro_name, step)
                    break

    def get_status(self) -> dict:
        with self._lock:
            now = time.monotonic()
            result = {}
            for name, comp in self._components.items():
                elapsed = now - comp.last_heartbeat
                if comp.is_disabled:
                    status = "disabled"
                elif elapsed > comp.heartbeat_interval * 3:
                    status = "critical"
                elif elapsed > comp.heartbeat_interval * 1.5:
                    status = "warning"
                else:
                    status = "healthy"
                result[name] = {"status": status, "last_heartbeat_ago": f"{elapsed:.0f}s",
                                "missed": comp.missed_count, "restarts": comp.restart_attempts}
            return result

    def get_diagnostics_text(self) -> str:
        status = self.get_status()
        if not status:
            return "No components registered."
        healthy = sum(1 for s in status.values() if s["status"] == "healthy")
        parts = [f"{healthy} of {len(status)} components healthy."]
        for name, info in status.items():
            if info["status"] != "healthy":
                parts.append(f"{name}: {info['status']}, last heartbeat {info['last_heartbeat_ago']} ago.")
        return " ".join(parts)

    def _check_loop(self):
        while self._running.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.debug(f"Watchdog: check error: {e}")
            for _ in range(int(self._check_interval * 2)):
                if not self._running.is_set():
                    return
                time.sleep(0.5)

    def _check_all(self):
        now = time.monotonic()
        with self._lock:
            for name, comp in self._components.items():
                if comp.is_disabled:
                    continue
                if name in self._intentionally_stopped:
                    continue
                if (now - comp.registered_at) < comp.heartbeat_interval * 2:
                    continue
                elapsed = now - comp.last_heartbeat
                if elapsed > comp.heartbeat_interval:
                    comp.missed_count += 1
                    if comp.missed_count == 1:
                        logger.warning(f"Watchdog: '{name}' missed 1 heartbeat")
                    elif comp.missed_count >= 3:
                        if comp.restart_fn and comp.restart_attempts < comp.max_restarts:
                            logger.warning(f"Watchdog: restarting '{name}'")
                            try:
                                comp.restart_fn()
                                comp.restart_attempts += 1
                                comp.last_heartbeat = now
                                comp.missed_count = 0
                            except Exception as e:
                                logger.error(f"Watchdog: restart failed: {e}")
                                comp.restart_attempts += 1
                        elif comp.restart_attempts >= comp.max_restarts:
                            comp.is_disabled = True
                            msg = f"{name} disabled after {comp.max_restarts} failed restarts."
                            logger.error(f"Watchdog: {msg}")
                            self._notify(msg)

    def _flag_broken_macro(self, macro_name: str, step: int):
        try:
            from storage import get_db
            get_db().mark_macro_broken(macro_name)
            self._notify(f"Macro '{macro_name}' flagged broken at step {step}. Re-record it.")
        except Exception:
            pass

    def _notify(self, text: str):
        if self._tts_callback:
            try:
                self._tts_callback(text)
            except Exception:
                pass

_instance: Optional[WatchdogSystem] = None

def get_watchdog() -> WatchdogSystem:
    global _instance
    if _instance is None:
        _instance = WatchdogSystem()
    return _instance
