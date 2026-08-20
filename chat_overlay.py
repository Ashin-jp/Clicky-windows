"""
chat_overlay.py — Silent Mode Chat Bubble

A sleek, dark chat widget that appears anchored to the blue cursor arrow.
When the user can't speak or hear, they can type queries and read responses
directly through this floating textbox — no voice required.

Features:
  · Chat-bubble message history (user right-aligned, AI left-aligned)
  · Animated thinking dots during processing
  · Smooth fade-in animation on open
  · Stop button and Escape cancel during processing
  · 60-second safety timer auto-recovers stuck states
"""

import ctypes
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QApplication, QPushButton,
    QGraphicsDropShadowEffect, QSizePolicy, QScrollArea,
    QFrame, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QPoint, QPropertyAnimation,
    QEasingCurve, Property, QRect, QSize,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QBrush, QPen,
    QFont, QFontMetrics, QLinearGradient, QKeyEvent,
)

from design_system import Colors, Fonts, Spacing

logger = logging.getLogger(__name__)


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


# ═══════════════════════════════════════════════════════════════════════
#  Chat Bubble Widget — individual message in the conversation
# ═══════════════════════════════════════════════════════════════════════

class ChatBubble(QFrame):
    """A single chat message bubble — user or AI styled."""

    def __init__(self, text: str, is_user: bool = False, parent=None):
        super().__init__(parent)
        ds = _dpi_scale()
        self._is_user = is_user

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        bubble.setFont(Fonts.primary(12))
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        max_w = int(280 * ds)
        bubble.setMaximumWidth(max_w)

        pad_v = int(10 * ds)
        pad_h = int(14 * ds)

        if is_user:
            bubble.setStyleSheet(f"""
                QLabel {{
                    color: #FFFFFF;
                    background-color: #007AFF;
                    border-radius: {int(14 * ds)}px;
                    padding: {pad_v}px {pad_h}px;
                }}
            """)
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            bubble.setStyleSheet(f"""
                QLabel {{
                    color: #E5E5EA;
                    background-color: #2C2C2E;
                    border: 1px solid #3A3A3C;
                    border-radius: {int(14 * ds)}px;
                    padding: {pad_v}px {pad_h}px;
                }}
            """)
            layout.addWidget(bubble)
            layout.addStretch()

        self._bubble_label = bubble

    def set_text(self, text: str):
        """Update the bubble text (used for typewriter effect)."""
        self._bubble_label.setText(text)


# ═══════════════════════════════════════════════════════════════════════
#  Thinking Dots — animated "●  ●  ●" indicator
# ═══════════════════════════════════════════════════════════════════════

class ThinkingDots(QWidget):
    """Animated three-dot thinking indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        ds = _dpi_scale()
        self._dot_index = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(int(14 * ds), int(8 * ds), int(14 * ds), int(8 * ds))
        layout.setSpacing(0)

        # Container with same style as AI bubble
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: #2C2C2E;
                border: 1px solid #3A3A3C;
                border-radius: {int(14 * ds)}px;
                padding: {int(8 * ds)}px {int(14 * ds)}px;
            }}
        """)
        dot_layout = QHBoxLayout(container)
        dot_layout.setContentsMargins(int(6 * ds), int(4 * ds), int(6 * ds), int(4 * ds))
        dot_layout.setSpacing(int(6 * ds))

        self._dots = []
        for _ in range(3):
            dot = QLabel("●")
            dot.setFont(Fonts.primary(int(10 * ds)))
            dot.setStyleSheet("color: #48484A;")
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setFixedWidth(int(14 * ds))
            dot_layout.addWidget(dot)
            self._dots.append(dot)

        layout.addWidget(container)
        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)

    def start(self):
        self._dot_index = 0
        self._timer.start(400)
        self.setVisible(True)

    def stop(self):
        self._timer.stop()
        self.setVisible(False)

    def _animate(self):
        active_color = "#FFFFFF"
        dim_color = "#48484A"
        mid_color = "#8E8E93"
        for i, dot in enumerate(self._dots):
            if i == self._dot_index:
                dot.setStyleSheet(f"color: {active_color};")
            elif i == (self._dot_index - 1) % 3:
                dot.setStyleSheet(f"color: {mid_color};")
            else:
                dot.setStyleSheet(f"color: {dim_color};")
        self._dot_index = (self._dot_index + 1) % 3


# ═══════════════════════════════════════════════════════════════════════
#  Chat Input — text field with Enter/Escape handling
# ═══════════════════════════════════════════════════════════════════════

class ChatInput(QLineEdit):
    """Custom line edit that emits on Enter and Escape."""

    submitted = Signal(str)
    dismissed = Signal()
    cancel_requested = Signal()

    def keyPressEvent(self, arg__1: QKeyEvent):
        event = arg__1
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            text = self.text().strip()
            if text:
                self.submitted.emit(text)
                self.clear()
        elif event.key() == Qt.Key.Key_Escape:
            # Fix 3: Escape cancels operation if busy, dismisses only when idle
            parent_overlay = self.parent()
            while parent_overlay and not isinstance(parent_overlay, ChatOverlay):
                parent_overlay = parent_overlay.parent()
            if parent_overlay and parent_overlay._state in ("processing", "responding"):
                self.cancel_requested.emit()
            else:
                self.dismissed.emit()
        else:
            super().keyPressEvent(event)


# ═══════════════════════════════════════════════════════════════════════
#  Chat Overlay — main floating chat panel
# ═══════════════════════════════════════════════════════════════════════

class ChatOverlay(QWidget):
    """
    Floating chat bubble anchored near the cursor.
    Provides text input + response display for silent mode.

    Signals:
        message_submitted: User typed a message and pressed Enter
        closed: User dismissed the overlay
        stop_requested: User clicked stop or pressed Esc during processing
    """

    message_submitted = Signal(str)
    closed = Signal()
    stop_requested = Signal()

    # Layout constants
    BUBBLE_WIDTH = 380
    MAX_CHAT_HEIGHT = 340
    ARROW_SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        ds = _dpi_scale()
        self.BUBBLE_WIDTH = int(380 * ds)
        self.MAX_CHAT_HEIGHT = int(340 * ds)
        self.ARROW_SIZE = int(12 * ds)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(self.BUBBLE_WIDTH + int(20 * ds))

        self._state = "idle"  # idle, processing, responding
        self._anchor_pos = QPoint(0, 0)
        self._response_text = ""
        self._is_typing_response = False
        self._typed_index = 0
        self._drag_pos = None
        self._active_ai_bubble = None  # Reference to the AI bubble being typed into
        self._messages = []  # List of (text, is_user) for history

        self._build_ui()

    def _build_ui(self):
        ds = _dpi_scale()
        layout = QVBoxLayout(self)
        m = int(10 * ds)
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(0)

        # ─── Container ────────────────────────────────────────────────
        self._container = QWidget()
        self._container.setObjectName("chatContainer")
        self._container.setStyleSheet(f"""
            QWidget#chatContainer {{
                background-color: #1C1C1E;
                border: 1px solid rgba(58, 58, 60, 200);
                border-radius: {int(16 * ds)}px;
            }}
        """)

        container_layout = QVBoxLayout(self._container)
        cm = int(14 * ds)
        ct = int(12 * ds)
        container_layout.setContentsMargins(cm, ct, cm, ct)
        container_layout.setSpacing(int(8 * ds))

        # ─── Header Row ──────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(int(8 * ds))

        # Animated status dot
        self._status_dot = QLabel("●")
        self._status_dot.setFont(Fonts.small(max(9, int(9 * ds))))
        self._status_dot.setStyleSheet("color: #34C759;")
        self._status_dot.setFixedWidth(int(14 * ds))
        header.addWidget(self._status_dot)

        # Title with status text
        self._title_label = QLabel("Clicky — Ready")
        self._title_label.setFont(Fonts.primary(11, bold=True))
        self._title_label.setStyleSheet("color: #AEAEB2;")
        header.addWidget(self._title_label)

        header.addStretch()

        # Clear chat button
        clear_btn = QPushButton("⟲")
        clear_btn.setFixedSize(int(22 * ds), int(22 * ds))
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setToolTip("Clear conversation")
        clear_btn.setStyleSheet("""
            QPushButton {
                color: #636366;
                background: transparent;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: #AEAEB2; }
        """)
        clear_btn.clicked.connect(self._clear_chat)
        header.addWidget(clear_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(int(22 * ds), int(22 * ds))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                color: #636366;
                background: transparent;
                border: none;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { color: #FF453A; }
        """)
        close_btn.clicked.connect(self._dismiss)
        header.addWidget(close_btn)

        container_layout.addLayout(header)

        # ─── Separator ────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2C2C2E; background-color: #2C2C2E; max-height: 1px;")
        container_layout.addWidget(sep)

        # ─── Chat Scroll Area ────────────────────────────────────────
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setMaximumHeight(self.MAX_CHAT_HEIGHT)
        self._scroll_area.setMinimumHeight(int(60 * ds))
        self._scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: {int(5 * ds)}px;
                margin: {int(4 * ds)}px {int(1 * ds)}px;
            }}
            QScrollBar::handle:vertical {{
                background: #48484A;
                border-radius: {int(2 * ds)}px;
                min-height: {int(20 * ds)}px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        # Chat content widget inside scroll area
        self._chat_content = QWidget()
        self._chat_content.setStyleSheet("background: transparent;")
        self._chat_layout = QVBoxLayout(self._chat_content)
        self._chat_layout.setContentsMargins(0, int(4 * ds), 0, int(4 * ds))
        self._chat_layout.setSpacing(int(8 * ds))
        self._chat_layout.addStretch()  # Push messages to bottom

        self._scroll_area.setWidget(self._chat_content)
        self._scroll_area.setVisible(False)  # Hidden until first message
        container_layout.addWidget(self._scroll_area)

        # ─── Thinking Dots ────────────────────────────────────────────
        self._thinking_dots = ThinkingDots()
        self._thinking_dots.setVisible(False)
        container_layout.addWidget(self._thinking_dots)

        # ─── Welcome message (shown when no messages) ─────────────────
        self._welcome = QLabel("Ask me anything — I'm listening 👂")
        self._welcome.setFont(Fonts.primary(12))
        self._welcome.setStyleSheet("color: #636366;")
        self._welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._welcome.setContentsMargins(0, int(16 * ds), 0, int(16 * ds))
        container_layout.addWidget(self._welcome)

        # ─── Input Row ────────────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setSpacing(int(6 * ds))

        self._input = ChatInput()
        self._input.setFont(Fonts.primary(12))
        self._input.setPlaceholderText("Type a message...")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                color: white;
                background-color: #2C2C2E;
                border: 1px solid #3A3A3C;
                border-radius: {int(12 * ds)}px;
                padding: {int(10 * ds)}px {int(14 * ds)}px;
                selection-background-color: #007AFF;
            }}
            QLineEdit:focus {{
                border: 1px solid #007AFF;
            }}
            QLineEdit::placeholder {{
                color: #48484A;
            }}
        """)
        self._input.submitted.connect(self._on_submit)
        self._input.dismissed.connect(self._dismiss)
        self._input.cancel_requested.connect(self._on_stop_clicked)
        input_row.addWidget(self._input)

        # Fix 2: Stop button (visible only during processing/responding)
        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedSize(int(38 * ds), int(38 * ds))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setToolTip("Stop (Esc)")
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                color: white;
                background-color: #FF453A;
                border: none;
                border-radius: {int(12 * ds)}px;
                font-size: {int(12 * ds)}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #FF6961;
            }}
            QPushButton:pressed {{
                background-color: #CC362E;
            }}
        """)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setVisible(False)
        input_row.addWidget(self._stop_btn)

        # Send button with gradient feel
        self._send_btn = QPushButton("↑")
        self._send_btn.setFixedSize(int(38 * ds), int(38 * ds))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("Send (Enter)")
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                color: white;
                background-color: #007AFF;
                border: none;
                border-radius: {int(12 * ds)}px;
                font-size: {int(16 * ds)}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0A84FF;
            }}
            QPushButton:pressed {{
                background-color: #0056CC;
            }}
        """)
        self._send_btn.clicked.connect(lambda: self._on_submit(self._input.text()))
        input_row.addWidget(self._send_btn)

        container_layout.addLayout(input_row)

        # ─── Shortcut hint (Fix 3: dynamic text) ─────────────────────
        self._hint_label = QLabel("Enter to send · Esc to close")
        self._hint_label.setFont(Fonts.small(max(8, int(8 * ds))))
        self._hint_label.setStyleSheet("color: #3A3A3C;")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setContentsMargins(0, int(2 * ds), 0, 0)
        container_layout.addWidget(self._hint_label)

        layout.addWidget(self._container)

        # ─── Drop shadow ─────────────────────────────────────────────
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(int(30 * ds))
        shadow.setColor(QColor(0, 0, 0, 140))
        shadow.setOffset(0, int(6 * ds))
        self._container.setGraphicsEffect(shadow)

        # ─── Typewriter timer ─────────────────────────────────────────
        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_next_char)

        # ─── Fix 5: Safety timer — auto-recover from stuck PROCESSING ─
        self._safety_timer = QTimer(self)
        self._safety_timer.setSingleShot(True)
        self._safety_timer.timeout.connect(self._safety_timeout)

        # ─── Pulsing dot timer for processing state ───────────────────
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_dot)
        self._pulse_phase = 0

    # ─── Public API ───────────────────────────────────────────────────

    def show_at_cursor(self):
        """Show the chat overlay anchored near the system cursor."""
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))

        screen = QApplication.screenAt(QPoint(pt.x, pt.y))
        if not screen:
            screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()

        self.adjustSize()

        ds = _dpi_scale()

        # Position: try to show below-right of cursor
        x = pt.x + int(30 * ds)
        y = pt.y + int(20 * ds)

        # Keep within screen bounds with extra margin
        margin = int(10 * ds)
        if x + self.width() > geo.right() - margin:
            x = pt.x - self.width() - margin
        if y + self.height() > geo.bottom() - margin:
            y = pt.y - self.height() - margin

        x = max(geo.x() + margin, min(x, geo.right() - self.width() - margin))
        y = max(geo.y() + margin, min(y, geo.bottom() - self.height() - margin))

        self._anchor_pos = QPoint(pt.x, pt.y)
        self.move(x, y)

        # Reset state but keep conversation history
        self._set_status("idle")
        self._input.clear()

        # Show/hide welcome vs scroll area based on message history
        has_messages = len(self._messages) > 0
        self._welcome.setVisible(not has_messages)
        self._scroll_area.setVisible(has_messages)

        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

        # Smooth fade-in
        self._fade_in()

    def show_response(self, text: str, typewriter: bool = True):
        """Display an AI response with optional typewriter effect."""
        self._thinking_dots.stop()

        # Add AI bubble to chat
        bubble = self._add_bubble(text if not typewriter else "", is_user=False)

        self._set_status("responding")
        self.adjustSize()

        if typewriter and len(text) < 2000:
            self._response_text = text
            self._typed_index = 0
            self._is_typing_response = True
            self._active_ai_bubble = bubble
            self._type_timer.start(16)  # ~62 chars/sec — slightly faster, smoother
        else:
            bubble.set_text(text)
            self._messages.append((text, False))
            self._set_status("idle")
            self._input.setEnabled(True)
            self._input.setFocus()
            self._scroll_to_bottom()

    def show_processing(self):
        """Show animated thinking indicator."""
        self._welcome.setVisible(False)
        self._scroll_area.setVisible(True)
        self._thinking_dots.start()
        self._set_status("processing")
        self._input.setEnabled(False)
        self.adjustSize()
        self._scroll_to_bottom()
        # Fix 5: Start 60s safety timer
        self._safety_timer.start(60000)

    def is_open(self) -> bool:
        return self.isVisible()

    # ─── Internal ─────────────────────────────────────────────────────

    def _add_bubble(self, text: str, is_user: bool) -> ChatBubble:
        """Add a chat bubble to the conversation area."""
        self._welcome.setVisible(False)
        self._scroll_area.setVisible(True)

        bubble = ChatBubble(text, is_user=is_user, parent=self._chat_content)

        # Insert before the stretch at the end
        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, bubble)

        if text:  # Don't track empty bubbles (they'll be filled by typewriter)
            self._messages.append((text, is_user))

        self.adjustSize()
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        """Scroll chat to the latest message."""
        QTimer.singleShot(50, lambda: (
            self._scroll_area.verticalScrollBar().setValue(
                self._scroll_area.verticalScrollBar().maximum()
            )
        ))

    def _clear_chat(self):
        """Clear all chat messages."""
        # Remove all bubble widgets
        while self._chat_layout.count() > 1:  # Keep the stretch
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._messages.clear()
        self._active_ai_bubble = None
        self._welcome.setVisible(True)
        self._scroll_area.setVisible(False)
        self.adjustSize()

    def _fade_in(self):
        """Smooth fade-in animation when showing the overlay."""
        try:
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(200)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: self.setGraphicsEffect(None))
            anim.start()
            self._fade_anim = anim  # prevent GC
        except Exception:
            pass  # Graceful fallback — just show without animation

    def _on_submit(self, text: str):
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return
        self._input.clear()
        self._input.setEnabled(False)

        # Add user bubble
        self._add_bubble(text, is_user=True)

        # Start processing state
        self.show_processing()
        self.message_submitted.emit(text)

    def _dismiss(self):
        self._type_timer.stop()
        self._thinking_dots.stop()
        self._pulse_timer.stop()
        self._is_typing_response = False
        self.hide()
        self.closed.emit()

    def _set_status(self, state: str):
        self._state = state
        colors = {
            "idle": "#34C759",
            "processing": "#FF9F0A",
            "responding": "#007AFF",
        }
        titles = {
            "idle": "Clicky — Ready",
            "processing": "Clicky — Thinking...",
            "responding": "Clicky — Responding",
        }
        self._status_dot.setStyleSheet(f"color: {colors.get(state, '#34C759')};")
        self._title_label.setText(titles.get(state, "Clicky — Ready"))

        # Fix 2: Show/hide stop button based on state
        is_busy = state in ("processing", "responding")
        self._stop_btn.setVisible(is_busy)
        self._send_btn.setVisible(not is_busy)

        # Fix 3: Update hint text dynamically
        if is_busy:
            self._hint_label.setText("Esc to cancel")
            self._hint_label.setStyleSheet("color: #FF9F0A;")
        else:
            self._hint_label.setText("Enter to send · Esc to close")
            self._hint_label.setStyleSheet("color: #3A3A3C;")

        # Pulse animation for processing dot
        if state == "processing":
            self._pulse_timer.start(600)
        else:
            self._pulse_timer.stop()
            self._status_dot.setStyleSheet(f"color: {colors.get(state, '#34C759')};")

        # Fix 5: Stop safety timer when idle
        if state == "idle":
            self._safety_timer.stop()
            self._input.setEnabled(True)
            self._input.setFocus()

    def _pulse_dot(self):
        """Pulsing animation for the status dot during processing."""
        self._pulse_phase = (self._pulse_phase + 1) % 2
        if self._pulse_phase == 0:
            self._status_dot.setStyleSheet("color: #FF9F0A;")
        else:
            self._status_dot.setStyleSheet("color: #FFD60A;")

    def _type_next_char(self):
        """Typewriter effect: reveal characters progressively."""
        if self._typed_index >= len(self._response_text):
            self._type_timer.stop()
            self._is_typing_response = False
            # Track the completed message
            self._messages.append((self._response_text, False))
            self._active_ai_bubble = None
            self._set_status("idle")
            self._input.setEnabled(True)
            self._input.setFocus()
            return

        # Type 2 chars at a time for smoother feel
        step = 2
        self._typed_index = min(self._typed_index + step, len(self._response_text))
        visible = self._response_text[:self._typed_index]

        if self._active_ai_bubble:
            self._active_ai_bubble.set_text(visible)

        self._scroll_to_bottom()

    def paintEvent(self, event):
        """Paint the connector arrow from cursor to bubble."""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        ds = _dpi_scale()
        container_rect = self._container.geometry()
        tip_x = container_rect.left()
        tip_y = container_rect.top() + int(8 * ds)

        path = QPainterPath()
        path.moveTo(tip_x - int(7 * ds), tip_y)
        path.lineTo(tip_x + int(2 * ds), tip_y - int(7 * ds))
        path.lineTo(tip_x + int(2 * ds), tip_y + int(7 * ds))
        path.closeSubpath()

        painter.setBrush(QBrush(Colors.cursor_blue))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        painter.end()

    def _on_stop_clicked(self):
        """Fix 2: Handle stop button click or Escape during processing."""
        logger.info("ChatOverlay: stop requested by user")
        self.stop_requested.emit()
        self._thinking_dots.stop()
        self._type_timer.stop()
        self._is_typing_response = False
        self._active_ai_bubble = None
        self._set_status("idle")
        self._input.setEnabled(True)
        self._input.setFocus()

    def _safety_timeout(self):
        """Fix 5: Auto-recover from stuck PROCESSING state after 60 seconds."""
        if self._state in ("processing", "responding"):
            logger.warning("ChatOverlay: safety timeout fired — auto-recovering from stuck state")
            self._thinking_dots.stop()
            self._type_timer.stop()
            self._is_typing_response = False
            self._active_ai_bubble = None
            # Add error message as AI bubble
            self._add_bubble("Something went wrong. Please try again.", is_user=False)
            self._set_status("idle")
            self.stop_requested.emit()

    def focusOutEvent(self, event):
        """Auto-dismiss when focus leaves (unless processing)."""
        super().focusOutEvent(event)
        if self._state == "idle" and not self._is_typing_response:
            QTimer.singleShot(300, lambda: self._dismiss() if not self.isActiveWindow() and self._state == "idle" else None)

    # ─── Dragging Support ─────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

