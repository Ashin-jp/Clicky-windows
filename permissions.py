"""
permissions.py — Windows Permissions Helper

Checks and guides the user through granting microphone permissions.
On Windows, screen capture does not require explicit permission (unlike macOS),
and there is no Accessibility permission model.
"""

import ctypes
import logging
import subprocess

logger = logging.getLogger(__name__)


def check_microphone_permission() -> bool:
    """
    Check if microphone access is available by attempting to enumerate
    audio input devices. On Windows, this is the simplest reliable check.
    Returns True if at least one input device is accessible.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if input_devices:
            logger.debug(f"Microphone: found {len(input_devices)} input device(s)")
            return True
        else:
            logger.warning("Microphone: no input devices found")
            return False
    except Exception as e:
        logger.error(f"Microphone: failed to query devices: {e}")
        return False


def open_microphone_settings():
    """Open the Windows Privacy settings page for microphone access."""
    try:
        subprocess.Popen(["start", "ms-settings:privacy-microphone"], shell=True)
    except Exception as e:
        logger.error(f"Failed to open microphone settings: {e}")


def open_sound_settings():
    """Open the Windows Sound settings page."""
    try:
        subprocess.Popen(["start", "ms-settings:sound"], shell=True)
    except Exception as e:
        logger.error(f"Failed to open sound settings: {e}")


def check_screen_capture_permission() -> bool:
    """
    Screen capture on Windows does not require explicit permission.
    The mss library uses GDI/BitBlt which works for all standard content.
    Always returns True.
    """
    return True


def is_running_as_admin() -> bool:
    """Check if the app is running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_permission_status() -> dict:
    """
    Returns a dictionary of all permission statuses.
    On Windows, only microphone needs checking — screen capture and
    keyboard hooks work without special permissions.
    """
    return {
        "microphone": check_microphone_permission(),
        "screen_capture": check_screen_capture_permission(),
        "keyboard_hook": True,  # Always available on Windows
        "is_admin": is_running_as_admin(),
    }


def all_permissions_granted() -> bool:
    """Returns True if all required permissions are granted."""
    status = get_permission_status()
    return status["microphone"] and status["screen_capture"]
