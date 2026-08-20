"""
system_tray.py — System Tray Integration

QSystemTrayIcon with custom icon, left-click panel toggle,
and right-click context menu. No taskbar button.

Windows equivalent of MenuBarPanelManager.swift.
"""

import logging

from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import QIcon, QPixmap, QPainter, QBrush, QColor, QAction
from PySide6.QtCore import Qt, Signal

from design_system import Colors
import config

logger = logging.getLogger(__name__)


def create_tray_icon_pixmap(size: int = 64) -> QPixmap:
    """Generate a simple blue triangle tray icon programmatically."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(Colors.cursor_blue))
    painter.setPen(Qt.PenStyle.NoPen)

    # Draw a rounded blue circle with a white triangle
    margin = 4
    painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)

    # White triangle inside
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF
    cx, cy = size / 2, size / 2
    s = size * 0.3
    import math
    h = s * math.sqrt(3) / 2
    tri = QPolygonF([
        QPointF(cx, cy - h / 1.5),
        QPointF(cx - s / 2, cy + h / 3),
        QPointF(cx + s / 2, cy + h / 3),
    ])
    painter.drawPolygon(tri)
    painter.end()

    return pixmap


class SystemTray(QSystemTrayIcon):
    """
    System tray icon with left-click panel toggle and right-click menu.
    """

    panel_toggle_requested = Signal()
    quit_requested = Signal()
    show_clicky_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Generate icon
        pixmap = create_tray_icon_pixmap()
        self.setIcon(QIcon(pixmap))
        self.setToolTip(f"{config.APP_NAME} — {config.APP_DESCRIPTION}")

        # Build context menu
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #2C2C2E;
                color: white;
                border: 1px solid #3A3A3C;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #007AFF;
            }
        """)

        open_action = QAction("Open Clicky", self)
        open_action.triggered.connect(self.panel_toggle_requested.emit)
        menu.addAction(open_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

        # Left-click toggles the panel
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.panel_toggle_requested.emit()

    def update_tooltip_state(self, state: str):
        state_text = {
            "idle": "Ready",
            "listening": "Listening...",
            "processing": "Processing...",
            "responding": "Speaking...",
        }.get(state, "Ready")
        self.setToolTip(f"{config.APP_NAME} — {state_text}")
