"""
overlay_window.py — Transparent Overlay with Cursor Animation

Full-screen transparent, always-on-top, click-through window that hosts
the animated blue cursor companion. One overlay per monitor.

Windows equivalent of OverlayWindow.swift + BlueCursorView.
"""

import ctypes
import math
import random
import time
import logging

from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPainterPath,
    QRadialGradient, QFont, QFontMetrics, QLinearGradient,
)

from design_system import Colors, Fonts
import config


def _dpi_scale() -> float:
    """Get the DPI scale factor. Computed live from primaryScreen so it
    automatically updates when monitors are added/removed/changed."""
    try:
        app = QApplication.instance()
        if app:
            screen = app.primaryScreen()
            if screen:
                return screen.devicePixelRatio()
        return 1.0
    except Exception:
        return 1.0

logger = logging.getLogger(__name__)

# Win32 constants for click-through
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


class BuddyNavigationMode:
    FOLLOWING_CURSOR = 0
    NAVIGATING_TO_TARGET = 1
    POINTING_AT_TARGET = 2


class OverlayWindow(QWidget):
    """
    Transparent overlay for a single monitor. Hosts the blue cursor,
    waveform, spinner, navigation bubble, and pointing animations.
    """

    def __init__(self, screen_geometry, is_first_appearance=False, parent=None):
        super().__init__(parent)
        self.screen_geometry = screen_geometry
        self.is_first_appearance = is_first_appearance

        # Window flags: frameless, always-on-top, tool window (no taskbar)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(screen_geometry)

        # State
        self._cursor_pos = QPointF(screen_geometry.width() / 2, screen_geometry.height() / 2)
        self._is_cursor_on_this_screen = False
        self._voice_state = "idle"  # idle, listening, processing, responding
        self._audio_power = 0.0
        self._cursor_opacity = 1.0
        self._nav_mode = BuddyNavigationMode.FOLLOWING_CURSOR
        self._triangle_rotation = -35.0
        self._flight_scale = 1.0
        self._nav_bubble_text = ""
        self._nav_bubble_opacity = 0.0
        self._nav_bubble_scale = 1.0
        self._welcome_text = ""
        self._welcome_opacity = 0.0
        self._show_welcome = is_first_appearance
        self._spinner_angle = 0.0
        self._detected_location = None  # Target for navigation

        # Animation state
        self._flight_start = None
        self._flight_end = None
        self._flight_control = None
        self._flight_progress = 0.0
        self._flight_duration = 0.0
        self._flight_start_time = 0.0
        self._is_returning = False
        self._cursor_at_nav_start = QPointF()
        self._nav_complete_callback = None
        self._is_pulsing = False
        self._pulse_start_time = 0.0
        self._pulse_duration_ms = 0
        self._custom_label_text = ""

        # Pointer phrases (matching macOS)
        self._pointer_phrases = [
            "right here!", "this one!", "over here!",
            "click this!", "here it is!", "found it!"
        ]

        # Timers
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._update_cursor_position)
        self._cursor_timer.start(config.CURSOR_FOLLOW_INTERVAL_MS)

        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self.update)
        self._paint_timer.start(config.CURSOR_FOLLOW_INTERVAL_MS)

        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._update_spinner)
        self._spinner_timer.start(20)

        self._flight_timer = QTimer(self)
        self._flight_timer.timeout.connect(self._update_flight)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._update_pulse)

        # Welcome animation
        if is_first_appearance:
            self._cursor_opacity = 0.0
            QTimer.singleShot(500, self._start_welcome_animation)

    def showEvent(self, event):
        super().showEvent(event)
        # Set Win32 extended styles for click-through
        hwnd = int(self.winId())
        try:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            )
        except Exception as e:
            logger.warning(f"Failed to set WS_EX_TRANSPARENT: {e}")

    # ─── Public API ───────────────────────────────────────────────────

    def set_voice_state(self, state: str):
        self._voice_state = state
        self.update()

    def set_audio_power(self, level: float):
        self._audio_power = level

    def set_cursor_opacity(self, opacity: float):
        self._cursor_opacity = opacity
        self.update()

    def navigate_to_element(self, screen_x: int, screen_y: int, label: str = None, bubble_text: str = None):
        """Start flying to a detected UI element."""
        local_x = screen_x - self.screen_geometry.x()
        local_y = screen_y - self.screen_geometry.y()
        target = QPointF(local_x, local_y)
        # Clamp to screen
        margin = int(20 * _dpi_scale())
        target.setX(max(margin, min(target.x(), self.screen_geometry.width() - margin)))
        target.setY(max(margin, min(target.y(), self.screen_geometry.height() - margin)))

        self._cursor_at_nav_start = QPointF(self._cursor_pos)
        self._nav_mode = BuddyNavigationMode.NAVIGATING_TO_TARGET
        self._is_returning = False
        self._custom_bubble_text = bubble_text

        self._start_bezier_flight(self._cursor_pos, target, self._on_forward_flight_complete)

    def pulse_at(self, screen_x: int, screen_y: int, duration_ms: int):
        """Pulse the cursor at a specific location."""
        local_x = screen_x - self.screen_geometry.x()
        local_y = screen_y - self.screen_geometry.y()
        self._cursor_pos = QPointF(local_x, local_y)
        self._nav_mode = BuddyNavigationMode.POINTING_AT_TARGET
        self._is_pulsing = True
        self._pulse_start_time = time.monotonic()
        self._pulse_duration_ms = duration_ms
        self._pulse_timer.start(16)
        QTimer.singleShot(duration_ms, self._stop_pulse)

    def _update_pulse(self):
        if not self._is_pulsing:
            return
        elapsed = time.monotonic() - self._pulse_start_time
        # Oscillate scale between 0.8 and 1.2 at 2Hz
        self._flight_scale = 1.0 + math.sin(elapsed * 2 * math.pi * 2) * 0.2
        self.update()

    def _stop_pulse(self):
        self._is_pulsing = False
        self._pulse_timer.stop()
        self._flight_scale = 1.0
        self._nav_mode = BuddyNavigationMode.FOLLOWING_CURSOR
        self.update()

    def point_and_label(self, screen_x: int, screen_y: int, label_text: str):
        """Point at a location and show a label."""
        local_x = screen_x - self.screen_geometry.x()
        local_y = screen_y - self.screen_geometry.y()
        self._cursor_pos = QPointF(local_x, local_y)
        self._nav_mode = BuddyNavigationMode.POINTING_AT_TARGET
        self._custom_label_text = label_text
        self._nav_bubble_opacity = 1.0
        self._nav_bubble_scale = 1.0
        self._nav_bubble_text = label_text
        self.update()
        QTimer.singleShot(3000, self._stop_label)

    def _stop_label(self):
        self._custom_label_text = ""
        self._nav_bubble_opacity = 0.0
        self._nav_mode = BuddyNavigationMode.FOLLOWING_CURSOR
        self.update()

    def cancel_navigation(self):
        """Cancel any in-progress navigation."""
        self._flight_timer.stop()
        self._nav_bubble_text = ""
        self._nav_bubble_opacity = 0.0
        self._flight_scale = 1.0
        self._nav_mode = BuddyNavigationMode.FOLLOWING_CURSOR
        self._is_returning = False
        self._triangle_rotation = -35.0
        self._detected_location = None
        self.update()

    # ─── Cursor Tracking ──────────────────────────────────────────────

    def _update_cursor_position(self):
        cursor = self._get_global_cursor_pos()
        geo = self.screen_geometry
        self._is_cursor_on_this_screen = geo.contains(cursor.x(), cursor.y())

        if self._nav_mode == BuddyNavigationMode.NAVIGATING_TO_TARGET and self._is_returning:
            local = QPointF(cursor.x() - geo.x(), cursor.y() - geo.y())
            dist = math.hypot(
                local.x() - self._cursor_at_nav_start.x(),
                local.y() - self._cursor_at_nav_start.y()
            )
            if dist > 100:
                self.cancel_navigation()
            return

        if self._nav_mode != BuddyNavigationMode.FOLLOWING_CURSOR:
            return

        local_x = cursor.x() - geo.x() + config.CURSOR_OFFSET_X
        local_y = cursor.y() - geo.y() + config.CURSOR_OFFSET_Y
        self._cursor_pos = QPointF(local_x, local_y)

    def _get_global_cursor_pos(self):
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return QPointF(pt.x, pt.y)

    # ─── Bezier Flight Animation ──────────────────────────────────────

    def _start_bezier_flight(self, start, end, on_complete):
        self._flight_start = QPointF(start)
        self._flight_end = QPointF(end)
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        distance = math.hypot(dx, dy)
        self._flight_duration = max(0.6, min(distance / 800.0, 1.4))
        mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
        arc_h = min(distance * 0.2, 80.0)
        self._flight_control = QPointF(mid.x(), mid.y() - arc_h)
        self._flight_start_time = time.monotonic()
        self._nav_complete_callback = on_complete
        self._flight_timer.start(16)

    def _update_flight(self):
        elapsed = time.monotonic() - self._flight_start_time
        linear = min(elapsed / self._flight_duration, 1.0)
        t = linear * linear * (3.0 - 2.0 * linear)  # smoothstep

        if linear >= 1.0:
            self._flight_timer.stop()
            self._cursor_pos = QPointF(self._flight_end)
            self._flight_scale = 1.0
            if self._nav_complete_callback:
                self._nav_complete_callback()
            self.update()
            return

        omt = 1.0 - t
        s, c, e = self._flight_start, self._flight_control, self._flight_end
        bx = omt*omt*s.x() + 2*omt*t*c.x() + t*t*e.x()
        by = omt*omt*s.y() + 2*omt*t*c.y() + t*t*e.y()
        self._cursor_pos = QPointF(bx, by)

        # Rotation: tangent to bezier
        tx = 2*omt*(c.x()-s.x()) + 2*t*(e.x()-c.x())
        ty = 2*omt*(c.y()-s.y()) + 2*t*(e.y()-c.y())
        self._triangle_rotation = math.degrees(math.atan2(ty, tx)) + 90

        # Scale pulse
        self._flight_scale = 1.0 + math.sin(linear * math.pi) * 0.3
        self.update()

    def _on_forward_flight_complete(self):
        self._nav_mode = BuddyNavigationMode.POINTING_AT_TARGET
        self._triangle_rotation = -35.0
        self._nav_bubble_text = ""
        self._nav_bubble_opacity = 1.0
        self._nav_bubble_scale = 0.5
        phrase = getattr(self, '_custom_bubble_text', None) or random.choice(self._pointer_phrases)
        self._stream_bubble_text(phrase, 0)

    def _stream_bubble_text(self, phrase, idx):
        if self._nav_mode != BuddyNavigationMode.POINTING_AT_TARGET or idx >= len(phrase):
            # Hold, then fly back
            QTimer.singleShot(3000, self._start_return_flight)
            return
        self._nav_bubble_text = phrase[:idx+1]
        if idx == 0:
            self._nav_bubble_scale = 1.0
        self.update()
        delay = random.randint(30, 60)
        QTimer.singleShot(delay, lambda: self._stream_bubble_text(phrase, idx + 1))

    def _start_return_flight(self):
        if self._nav_mode != BuddyNavigationMode.POINTING_AT_TARGET:
            return
        self._nav_bubble_opacity = 0.0
        self.update()
        QTimer.singleShot(500, self._do_return_flight)

    def _do_return_flight(self):
        if self._nav_mode != BuddyNavigationMode.POINTING_AT_TARGET:
            return
        cursor = self._get_global_cursor_pos()
        geo = self.screen_geometry
        target = QPointF(
            cursor.x() - geo.x() + config.CURSOR_OFFSET_X,
            cursor.y() - geo.y() + config.CURSOR_OFFSET_Y
        )
        self._cursor_at_nav_start = QPointF(
            cursor.x() - geo.x(), cursor.y() - geo.y()
        )
        self._nav_mode = BuddyNavigationMode.NAVIGATING_TO_TARGET
        self._is_returning = True
        self._start_bezier_flight(self._cursor_pos, target, self._finish_navigation)

    def _finish_navigation(self):
        self._nav_mode = BuddyNavigationMode.FOLLOWING_CURSOR
        self._is_returning = False
        self._triangle_rotation = -35.0
        self._flight_scale = 1.0
        self._nav_bubble_text = ""
        self._nav_bubble_opacity = 0.0
        self._nav_bubble_scale = 1.0
        self._detected_location = None
        self.update()

    # ─── Spinner ──────────────────────────────────────────────────────

    def _update_spinner(self):
        if self._voice_state == "processing":
            self._spinner_angle = (self._spinner_angle + 6) % 360

    # ─── Welcome Animation ────────────────────────────────────────────

    def _start_welcome_animation(self):
        self._cursor_opacity = 1.0
        full_msg = "hey! i'm clicky"
        self._welcome_idx = 0
        self._welcome_full = full_msg
        self._welcome_opacity = 1.0
        self._show_welcome = True

        def type_char():
            if self._welcome_idx < len(self._welcome_full):
                self._welcome_text = self._welcome_full[:self._welcome_idx + 1]
                self._welcome_idx += 1
                self.update()
                QTimer.singleShot(30, type_char)
            else:
                QTimer.singleShot(2000, self._fade_welcome)

        QTimer.singleShot(300, type_char)

    def _fade_welcome(self):
        self._welcome_opacity = 0.0
        self.update()
        QTimer.singleShot(500, lambda: setattr(self, '_show_welcome', False))

    # ─── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        show_on_screen = self._is_cursor_on_this_screen or self._nav_mode != BuddyNavigationMode.FOLLOWING_CURSOR
        if not show_on_screen:
            painter.end()
            return

        opacity = self._cursor_opacity

        # Welcome bubble
        if self._show_welcome and self._welcome_text:
            self._paint_bubble(painter, self._welcome_text, self._welcome_opacity * opacity)

        # Navigation bubble
        if self._nav_mode == BuddyNavigationMode.POINTING_AT_TARGET and self._nav_bubble_text:
            self._paint_bubble(painter, self._nav_bubble_text, self._nav_bubble_opacity * opacity)

        # Voice state visuals
        if self._voice_state == "listening":
            self._paint_waveform(painter, opacity)
        elif self._voice_state == "processing":
            self._paint_spinner(painter, opacity)
        else:
            self._paint_triangle(painter, opacity)

        painter.end()

    def _paint_triangle(self, painter: QPainter, opacity: float):
        """Paint the blue cursor triangle with glow."""
        if opacity <= 0:
            return
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(self._cursor_pos.x(), self._cursor_pos.y())
        painter.rotate(self._triangle_rotation)
        painter.scale(self._flight_scale, self._flight_scale)

        s = config.CURSOR_TRIANGLE_SIZE
        h = s * math.sqrt(3) / 2
        path = QPainterPath()
        path.moveTo(0, -h / 1.5)
        path.lineTo(-s / 2, h / 3)
        path.lineTo(s / 2, h / 3)
        path.closeSubpath()

        # Glow
        ds = _dpi_scale()
        glow_radius = int(8 * ds) + (self._flight_scale - 1.0) * int(20 * ds)
        glow = QRadialGradient(0, 0, glow_radius + s)
        glow.setColorAt(0, QColor(0, 122, 255, int(100 * opacity)))
        glow.setColorAt(1, QColor(0, 122, 255, 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), glow_radius + s, glow_radius + s)

        painter.setBrush(QBrush(Colors.cursor_blue))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        painter.restore()

    def _paint_waveform(self, painter: QPainter, opacity: float):
        """Paint the reactive waveform (5 bars)."""
        if opacity <= 0:
            return
        painter.save()
        painter.setOpacity(opacity)
        ds = _dpi_scale()
        bar_count = 5
        profile = [0.4, 0.7, 1.0, 0.7, 0.4]
        bar_w = int(2 * ds)
        spacing = int(2 * ds)
        total_w = bar_count * bar_w + (bar_count - 1) * spacing
        start_x = self._cursor_pos.x() - total_w / 2

        t = time.monotonic()
        norm_power = max(self._audio_power - 0.008, 0)
        eased = min(norm_power * 2.85, 1) ** 0.76

        for i in range(bar_count):
            phase = t * 3.6 + i * 0.35
            reactive = eased * 10 * ds * profile[i]
            idle_pulse = (math.sin(phase) + 1) / 2 * 1.5 * ds
            bar_h = int(3 * ds) + reactive + idle_pulse
            x = start_x + i * (bar_w + spacing)
            y = self._cursor_pos.y() - bar_h / 2
            painter.setBrush(QBrush(Colors.cursor_blue))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 1.5, 1.5)

        painter.restore()

    def _paint_spinner(self, painter: QPainter, opacity: float):
        """Paint the processing spinner."""
        if opacity <= 0:
            return
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(self._cursor_pos.x(), self._cursor_pos.y())
        painter.rotate(self._spinner_angle)

        ds = _dpi_scale()
        pen = QPen(Colors.cursor_blue, 2.5 * ds)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = int(7 * ds)
        rect = QRectF(-r, -r, r * 2, r * 2)
        painter.drawArc(rect, 54 * 16, 252 * 16)  # ~70% arc
        painter.restore()

    def _paint_bubble(self, painter: QPainter, text: str, opacity: float):
        """Paint a speech bubble next to the cursor."""
        if opacity <= 0 or not text:
            return
        painter.save()
        painter.setOpacity(opacity)
        font = Fonts.bubble()
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(text)
        ds = _dpi_scale()
        pad_h, pad_v = int(8 * ds), int(4 * ds)
        bw = text_rect.width() + pad_h * 2
        bh = text_rect.height() + pad_v * 2
        bx = self._cursor_pos.x() + int(10 * ds)
        by = self._cursor_pos.y() + int(10 * ds)

        # Background
        path = QPainterPath()
        br = int(6 * ds)
        path.addRoundedRect(QRectF(bx, by, bw, bh), br, br)
        painter.setBrush(QBrush(Colors.cursor_blue))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        # Text
        painter.setPen(QPen(Colors.bubble_text))
        painter.drawText(QRectF(bx + pad_h, by + pad_v, text_rect.width(), text_rect.height()),
                         Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class OverlayWindowManager:
    """Manages one overlay window per screen."""

    def __init__(self):
        self._windows: list[OverlayWindow] = []
        self._has_shown_before = False

    def show_overlay(self, companion_manager=None):
        self.hide_overlay()
        is_first = not self._has_shown_before
        self._has_shown_before = True

        for screen in QApplication.screens():
            geo = screen.geometry()
            window = OverlayWindow(geo, is_first_appearance=is_first)
            window.show()
            self._windows.append(window)

        logger.info(f"Overlay: showing on {len(self._windows)} screen(s)")

    def hide_overlay(self):
        for w in self._windows:
            w.close()
        self._windows.clear()

    def set_voice_state(self, state: str):
        for w in self._windows:
            w.set_voice_state(state)

    def set_audio_power(self, level: float):
        for w in self._windows:
            w.set_audio_power(level)

    def navigate_to_element(self, screen_x: int, screen_y: int, label: str = None, bubble_text: str = None):
        for w in self._windows:
            geo = w.screen_geometry
            if geo.x() <= screen_x < geo.x() + geo.width() and \
               geo.y() <= screen_y < geo.y() + geo.height():
                w.navigate_to_element(screen_x, screen_y, label, bubble_text)
                return

    def pulse_at(self, screen_x: int, screen_y: int, duration_ms: int):
        for w in self._windows:
            geo = w.screen_geometry
            if geo.x() <= screen_x < geo.x() + geo.width() and \
               geo.y() <= screen_y < geo.y() + geo.height():
                w.pulse_at(screen_x, screen_y, duration_ms)
                return

    def point_and_label(self, screen_x: int, screen_y: int, label_text: str):
        for w in self._windows:
            geo = w.screen_geometry
            if geo.x() <= screen_x < geo.x() + geo.width() and \
               geo.y() <= screen_y < geo.y() + geo.height():
                w.point_and_label(screen_x, screen_y, label_text)
                return

    def cancel_navigation(self):
        for w in self._windows:
            w.cancel_navigation()

    @property
    def windows(self):
        return self._windows
