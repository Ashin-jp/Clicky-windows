"""
screen_reader_mode.py — Reads visible screen content aloud.

Extracts content via UIA tree, filters noise, reads in sentence chunks
with natural pauses. Fallback to OCR if UIA empty.
"""

import logging
import re
import threading
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# UIA control types to skip
SKIP_TYPES = {"ToolBar", "StatusBar", "MenuBar", "ScrollBar", "TitleBar", "Separator"}


class ScreenReaderMode:
    """Reads screen content aloud with structural intelligence."""

    def __init__(self):
        self._tts_callback: Optional[Callable] = None
        self._reading_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reading_speed = 1.0  # multiplier
        logger.info("ScreenReaderMode: initialized")

    def set_tts_callback(self, callback: Callable):
        self._tts_callback = callback

    def set_speed(self, speed: float):
        self._reading_speed = max(0.5, min(2.0, speed))

    def is_reading(self) -> bool:
        return self._reading_thread is not None and self._reading_thread.is_alive()

    def start_reading(self):
        """Start reading the screen content."""
        if self.is_reading():
            return

        self._stop_event.clear()
        self._reading_thread = threading.Thread(
            target=self._read_loop, name="ScreenReaderThread", daemon=True
        )
        self._reading_thread.start()

    def stop_reading(self):
        """Stop reading immediately."""
        self._stop_event.set()
        if self._reading_thread:
            self._reading_thread.join(timeout=3)
            self._reading_thread = None
        logger.info("ScreenReaderMode: stopped")

    def _read_loop(self):
        """Extract and read screen content."""
        text = self._extract_content()
        if not text:
            self._speak("I can't read any content on the screen right now.")
            return

        self._speak("Reading screen content.")
        time.sleep(0.5)

        # Split into sentences
        sentences = self._split_sentences(text)
        for sentence in sentences:
            if self._stop_event.is_set():
                return
            sentence = sentence.strip()
            if not sentence:
                continue
            self._speak(sentence)

            # Natural pauses
            if sentence.endswith(".") or sentence.endswith("!") or sentence.endswith("?"):
                pause = 0.4 / self._reading_speed
            elif sentence.endswith(",") or sentence.endswith(";"):
                pause = 0.15 / self._reading_speed
            else:
                pause = 0.2 / self._reading_speed

            for _ in range(int(pause * 10)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    def _extract_content(self) -> Optional[str]:
        """Extract readable content from the screen."""
        # Try UIA first
        try:
            from uia_helper import get_app_tree_as_text, get_foreground_app_name
            app = get_foreground_app_name() or ""
            tree = get_app_tree_as_text(max_depth=5, max_chars=4000)

            if tree and len(tree) > 50:
                # Filter and clean
                lines = []
                for line in tree.split("\n"):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Skip filtered control types
                    skip = False
                    for st in SKIP_TYPES:
                        if f"[{st}]" in stripped:
                            skip = True
                            break
                    if skip:
                        continue

                    # Clean up control type markers for reading
                    clean = re.sub(r"\[(?:Text|Edit|Document|Pane|Group|Button|ListItem)\]\s*", "", stripped)
                    clean = clean.strip()
                    if clean and len(clean) > 2:
                        lines.append(clean)

                if lines:
                    return "\n".join(lines)
        except Exception as e:
            logger.debug(f"ScreenReader: UIA extraction failed: {e}")

        # Fallback: OCR
        try:
            from screen_pipeline import get_screen_pipeline
            ctx = get_screen_pipeline().get_context(force_screenshot=True)
            if ctx.text and len(ctx.text) > 50:
                return ctx.text
        except Exception as e:
            logger.debug(f"ScreenReader: OCR fallback failed: {e}")

        return None

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentence-sized chunks for natural reading."""
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = []
        for s in sentences:
            # Further split very long sentences
            if len(s) > 200:
                parts = re.split(r'(?<=[,;:])\s+', s)
                result.extend(parts)
            else:
                result.append(s)
        return result

    def _speak(self, text: str):
        if self._tts_callback and not self._stop_event.is_set():
            try:
                self._tts_callback(text)
            except Exception:
                pass


_instance: Optional[ScreenReaderMode] = None

def get_screen_reader() -> ScreenReaderMode:
    global _instance
    if _instance is None:
        _instance = ScreenReaderMode()
    return _instance
