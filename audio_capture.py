"""
audio_capture.py — Microphone Audio Capture

Captures microphone audio using sounddevice with WASAPI backend.
Streams PCM16 mono audio at 16kHz for transcription, and computes
RMS power levels for waveform visualization.

This is the Windows equivalent of AVAudioEngine in BuddyDictationManager.swift.
"""

import logging
import struct
import threading

import numpy as np
import sounddevice as sd

from PySide6.QtCore import QObject, Signal

import config

logger = logging.getLogger(__name__)


class AudioCapture(QObject):
    """
    Microphone capture using sounddevice/WASAPI.
    Streams PCM16 audio buffers and audio power levels.

    Signals:
        audio_buffer_ready: Emitted with raw PCM16 bytes for each audio block
        audio_power_changed: Emitted with current RMS power level (0.0 - 1.0)
        error_occurred: Emitted when an audio error occurs
    """

    audio_buffer_ready = Signal(bytes)
    audio_power_changed = Signal(float)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream = None
        self._is_recording = False
        self._current_power_level = 0.0

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def current_power_level(self) -> float:
        return self._current_power_level

    def start(self):
        """Start capturing microphone audio."""
        if self._is_recording:
            logger.warning("Audio capture: already recording")
            return

        try:
            self._stream = sd.InputStream(
                samplerate=config.AUDIO_SAMPLE_RATE,
                channels=config.AUDIO_CHANNELS,
                dtype=config.AUDIO_DTYPE,
                blocksize=config.AUDIO_BLOCK_SIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._is_recording = True
            logger.info(
                f"Audio capture: started ({config.AUDIO_SAMPLE_RATE}Hz, "
                f"mono, PCM16, block={config.AUDIO_BLOCK_SIZE})"
            )
        except Exception as e:
            error_msg = f"Failed to start audio capture: {e}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)

    def stop(self):
        """Stop capturing microphone audio."""
        if not self._is_recording:
            return

        self._is_recording = False
        self._current_power_level = 0.0

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning(f"Audio capture: error stopping stream: {e}")
            finally:
                self._stream = None

        logger.info("Audio capture: stopped")

    def _audio_callback(self, indata, frames, time_info, status):
        """
        Called by sounddevice for each audio block.
        Converts to PCM16 bytes and computes power level.
        This runs in the audio thread — we emit signals to cross thread boundary.
        """
        if status:
            logger.warning(f"Audio capture status: {status}")

        if not self._is_recording:
            return

        # Convert numpy array to raw PCM16 bytes
        pcm16_bytes = indata.tobytes()
        self.audio_buffer_ready.emit(pcm16_bytes)

        # Compute RMS power level for waveform visualization
        # Convert int16 samples to float for RMS calculation
        samples = indata.flatten().astype(np.float32) / 32768.0
        if len(samples) > 0:
            rms = float(np.sqrt(np.mean(samples ** 2)))
            # Boost and clamp to 0-1 range (matching macOS boostedLevel = rms * 10.2)
            boosted = min(max(rms * 10.2, 0.0), 1.0)
            # Smooth: keep some of the previous level for less jitter
            smoothed = max(boosted, self._current_power_level * 0.72)
            self._current_power_level = smoothed
            self.audio_power_changed.emit(smoothed)


def list_input_devices() -> list[dict]:
    """List available audio input devices for debugging."""
    devices = sd.query_devices()
    input_devices = []
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            input_devices.append({
                "index": i,
                "name": d["name"],
                "channels": d["max_input_channels"],
                "sample_rate": d["default_samplerate"],
            })
    return input_devices
