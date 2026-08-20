"""
google_stt_provider.py — Google Free Speech-to-Text Provider

Uses the SpeechRecognition library with Google's free web speech API.
No API key required. Records audio during PTT, transcribes on release.

Drop-in replacement for assemblyai_provider.py when running without a Worker.
"""

import io
import logging
import struct
import wave

import speech_recognition as sr

from transcription_provider import StreamingTranscriptionSession, TranscriptionProvider

logger = logging.getLogger(__name__)


class GoogleFreeSTTProvider(TranscriptionProvider):
    """
    Speech-to-text using Google's free web speech API via SpeechRecognition.
    No API key needed — works out of the box.
    """

    @property
    def display_name(self) -> str:
        return "Google (Free)"

    @property
    def is_configured(self) -> bool:
        return True

    async def start_streaming_session(
        self,
        keyterms: list[str],
        on_transcript_update: callable,
        on_final_transcript_ready: callable,
        on_error: callable,
    ) -> StreamingTranscriptionSession:
        """Create a session that buffers audio and transcribes on release."""
        session = GoogleFreeSTTSession(
            on_transcript_update=on_transcript_update,
            on_final_transcript_ready=on_final_transcript_ready,
            on_error=on_error,
        )
        return session


class GoogleFreeSTTSession(StreamingTranscriptionSession):
    """
    Buffers PCM16 audio during recording, then transcribes the full
    recording when request_final_transcript() is called.
    """

    def __init__(
        self,
        on_transcript_update: callable,
        on_final_transcript_ready: callable,
        on_error: callable,
    ):
        self._on_transcript_update = on_transcript_update
        self._on_final_transcript_ready = on_final_transcript_ready
        self._on_error = on_error
        self._audio_chunks: list[bytes] = []
        self._recognizer = sr.Recognizer()
        self._has_delivered_final = False

    @property
    def final_transcript_fallback_delay_seconds(self) -> float:
        return 5.0  # Google free API can be slow

    def append_audio_buffer(self, pcm16_data: bytes):
        """Buffer audio data during recording."""
        self._audio_chunks.append(pcm16_data)

    async def request_final_transcript(self):
        """Transcribe the buffered audio."""
        if self._has_delivered_final:
            return

        if not self._audio_chunks:
            self._has_delivered_final = True
            self._on_final_transcript_ready("")
            return

        # Combine all audio chunks
        all_audio = b"".join(self._audio_chunks)

        # Convert raw PCM16 to WAV format for SpeechRecognition
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)        # Mono
            wav_file.setsampwidth(2)         # 16-bit
            wav_file.setframerate(16000)     # 16kHz
            wav_file.writeframes(all_audio)

        wav_buffer.seek(0)

        # Transcribe using Google free API
        try:
            audio_data = sr.AudioData(
                all_audio,
                sample_rate=16000,
                sample_width=2,
            )

            # Show "transcribing..." feedback
            self._on_transcript_update("transcribing...")

            # Use Google's free web speech API (no key needed)
            text = self._recognizer.recognize_google(audio_data)
            text = text.strip()

            logger.info(f"Google STT: transcribed {len(all_audio)//1024}KB → \"{text}\"")

            self._has_delivered_final = True
            self._on_transcript_update(text)
            self._on_final_transcript_ready(text)

        except sr.UnknownValueError:
            logger.warning("Google STT: could not understand audio")
            self._has_delivered_final = True
            self._on_final_transcript_ready("")

        except sr.RequestError as e:
            logger.error(f"Google STT: request error: {e}")
            self._on_error(e)

        except Exception as e:
            logger.error(f"Google STT: unexpected error: {e}")
            self._on_error(e)

    async def cancel(self):
        """Cancel the session."""
        self._audio_chunks.clear()
        self._has_delivered_final = True
