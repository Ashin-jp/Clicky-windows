"""
conftest.py — Shared pytest configuration.

Adds the project root to sys.path and mocks heavy dependencies
so tests can import project modules without launching the full app.
"""
import sys
import os
from unittest.mock import MagicMock

# ── Add project root to path ──────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Mock heavy native dependencies before any project imports ─────────
# These modules require Windows APIs, COM, or hardware access.
# We mock them at the sys.modules level so imports succeed in test.

_MOCK_MODULES = [
    "PySide6", "PySide6.QtCore", "PySide6.QtWidgets", "PySide6.QtGui",
    "PySide6.QtMultimedia",
    "comtypes", "comtypes.client", "comtypes.gen",
    "pyautogui", "pyperclip",
    "win32gui", "win32process", "win32con", "win32api",
    "pycaw", "pycaw.pycaw",
    "mss",
    "sounddevice",
    "keyboard",
    "elevenlabs",
    "playwright", "playwright.async_api",
]

for mod_name in _MOCK_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock specific PySide6 attributes that are used as base classes or decorators
_qt_core = sys.modules["PySide6.QtCore"]
_qt_core.QObject = type("QObject", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
_qt_core.Signal = lambda *a, **kw: MagicMock()
_qt_core.QTimer = MagicMock()
_qt_core.QSettings = MagicMock()
_qt_core.Qt = MagicMock()

_qt_widgets = sys.modules["PySide6.QtWidgets"]
_qt_widgets.QApplication = MagicMock()

_qt_gui = sys.modules["PySide6.QtGui"]
_qt_gui.QGuiApplication = MagicMock()
