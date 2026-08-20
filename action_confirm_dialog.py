"""
action_confirm_dialog.py — Trust-Level-Aware Action Confirmation

Shows different confirmation UIs based on trust level:
  SILENT:         No dialog, auto-approved
  CONFIRM_ONCE:   Blue dialog, 15s timeout → auto-deny
  ALWAYS_CONFIRM: Orange/Red warning dialog, no auto-approve
  BLOCKED:        Red refusal dialog, action refused

Also supports batch confirmation for multiple pending actions.
"""

import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QApplication, QScrollArea, QWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from design_system import Colors, Fonts
from trust_engine import TrustLevel

logger = logging.getLogger(__name__)


class ActionConfirmDialog(QDialog):
    """
    Trust-level-aware confirmation popup.
    Returns QDialog.Accepted if user clicks Allow, Rejected if Deny.
    """

    def __init__(
        self,
        action_display_text: str,
        trust_level: TrustLevel = TrustLevel.ALWAYS_CONFIRM,
        parent=None,
    ):
        super().__init__(parent)
        self._trust_level = trust_level

        # Configure timeout based on trust level
        if trust_level == TrustLevel.CONFIRM_ONCE:
            self._timeout = 15
            self._auto_action = "deny"
        elif trust_level == TrustLevel.ALWAYS_CONFIRM:
            self._timeout = 30
            self._auto_action = "deny"
        else:
            self._timeout = 15
            self._auto_action = "deny"

        self.setWindowTitle("Clicky wants to act")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(400)

        self._remaining = self._timeout
        self._build_ui(action_display_text, trust_level)

        # Countdown timer
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._tick)
        self._countdown_timer.start(1000)

    def _build_ui(self, action_text: str, trust: TrustLevel):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QLabel()
        container.setObjectName("confirmContainer")

        # Colors based on trust level
        if trust == TrustLevel.ALWAYS_CONFIRM:
            border_color = "#FF9F0A"  # Orange warning
            accent_color = "#FF9F0A"
            header_text = "⚠️ Clicky wants to perform a sensitive action"
        elif trust == TrustLevel.CONFIRM_ONCE:
            border_color = "#007AFF"  # Blue
            accent_color = "#007AFF"
            header_text = "Clicky wants to perform an action"
        else:
            border_color = "#3A3A3C"
            accent_color = "#007AFF"
            header_text = "Clicky wants to perform an action"

        container.setStyleSheet(f"""
            QLabel#confirmContainer {{
                background-color: #1C1C1E;
                border: 1px solid {border_color};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header
        header = QLabel(header_text)
        header.setFont(Fonts.heading(13))
        header.setStyleSheet("color: white; background: transparent; border: none;")
        layout.addWidget(header)

        # Trust level indicator
        trust_colors = {
            TrustLevel.CONFIRM_ONCE: ("#007AFF", "Session Approval"),
            TrustLevel.ALWAYS_CONFIRM: ("#FF9F0A", "Requires Approval Every Time"),
        }
        if trust in trust_colors:
            color, label = trust_colors[trust]
            trust_indicator = QLabel(f"● {label}")
            trust_indicator.setFont(Fonts.small(9))
            trust_indicator.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )
            layout.addWidget(trust_indicator)

        # Action description
        desc = QLabel(action_text)
        desc.setFont(Fonts.primary(12))
        desc.setStyleSheet(f"""
            color: #AEAEB2;
            background-color: #2C2C2E;
            border: 1px solid {border_color};
            border-radius: 8px;
            padding: 10px;
        """)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Session note for CONFIRM_ONCE
        if trust == TrustLevel.CONFIRM_ONCE:
            note = QLabel("Allowing will remember this for the current session")
            note.setFont(Fonts.small(9))
            note.setStyleSheet(
                "color: #636366; background: transparent; border: none; font-style: italic;"
            )
            layout.addWidget(note)

        # Countdown label
        self._countdown_label = QLabel(f"Auto-denying in {self._remaining}s")
        self._countdown_label.setFont(Fonts.small(10))
        self._countdown_label.setStyleSheet(
            "color: #636366; background: transparent; border: none;"
        )
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._countdown_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        deny_btn = QPushButton("Deny")
        deny_btn.setFixedHeight(36)
        deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        deny_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background-color: #3A3A3C;
                border: 1px solid #636366;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 20px;
            }
            QPushButton:hover {
                background-color: #48484A;
            }
        """)
        deny_btn.clicked.connect(self.reject)
        btn_layout.addWidget(deny_btn)

        allow_btn = QPushButton("Allow")
        allow_btn.setFixedHeight(36)
        allow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        allow_btn.setStyleSheet(f"""
            QPushButton {{
                color: white;
                background-color: {accent_color};
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                background-color: {accent_color};
                opacity: 0.9;
            }}
        """)
        allow_btn.clicked.connect(self.accept)
        allow_btn.setDefault(True)
        btn_layout.addWidget(allow_btn)

        layout.addLayout(btn_layout)
        outer.addWidget(container)

    def _tick(self):
        self._remaining -= 1
        self._countdown_label.setText(f"Auto-denying in {self._remaining}s")
        if self._remaining <= 0:
            self._countdown_timer.stop()
            self.reject()

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)


class BlockedActionDialog(QDialog):
    """
    Dialog shown when an action is BLOCKED.
    Shows a red warning with explanation. No Allow button.
    """

    def __init__(self, action_text: str, reason: str, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Action Blocked")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(400)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QLabel()
        container.setObjectName("blockedContainer")
        container.setStyleSheet("""
            QLabel#blockedContainer {
                background-color: #1C1C1E;
                border: 1px solid #FF453A;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header
        header = QLabel("🚫 Action Blocked")
        header.setFont(Fonts.heading(14))
        header.setStyleSheet("color: #FF453A; background: transparent; border: none;")
        layout.addWidget(header)

        # Action that was attempted
        desc = QLabel(action_text)
        desc.setFont(Fonts.primary(12))
        desc.setStyleSheet("""
            color: #AEAEB2;
            background-color: #2C2C2E;
            border: 1px solid #FF453A;
            border-radius: 8px;
            padding: 10px;
        """)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Reason
        reason_label = QLabel(reason)
        reason_label.setFont(Fonts.small(11))
        reason_label.setStyleSheet("color: #FF9F0A; background: transparent; border: none;")
        reason_label.setWordWrap(True)
        layout.addWidget(reason_label)

        # OK button
        ok_btn = QPushButton("Understood")
        ok_btn.setFixedHeight(36)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background-color: #3A3A3C;
                border: 1px solid #636366;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 30px;
            }
            QPushButton:hover {
                background-color: #48484A;
            }
        """)
        ok_btn.clicked.connect(self.accept)
        layout.addWidget(ok_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(container)

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)


def confirm_action(
    action_display_text: str,
    trust_level: TrustLevel = TrustLevel.ALWAYS_CONFIRM,
) -> bool:
    """
    Show confirmation dialog based on trust level.

    Returns True if user clicks Allow (or trust is SILENT).
    Returns False if denied, blocked, or timed out.
    """
    if trust_level == TrustLevel.SILENT:
        return True

    if trust_level == TrustLevel.BLOCKED:
        return False

    dialog = ActionConfirmDialog(action_display_text, trust_level)
    result = dialog.exec()
    return result == QDialog.DialogCode.Accepted


def show_blocked_action(action_text: str, reason: str):
    """Show a blocked action notification."""
    dialog = BlockedActionDialog(action_text, reason)
    dialog.exec()
