"""
health_monitor.py — System health monitoring via psutil.

Polls CPU/RAM/disk every 10 seconds, stores rolling 1-hour history,
triggers TTS warnings on threshold breaches, and provides health reports.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

# ─── Thresholds ──────────────────────────────────────────────────────
CPU_WARN_THRESHOLD = 90.0       # % sustained
CPU_WARN_DURATION = 30           # seconds of sustained high CPU
RAM_WARN_THRESHOLD = 85.0       # %
DISK_WARN_THRESHOLD = 95.0      # %
TEMP_WARN_THRESHOLD = 85.0      # °C


@dataclass
class HealthSnapshot:
    cpu_percent: float
    ram_percent: float
    ram_available_mb: float
    disk_percent: float
    cpu_temp: Optional[float]
    top_cpu_processes: list[str]
    top_ram_processes: list[str]
    timestamp: str


class HealthMonitor:
    """System health monitor with threshold alerts."""

    def __init__(self, poll_interval: float = 10.0):
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._db = None
        self._tts_callback = None
        self._ram_callback = None  # Called when RAM > 85%

        # Track sustained CPU for warnings
        self._high_cpu_start: Optional[float] = None
        self._last_cpu_warn: float = 0
        self._last_ram_warn: float = 0
        self._last_disk_warn: float = 0

        # Latest snapshot for quick access
        self._latest: Optional[HealthSnapshot] = None

        logger.info("HealthMonitor: created")

    def start(self):
        """Start health monitoring."""
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._poll_loop, name="HealthMonitorThread", daemon=True,
        )
        self._thread.start()
        logger.info("HealthMonitor: started")

    def stop(self):
        """Stop health monitoring."""
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("HealthMonitor: stopped")

    def set_tts_callback(self, callback):
        """Set callback for TTS warnings: callback(text: str)."""
        self._tts_callback = callback

    def set_ram_callback(self, callback):
        """Set callback for RAM threshold: callback(ram_percent: float)."""
        self._ram_callback = callback

    def get_available_ram_mb(self) -> float:
        """Get available RAM in MB (fast, no poll)."""
        try:
            return psutil.virtual_memory().available / (1024 * 1024)
        except Exception:
            return 0.0

    def get_health_report(self) -> dict:
        """Get structured health report for voice query."""
        snap = self._latest or self._take_snapshot()
        return {
            "cpu_percent": snap.cpu_percent,
            "ram_percent": snap.ram_percent,
            "ram_available_mb": snap.ram_available_mb,
            "disk_percent": snap.disk_percent,
            "cpu_temp": snap.cpu_temp,
            "top_cpu": snap.top_cpu_processes[:3],
            "top_ram": snap.top_ram_processes[:3],
            "status": self._overall_status(snap),
            "summary": self._format_summary(snap),
        }

    def get_health_summary_text(self) -> str:
        """Get a TTS-ready health summary."""
        report = self.get_health_report()
        return report["summary"]

    def _overall_status(self, snap: HealthSnapshot) -> str:
        if snap.cpu_percent > CPU_WARN_THRESHOLD or snap.ram_percent > RAM_WARN_THRESHOLD:
            return "warning"
        if snap.disk_percent > DISK_WARN_THRESHOLD:
            return "critical"
        return "healthy"

    def _format_summary(self, snap: HealthSnapshot) -> str:
        parts = [f"CPU is at {snap.cpu_percent:.0f}%"]
        parts.append(f"RAM is at {snap.ram_percent:.0f}% with {snap.ram_available_mb:.0f} megabytes free")
        parts.append(f"Disk usage is {snap.disk_percent:.0f}%")

        if snap.cpu_temp:
            parts.append(f"CPU temperature is {snap.cpu_temp:.0f} degrees")

        status = self._overall_status(snap)
        if status == "healthy":
            parts.append("Everything looks good.")
        elif status == "warning":
            parts.append("Some resources are running high.")
            if snap.top_cpu_processes:
                parts.append(f"Top CPU consumers: {', '.join(snap.top_cpu_processes[:3])}")
            if snap.top_ram_processes:
                parts.append(f"Top RAM consumers: {', '.join(snap.top_ram_processes[:3])}")
        else:
            parts.append("System resources are critically low!")

        return " ".join(parts)

    def _take_snapshot(self) -> HealthSnapshot:
        """Take a health snapshot."""
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")

        # Top processes by CPU
        top_cpu = []
        top_ram = []
        try:
            procs = []
            for p in psutil.process_iter(['name', 'cpu_percent', 'memory_percent']):
                try:
                    info = p.info
                    if info['name'] and info['cpu_percent'] is not None:
                        procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            procs_by_cpu = sorted(procs, key=lambda x: x.get('cpu_percent', 0), reverse=True)
            top_cpu = [p['name'] for p in procs_by_cpu[:5] if p.get('cpu_percent', 0) > 1]

            procs_by_ram = sorted(procs, key=lambda x: x.get('memory_percent', 0), reverse=True)
            top_ram = [p['name'] for p in procs_by_ram[:5] if p.get('memory_percent', 0) > 1]
        except Exception:
            pass

        # CPU temperature (best effort via WMI)
        cpu_temp = None
        try:
            import wmi
            w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
            sensors = w.Sensor()
            for s in sensors:
                if s.SensorType == "Temperature" and "CPU" in s.Name:
                    cpu_temp = float(s.Value)
                    break
        except Exception:
            pass

        return HealthSnapshot(
            cpu_percent=cpu,
            ram_percent=mem.percent,
            ram_available_mb=mem.available / (1024 * 1024),
            disk_percent=disk.percent,
            cpu_temp=cpu_temp,
            top_cpu_processes=top_cpu,
            top_ram_processes=top_ram,
            timestamp=datetime.now().isoformat(),
        )

    def _poll_loop(self):
        """Main polling loop."""
        while self._running.is_set():
            try:
                snap = self._take_snapshot()
                self._latest = snap

                # Store in DB
                self._log_to_db(snap)

                # Check thresholds
                self._check_thresholds(snap)

            except Exception as e:
                logger.debug(f"HealthMonitor: poll error: {e}")

            for _ in range(int(self._poll_interval * 2)):
                if not self._running.is_set():
                    return
                time.sleep(0.5)

    def _log_to_db(self, snap: HealthSnapshot):
        try:
            if self._db is None:
                from storage import get_db
                self._db = get_db()
            self._db.log_health(
                cpu=snap.cpu_percent, ram=snap.ram_percent,
                ram_avail=snap.ram_available_mb, disk=snap.disk_percent,
                temp=snap.cpu_temp,
                top_procs=", ".join(snap.top_cpu_processes[:3]),
            )
        except Exception as e:
            logger.debug(f"HealthMonitor: DB log failed: {e}")

    def _check_thresholds(self, snap: HealthSnapshot):
        now = time.monotonic()
        warn_cooldown = 120  # Don't repeat warnings within 2 minutes

        # CPU sustained high
        if snap.cpu_percent > CPU_WARN_THRESHOLD:
            if self._high_cpu_start is None:
                self._high_cpu_start = now
            elif (now - self._high_cpu_start) > CPU_WARN_DURATION and (now - self._last_cpu_warn) > warn_cooldown:
                self._warn(f"CPU has been above {CPU_WARN_THRESHOLD:.0f}% for {CPU_WARN_DURATION} seconds. "
                           f"Top consumers: {', '.join(snap.top_cpu_processes[:3])}")
                self._last_cpu_warn = now
        else:
            self._high_cpu_start = None

        # RAM high
        if snap.ram_percent > RAM_WARN_THRESHOLD and (now - self._last_ram_warn) > warn_cooldown:
            self._warn(f"RAM usage is at {snap.ram_percent:.0f}% with only "
                       f"{snap.ram_available_mb:.0f}MB free. "
                       f"Top consumers: {', '.join(snap.top_ram_processes[:3])}")
            self._last_ram_warn = now
            if self._ram_callback:
                try:
                    self._ram_callback(snap.ram_percent)
                except Exception:
                    pass

        # Disk full
        if snap.disk_percent > DISK_WARN_THRESHOLD and (now - self._last_disk_warn) > warn_cooldown:
            self._warn(f"Disk C: is {snap.disk_percent:.0f}% full!")
            self._last_disk_warn = now

        # Temperature
        if snap.cpu_temp and snap.cpu_temp > TEMP_WARN_THRESHOLD:
            self._warn(f"CPU temperature is {snap.cpu_temp:.0f}°C! Consider closing heavy applications.")

    def _warn(self, text: str):
        logger.warning(f"HealthMonitor: {text}")
        if self._tts_callback:
            try:
                self._tts_callback(text)
            except Exception:
                pass


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[HealthMonitor] = None


def get_health_monitor() -> HealthMonitor:
    global _instance
    if _instance is None:
        _instance = HealthMonitor()
    return _instance
