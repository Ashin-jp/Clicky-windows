"""
transcription_provider.py — Transcription Provider Protocol & Factory

Abstract base class for voice transcription backends with a factory
that resolves the default provider (AssemblyAI → fallback chain).

This is the Windows equivalent of BuddyTranscriptionProvider.swift.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StreamingTranscriptionSession(ABC):
    """
    A single streaming transcription session.
    Audio buffers are appended during recording, and a final transcript
    is requested when the user releases the push-to-talk key.
    """

    @property
    def final_transcript_fallback_delay_seconds(self) -> float:
        """Fallback timeout for final transcript delivery."""
        return 2.8

    @abstractmethod
    def append_audio_buffer(self, pcm16_data: bytes):
        """Send a chunk of PCM16 audio to the transcription service."""
        ...

    @abstractmethod
    async def request_final_transcript(self):
        """Request the final transcript (called on key-up)."""
        ...

    @abstractmethod
    async def cancel(self):
        """Cancel the session and clean up resources."""
        ...


class TranscriptionProvider(ABC):
    """
    Base class for voice transcription providers.
    Each provider can create streaming sessions.
    """

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @property
    def requires_speech_recognition_permission(self) -> bool:
        return False

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def unavailable_explanation(self) -> str | None:
        return None

    @abstractmethod
    async def start_streaming_session(
        self,
        keyterms: list[str],
        on_transcript_update: callable,
        on_final_transcript_ready: callable,
        on_error: callable,
    ) -> StreamingTranscriptionSession:
        """Start a new streaming transcription session."""
        ...


class TranscriptionProviderFactory:
    """Resolves the default transcription provider."""

    @staticmethod
    def make_default_provider() -> TranscriptionProvider:
        # Try AssemblyAI first (our primary provider)
        try:
            from assemblyai_provider import AssemblyAITranscriptionProvider
            provider = AssemblyAITranscriptionProvider()
            if provider.is_configured:
                logger.info(f"Transcription: using {provider.display_name}")
                return provider
        except ImportError:
            logger.warning("Transcription: AssemblyAI provider not available")

        # No fallback providers implemented yet — AssemblyAI is required
        raise RuntimeError(
            "No transcription provider available. "
            "AssemblyAI is required — ensure the Worker proxy is deployed."
        )
