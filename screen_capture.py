"""
screen_capture.py — Multi-Monitor Screen Capture

Captures screenshots of all connected monitors using the mss library (GDI/BitBlt).
Identifies the cursor monitor, scales images, and returns labeled captures
with coordinate mapping data for the AI vision pipeline.

This is the Windows equivalent of CompanionScreenCaptureUtility.swift.
"""

import ctypes
import io
import logging
from dataclasses import dataclass

import mss
import mss.tools
from PIL import Image

import config

logger = logging.getLogger(__name__)


@dataclass
class ScreenCapture:
    """Captured screenshot data with metadata for coordinate mapping."""

    image_data: bytes  # JPEG bytes
    label: str  # Human-readable screen label for Claude
    is_cursor_screen: bool
    display_x: int  # Display origin X in Windows screen coordinates
    display_y: int  # Display origin Y in Windows screen coordinates
    display_width: int  # Display width in logical pixels
    display_height: int  # Display height in logical pixels
    screenshot_width: int  # Screenshot image width in pixels
    screenshot_height: int  # Screenshot image height in pixels


def get_cursor_position() -> tuple[int, int]:
    """Get the current cursor position in Windows screen coordinates."""
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


def _scale_dimensions(width: int, height: int, max_dim: int) -> tuple[int, int]:
    """Scale dimensions so the longest side is max_dim, preserving aspect ratio."""
    if width >= height:
        new_width = max_dim
        new_height = int(max_dim * height / width)
    else:
        new_height = max_dim
        new_width = int(max_dim * width / height)
    return (new_width, new_height)


def _point_in_rect(px: int, py: int, x: int, y: int, w: int, h: int) -> bool:
    """Check if a point is inside a rectangle."""
    return x <= px < x + w and y <= py < y + h


def capture_all_screens() -> list[ScreenCapture]:
    """
    Capture all connected monitors as JPEG images, sorted so the
    cursor's screen is first. Returns labeled captures with metadata
    for coordinate mapping.

    This matches the macOS captureAllScreensAsJPEG() behavior:
    - Captures all displays
    - Scales to max 1280px on longest side
    - JPEG at 80% quality
    - Labels each with cursor/screen info
    """
    cursor_x, cursor_y = get_cursor_position()
    captures = []

    with mss.mss() as sct:
        monitors = sct.monitors[1:]  # Skip the "all monitors combined" entry at index 0

        if not monitors:
            raise RuntimeError("No display available for capture")

        # Sort so cursor's monitor is first
        sorted_monitors = sorted(
            enumerate(monitors),
            key=lambda idx_mon: (
                not _point_in_rect(
                    cursor_x, cursor_y,
                    idx_mon[1]["left"], idx_mon[1]["top"],
                    idx_mon[1]["width"], idx_mon[1]["height"],
                ),
            ),
        )

        for display_index, (original_index, monitor) in enumerate(sorted_monitors):
            is_cursor_screen = _point_in_rect(
                cursor_x, cursor_y,
                monitor["left"], monitor["top"],
                monitor["width"], monitor["height"],
            )

            # Capture the screen
            screenshot = sct.grab(monitor)

            # Convert to PIL Image and scale
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            target_w, target_h = _scale_dimensions(
                img.width, img.height, config.SCREENSHOT_MAX_DIMENSION
            )
            img_scaled = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Encode to JPEG
            jpeg_buffer = io.BytesIO()
            img_scaled.save(
                jpeg_buffer,
                format="JPEG",
                quality=config.SCREENSHOT_JPEG_QUALITY,
            )
            jpeg_data = jpeg_buffer.getvalue()

            # Build label matching the macOS format
            total_screens = len(monitors)
            if total_screens == 1:
                screen_label = "user's screen (cursor is here)"
            elif is_cursor_screen:
                screen_label = (
                    f"screen {display_index + 1} of {total_screens} — "
                    f"cursor is on this screen (primary focus)"
                )
            else:
                screen_label = (
                    f"screen {display_index + 1} of {total_screens} — "
                    f"secondary screen"
                )

            captures.append(ScreenCapture(
                image_data=jpeg_data,
                label=screen_label,
                is_cursor_screen=is_cursor_screen,
                display_x=monitor["left"],
                display_y=monitor["top"],
                display_width=monitor["width"],
                display_height=monitor["height"],
                screenshot_width=target_w,
                screenshot_height=target_h,
            ))

    if not captures:
        raise RuntimeError("Failed to capture any screen")

    logger.info(
        f"Screen capture: {len(captures)} screen(s), "
        f"cursor on screen {next((i+1 for i, c in enumerate(captures) if c.is_cursor_screen), '?')}"
    )

    return captures


def capture_region(rect: tuple) -> bytes:
    """
    Capture a specific screen region as JPEG bytes.

    Args:
        rect: (left, top, width, height) in physical pixel coordinates

    Returns:
        JPEG bytes of the captured region

    Raises:
        ValueError: If rect dimensions are zero or negative
    """
    left, top, width, height = rect

    if width <= 0 or height <= 0:
        raise ValueError(
            f"capture_region received invalid dimensions: width={width}, height={height}. "
            "Both must be positive integers."
        )

    monitor = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}

    with mss.mss() as sct:
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    jpeg_buffer = io.BytesIO()
    img.save(jpeg_buffer, format="JPEG", quality=85)
    return jpeg_buffer.getvalue()
