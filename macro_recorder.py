"""
macro_recorder.py — Action Recording & Playback Engine

Records sequences of Clicky actions and replays them on demand.
Macros are stored in the SQLite database and can be exported as JSON.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer

from storage import get_db, MACROS_DIR

logger = logging.getLogger(__name__)


@dataclass
class MacroStep:
    """A single step in a macro."""
    action_type: str
    params: str
    delay_ms: int = 500  # Delay before this step


class MacroRecorder(QObject):
    """
    Records and plays back action sequences.

    Usage:
        recorder.start_recording("my_macro")
        # ... actions happen ...
        recorder.record_step("TYPE", "hello world")
        recorder.record_step("HOTKEY", "ctrl+s")
        recorder.stop_recording()

        recorder.play_macro("my_macro", on_step_callback)
    """

    recording_started = Signal(str)   # macro name
    recording_stopped = Signal(str)   # macro name
    playback_started = Signal(str)    # macro name
    playback_step = Signal(int, int)  # current step, total steps
    playback_finished = Signal(str)   # macro name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_recording = False
        self._current_macro_name = ""
        self._recorded_steps: list[dict] = []
        self._last_step_time = 0.0
        self._is_playing = False
        self._db = get_db()

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def current_macro_name(self) -> str:
        return self._current_macro_name

    def start_recording(self, name: str):
        """Start recording a new macro."""
        if self._is_recording:
            self.stop_recording()

        self._is_recording = True
        self._current_macro_name = name
        self._recorded_steps = []
        self._last_step_time = time.monotonic()
        self.recording_started.emit(name)
        logger.info(f"Macro: started recording '{name}'")

    def record_step(self, action_type: str, params: str):
        """Record a single action step."""
        if not self._is_recording:
            return

        now = time.monotonic()
        delay_ms = int((now - self._last_step_time) * 1000)
        self._last_step_time = now

        step = {
            "action_type": action_type,
            "params": params,
            "delay_ms": min(delay_ms, 5000),  # Cap delay at 5s
        }
        self._recorded_steps.append(step)
        logger.debug(f"Macro: recorded step {action_type}:{params[:30]}")

    def stop_recording(self) -> str:
        """Stop recording and save the macro. Returns macro name."""
        if not self._is_recording:
            return ""

        name = self._current_macro_name
        self._is_recording = False

        if self._recorded_steps:
            self._db.save_macro(
                name=name,
                actions=self._recorded_steps,
                description=f"Recorded macro with {len(self._recorded_steps)} steps",
            )
            logger.info(f"Macro: saved '{name}' ({len(self._recorded_steps)} steps)")
        else:
            logger.warning(f"Macro: '{name}' had no steps, not saved")

        self.recording_stopped.emit(name)
        self._current_macro_name = ""
        self._recorded_steps = []
        return name

    def play_macro(self, name: str, execute_callback):
        """
        Play a saved macro.

        Args:
            name: Macro name to play
            execute_callback: Function(action_type, params) to execute each step
        """
        macro = self._db.get_macro(name)
        if not macro:
            logger.error(f"Macro: '{name}' not found")
            return

        actions = macro["actions"]
        if not actions:
            logger.warning(f"Macro: '{name}' has no steps")
            return

        self._is_playing = True
        self._db.increment_macro_run(name)
        self.playback_started.emit(name)
        logger.info(f"Macro: playing '{name}' ({len(actions)} steps)")

        # Play steps sequentially with delays
        self._play_step(actions, 0, execute_callback, name)

    def _play_step(self, actions: list, index: int, callback, macro_name: str):
        """Play a single step and schedule the next."""
        if index >= len(actions) or not self._is_playing:
            self._is_playing = False
            self.playback_finished.emit(macro_name)
            logger.info(f"Macro: finished playing '{macro_name}'")
            return

        step = actions[index]
        self.playback_step.emit(index + 1, len(actions))

        # Execute the step
        callback(step["action_type"], step["params"])

        # Schedule next step
        delay = step.get("delay_ms", 500)
        QTimer.singleShot(
            delay,
            lambda: self._play_step(actions, index + 1, callback, macro_name),
        )

    def stop_playback(self):
        """Stop macro playback."""
        self._is_playing = False

    def list_macros(self) -> list[dict]:
        """List all saved macros."""
        return self._db.list_macros()

    def delete_macro(self, name: str):
        """Delete a saved macro."""
        self._db.delete_macro(name)
        logger.info(f"Macro: deleted '{name}'")

    def export_macro(self, name: str) -> Path | None:
        """Export a macro to a JSON file in the macros directory."""
        macro = self._db.get_macro(name)
        if not macro:
            return None

        path = MACROS_DIR / f"{name}.json"
        path.write_text(json.dumps(macro, indent=2), encoding="utf-8")
        logger.info(f"Macro: exported '{name}' to {path}")
        return path

    def import_macro(self, path: str) -> str | None:
        """Import a macro from a JSON file."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            name = data.get("name", Path(path).stem)
            actions = data.get("actions", [])
            self._db.save_macro(name, actions, data.get("description", ""))
            logger.info(f"Macro: imported '{name}' from {path}")
            return name
        except Exception as e:
            logger.error(f"Macro import failed: {e}")
            return None
