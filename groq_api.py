"""
groq_api.py — Groq Vision API Client with Streaming

Sends screenshots + transcript to Groq's LPU-accelerated models.
Uses the official Groq SDK with streaming support.

Alternative to claude_api.py / gemini_api.py — ultra-fast inference.
"""

import base64
import logging
import time

from groq import Groq

import config

logger = logging.getLogger(__name__)


class GroqAPI:
    """
    Groq API client with streaming support.
    Calls Groq directly — no Cloudflare Worker needed.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or config.GROQ_API_KEY
        self.model = model or config.DEFAULT_GROQ_MODEL

        if not self.api_key:
            raise ValueError(
                "Groq API key required. Set GROQ_API_KEY in config.py "
                "or pass it directly."
            )

        self._client = Groq(api_key=self.api_key)

    @staticmethod
    def detect_image_media_type(image_data: bytes) -> str:
        if len(image_data) >= 4 and image_data[:4] == b'\x89PNG':
            return "image/png"
        return "image/jpeg"

    async def analyze_image_streaming(
        self,
        images: list[tuple[bytes, str]],
        system_prompt: str,
        conversation_history: list[tuple[str, str]] | None = None,
        user_prompt: str = "",
        on_text_chunk: callable = None,
    ) -> tuple[str, float]:
        """
        Send a vision request to Groq with streaming.

        Args:
            images: List of (image_data_bytes, label_string)
            system_prompt: System instruction
            conversation_history: List of (user, assistant) pairs
            user_prompt: The user's current transcript
            on_text_chunk: Callback with accumulated text

        Returns:
            (full_response_text, duration_seconds)
        """
        start_time = time.monotonic()

        # Build messages array (OpenAI-compatible format)
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        if conversation_history:
            for user_text, assistant_text in conversation_history:
                messages.append({"role": "user", "content": user_text})
                messages.append({"role": "assistant", "content": assistant_text})

        # Build current message with images + prompt
        content_blocks = []

        for image_data, label in images:
            media_type = self.detect_image_media_type(image_data)
            b64_data = base64.b64encode(image_data).decode("ascii")
            content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{b64_data}",
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

        payload_kb = sum(len(img) for img, _ in images) / 1024
        logger.info(
            f"Groq streaming request: {payload_kb:.0f}KB images, "
            f"{len(images)} image(s), model={self.model}"
        )

        # Stream the response
        accumulated_text = ""

        stream = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=config.GROQ_MAX_TOKENS,
            temperature=0.7,
            stream=True,
        )

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                accumulated_text += delta.content
                if on_text_chunk:
                    on_text_chunk(accumulated_text)

        duration = time.monotonic() - start_time
        logger.info(f"Groq response: {len(accumulated_text)} chars in {duration:.1f}s")
        return (accumulated_text, duration)

    async def close(self):
        """No persistent connection to close."""
        pass
