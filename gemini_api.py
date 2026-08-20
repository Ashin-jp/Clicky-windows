"""
gemini_api.py — Google Gemini Vision API Client with Streaming

Sends screenshots + transcript to Gemini directly (no Worker needed).
Uses the google-genai SDK for streaming responses.

Alternative to claude_api.py — can be selected in the floating panel.
"""

import base64
import logging
import time

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)


class GeminiAPI:
    """
    Gemini API client with streaming support.
    Calls Gemini directly — no Cloudflare Worker needed.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or config.GEMINI_API_KEY
        self.model = model or config.DEFAULT_GEMINI_MODEL

        if not self.api_key:
            raise ValueError(
                "Gemini API key required. Set GEMINI_API_KEY in config.py "
                "or pass it directly."
            )

        self._client = genai.Client(api_key=self.api_key)

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
        Send a vision request to Gemini with streaming.

        Args:
            images: List of (image_data_bytes, label_string)
            system_prompt: System instruction for Gemini
            conversation_history: List of (user, assistant) pairs
            user_prompt: The user's current transcript
            on_text_chunk: Callback with accumulated text

        Returns:
            (full_response_text, duration_seconds)
        """
        start_time = time.monotonic()

        # Build contents array
        contents = []

        # Add conversation history
        if conversation_history:
            for user_text, assistant_text in conversation_history:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=user_text)],
                    )
                )
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=assistant_text)],
                    )
                )

        # Build current message with images + prompt
        current_parts = []

        for image_data, label in images:
            media_type = self.detect_image_media_type(image_data)
            current_parts.append(
                types.Part.from_bytes(data=image_data, mime_type=media_type)
            )
            current_parts.append(types.Part.from_text(text=label))

        current_parts.append(types.Part.from_text(text=user_prompt))
        contents.append(
            types.Content(role="user", parts=current_parts)
        )

        payload_kb = sum(len(img) for img, _ in images) / 1024
        logger.info(
            f"Gemini streaming request: {payload_kb:.0f}KB images, "
            f"{len(images)} image(s), model={self.model}"
        )

        # Stream the response
        accumulated_text = ""

        response = self._client.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=config.GEMINI_MAX_TOKENS,
                temperature=0.7,
            ),
        )

        for chunk in response:
            if chunk.text:
                accumulated_text += chunk.text
                if on_text_chunk:
                    on_text_chunk(accumulated_text)

        duration = time.monotonic() - start_time
        logger.info(f"Gemini response: {len(accumulated_text)} chars in {duration:.1f}s")
        return (accumulated_text, duration)

    async def close(self):
        """No persistent connection to close for Gemini."""
        pass
