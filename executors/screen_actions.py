"""
executors/screen_actions.py — Screen Interaction Actions

Handles: CLICK, SCROLL, DRAG, RIGHTCLICK, SCREENSHOT_REGION
Uses pyautogui for mouse/keyboard simulation.
"""

import io
import logging
import time

import pyautogui
from PIL import Image

from executors import register_action, ActionResult

logger = logging.getLogger(__name__)

# Safety settings
pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True


@register_action(
    "CLICK", "🖱️ Click", "Click at a screen position", "screen"
)
def handle_click(params: str) -> ActionResult:
    """
    Click at coordinates. Params: "x,y" or "x,y,button" (button=left/right/middle)
    """
    parts = [p.strip() for p in params.split(",")]
    if len(parts) < 2:
        return ActionResult(False, "Click requires x,y coordinates")

    try:
        x = int(parts[0].split(":")[0])
        y = int(parts[1].split(":")[0])
        button = parts[2].split(":")[0].lower() if len(parts) > 2 else "left"
    except ValueError:
        return ActionResult(False, f"Invalid coordinates: {params}")

    time.sleep(0.3)
    pyautogui.click(x, y, button=button)
    logger.info(f"Action: clicked ({x},{y}) button={button}")
    return ActionResult(True, f"Clicked at ({x},{y})")


@register_action(
    "SCROLL", "🔄 Scroll", "Scroll a window", "screen"
)
def handle_scroll(params: str) -> ActionResult:
    """
    Scroll. Params: "direction" or "direction,amount"
    direction = up/down/left/right, amount = number of clicks (default 5)
    """
    parts = [p.strip().lower() for p in params.split(",")]
    direction = parts[0] if parts else "down"
    amount = int(parts[1]) if len(parts) > 1 else 5

    if direction == "up":
        pyautogui.scroll(amount)
    elif direction == "down":
        pyautogui.scroll(-amount)
    elif direction == "left":
        pyautogui.hscroll(-amount)
    elif direction == "right":
        pyautogui.hscroll(amount)
    else:
        return ActionResult(False, f"Unknown scroll direction: {direction}")

    logger.info(f"Action: scrolled {direction} by {amount}")
    return ActionResult(True, f"Scrolled {direction}")


@register_action(
    "DRAG", "↔️ Drag", "Drag from point A to point B", "screen"
)
def handle_drag(params: str) -> ActionResult:
    """
    Drag & drop. Params: "x1,y1,x2,y2" or "x1,y1,x2,y2,duration"
    """
    parts = [p.strip() for p in params.split(",")]
    if len(parts) < 4:
        return ActionResult(False, "Drag requires x1,y1,x2,y2")

    try:
        x1 = int(parts[0].split(":")[0])
        y1 = int(parts[1].split(":")[0])
        x2 = int(parts[2].split(":")[0])
        y2 = int(parts[3].split(":")[0])
        duration = float(parts[4].split(":")[0]) if len(parts) > 4 else 0.5
    except ValueError:
        return ActionResult(False, f"Invalid drag coordinates: {params}")

    time.sleep(0.3)
    pyautogui.moveTo(x1, y1)
    time.sleep(0.1)
    pyautogui.mouseDown()
    pyautogui.moveTo(x2, y2, duration=duration)
    pyautogui.mouseUp()

    logger.info(f"Action: dragged ({x1},{y1}) → ({x2},{y2})")
    return ActionResult(True, f"Dragged from ({x1},{y1}) to ({x2},{y2})")


@register_action(
    "RIGHTCLICK", "🖱️ Right-Click", "Right-click at position", "screen"
)
def handle_rightclick(params: str) -> ActionResult:
    """
    Right-click at coordinates. Params: "x,y"
    """
    parts = [p.strip() for p in params.split(",")]
    if len(parts) < 2:
        return ActionResult(False, "Right-click requires x,y coordinates")

    try:
        x = int(parts[0].split(":")[0])
        y = int(parts[1].split(":")[0])
    except ValueError:
        return ActionResult(False, f"Invalid coordinates: {params}")

    time.sleep(0.3)
    pyautogui.rightClick(x, y)
    logger.info(f"Action: right-clicked ({x},{y})")
    return ActionResult(True, f"Right-clicked at ({x},{y})")


@register_action(
    "SCREENSHOT_REGION", "📸 Screenshot Region",
    "Capture a specific screen region", "screen"
)
def handle_screenshot_region(params: str) -> ActionResult:
    """
    Capture a region screenshot. Params: "x,y,width,height"
    Returns the image data for AI context injection.
    """
    parts = [p.strip() for p in params.split(",")]
    if len(parts) < 4:
        return ActionResult(False, "Screenshot region requires x,y,width,height")

    try:
        x = int(parts[0].split(":")[0])
        y = int(parts[1].split(":")[0])
        w = int(parts[2].split(":")[0])
        h = int(parts[3].split(":")[0])
    except ValueError:
        return ActionResult(False, f"Invalid region: {params}")

    # Clamp dimensions
    w = max(10, min(w, 3840))
    h = max(10, min(h, 2160))

    screenshot = pyautogui.screenshot(region=(x, y, w, h))

    # Convert to JPEG bytes
    buffer = io.BytesIO()
    screenshot.save(buffer, format="JPEG", quality=85)
    image_data = buffer.getvalue()

    logger.info(f"Action: screenshot region ({x},{y},{w},{h}) → {len(image_data)}B")
    return ActionResult(
        success=True,
        message=f"Captured region ({w}x{h})",
        data=image_data,
        inject_context=True,
        context_label=f"Screenshot of region ({x},{y}) {w}x{h}px",
    )


@register_action(
    "SAVE_SCREENSHOT", "📸 Save Screenshot",
    "Take a full-screen screenshot and save it to the Pictures/Screenshots folder", "screen"
)
def handle_save_screenshot(params: str) -> ActionResult:
    """
    Take a full-screen screenshot and save it. Params: optional "filename"
    """
    from pathlib import Path
    from datetime import datetime
    
    screenshot_dir = Path.home() / "Pictures" / "Screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    
    filename = params.strip()
    if not filename:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"Screenshot_{timestamp}.png"
    elif not filename.lower().endswith(".png"):
        filename += ".png"
        
    filepath = screenshot_dir / filename
    
    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(filepath)
        logger.info(f"Action: saved screenshot to {filepath}")
        return ActionResult(
            success=True,
            message=f"Saved screenshot to {filepath.name}",
            data=f"Saved to: {filepath}",
            inject_context=True,
            context_label="Screenshot saved",
        )
    except Exception as e:
        return ActionResult(False, f"Failed to save screenshot: {e}")
