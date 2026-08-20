"""
screen_pipeline.py — Smart screen context with UIA-first strategy.

Attempts UIA tree extraction first (zero screenshot cost).
Falls back to cursor-proximate screenshot only when UIA insufficient.
Includes perceptual hash caching for follow-up questions.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ScreenContext:
    """Assembled screen context for AI."""
    text: str
    source: str  # "uia", "screenshot", "hybrid", "cached"
    app_name: str = ""
    window_title: str = ""
    cursor_x: int = 0
    cursor_y: int = 0
    timestamp: float = 0.0
    image_b64: Optional[str] = None

    def is_sufficient(self) -> bool:
        return len(self.text) > 200


class ScreenPipeline:
    """Smart screen context pipeline — UIA first, screenshot fallback."""

    def __init__(self):
        self._cache: Optional[ScreenContext] = None
        self._cache_hash: str = ""
        self._cache_time: float = 0
        self._cache_ttl = 5.0  # seconds
        logger.info("ScreenPipeline: initialized")

    def get_context(self, force_screenshot: bool = False) -> ScreenContext:
        """Get screen context using the best available strategy."""
        # Check cache first
        if not force_screenshot and self._cache and (time.monotonic() - self._cache_time) < self._cache_ttl:
            cached_app = self._get_app_name()
            if cached_app == self._cache.app_name:
                self._cache.source = "cached"
                return self._cache

        app_name = self._get_app_name() or "unknown"
        title = self._get_title() or ""
        cursor_x, cursor_y = self._get_cursor()

        # Strategy 1: UIA tree (free, fast)
        if not force_screenshot:
            uia_text = self._try_uia(app_name)
            if uia_text and len(uia_text) > 200:
                ctx = ScreenContext(
                    text=uia_text, source="uia",
                    app_name=app_name, window_title=title,
                    cursor_x=cursor_x, cursor_y=cursor_y,
                    timestamp=time.monotonic(),
                )
                self._update_cache(ctx)
                return ctx

        # Strategy 2: Cursor-proximate screenshot
        screenshot_b64 = self._capture_region(cursor_x, cursor_y, 500, 500)
        text = f"[Screenshot: {app_name} - {title}]"

        # Try OCR on the screenshot region
        ocr_text = self._try_ocr(screenshot_b64) if screenshot_b64 else ""
        if ocr_text:
            text = ocr_text

        ctx = ScreenContext(
            text=text, source="screenshot",
            app_name=app_name, window_title=title,
            cursor_x=cursor_x, cursor_y=cursor_y,
            timestamp=time.monotonic(),
            image_b64=screenshot_b64,
        )
        self._update_cache(ctx)
        return ctx

    def get_context_text_only(self) -> str:
        """Quick text-only context for injection into prompts."""
        ctx = self.get_context()
        parts = [f"App: {ctx.app_name}", f"Window: {ctx.window_title}"]
        if ctx.source == "uia" and ctx.text:
            parts.append(f"Screen content:\n{ctx.text[:1500]}")
        return " | ".join(parts)

    def _try_uia(self, app_name: str) -> Optional[str]:
        try:
            from uia_helper import get_app_tree_as_text
            return get_app_tree_as_text(max_depth=4, max_chars=2000)
        except Exception as e:
            logger.debug(f"ScreenPipeline: UIA failed: {e}")
            return None

    def _capture_region(self, cx: int, cy: int, w: int, h: int) -> Optional[str]:
        try:
            import mss
            import base64
            from io import BytesIO
            from PIL import Image

            left = max(0, cx - w // 2)
            top = max(0, cy - h // 2)

            with mss.mss() as sct:
                region = {"left": left, "top": top, "width": w, "height": h}
                img = sct.grab(region)
                pil_img = Image.frombytes("RGB", (img.width, img.height), img.rgb)
                buf = BytesIO()
                pil_img.save(buf, format="JPEG", quality=70)
                return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:
            logger.debug(f"ScreenPipeline: capture failed: {e}")
            return None

    def _try_ocr(self, image_b64: str) -> Optional[str]:
        try:
            from rapidocr_onnxruntime import RapidOCR
            import base64
            from io import BytesIO
            from PIL import Image

            img_bytes = base64.b64decode(image_b64)
            img = Image.open(BytesIO(img_bytes))
            ocr = RapidOCR()
            result, _ = ocr(img)
            if result:
                return "\n".join([text for _, text, _ in result])
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"ScreenPipeline: OCR failed: {e}")
        return None

    def _get_app_name(self) -> Optional[str]:
        try:
            from uia_helper import get_foreground_app_name
            return get_foreground_app_name()
        except Exception:
            return None

    def _get_title(self) -> Optional[str]:
        try:
            from uia_helper import get_window_title
            return get_window_title()
        except Exception:
            return None

    def _get_cursor(self) -> tuple[int, int]:
        try:
            import win32gui
            return win32gui.GetCursorPos()
        except Exception:
            return (0, 0)

    def _update_cache(self, ctx: ScreenContext):
        self._cache = ctx
        self._cache_time = time.monotonic()
        content_hash = hashlib.md5(ctx.text[:500].encode()).hexdigest()
        self._cache_hash = content_hash


_instance: Optional[ScreenPipeline] = None

def get_screen_pipeline() -> ScreenPipeline:
    global _instance
    if _instance is None:
        _instance = ScreenPipeline()
    return _instance
