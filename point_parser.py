"""
point_parser.py — [POINT:x,y:label:screenN] Tag Parser

Parses cursor pointing tags from Claude's response text.
Matches the macOS CompanionManager.parsePointingCoordinates() logic exactly.
"""

import re
from dataclasses import dataclass


@dataclass
class PointingParseResult:
    """Result of parsing a [POINT:...] tag from Claude's response."""

    # The response text with the [POINT:...] tag removed — this is what gets spoken
    spoken_text: str
    # The parsed pixel coordinate, or None if Claude said "none" or no tag was found
    coordinate: tuple[float, float] | None
    # Short label describing the element (e.g. "run button"), or None
    element_label: str | None
    # Which screen the coordinate refers to (1-based), or None to default to cursor screen
    screen_number: int | None


# Regex pattern matching [POINT:none] or [POINT:123,456:label] or [POINT:123,456:label:screen2]
_POINT_TAG_PATTERN = re.compile(
    r'\[POINT:(?:none|(\d+)\s*,\s*(\d+)(?::([^\]:\s][^\]:]*?))?(?::screen(\d+))?)\]',
    re.IGNORECASE
)


def parse_pointing_coordinates(response_text: str) -> PointingParseResult:
    """
    Parses a [POINT:x,y:label:screenN] or [POINT:none] tag from the end
    of Claude's response. Returns the spoken text (tag removed) and the
    optional coordinate + label + screen number.
    """
    matches = list(_POINT_TAG_PATTERN.finditer(response_text))

    if not matches:
        # No tag found at all
        return PointingParseResult(
            spoken_text=response_text,
            coordinate=None,
            element_label=None,
            screen_number=None,
        )

    # Remove all point tags from the spoken text
    spoken_text = _POINT_TAG_PATTERN.sub("", response_text).strip()
    
    # Use the last match for coordinates
    last_match = matches[-1]

    # Check if it's [POINT:none]
    x_str = last_match.group(1)
    y_str = last_match.group(2)

    if x_str is None or y_str is None:
        # It was [POINT:none]
        return PointingParseResult(
            spoken_text=spoken_text,
            coordinate=None,
            element_label="none",
            screen_number=None,
        )

    x = float(x_str)
    y = float(y_str)

    element_label = last_match.group(3)
    if element_label:
        element_label = element_label.strip()

    screen_number = None
    screen_str = last_match.group(4)
    if screen_str:
        screen_number = int(screen_str)

    return PointingParseResult(
        spoken_text=spoken_text,
        coordinate=(x, y),
        element_label=element_label,
        screen_number=screen_number,
    )


def map_screenshot_to_screen_coordinates(
    point_x: float,
    point_y: float,
    screenshot_width: int,
    screenshot_height: int,
    display_x: int,
    display_y: int,
    display_width: int,
    display_height: int,
) -> tuple[int, int]:
    """
    Maps coordinates from screenshot pixel space to Windows screen coordinates.

    On Windows, screen coordinates use top-left origin (same as screenshot),
    unlike macOS which uses bottom-left origin. This simplifies the conversion.

    Args:
        point_x, point_y: Coordinates in screenshot pixel space
        screenshot_width, screenshot_height: Screenshot dimensions in pixels
        display_x, display_y: Display origin in Windows screen coordinates
        display_width, display_height: Display size in pixels (logical, DPI-scaled)

    Returns:
        (screen_x, screen_y) in Windows global screen coordinates
    """
    # Clamp to screenshot coordinate space
    clamped_x = max(0, min(point_x, screenshot_width))
    clamped_y = max(0, min(point_y, screenshot_height))

    # Scale from screenshot pixels to display pixels
    scale_x = display_width / screenshot_width
    scale_y = display_height / screenshot_height

    display_local_x = clamped_x * scale_x
    display_local_y = clamped_y * scale_y

    # Convert to global screen coordinates (top-left origin on Windows)
    screen_x = int(display_local_x + display_x)
    screen_y = int(display_local_y + display_y)

    return (screen_x, screen_y)
