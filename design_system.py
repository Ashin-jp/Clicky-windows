"""
design_system.py — Windows Clicky Design System

Color palette, fonts, spacing, and shared style constants.
Mirrors the macOS DesignSystem.swift aesthetic with a dark,
premium feel tuned for Windows rendering.
"""

from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt


class Colors:
    """Curated color palette matching the macOS Clicky dark theme."""

    # ─── Primary ──────────────────────────────────────────────────────
    cursor_blue = QColor(0, 122, 255)          # #007AFF — the signature blue
    cursor_blue_glow = QColor(0, 122, 255, 100)  # Glow variant
    cursor_blue_bright = QColor(40, 150, 255)  # Brighter variant for hover

    # ─── Panel Background ─────────────────────────────────────────────
    panel_background = QColor(28, 28, 30)       # #1C1C1E — deep dark
    panel_background_secondary = QColor(44, 44, 46)  # #2C2C2E — slightly lighter
    panel_surface = QColor(58, 58, 60)          # #3A3A3C — card/section background
    panel_border = QColor(68, 68, 70, 180)      # Subtle border

    # ─── Text ─────────────────────────────────────────────────────────
    text_primary = QColor(255, 255, 255)        # Pure white
    text_secondary = QColor(174, 174, 178)      # #AEAEB2 — muted
    text_tertiary = QColor(99, 99, 102)         # #636366 — very muted
    text_accent = QColor(0, 122, 255)           # Blue for links/accent text

    # ─── Status ───────────────────────────────────────────────────────
    status_active = QColor(52, 199, 89)         # #34C759 — green
    status_listening = QColor(0, 122, 255)      # Blue — recording
    status_processing = QColor(255, 159, 10)    # #FF9F0A — orange
    status_error = QColor(255, 69, 58)          # #FF453A — red

    # ─── Overlay ──────────────────────────────────────────────────────
    overlay_background = QColor(0, 0, 0, 1)     # Nearly invisible (for compositing)
    bubble_background = QColor(0, 122, 255)     # Blue bubble
    bubble_text = QColor(255, 255, 255)         # White text in bubbles

    # ─── Misc ─────────────────────────────────────────────────────────
    separator = QColor(68, 68, 70, 100)
    shadow = QColor(0, 0, 0, 80)


class Fonts:
    """Typography constants. Uses Segoe UI (Windows system) with Inter as web fallback."""

    @staticmethod
    def primary(size: int = 13, bold: bool = False) -> QFont:
        font = QFont("Segoe UI", size)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    @staticmethod
    def heading(size: int = 16) -> QFont:
        font = QFont("Segoe UI", size)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    @staticmethod
    def small(size: int = 11) -> QFont:
        font = QFont("Segoe UI", size)
        font.setWeight(QFont.Weight.Normal)
        return font

    @staticmethod
    def mono(size: int = 12) -> QFont:
        font = QFont("Cascadia Code", size)
        font.setWeight(QFont.Weight.Normal)
        return font

    @staticmethod
    def bubble(size: int = 11) -> QFont:
        font = QFont("Segoe UI", size)
        font.setWeight(QFont.Weight.Medium)
        return font


class Spacing:
    """Consistent spacing and sizing tokens."""

    # Padding
    xs = 4
    sm = 8
    md = 12
    lg = 16
    xl = 24

    # Corner radius
    radius_sm = 6
    radius_md = 10
    radius_lg = 12
    radius_xl = 16

    # Panel dimensions
    panel_width = 300
    panel_min_height = 200
    panel_max_height = 500
    panel_margin_from_tray = 8


class Stylesheets:
    """Pre-built Qt stylesheets for common widget patterns."""

    PANEL = """
        QWidget#floatingPanel {
            background-color: #1C1C1E;
            border: 1px solid rgba(68, 68, 70, 180);
            border-radius: 12px;
        }
    """

    BUTTON_PRIMARY = """
        QPushButton {
            background-color: #007AFF;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: #2896FF;
        }
        QPushButton:pressed {
            background-color: #0056CC;
        }
    """

    BUTTON_SECONDARY = """
        QPushButton {
            background-color: #2C2C2E;
            color: #AEAEB2;
            border: 1px solid rgba(68, 68, 70, 180);
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 13px;
        }
        QPushButton:hover {
            background-color: #3A3A3C;
            color: white;
        }
        QPushButton:pressed {
            background-color: #1C1C1E;
        }
    """

    LABEL_PRIMARY = """
        QLabel {
            color: white;
            font-size: 13px;
        }
    """

    LABEL_SECONDARY = """
        QLabel {
            color: #AEAEB2;
            font-size: 11px;
        }
    """

    COMBOBOX = """
        QComboBox {
            background-color: #2C2C2E;
            color: white;
            border: 1px solid rgba(68, 68, 70, 180);
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 13px;
            min-width: 120px;
        }
        QComboBox:hover {
            border-color: #007AFF;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox::down-arrow {
            image: none;
            border: none;
        }
        QComboBox QAbstractItemView {
            background-color: #2C2C2E;
            color: white;
            border: 1px solid rgba(68, 68, 70, 180);
            selection-background-color: #007AFF;
            outline: none;
        }
    """
