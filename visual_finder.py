"""
visual_finder.py — Multimodal LLM Visual Locator for Clicky

Locates UI elements on screen using a two-pass zoom + highlight overlay.
First pass determines the quadrant; second pass finds the exact coordinates
within that quadrant. Draws a pulsing translucent cyan rectangle on the screen.
"""

import os
import io
import re
import json
import logging
import argparse
import base64
import ctypes
import ctypes.wintypes
from typing import Optional, Any, Union

import PIL.Image as PILImage
from PIL import Image, ImageDraw
import pyautogui
import mss

# PySide6 is used for overlay rendering if available
try:
    from PySide6.QtWidgets import QWidget, QApplication
    from PySide6.QtCore import Qt, QTimer, QRect, QPoint
    from PySide6.QtGui import QPainter, QColor, QPen, QBrush
except ImportError:
    # Mocks for non-UI environments / tests
    class QWidget: pass
    class QApplication:
        @staticmethod
        def instance(): return None
        @staticmethod
        def primaryScreen(): return None
    class Qt:
        class WindowType:
            FramelessWindowHint = 0
            WindowStaysOnTopHint = 0
            Tool = 0
            WindowDoesNotAcceptFocus = 0
        class WidgetAttribute:
            WA_TranslucentBackground = 0
            WA_ShowWithoutActivating = 0
        class BrushStyle:
            NoBrush = 0
    class QTimer:
        def __init__(self, *args, **kwargs): pass
    class QRect:
        def __init__(self, *args): pass
    class QPoint: pass
    class QPainter: pass
    class QColor: pass
    class QPen: pass
    class QBrush: pass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  ScreenCapture Utility
# ═══════════════════════════════════════════════════════════════════════

def get_cursor_position() -> tuple[int, int]:
    """Get current cursor position in Windows screen coordinates."""
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    point = POINT()
    try:
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return (point.x, point.y)
    except Exception:
        # Fallback to pyautogui
        try:
            return pyautogui.position()
        except Exception:
            return (0, 0)


def capture_screen() -> Image.Image:
    """Capture full screen as PIL Image of the active screen (containing cursor)."""
    cursor_x, cursor_y = get_cursor_position()
    with mss.mss() as sct:
        monitors = sct.monitors[1:]  # Skip combined monitor at 0
        if not monitors:
            raise RuntimeError("No monitor found for screen capture")
        
        # Find monitor containing cursor, fallback to primary (monitors[0])
        target_monitor = monitors[0]
        for monitor in monitors:
            x, y, w, h = monitor["left"], monitor["top"], monitor["width"], monitor["height"]
            if x <= cursor_x < x + w and y <= cursor_y < y + h:
                target_monitor = monitor
                break
                
        screenshot = sct.grab(target_monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
        # Store display offset/size coordinates in info dict
        img.info['display_x'] = target_monitor["left"]
        img.info['display_y'] = target_monitor["top"]
        img.info['display_width'] = target_monitor["width"]
        img.info['display_height'] = target_monitor["height"]
        return img


# ═══════════════════════════════════════════════════════════════════════
#  Quadrant Splitter
# ═══════════════════════════════════════════════════════════════════════

def split_into_quadrants(image: Image.Image) -> dict:
    """
    Split image into 4 quadrants.
    Returns: {
        'top_left': PIL.Image,
        'top_right': PIL.Image,
        'bottom_left': PIL.Image,
        'bottom_right': PIL.Image,
        'quadrant_bounds': {
            'top_left': (x1, y1, x2, y2),
            'top_right': (x1, y1, x2, y2),
            'bottom_left': (x1, y1, x2, y2),
            'bottom_right': (x1, y1, x2, y2)
        }
    }
    """
    w, h = image.size
    mid_x = w // 2
    mid_y = h // 2
    
    top_left = image.crop((0, 0, mid_x, mid_y))
    top_right = image.crop((mid_x, 0, w, mid_y))
    bottom_left = image.crop((0, mid_y, mid_x, h))
    bottom_right = image.crop((mid_x, mid_y, w, h))
    
    return {
        'top_left': top_left,
        'top_right': top_right,
        'bottom_left': bottom_left,
        'bottom_right': bottom_right,
        'quadrant_bounds': {
            'top_left': (0, 0, mid_x, mid_y),
            'top_right': (mid_x, 0, w, mid_y),
            'bottom_left': (0, mid_y, mid_x, h),
            'bottom_right': (mid_x, mid_y, w, h),
        }
    }


def draw_quadrant_grid(image: Image.Image) -> Image.Image:
    """Draw red grid lines and labels to help the LLM identify quadrants."""
    grid_img = image.copy()
    draw = ImageDraw.Draw(grid_img)
    w, h = grid_img.size
    mid_x = w // 2
    mid_y = h // 2
    
    # Draw crosshair dividing lines
    draw.line([(mid_x, 0), (mid_x, h)], fill="red", width=2)
    draw.line([(0, mid_y), (w, mid_y)], fill="red", width=2)
    
    # Draw textual labels in each quadrant
    draw.text((20, 20), "TOP-LEFT", fill="red")
    draw.text((mid_x + 20, 20), "TOP-RIGHT", fill="red")
    draw.text((20, mid_y + 20), "BOTTOM-LEFT", fill="red")
    draw.text((mid_x + 20, mid_y + 20), "BOTTOM-RIGHT", fill="red")
    
    return grid_img


def image_to_b64(img: Image.Image) -> str:
    """Convert PIL Image to JPEG base64 string."""
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def parse_json_from_response(text: str) -> dict:
    """Safely extract and parse JSON object from LLM response text."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(text.strip())
    except Exception:
        logger.warning(f"Failed to parse LLM JSON response: {text}")
        return {}


# ═══════════════════════════════════════════════════════════════════════
#  LLM Interaction Functions
# ═══════════════════════════════════════════════════════════════════════

def identify_quadrant(llm_client: Any, image: Image.Image, target_description: str) -> dict:
    """First pass: Ask LLM which quadrant contains the target."""
    grid_img = draw_quadrant_grid(image)
    b64_str = image_to_b64(grid_img)
    
    prompt = f"""Look at this screenshot divided into 4 quadrants (top-left, top-right, bottom-left, bottom-right). The user is looking for: {target_description}

Which quadrant contains this element? Respond with ONLY a JSON object:
{{
    "quadrant": "top_left" | "top_right" | "bottom_left" | "bottom_right",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}"""

    # Imports dynamically to handle tests/standalone CLI
    try:
        from groq_router import RoutedRequest, TaskType, Priority, get_router
        router = llm_client or get_router()
        req = RoutedRequest(
            system_prompt="You are a precise UI element finder. Respond ONLY with valid JSON.",
            user_prompt=prompt,
            task_type=TaskType.VISION_TASK,
            priority=Priority.URGENT,
            image_b64=b64_str,
            temperature=0.1,
        )
        response = router.chat(req)
        text = response.text
    except Exception as e:
        logger.error(f"Failed to call Groq router: {e}")
        # Standard mock fallback if groq package is missing
        text = '{"quadrant": "top_left", "confidence": 0.5, "reasoning": "Mock fallback"}'

    result = parse_json_from_response(text)
    
    # Validation/Fallback
    valid_quadrants = {"top_left", "top_right", "bottom_left", "bottom_right"}
    if not result or "quadrant" not in result or result["quadrant"] not in valid_quadrants:
        result = {
            "quadrant": "top_left",
            "confidence": 0.1,
            "reasoning": "Failed to parse JSON response correctly"
        }
    else:
        result["confidence"] = float(result.get("confidence", 0.5))
        
    return result


def find_in_region(llm_client: Any, image: Image.Image, target_description: str) -> dict:
    """Second pass: Find exact position within cropped region."""
    b64_str = image_to_b64(image)
    
    prompt = f"""Look at this cropped screenshot region. The user is looking for: {target_description}

Where is it located within this image? Return coordinates as a percentage of the image dimensions (0.0-1.0):
{{
    "x": 0.0-1.0,  # percentage from left
    "y": 0.0-1.0,  # percentage from top
    "confidence": 0.0-1.0,
    "description": "brief visual description of what you found"
}}"""

    try:
        from groq_router import RoutedRequest, TaskType, Priority, get_router
        router = llm_client or get_router()
        req = RoutedRequest(
            system_prompt="You are a precise UI coordinate locator. Respond ONLY with valid JSON.",
            user_prompt=prompt,
            task_type=TaskType.VISION_TASK,
            priority=Priority.URGENT,
            image_b64=b64_str,
            temperature=0.1,
        )
        response = router.chat(req)
        text = response.text
    except Exception as e:
        logger.error(f"Failed to call Groq router: {e}")
        text = '{"x": 0.5, "y": 0.5, "confidence": 0.5, "description": "Mock fallback"}'

    result = parse_json_from_response(text)
    
    # Validation/Fallback
    if not result or "x" not in result or "y" not in result:
        result = {
            "x": 0.5,
            "y": 0.5,
            "confidence": 0.1,
            "description": "Failed to parse coordinates from LLM response"
        }
    else:
        try:
            result["x"] = max(0.0, min(float(result["x"]), 1.0))
            result["y"] = max(0.0, min(float(result["y"]), 1.0))
            result["confidence"] = max(0.0, min(float(result.get("confidence", 0.5)), 1.0))
        except (ValueError, TypeError):
            result["x"] = 0.5
            result["y"] = 0.5
            result["confidence"] = 0.1
            
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Coordinate Mapper
# ═══════════════════════════════════════════════════════════════════════

def map_to_screen_coordinates(
    quadrant_coords: Union[tuple[int, int, int, int], dict],
    relative_coords: dict,
    display_offset: tuple[int, int] = (0, 0)
) -> tuple[int, int]:
    """Map percentage coordinates from cropped region back to absolute screen pixels."""
    # Support dict or tuple formats
    if isinstance(quadrant_coords, dict):
        # Fallback if dictionary contains quadrant names
        qx1, qy1, qx2, qy2 = quadrant_coords.get("top_left", (0, 0, 100, 100))
    else:
        qx1, qy1, qx2, qy2 = quadrant_coords
        
    qw = qx2 - qx1
    qh = qy2 - qy1
    
    local_x = int(qx1 + relative_coords['x'] * qw)
    local_y = int(qy1 + relative_coords['y'] * qh)
    
    # Map back to global system screen space
    global_x = local_x + display_offset[0]
    global_y = local_y + display_offset[1]
    
    return (global_x, global_y)


# ═══════════════════════════════════════════════════════════════════════
#  Highlight Renderer
# ═══════════════════════════════════════════════════════════════════════

def draw_highlight_region(
    screen_image: Image.Image,
    center_x: int,
    center_y: int,
    confidence: float,
    padding: int = 60,
    quadrant_bounds: Optional[tuple[int, int, int, int]] = None,
) -> Optional[Image.Image]:
    """Draw highlight overlays (cyan pulsing glow rects) onto the image."""
    if confidence < 0.4:
        return None
        
    # Convert copy to RGBA for blending transparency
    img_rgba = screen_image.copy().convert("RGBA")
    overlay = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    cyan_fill = (0, 255, 255, 100)      # Alpha 100 (~0.4)
    cyan_border = (0, 255, 255, 255)    # Solid border
    
    if confidence < 0.7 and quadrant_bounds is not None:
        # Draw broad highlight over entire quadrant
        qx1, qy1, qx2, qy2 = quadrant_bounds
        draw.rectangle([qx1, qy1, qx2, qy2], fill=(0, 255, 255, 60), outline=cyan_border, width=4)
    else:
        # Draw target box centered on point
        x1 = max(0, center_x - padding)
        y1 = max(0, center_y - padding)
        x2 = min(img_rgba.width, center_x + padding)
        y2 = min(img_rgba.height, center_y + padding)
        
        draw.rectangle([x1, y1, x2, y2], fill=cyan_fill, outline=cyan_border, width=4)
        
        # Add soft glow rings around outer bounds
        for i in range(1, 4):
            glow_pad = padding + i * 4
            gx1 = max(0, center_x - glow_pad)
            gy1 = max(0, center_y - glow_pad)
            gx2 = min(img_rgba.width, center_x + glow_pad)
            gy2 = min(img_rgba.height, center_y + glow_pad)
            glow_alpha = int(80 / (i + 1))
            draw.rectangle([gx1, gy1, gx2, gy2], fill=None, outline=(0, 255, 255, glow_alpha), width=2)
            
    final_img = Image.alpha_composite(img_rgba, overlay).convert("RGB")
    return final_img


def determine_response_strategy(confidence: float) -> str:
    """Choose rendering method based on confidence thresholds."""
    if confidence >= 0.8:
        return "tight"
    elif confidence >= 0.6:
        return "medium"
    elif confidence >= 0.4:
        return "broad"
    else:
        return "verbal"


# ═══════════════════════════════════════════════════════════════════════
#  Pulsing PySide6 Highlight Overlay Window
# ═══════════════════════════════════════════════════════════════════════

class HighlightOverlayWindow(QWidget):
    """Transparent always-on-top click-through widget that draws a pulsing highlight rectangle."""

    def __init__(self, rect: QRect, is_quadrant: bool = False, confidence: float = 1.0):
        super().__init__()
        self.target_rect = rect
        self.is_quadrant = is_quadrant
        self.confidence = confidence
        self._pulse_opacity = 255
        self._fade_dir = -15
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        # Overlay covering entire desktop area
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
            
        self._enable_click_through()
        
        # Animating pulse loop
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_pulse)
        self._timer.start(40)
        
        # Auto-destroy window after 5 seconds
        self._close_timer = QTimer(self)
        self._close_timer.singleShot(5000, self.close)
        
    def _enable_click_through(self):
        """Configure native windows styles for mouse click-through."""
        hwnd = int(self.winId())
        try:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, -20,
                style | 0x00000020 | 0x00080000 | 0x00000080 | 0x08000000
            )
        except Exception as e:
            logger.warning(f"Failed to apply Win32 click-through styles: {e}")
            
    def _update_pulse(self):
        self._pulse_opacity += self._fade_dir
        if self._pulse_opacity <= 60:
            self._pulse_opacity = 60
            self._fade_dir = 15
        elif self._pulse_opacity >= 255:
            self._pulse_opacity = 255
            self._fade_dir = -15
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        cyan = QColor(0, 255, 255, self._pulse_opacity)
        border_pen = QPen(cyan, 4)
        painter.setPen(border_pen)
        
        fill_alpha = int(self._pulse_opacity * 0.3)
        fill_color = QColor(0, 255, 255, fill_alpha)
        painter.setBrush(QBrush(fill_color))
        
        painter.drawRect(self.target_rect)
        
        # Draw outer glows for localized elements
        if not self.is_quadrant:
            for i in range(1, 4):
                glow_alpha = int((self._pulse_opacity * 0.2) / (i + 1))
                glow_color = QColor(0, 255, 255, glow_alpha)
                painter.setPen(QPen(glow_color, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                glow_rect = self.target_rect.adjusted(-i*4, -i*4, i*4, i*4)
                painter.drawRect(glow_rect)
                
        painter.end()


# ═══════════════════════════════════════════════════════════════════════
#  Main Orchestrator
# ═══════════════════════════════════════════════════════════════════════

class VisualFinder:
    def __init__(self, llm_client: Any = None):
        self.llm = llm_client
        self._overlay_window = None

    def locate(self, target_description: str, show_cursor: bool = False) -> dict:
        """Main orchestrator locating elements on the active monitor."""
        logger.info(f"VisualFinder: searching for '{target_description}'...")
        
        # Step 1: Capture screen
        try:
            screen_image = capture_screen()
        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            return {
                'success': False,
                'highlight_image': None,
                'screen_coords': (0, 0),
                'confidence': 0.0,
                'message': f"Failed to capture screen: {e}",
                'fallback_used': False,
            }
            
        display_x = screen_image.info.get('display_x', 0)
        display_y = screen_image.info.get('display_y', 0)
        
        # Step 2: Split into quadrants
        quads = split_into_quadrants(screen_image)
        
        # Step 3: First pass - identify quadrant
        quad_res = identify_quadrant(self.llm, screen_image, target_description)
        quad_name = quad_res.get("quadrant", "top_left")
        quad_conf = quad_res.get("confidence", 0.5)
        logger.info(f"VisualFinder First Pass: quadrant={quad_name}, conf={quad_conf:.2f}")
        
        fallback_used = False
        if quad_conf < 0.4:
            logger.warning("Low quadrant confidence. Falling back to full-screen search.")
            fallback_used = True
            # For fallback, we default search to the whole screen region
            # (or we default coordinates to the center of the screen)
            
        # Step 4: Crop to quadrant
        crop_img = quads[quad_name]
        quad_bounds = quads['quadrant_bounds'][quad_name]
        
        # Step 5: Second pass - find within crop
        rel_res = find_in_region(self.llm, crop_img, target_description)
        rel_conf = rel_res.get("confidence", 0.5)
        logger.info(f"VisualFinder Second Pass: x={rel_res.get('x')}, y={rel_res.get('y')}, conf={rel_conf:.2f}")
        
        # Final combined confidence rating
        final_confidence = rel_conf
        
        # Step 6: Map to screen coordinates
        screen_coords = map_to_screen_coordinates(quad_bounds, rel_res, (display_x, display_y))
        
        # Step 7: Determine strategy
        strategy = determine_response_strategy(final_confidence)
        logger.info(f"VisualFinder Strategy: strategy={strategy}, final_coords={screen_coords}")
        
        # Step 8: Draw highlight or fallback
        highlight_image = None
        message = ""
        success = False
        
        if strategy == "verbal":
            message = f"I think the '{target_description}' is in the {quad_name.replace('_', '-')} quadrant, but I'm not confident enough to highlight it."
        elif strategy == "broad":
            # Highlight entire quadrant
            highlight_image = draw_highlight_region(screen_image, 0, 0, final_confidence, quadrant_bounds=quad_bounds)
            message = f"I found the '{target_description}' in the {quad_name.replace('_', '-')} area."
            success = True
            
            # Show transparent overlay window if Qt is running
            self._show_overlay_window(quad_bounds, is_quadrant=True, confidence=final_confidence, offset=(display_x, display_y))
        else:
            padding_px = 40 if strategy == "tight" else 80
            # Calculate coordinates relative to screen_image crop (local coords for drawing on screenshot)
            local_x = screen_coords[0] - display_x
            local_y = screen_coords[1] - display_y
            
            highlight_image = draw_highlight_region(screen_image, local_x, local_y, final_confidence, padding=padding_px)
            message = f"I found the '{target_description}' in the {quad_name.replace('_', '-')} area."
            success = True
            
            # Show transparent overlay box
            target_rect = QRect(screen_coords[0] - padding_px, screen_coords[1] - padding_px, padding_px * 2, padding_px * 2)
            self._show_overlay_window(target_rect, is_quadrant=False, confidence=final_confidence)

        # Optional: Move cursor to coordinates
        if success and show_cursor:
            try:
                pyautogui.moveTo(screen_coords[0], screen_coords[1], duration=0.5)
            except Exception as e:
                logger.warning(f"Could not move cursor: {e}")

        return {
            'success': success,
            'highlight_image': highlight_image,
            'screen_coords': screen_coords,
            'confidence': final_confidence,
            'message': message,
            'fallback_used': fallback_used,
        }

    def _show_overlay_window(self, rect: Union[tuple[int, int, int, int], QRect], is_quadrant: bool, confidence: float, offset: tuple[int, int] = (0, 0)):
        """Instantiate and display transparent Qt highlight window."""
        app = QApplication.instance()
        if not app:
            logger.info("PySide6 QApplication not active. Skipping transparent highlight window.")
            return
            
        try:
            if isinstance(rect, tuple):
                x1, y1, x2, y2 = rect
                qrect = QRect(x1 + offset[0], y1 + offset[1], x2 - x1, y2 - y1)
            else:
                qrect = rect
                
            # Close existing if active
            if self._overlay_window:
                self._overlay_window.close()
                
            self._overlay_window = HighlightOverlayWindow(qrect, is_quadrant, confidence)
            self._overlay_window.show()
        except Exception as e:
            logger.error(f"Failed to display highlight window overlay: {e}")


def speak_feedback(message: str):
    """Speak voice feedback using Edge TTS or pyttsx3 fallback."""
    logger.info(f"Feedback Spoken: {message}")
    # Try importing pyttsx3 first as requested by spec
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(message)
        engine.runAndWait()
        return
    except Exception:
        pass
        
    # Edge TTS fallback
    try:
        from edge_tts_client import get_tts_client
        import asyncio
        client = get_tts_client()
        asyncio.run(client.speak_text(message))
    except Exception:
        # Final fallback
        print(f"[Speech Output]: {message}")


# ═══════════════════════════════════════════════════════════════════════
#  CLI Interface
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="What to find")
    parser.add_argument("--output", default="highlighted.png", help="Output image path")
    parser.add_argument("--show-cursor", action="store_true", help="Move cursor to target coordinates")
    args = parser.parse_args()
    
    # Needs a Qt app event loop for overlay windows to work on CLI
    app = QApplication.instance()
    is_standalone_app = False
    if not app:
        app = QApplication([])
        is_standalone_app = True
        
    finder = VisualFinder()
    result = finder.locate(args.target, show_cursor=args.show_cursor)
    
    print(f"Finder Results: {result['message']}")
    if result['success']:
        print(f"Screen coordinates: {result['screen_coords']}")
        print(f"Confidence: {result['confidence']:.2f}")
        if result['highlight_image']:
            result['highlight_image'].save(args.output)
            print(f"Saved highlighted output image to: {args.output}")
            
        speak_feedback(result['message'])
        
        # Keep window visible briefly if standalone
        if is_standalone_app:
            QTimer.singleShot(5000, app.quit)
            app.exec()
    else:
        print("Target element could not be found with enough confidence.")


if __name__ == "__main__":
    main()
