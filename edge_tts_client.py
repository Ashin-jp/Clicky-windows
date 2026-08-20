"""
edge_tts_client.py — Microsoft Edge TTS Client

Free, high-quality text-to-speech using Microsoft Edge's online TTS service.
No API key required. Supports many voices and languages.

Drop-in replacement for elevenlabs_tts.py when running without a Worker.
"""

import asyncio
import logging
import os
import tempfile

import edge_tts
import pygame

import config

logger = logging.getLogger(__name__)

# High-quality English voices from Edge TTS
EDGE_VOICES = {
    "default": "en-US-AriaNeural",        # Female, natural, warm
    "male": "en-US-GuyNeural",            # Male, natural
    "friendly": "en-US-JennyNeural",      # Female, friendly
    "professional": "en-US-BrandonNeural", # Male, professional
}


class EdgeTTSClient:
    """
    Text-to-speech using Microsoft Edge's free TTS service.
    No API key needed — works out of the box.
    """

    def __init__(self, voice: str | None = None):
        self.voice = voice or EDGE_VOICES["default"]
        self._is_playing = False
        self._temp_file = None

        # Initialize pygame mixer
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
        except Exception as e:
            logger.error(f"Failed to initialize pygame mixer: {e}")

    @property
    def is_playing(self) -> bool:
        """Whether TTS audio is currently playing."""
        if self._is_playing and not pygame.mixer.music.get_busy():
            self._is_playing = False
            self._cleanup_temp_file()
        return self._is_playing

    async def speak_text(self, text: str):
        """
        Convert text to speech and play it.
        Uses edge-tts to generate MP3 audio, then plays via pygame.
        """
        if not text.strip():
            return

        # Generate audio with edge-tts
        communicate = edge_tts.Communicate(text, self.voice)

        # Write to temp file
        self._cleanup_temp_file()
        self._temp_file = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False
        )
        temp_path = self._temp_file.name
        self._temp_file.close()

        try:
            await communicate.save(temp_path)

            # Play the audio
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            self._is_playing = True

            file_size = os.path.getsize(temp_path) // 1024
            logger.info(f"Edge TTS: playing {file_size}KB audio (voice: {self.voice})")

        except Exception as e:
            self._cleanup_temp_file()
            raise RuntimeError(f"Edge TTS failed: {e}")

    def stop_playback(self):
        """Stop any in-progress playback."""
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
        self._is_playing = False
        self._cleanup_temp_file()

    def _cleanup_temp_file(self):
        """Remove temp audio file."""
        if self._temp_file:
            try:
                os.unlink(self._temp_file.name)
            except Exception:
                pass
            self._temp_file = None

    async def close(self):
        """Clean up."""
        self.stop_playback()
