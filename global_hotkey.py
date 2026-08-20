"""
global_hotkey.py — Global Push-to-Talk Keyboard Hook

System-wide Ctrl+Alt detection using a low-level keyboard hook
via SetWindowsHookExW. Runs the hook message pump in a dedicated
thread and emits press/release transitions via Qt signals.

This is the Windows equivalent of the macOS CGEvent tap in
GlobalPushToTalkShortcutMonitor.swift.
"""

import ctypes
import ctypes.wintypes
import logging
import threading

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# Win32 constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4  # Left Alt
VK_RMENU = 0xA5  # Right Alt
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt/Menu

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Hook callback signature (MUST use WINFUNCTYPE for stdcall on Windows)
HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

# Configure Win32 API function signatures
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD)
user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK

user32.UnhookWindowsHookEx.argtypes = (ctypes.wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL

user32.CallNextHookEx.argtypes = (ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_long


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class GlobalHotkey(QObject):
    """
    System-wide push-to-talk monitor. Detects Ctrl+Alt press/release
    transitions using a low-level keyboard hook.

    Signals:
        shortcut_pressed: Emitted when Ctrl+Alt is pressed
        shortcut_released: Emitted when Ctrl+Alt is released
    """

    shortcut_pressed = Signal()
    shortcut_released = Signal()

    dictation_pressed = Signal()
    dictation_released = Signal()

    silent_mode_triggered = Signal()  # Ctrl+Shift+Alt tap → toggle chat overlay

    # Phase 5 hotkeys
    focus_mode_triggered = Signal()       # Ctrl+Shift+F
    visual_finder_triggered = Signal()    # Ctrl+Shift+V
    screen_read_triggered = Signal()      # Ctrl+Shift+S
    health_check_triggered = Signal()     # Ctrl+Shift+H
    macro_record_triggered = Signal()     # Ctrl+Shift+R
    save_workspace_triggered = Signal()   # Ctrl+Shift+W

    # System 1 & 2 Hotkeys
    ui_guide_triggered = Signal()         # Ctrl+Shift+G
    ui_explain_triggered = Signal()       # Ctrl+Shift+E
    ui_tour_triggered = Signal()          # Ctrl+Shift+T
    linux_mode_triggered = Signal()       # Ctrl+Shift+L

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hook_handle = None
        self._hook_thread = None
        self._hook_thread_id = None
        self._is_running = False
        self._shortcut_currently_pressed = False
        self._dictation_currently_pressed = False
        self._silent_mode_currently_pressed = False
        self._ctrl_held = False
        self._alt_held = False
        self._shift_held = False
        self._pending_hotkey_letters: set = set()  # Track Ctrl+Shift+letter combos
        # Must keep reference to prevent garbage collection of the callback
        self._hook_proc = HOOKPROC(self._low_level_keyboard_proc)

    @property
    def is_shortcut_pressed(self) -> bool:
        return self._shortcut_currently_pressed

    def start(self):
        """Install the global keyboard hook in a background thread."""
        if self._is_running:
            return

        self._is_running = True
        self._hook_thread = threading.Thread(
            target=self._hook_thread_main,
            daemon=True,
            name="GlobalHotkeyHookThread",
        )
        self._hook_thread.start()
        logger.info("Global hotkey: started (Ctrl+Alt)")

    def stop(self):
        """Remove the global keyboard hook and stop the thread."""
        if not self._is_running:
            return

        self._is_running = False
        self._shortcut_currently_pressed = False

        # Post WM_QUIT to the hook thread's message loop to break GetMessage
        if self._hook_thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._hook_thread_id, 0x0012, 0, 0  # WM_QUIT
            )

        if self._hook_thread and self._hook_thread.is_alive():
            self._hook_thread.join(timeout=2.0)

        self._hook_thread = None
        self._hook_thread_id = None
        logger.info("Global hotkey: stopped")

    def _hook_thread_main(self):
        """
        Thread entry point. Installs the hook and runs a message pump.
        The message pump is required for low-level hooks to work.
        """
        self._hook_thread_id = kernel32.GetCurrentThreadId()

        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            None,
            0,
        )

        if not self._hook_handle:
            error = ctypes.get_last_error()
            logger.error(f"Global hotkey: SetWindowsHookExW failed (error {error})")
            return

        logger.debug("Global hotkey: hook installed, entering message loop")

        # Message pump — required for the hook to receive callbacks
        msg = ctypes.wintypes.MSG()
        while self._is_running:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0 or result == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Unhook
        if self._hook_handle:
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None
            logger.debug("Global hotkey: hook removed")

    def _low_level_keyboard_proc(self, nCode, wParam, lParam):
        """
        Low-level keyboard hook callback. Detects Ctrl and Alt key
        state changes and emits press/release signals.

        This is listen-only — we always call CallNextHookEx to pass
        the event through, matching the macOS .listenOnly behavior.
        """
        if nCode >= 0:
            kb_struct = ctypes.cast(
                lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)
            ).contents
            vk_code = kb_struct.vkCode

            is_key_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_key_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

            # Track Ctrl state
            if vk_code in (VK_LCONTROL, VK_RCONTROL, VK_CONTROL):
                if is_key_down:
                    self._ctrl_held = True
                elif is_key_up:
                    self._ctrl_held = False

            # Track Alt state
            if vk_code in (VK_LMENU, VK_RMENU, VK_MENU):
                if is_key_down:
                    self._alt_held = True
                elif is_key_up:
                    self._alt_held = False

            # Track Shift state
            from config import VK_LSHIFT, VK_RSHIFT
            VK_SHIFT = 0x10
            if vk_code in (VK_LSHIFT, VK_RSHIFT, VK_SHIFT):
                if is_key_down:
                    self._shift_held = True
                elif is_key_up:
                    self._shift_held = False

            # Detect Ctrl+Alt transitions (AI Companion)
            ctrl_alt_held = self._ctrl_held and self._alt_held and not self._shift_held

            if ctrl_alt_held and not self._shortcut_currently_pressed:
                self._shortcut_currently_pressed = True
                self.shortcut_pressed.emit()
            elif not ctrl_alt_held and self._shortcut_currently_pressed:
                self._shortcut_currently_pressed = False
                self.shortcut_released.emit()

            # Detect Shift+Alt transitions (Dictation)
            shift_alt_held = self._shift_held and self._alt_held and not self._ctrl_held

            if shift_alt_held and not self._dictation_currently_pressed:
                self._dictation_currently_pressed = True
                self.dictation_pressed.emit()
            elif not shift_alt_held and self._dictation_currently_pressed:
                self._dictation_currently_pressed = False
                self.dictation_released.emit()

            # Detect Ctrl+Shift+Alt tap (Silent Mode toggle)
            all_three_held = self._ctrl_held and self._shift_held and self._alt_held

            if all_three_held and not self._silent_mode_currently_pressed:
                self._silent_mode_currently_pressed = True
                self.silent_mode_triggered.emit()
            elif not all_three_held and self._silent_mode_currently_pressed:
                self._silent_mode_currently_pressed = False

            # Detect Ctrl+Shift+Letter hotkeys (no Alt)
            if is_key_down and self._ctrl_held and self._shift_held and not self._alt_held:
                if vk_code == 0x46 and 0x46 not in self._pending_hotkey_letters:  # F
                    self._pending_hotkey_letters.add(0x46)
                    self.focus_mode_triggered.emit()
                elif vk_code == 0x56 and 0x56 not in self._pending_hotkey_letters:  # V
                    self._pending_hotkey_letters.add(0x56)
                    self.visual_finder_triggered.emit()
                elif vk_code == 0x53 and 0x53 not in self._pending_hotkey_letters:  # S
                    self._pending_hotkey_letters.add(0x53)
                    self.screen_read_triggered.emit()
                elif vk_code == 0x48 and 0x48 not in self._pending_hotkey_letters:  # H
                    self._pending_hotkey_letters.add(0x48)
                    self.health_check_triggered.emit()
                elif vk_code == 0x52 and 0x52 not in self._pending_hotkey_letters:  # R
                    self._pending_hotkey_letters.add(0x52)
                    self.macro_record_triggered.emit()
                elif vk_code == 0x57 and 0x57 not in self._pending_hotkey_letters:  # W
                    self._pending_hotkey_letters.add(0x57)
                    self.save_workspace_triggered.emit()
                elif vk_code == 0x47 and 0x47 not in self._pending_hotkey_letters:  # G
                    self._pending_hotkey_letters.add(0x47)
                    self.ui_guide_triggered.emit()
                elif vk_code == 0x45 and 0x45 not in self._pending_hotkey_letters:  # E
                    self._pending_hotkey_letters.add(0x45)
                    self.ui_explain_triggered.emit()
                elif vk_code == 0x54 and 0x54 not in self._pending_hotkey_letters:  # T
                    self._pending_hotkey_letters.add(0x54)
                    self.ui_tour_triggered.emit()
                elif vk_code == 0x4C and 0x4C not in self._pending_hotkey_letters:  # L
                    self._pending_hotkey_letters.add(0x4C)
                    self.linux_mode_triggered.emit()
            # Clear letter tracking on key up
            if is_key_up and vk_code in self._pending_hotkey_letters:
                self._pending_hotkey_letters.discard(vk_code)

        return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)
