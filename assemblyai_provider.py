"""
assemblyai_provider.py — AssemblyAI Streaming Transcription Provider

Streaming speech-to-text via AssemblyAI's WebSocket API.
Fetches temporary tokens from the Cloudflare Worker proxy,
streams PCM16 audio, and delivers turn-based transcripts.

This is the Windows equivalent of AssemblyAIStreamingTranscriptionProvider.swift.
"""

import asyncio
import json
import logging
import threading

import httpx
import websockets

import config
from transcription_provider import StreamingTranscriptionSession, TranscriptionProvider

logger = logging.getLogger(__name__)


class AssemblyAITranscriptionProvider(TranscriptionProvider):
    """
    AssemblyAI streaming transcription provider.
    Fetches temp tokens from the Worker proxy and creates WebSocket sessions.
    """

    @property
    def display_name(self) -> str:
        return "AssemblyAI"

    @property
    def is_configured(self) -> bool:
        return True  # Configured via Worker proxy

    async def start_streaming_session(
        self,
        keyterms: list[str],
        on_transcript_update: callable,
        on_final_transcript_ready: callable,
        on_error: callable,
    ) -> StreamingTranscriptionSession:
        """Fetch a temp token and create a WebSocket streaming session."""
        token = await self._fetch_temporary_token()
        logger.info(f"AssemblyAI: fetched temporary token ({token[:20]}...)")

        session = AssemblyAIStreamingSession(
            temporary_token=token,
            keyterms=keyterms,
            on_transcript_update=on_transcript_update,
            on_final_transcript_ready=on_final_transcript_ready,
            on_error=on_error,
        )

        await session.open()
        return session

    async def _fetch_temporary_token(self) -> str:
        """Call the Worker proxy to get a short-lived AssemblyAI token."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(config.TRANSCRIBE_TOKEN_ENDPOINT)

            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(
                    f"Failed to fetch AssemblyAI token "
                    f"(HTTP {response.status_code}): {response.text}"
                )

            data = response.json()
            token = data.get("token")
            if not token:
                raise RuntimeError("Invalid token response from proxy")

            return token


class AssemblyAIStreamingSession(StreamingTranscriptionSession):
    """
    A single AssemblyAI WebSocket streaming session.
    Streams PCM16 audio and receives turn-based transcripts.
    """

    EXPLICIT_FINAL_GRACE_PERIOD_SECONDS = 1.4

    def __init__(
        self,
        temporary_token: str,
        keyterms: list[str],
        on_transcript_update: callable,
        on_final_transcript_ready: callable,
        on_error: callable,
    ):
        self._token = temporary_token
        self._keyterms = keyterms
        self._on_transcript_update = on_transcript_update
        self._on_final_transcript_ready = on_final_transcript_ready
        self._on_error = on_error

        self._websocket = None
        self._receive_task = None
        self._is_open = False
        self._has_delivered_final = False
        self._is_awaiting_final = False
        self._latest_transcript = ""
        self._active_turn_order = None
        self._active_turn_text = ""
        self._stored_turns: dict[int, dict] = {}  # turn_order -> {text, is_formatted}
        self._grace_period_task = None
        self._lock = threading.Lock()

    @property
    def final_transcript_fallback_delay_seconds(self) -> float:
        return 2.8

    async def open(self):
        """Connect to AssemblyAI WebSocket and start receiving messages."""
        url = self._build_websocket_url()
        logger.info(f"AssemblyAI: connecting to WebSocket...")

        try:
            self._websocket = await websockets.connect(
                url,
                additional_headers={},
                max_size=None,
                ping_interval=None,
            )
            self._is_open = True

            def _log_exc(t):
                if not t.cancelled() and t.exception():
                    import traceback
                    exc = t.exception()
                    logger.error(f"AssemblyAI Task failed: {''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}")

            self._receive_task = asyncio.create_task(self._receive_loop())
            self._receive_task.add_done_callback(_log_exc)

            # Wait for the "Begin" message
            # The receive loop will handle it, but we give it a moment
            await asyncio.sleep(0.1)
            logger.info("AssemblyAI: WebSocket connected")

        except Exception as e:
            logger.error(f"AssemblyAI: WebSocket connection failed: {e}")
            raise

    def append_audio_buffer(self, pcm16_data: bytes):
        """Send PCM16 audio data over the WebSocket."""
        if not self._is_open or not self._websocket:
            return

        try:
            # Use asyncio to send from potentially different thread
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._send_audio(pcm16_data))
            else:
                loop.run_until_complete(self._send_audio(pcm16_data))
        except Exception as e:
            logger.warning(f"AssemblyAI: failed to send audio: {e}")

    async def _send_audio(self, data: bytes):
        if self._websocket and self._is_open:
            try:
                await self._websocket.send(data)
            except Exception as e:
                self._fail_session(e)

    async def request_final_transcript(self):
        """Request final transcript (called on key-up)."""
        with self._lock:
            if self._has_delivered_final:
                return
            self._is_awaiting_final = True

        # Send ForceEndpoint to trigger final processing
        await self._send_json({"type": "ForceEndpoint"})

        def _log_exc(t):
            if not t.cancelled() and t.exception():
                import traceback
                exc = t.exception()
                logger.error(f"AssemblyAI Task failed: {''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}")

        # Schedule grace period fallback
        self._grace_period_task = asyncio.create_task(
            self._grace_period_fallback()
        )
        self._grace_period_task.add_done_callback(_log_exc)

    async def cancel(self):
        """Cancel the session and close the WebSocket."""
        if self._grace_period_task:
            self._grace_period_task.cancel()

        if self._receive_task:
            self._receive_task.cancel()

        await self._send_json({"type": "Terminate"})

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass

        self._is_open = False

    async def _receive_loop(self):
        """Receive and process WebSocket messages."""
        try:
            async for message in self._websocket:
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                self._handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.debug("AssemblyAI: WebSocket closed")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._fail_session(e)

    def _handle_message(self, text: str):
        """Process an incoming WebSocket message."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type", "").lower()

        if msg_type == "begin":
            logger.debug("AssemblyAI: session ready")

        elif msg_type == "turn":
            self._handle_turn(data)

        elif msg_type == "termination":
            with self._lock:
                if self._is_awaiting_final and not self._has_delivered_final:
                    self._deliver_final(self._best_transcript())

        elif msg_type == "error":
            error_msg = data.get("error") or data.get("message") or "Unknown error"
            self._fail_session(RuntimeError(error_msg))

    def _handle_turn(self, data: dict):
        """Handle a 'turn' message with partial/final transcript."""
        transcript = (data.get("transcript") or "").strip()
        turn_order = data.get("turn_order") or self._active_turn_order
        if turn_order is None:
            turn_order = (max(self._stored_turns.keys(), default=-1)) + 1

        end_of_turn = data.get("end_of_turn", False)
        is_formatted = data.get("turn_is_formatted", False)

        with self._lock:
            if end_of_turn or is_formatted:
                self._active_turn_order = None
                self._active_turn_text = ""
                self._store_turn(transcript, turn_order, is_formatted)
            else:
                self._active_turn_order = turn_order
                self._active_turn_text = transcript

            full_text = self._compose_full_transcript()
            self._latest_transcript = full_text

            if full_text:
                self._on_transcript_update(full_text)

            if self._is_awaiting_final and (end_of_turn or is_formatted):
                if self._grace_period_task:
                    self._grace_period_task.cancel()
                self._deliver_final(self._best_transcript())

    def _store_turn(self, text: str, turn_order: int, is_formatted: bool):
        if not text:
            return
        existing = self._stored_turns.get(turn_order)
        if existing and existing["is_formatted"] and not is_formatted:
            return
        self._stored_turns[turn_order] = {"text": text, "is_formatted": is_formatted}

    def _compose_full_transcript(self) -> str:
        segments = [
            v["text"]
            for _, v in sorted(self._stored_turns.items())
            if v["text"]
        ]
        if self._active_turn_text.strip():
            segments.append(self._active_turn_text.strip())
        return " ".join(segments)

    def _best_transcript(self) -> str:
        composed = self._compose_full_transcript().strip()
        return composed if composed else self._latest_transcript.strip()

    def _deliver_final(self, text: str):
        if self._has_delivered_final:
            return
        self._has_delivered_final = True
        if self._grace_period_task:
            self._grace_period_task.cancel()
        self._on_final_transcript_ready(text)

    async def _grace_period_fallback(self):
        """After grace period, deliver whatever transcript we have."""
        try:
            await asyncio.sleep(self.EXPLICIT_FINAL_GRACE_PERIOD_SECONDS)
            with self._lock:
                self._deliver_final(self._best_transcript())
        except asyncio.CancelledError:
            pass

    async def _send_json(self, payload: dict):
        if self._websocket and self._is_open:
            try:
                await self._websocket.send(json.dumps(payload))
            except Exception as e:
                logger.warning(f"AssemblyAI: failed to send JSON: {e}")

    def _fail_session(self, error: Exception):
        with self._lock:
            text = self._best_transcript()
            if self._is_awaiting_final and not self._has_delivered_final and text:
                logger.warning(
                    f"AssemblyAI: error during session, delivering partial: {error}"
                )
                self._deliver_final(text)
                return

        logger.error(f"AssemblyAI: session failed: {error}")
        self._on_error(error)

    def _build_websocket_url(self) -> str:
        """Build the AssemblyAI WebSocket URL with query parameters."""
        params = [
            f"sample_rate={config.ASSEMBLYAI_SAMPLE_RATE}",
            "encoding=pcm_s16le",
            "format_turns=true",
            f"speech_model={config.ASSEMBLYAI_SPEECH_MODEL}",
        ]

        # Add keyterms
        normalized = [k.strip() for k in self._keyterms if k.strip()]
        if normalized:
            keyterms_json = json.dumps(normalized)
            from urllib.parse import quote
            params.append(f"keyterms_prompt={quote(keyterms_json)}")

        # Add token
        params.append(f"token={self._token}")

        return f"{config.ASSEMBLYAI_WEBSOCKET_URL}?{'&'.join(params)}"
