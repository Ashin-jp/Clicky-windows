"""
claude_api.py — Claude Vision API Client with SSE Streaming

Sends screenshots + transcript to Claude via the Cloudflare Worker proxy.
Parses Server-Sent Events for progressive text display.

This is the Windows equivalent of ClaudeAPI.swift.
"""

import base64
import json
import logging
import time

import httpx

import config

logger = logging.getLogger(__name__)


class ClaudeAPI:
    """
    Claude API client with streaming SSE support.
    All requests go through the Cloudflare Worker proxy.
    """

    _tls_warmed_up = False

    def __init__(self, proxy_url: str | None = None, model: str | None = None):
        self.api_url = proxy_url or config.CHAT_ENDPOINT
        self.model = model or config.DEFAULT_CLAUDE_MODEL

        # Use a persistent client for connection pooling and TLS session reuse
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=config.CLAUDE_TIMEOUT_SECONDS,
                write=30.0,
                pool=30.0,
            ),
        )

        # TLS warmup — fire a lightweight request to pre-establish the connection
        self._warmup_tls_if_needed()

    def _warmup_tls_if_needed(self):
        """
        Pre-establish TLS connection with a HEAD request so the first real
        API call (with large image payload) doesn't need a cold handshake.
        """
        if ClaudeAPI._tls_warmed_up:
            return
        ClaudeAPI._tls_warmed_up = True

        try:
            # Parse base URL from the chat endpoint
            from urllib.parse import urlparse
            parsed = urlparse(self.api_url)
            warmup_url = f"{parsed.scheme}://{parsed.netloc}/"

            # Fire and forget — we don't care about the response
            import asyncio
            try:
                task = loop.create_task(self._do_warmup(warmup_url))
                task.add_done_callback(lambda t: logger.error(f"Claude warmup failed: {t.exception()}") if not t.cancelled() and t.exception() else None)
            except RuntimeError:
                # No running loop yet — skip async warmup, it'll happen on first request
                pass
        except Exception:
            pass  # Warmup failure is fine — it's purely an optimization

    async def _do_warmup(self, url: str):
        try:
            await self._client.head(url, timeout=10.0)
        except Exception:
            pass

    @staticmethod
    def detect_image_media_type(image_data: bytes) -> str:
        """
        Detect MIME type by inspecting the first bytes.
        Screen captures use JPEG; clipboard images may be PNG.
        """
        if len(image_data) >= 4:
            png_signature = b'\x89PNG'
            if image_data[:4] == png_signature:
                return "image/png"
        return "image/jpeg"

    async def analyze_image_streaming(
        self,
        images: list[tuple[bytes, str]],  # List of (image_data, label)
        system_prompt: str,
        conversation_history: list[tuple[str, str]] | None = None,
        user_prompt: str = "",
        on_text_chunk: callable = None,
    ) -> tuple[str, float]:
        """
        Send a vision request to Claude with streaming SSE.

        Args:
            images: List of (image_data_bytes, label_string)
            system_prompt: System prompt for Claude
            conversation_history: List of (user_transcript, assistant_response) pairs
            user_prompt: The user's current transcript
            on_text_chunk: Callback with accumulated text so far

        Returns:
            (full_response_text, duration_seconds)
        """
        start_time = time.monotonic()

        # Build messages array
        messages = []

        if conversation_history:
            for user_text, assistant_text in conversation_history:
                messages.append({"role": "user", "content": user_text})
                messages.append({"role": "assistant", "content": assistant_text})

        # Build current message with all labeled images + prompt
        content_blocks = []
        for image_data, label in images:
            media_type = self.detect_image_media_type(image_data)
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image_data).decode("ascii"),
                },
            })
            content_blocks.append({
                "type": "text",
                "text": label,
            })

        content_blocks.append({
            "type": "text",
            "text": user_prompt,
        })
        messages.append({"role": "user", "content": content_blocks})

        body = {
            "model": self.model,
            "max_tokens": config.CLAUDE_MAX_TOKENS,
            "stream": True,
            "system": system_prompt,
            "messages": messages,
        }

        body_json = json.dumps(body)
        payload_mb = len(body_json) / 1_048_576.0
        logger.info(
            f"Claude streaming request: {payload_mb:.1f}MB, "
            f"{len(images)} image(s), model={self.model}"
        )

        # Stream the response using httpx
        accumulated_text = ""

        async with self._client.stream(
            "POST",
            self.api_url,
            content=body_json,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                error_body = await response.aread()
                error_text = error_body.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Claude API Error ({response.status_code}): {error_text}"
                )

            # Parse SSE stream
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                json_string = line[6:]  # Drop "data: " prefix

                if json_string == "[DONE]":
                    break

                try:
                    event = json.loads(json_string)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")
                if event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text_chunk = delta.get("text", "")
                        accumulated_text += text_chunk
                        if on_text_chunk:
                            on_text_chunk(accumulated_text)

        duration = time.monotonic() - start_time
        logger.info(f"Claude response: {len(accumulated_text)} chars in {duration:.1f}s")
        return (accumulated_text, duration)

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
