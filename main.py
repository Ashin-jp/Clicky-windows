"""
main.py — Windows Clicky Entry Point

Initializes the QApplication (no taskbar button), creates the system tray,
floating panel, companion manager, and wires everything together.
Runs an asyncio event loop alongside the Qt event loop.
"""

# ─── DPI Awareness (must be before ANY other imports) ─────────────────
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import asyncio
import logging
import sys
import os

# Configure logging before any imports that use it
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clicky")

# Ensure the app directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

import config

# Module-level DPI scale factor (set after QApplication creation)
_DPI_SCALE = 1.0


def main():
    global _DPI_SCALE

    # Create QApplication with no taskbar button
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running when panel is closed
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)

    # Calculate DPI scale factor from the primary screen
    _DPI_SCALE = app.primaryScreen().devicePixelRatio()
    logger.info(f"DPI scale factor: {_DPI_SCALE}")

    # Set up asyncio event loop integration with Qt
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Timer to process asyncio events from within the Qt event loop
    async_timer = QTimer()
    async_timer.setInterval(10)  # Process async tasks every 10ms

    def process_async_events():
        loop.stop()
        loop.run_forever()

    async_timer.timeout.connect(process_async_events)
    async_timer.start()

    # Initialize pygame for audio playback
    try:
        import pygame
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
        logger.info("Pygame mixer initialized")
    except Exception as e:
        logger.warning(f"Pygame mixer init failed: {e}")

    # Import components after app is created
    from companion_manager import CompanionManager
    from system_tray import SystemTray
    from floating_panel import FloatingPanel

    # Create components
    manager = CompanionManager()
    tray = SystemTray()
    panel = FloatingPanel()

    # Wire panel signals
    panel.quit_requested.connect(app.quit)
    panel.model_changed.connect(manager.set_selected_model)
    panel.cursor_toggled.connect(manager.set_cursor_enabled)
    panel.provider_changed.connect(manager.apply_provider_change)
    
    # Initialize panel values from DB/manager
    panel.set_providers(manager.ai_provider, manager.tts_provider, manager.stt_provider)

    # Wire tray signals
    tray.quit_requested.connect(app.quit)

    def toggle_panel():
        if panel.isVisible():
            panel.hide()
        else:
            tray_geo = tray.geometry()
            panel.refresh_workspaces()
            panel.show_near_tray(tray_geo)

    tray.panel_toggle_requested.connect(toggle_panel)

    # Wire manager signals to UI
    manager.voice_state_changed.connect(panel.update_voice_state)
    manager.voice_state_changed.connect(tray.update_tooltip_state)
    manager.permissions_changed.connect(panel.update_permissions)
    manager.overlay_visibility_changed.connect(panel.update_cursor_enabled)
    manager.silent_mode_changed.connect(panel.sync_silent_state)
    manager.workspace_status_changed.connect(panel.update_workspace_status)

    # ─── Fix 1: Startup Dependency Checks ──────────────────────────────────
    def check_dependencies():
        try:
            import playwright
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright, then python -m playwright install chromium")
            def notify_missing():
                from companion_manager import create_tracked_task
                create_tracked_task(manager.tts.speak_text("Playwright is not installed. Please run: pip install playwright, then python dash m playwright install chromium"), "deps_tts")
            loop.call_soon_threadsafe(notify_missing)
            
    check_dependencies()

    # Wire UI controls to manager
    panel.silent_mode_toggled.connect(manager.set_silent_mode)
    panel.ui_guide_requested.connect(manager._on_ui_guide_hotkey)
    panel.ui_explain_requested.connect(manager._on_ui_explain_hotkey)
    panel.ui_tour_requested.connect(manager._on_ui_tour_hotkey)
    panel.linux_mode_toggled.connect(manager.set_linux_mode)
    
    # Sync states back to panel
    manager.linux_mode_changed.connect(panel.sync_linux_state)

    # Click-outside-to-dismiss for the panel
    def on_focus_changed(old, new):
        if panel.isVisible() and new is None:
            QTimer.singleShot(200, lambda: panel.hide() if panel.isVisible() and not panel.isActiveWindow() else None)

    app.focusChanged.connect(on_focus_changed)

    # Handle restarts
    def handle_restart():
        logger.info("Restarting application to apply settings...")
        async_timer.stop()
        manager.stop()
        try:
            loop.run_until_complete(manager.ai_client.close())
            loop.run_until_complete(manager.tts.close())
        except Exception:
            pass
        loop.close()
        
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        app.quit()

    manager.restart_requested.connect(handle_restart)

    # Start everything
    tray.show()
    manager.start()

    logger.info(f"🚀 {config.APP_NAME} v{config.APP_VERSION} is running")
    logger.info(f"   AI Provider: {manager.ai_provider}")
    logger.info(f"   Push-to-talk: {config.PTT_DISPLAY_TEXT}")
    logger.info(f"   Model: {manager.selected_model}")

    # Clean up on quit
    def cleanup():
        logger.info("Shutting down...")
        async_timer.stop()
        manager.stop()
        try:
            # Clean up async resources
            loop.run_until_complete(manager.ai_client.close())
            loop.run_until_complete(manager.tts.close())
        except Exception:
            pass
        loop.close()

    app.aboutToQuit.connect(cleanup)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
