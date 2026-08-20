"""
elevenlabs_tts.py — ElevenLabs Text-to-Speech Client

Sends text to the ElevenLabs API via the Cloudflare Worker proxy,
receives MP3 audio, and plays it back using pygame.mixer.

This is the Windows equivalent of ElevenLabsTTSClient.swift.
"""

import io
import json
import logging
import tempfile
import os

import httpx
import pygame

import config

logger = logging.getLogger(__name__)


class ElevenLabsTTSClient:
    """
    ElevenLabs TTS client. Sends text to the Worker proxy,
    receives MP3 audio, and plays it via pygame.mixer.
    """

    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = proxy_url or config.TTS_ENDPOINT
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
        )
        self._is_playing = False
        self._temp_file = None

        # Initialize pygame mixer for audio playback
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
        except Exception as e:
            logger.error(f"Failed to initialize pygame mixer: {e}")

    @property
    def is_playing(self) -> bool:
        """Whether TTS audio is currently playing back."""
        if self._is_playing and not pygame.mixer.music.get_busy():
            self._is_playing = False
            self._cleanup_temp_file()
        return self._is_playing

    async def speak_text(self, text: str):
        """
        Send text to ElevenLabs TTS and play the resulting audio.
        Raises on network or decoding errors.
        """
        body = {
            "text": text,
            "model_id": config.TTS_MODEL_ID,
            "voice_settings": {
                "stability": config.TTS_STABILITY,
                "similarity_boost": config.TTS_SIMILARITY_BOOST,
            },
        }

        response = await self._client.post(
            self.proxy_url,
            content=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )

        if response.status_code < 200 or response.status_code >= 300:
            error_body = response.text
            raise RuntimeError(
                f"TTS API error ({response.status_code}): {error_body}"
            )

        audio_data = response.content

        # pygame.mixer.music requires a file path, so write to a temp file
        self._cleanup_temp_file()
        self._temp_file = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False
        )
        self._temp_file.write(audio_data)
        self._temp_file.close()

        try:
            pygame.mixer.music.load(self._temp_file.name)
            pygame.mixer.music.play()
            self._is_playing = True
            logger.info(f"ElevenLabs TTS: playing {len(audio_data) // 1024}KB audio")
        except Exception as e:
            self._cleanup_temp_file()
            raise RuntimeError(f"Failed to play TTS audio: {e}")

    def stop_playback(self):
        """Stop any in-progress playback immediately."""
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
        self._is_playing = False
        self._cleanup_temp_file()

    def _cleanup_temp_file(self):
        """Remove the temporary audio file if it exists."""
        if self._temp_file:
            try:
                os.unlink(self._temp_file.name)
            except Exception:
                pass
            self._temp_file = None

    async def close(self):
        """Close the HTTP client and clean up."""
        self.stop_playback()
        await self._client.aclose()
