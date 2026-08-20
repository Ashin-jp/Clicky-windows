"""
uia_helper.py — Windows UI Automation interface via comtypes.

Provides safe, timeout-protected access to the Windows UIA tree
for context extraction, semantic element targeting, and text insertion.
All functions return None on failure with errors logged.
"""

import logging
import threading
import ctypes
import ctypes.wintypes
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── COM + UIA constants ─────────────────────────────────────────────
_uia_client = None
_uia_lock = threading.Lock()
_initialized = False

# UIA Control Type IDs
UIA_BUTTON = 50000
UIA_CALENDAR = 50001
UIA_CHECKBOX = 50002
UIA_COMBOBOX = 50003
UIA_EDIT = 50004
UIA_HYPERLINK = 50005
UIA_IMAGE = 50006
UIA_LISTITEM = 50007
UIA_LIST = 50008
UIA_MENU = 50009
UIA_MENUBAR = 50010
UIA_MENUITEM = 50011
UIA_PANE = 50033
UIA_PROGRESSBAR = 50012
UIA_RADIOBUTTON = 50013
UIA_SCROLLBAR = 50014
UIA_SLIDER = 50015
UIA_SPINNER = 50016
UIA_STATUSBAR = 50017
UIA_TAB = 50018
UIA_TABITEM = 50019
UIA_TEXT = 50020
UIA_TOOLBAR = 50021
UIA_TOOLTIP = 50025
UIA_TREE = 50023
UIA_TREEITEM = 50024
UIA_WINDOW = 50032
UIA_DOCUMENT = 50030
UIA_GROUP = 50026
UIA_TITLEBAR = 50037

CONTROL_TYPE_NAMES = {
    UIA_BUTTON: "Button", UIA_EDIT: "Edit", UIA_TEXT: "Text",
    UIA_CHECKBOX: "CheckBox", UIA_COMBOBOX: "ComboBox",
    UIA_HYPERLINK: "Hyperlink", UIA_IMAGE: "Image",
    UIA_LISTITEM: "ListItem", UIA_LIST: "List",
    UIA_MENU: "Menu", UIA_MENUBAR: "MenuBar", UIA_MENUITEM: "MenuItem",
    UIA_PANE: "Pane", UIA_PROGRESSBAR: "ProgressBar",
    UIA_SCROLLBAR: "ScrollBar", UIA_SLIDER: "Slider",
    UIA_STATUSBAR: "StatusBar", UIA_TAB: "Tab", UIA_TABITEM: "TabItem",
    UIA_TOOLBAR: "ToolBar", UIA_TOOLTIP: "ToolTip",
    UIA_TREE: "Tree", UIA_TREEITEM: "TreeItem",
    UIA_WINDOW: "Window", UIA_DOCUMENT: "Document",
    UIA_GROUP: "Group", UIA_TITLEBAR: "TitleBar",
}

# Filter these from tree output (noisy, not useful for context)
FILTERED_TYPES = {UIA_TOOLBAR, UIA_STATUSBAR, UIA_MENUBAR, UIA_SCROLLBAR, UIA_TITLEBAR}


@dataclass
class ElementInfo:
    """Information about a UIA element."""
    name: str = ""
    control_type: str = ""
    control_type_id: int = 0
    automation_id: str = ""
    value: str = ""
    bounding_rect: tuple = (0, 0, 0, 0)
    is_enabled: bool = True
    class_name: str = ""


def _initialize():
    """Initialize the UIA client (thread-safe)."""
    global _uia_client, _initialized
    if _initialized:
        return
    with _uia_lock:
        if _initialized:
            return
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
            _uia_client = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",  # CUIAutomation
                interface=None
            )
            _initialized = True
            logger.info("UIA: COM client initialized")
        except Exception as e:
            logger.error(f"UIA: failed to initialize COM: {e}")


def _get_uia():
    """Get the UIA client."""
    return _uia_client


def _run_with_timeout(func, timeout_ms=100):
    """Run a function with a timeout. Returns None on timeout or error."""
    result = [None]
    error = [None]

    def wrapper():
        try:
            result[0] = func()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000.0)

    if t.is_alive():
        logger.warning("UIA: operation timed out")
        return None
    if error[0]:
        logger.debug(f"UIA: operation failed: {error[0]}")
        return None
    return result[0]


def get_foreground_app_name() -> Optional[str]:
    """Get the exe name of the foreground window."""
    if not _initialized:
        _initialize()
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            return proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    except Exception as e:
        logger.debug(f"UIA: get_foreground_app_name failed: {e}")
        return None


def get_window_title() -> Optional[str]:
    """Get the title bar text of the foreground window."""
    if not _initialized:
        _initialize()
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd)
        return title if title else None
    except Exception as e:
        logger.debug(f"UIA: get_window_title failed: {e}")
        return None


def get_focused_element_info() -> Optional[ElementInfo]:
    """Get info about the currently focused element."""
    if not _initialized:
        _initialize()
    def _inner():
        uia = _get_uia()
        if not uia:
            return None

        focused = uia.GetFocusedElement()
        if not focused:
            return None

        try:
            ct_id = focused.CurrentControlType
            return ElementInfo(
                name=focused.CurrentName or "",
                control_type=CONTROL_TYPE_NAMES.get(ct_id, f"Unknown({ct_id})"),
                control_type_id=ct_id,
                automation_id=getattr(focused, 'CurrentAutomationId', "") or "",
                class_name=getattr(focused, 'CurrentClassName', "") or "",
                is_enabled=bool(getattr(focused, 'CurrentIsEnabled', True)),
            )
        except Exception:
            return None

    return _run_with_timeout(_inner, timeout_ms=100)


def get_app_tree_as_text(max_depth: int = 4, max_chars: int = 2000) -> Optional[str]:
    """
    Get a text representation of the foreground app's UIA tree.
    Filters noisy elements and limits depth/size for AI context.
    """
    if not _initialized:
        _initialize()
    def _inner():
        uia = _get_uia()
        if not uia:
            return None

        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None

            root = uia.ElementFromHandle(hwnd)
            if not root:
                return None

            lines = []
            _walk_tree(root, lines, depth=0, max_depth=max_depth, max_chars=max_chars)
            result = "\n".join(lines)
            return result[:max_chars] if len(result) > max_chars else result
        except Exception as e:
            logger.debug(f"UIA: tree extraction failed: {e}")
            return None

    return _run_with_timeout(_inner, timeout_ms=200)


def _walk_tree(element, lines: list, depth: int, max_depth: int, max_chars: int):
    """Recursively walk UIA tree, building text representation."""
    if depth > max_depth:
        return
    if sum(len(l) for l in lines) > max_chars:
        return

    try:
        ct_id = element.CurrentControlType
        if ct_id in FILTERED_TYPES:
            return

        name = element.CurrentName or ""
        ct_name = CONTROL_TYPE_NAMES.get(ct_id, "")
        indent = "  " * depth

        if name and ct_name:
            lines.append(f"{indent}[{ct_name}] {name}")
        elif ct_name:
            lines.append(f"{indent}[{ct_name}]")

        # Walk children
        try:
            walker = element.GetCurrentPattern(10000)  # UIA_ControlPattern
        except Exception:
            pass

        try:
            import comtypes
            condition = _get_uia().CreateTrueCondition()
            children = element.FindAll(1, condition)  # TreeScope_Children = 1
            if children:
                for i in range(children.Length):
                    child = children.GetElement(i)
                    _walk_tree(child, lines, depth + 1, max_depth, max_chars)
        except Exception:
            pass
    except Exception:
        pass


def find_element_by_name(name: str, control_type: int = None) -> Optional[object]:
    """Find a UIA element by name (and optionally control type)."""
    if not _initialized:
        _initialize()
    def _inner():
        uia = _get_uia()
        if not uia:
            return None
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            root = uia.ElementFromHandle(hwnd)
            if not root:
                return None

            import comtypes
            name_cond = uia.CreatePropertyCondition(30005, name)  # UIA_NamePropertyId
            if control_type:
                type_cond = uia.CreatePropertyCondition(30003, control_type)  # UIA_ControlTypePropertyId
                cond = uia.CreateAndCondition(name_cond, type_cond)
            else:
                cond = name_cond

            element = root.FindFirst(4, cond)  # TreeScope_Descendants
            return element
        except Exception as e:
            logger.debug(f"UIA: find_element_by_name failed: {e}")
            return None

    return _run_with_timeout(_inner, timeout_ms=150)


def insert_text_to_focused(text: str) -> bool:
    """
    Insert text into the currently focused element.
    Tries UIA ValuePattern first, falls back to pyautogui.
    """
    if not _initialized:
        _initialize()
    try:
        uia = _get_uia()
        if uia:
            focused = uia.GetFocusedElement()
            if focused:
                try:
                    # Try ValuePattern (UIA_ValuePatternId = 10002)
                    pattern = focused.GetCurrentPattern(10002)
                    if pattern:
                        pattern.SetValue(text)
                        logger.debug(f"UIA: inserted text via ValuePattern ({len(text)} chars)")
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # Fallback: pyautogui
    try:
        import pyautogui
        import pyperclip

        old_clip = pyperclip.paste()
        pyperclip.copy(text)
        pyautogui.hotkey('ctrl', 'v')
        import time
        time.sleep(0.1)
        pyperclip.copy(old_clip)
        logger.debug(f"UIA: inserted text via clipboard fallback ({len(text)} chars)")
        return True
    except Exception as e:
        logger.error(f"UIA: text insertion failed: {e}")
        return False


def get_element_at_cursor() -> Optional[ElementInfo]:
    """Get info about the UIA element under the current mouse cursor."""
    if not _initialized:
        _initialize()
    def _inner():
        uia = _get_uia()
        if not uia:
            return None
        try:
            import win32gui
            pos = win32gui.GetCursorPos()
            import comtypes
            point = (ctypes.c_int * 2)(pos[0], pos[1])

            # Use ElementFromPoint
            element = uia.ElementFromPoint(comtypes.gen._944de083_8fb8_45cf_bcb7_c477acb2f897_0_1_0.tagPOINT(pos[0], pos[1]))
            if element:
                ct_id = element.CurrentControlType
                return ElementInfo(
                    name=element.CurrentName or "",
                    control_type=CONTROL_TYPE_NAMES.get(ct_id, f"Unknown({ct_id})"),
                    control_type_id=ct_id,
                    automation_id=getattr(element, 'CurrentAutomationId', "") or "",
                )
        except Exception as e:
            logger.debug(f"UIA: get_element_at_cursor failed: {e}")
        return None

    return _run_with_timeout(_inner, timeout_ms=100)
