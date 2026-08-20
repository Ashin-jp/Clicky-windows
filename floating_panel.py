"""
floating_panel.py — Floating Control Panel

Dark-themed frameless panel that opens near the system tray icon.
Shows companion status, model picker, permissions, PTT instructions, and quit button.

Windows equivalent of CompanionPanelView.swift + MenuBarPanelManager.swift.
"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFrame, QApplication, QMessageBox,
    QLineEdit, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QFont, QPainter, QPainterPath, QColor, QBrush

from design_system import Colors, Fonts, Spacing, Stylesheets
import config

logger = logging.getLogger(__name__)


class FloatingPanel(QWidget):
    """
    Floating control panel opened from the system tray icon.
    Dark, rounded, non-activating — matches the macOS NSPanel aesthetic.
    """

    quit_requested = Signal()
    model_changed = Signal(str)
    provider_changed = Signal(str, str, bool)  # key, value, restart_now
    cursor_toggled = Signal(bool)
    silent_mode_toggled = Signal(bool)
    
    # System 1 & 2 signals
    ui_guide_requested = Signal()
    ui_explain_requested = Signal()
    ui_tour_requested = Signal()
    linux_mode_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(Spacing.panel_width)
        self.setObjectName("floatingPanel")

        self._voice_state = "idle"
        self._is_cursor_enabled = True
        self._permissions = {}

        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Container with background
        container = QWidget()
        container.setObjectName("panelContainer")
        container.setStyleSheet("""
            QWidget#panelContainer {
                background-color: #1C1C1E;
                border: 1px solid rgba(68, 68, 70, 180);
                border-radius: 12px;
            }
        """)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(16, 16, 16, 16)
        container_layout.setSpacing(12)

        # ─── Header ──────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Clicky")
        title.setFont(Fonts.heading(15))
        title.setStyleSheet("color: white;")
        header.addWidget(title)

        self._status_label = QLabel("Ready")
        self._status_label.setFont(Fonts.small(11))
        self._status_label.setStyleSheet("color: #34C759;")
        header.addStretch()
        header.addWidget(self._status_label)
        container_layout.addLayout(header)

        # ─── Separator ────────────────────────────────────────────────
        container_layout.addWidget(self._make_separator())

        # ─── Status Section ───────────────────────────────────────────
        self._state_label = QLabel("Press Ctrl + Alt to talk")
        self._state_label.setFont(Fonts.primary(12))
        self._state_label.setStyleSheet("color: #AEAEB2;")
        self._state_label.setWordWrap(True)
        container_layout.addWidget(self._state_label)

        # ─── PTT Indicator ────────────────────────────────────────────
        # PTT keys layout
        ptt_layout = QHBoxLayout()
        ptt_label = QLabel("Talk to Clicky")
        ptt_label.setFont(Fonts.primary(13))
        ptt_label.setStyleSheet("color: white;")
        ptt_layout.addWidget(ptt_label)
        ptt_layout.addStretch()
        
        # Helper for drawing keys
        def make_key(text):
            k = QLabel(text)
            k.setFont(Fonts.small(10))
            k.setAlignment(Qt.AlignmentFlag.AlignCenter)
            k.setFixedHeight(24)
            k.setMinimumWidth(36)
            k.setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #3A3A3C;
                    border: 1px solid #636366;
                    border-radius: 4px;
                    padding: 2px 8px;
                }
            """)
            return k

        ptt_layout.addWidget(make_key("Ctrl"))
        ptt_layout.addWidget(make_key("Alt"))
        container_layout.addLayout(ptt_layout)
        
        # Dictation keys layout
        dict_layout = QHBoxLayout()
        dict_label = QLabel("Voice Type")
        dict_label.setFont(Fonts.primary(13))
        dict_label.setStyleSheet("color: white;")
        dict_layout.addWidget(dict_label)
        dict_layout.addStretch()
        dict_layout.addWidget(make_key("Shift"))
        dict_layout.addWidget(make_key("Alt"))
        container_layout.addLayout(dict_layout)

        # ─── Separator ────────────────────────────────────────────────
        container_layout.addWidget(self._make_separator())

        # ─── Settings / Providers ─────────────────────────────────────
        settings_label = QLabel("Settings")
        settings_label.setFont(Fonts.small(11))
        settings_label.setStyleSheet("color: #636366;")
        container_layout.addWidget(settings_label)

        # AI Provider
        ai_layout = QHBoxLayout()
        ai_label = QLabel("AI:")
        ai_label.setStyleSheet("color: white;")
        ai_layout.addWidget(ai_label)
        self._ai_combo = QComboBox()
        self._ai_combo.addItems(["groq", "gemini", "claude"])
        self._ai_combo.setStyleSheet(Stylesheets.COMBOBOX)
        self._ai_combo.currentTextChanged.connect(lambda t: self._on_provider_changed("AI_PROVIDER", t))
        ai_layout.addWidget(self._ai_combo)
        container_layout.addLayout(ai_layout)

        # TTS Provider
        tts_layout = QHBoxLayout()
        tts_label = QLabel("Voice:")
        tts_label.setStyleSheet("color: white;")
        tts_layout.addWidget(tts_label)
        self._tts_combo = QComboBox()
        self._tts_combo.addItems(["edge", "elevenlabs"])
        self._tts_combo.setStyleSheet(Stylesheets.COMBOBOX)
        self._tts_combo.currentTextChanged.connect(lambda t: self._on_provider_changed("TTS_PROVIDER", t))
        tts_layout.addWidget(self._tts_combo)
        container_layout.addLayout(tts_layout)

        # STT Provider
        stt_layout = QHBoxLayout()
        stt_label = QLabel("Ear:")
        stt_label.setStyleSheet("color: white;")
        stt_layout.addWidget(stt_label)
        self._stt_combo = QComboBox()
        self._stt_combo.addItems(["google_free", "assemblyai"])
        self._stt_combo.setStyleSheet(Stylesheets.COMBOBOX)
        self._stt_combo.currentTextChanged.connect(lambda t: self._on_provider_changed("STT_PROVIDER", t))
        stt_layout.addWidget(self._stt_combo)
        container_layout.addLayout(stt_layout)

        container_layout.addWidget(self._make_separator())

        # ─── Model Picker ─────────────────────────────────────────────
        model_label = QLabel("Model")
        model_label.setFont(Fonts.small(11))
        model_label.setStyleSheet("color: #636366;")
        container_layout.addWidget(model_label)

        self._model_combo = QComboBox()
        self._populate_model_combo(config.AI_PROVIDER)
        self._model_combo.setStyleSheet(Stylesheets.COMBOBOX)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        container_layout.addWidget(self._model_combo)

        # ─── Toggles ──────────────────────────────────────────────────
        toggle_layout = QHBoxLayout()
        toggle_layout.setSpacing(10)

        self._cursor_btn = QPushButton("Hide Clicky")
        self._cursor_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        self._cursor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cursor_btn.clicked.connect(self._on_cursor_toggle)
        toggle_layout.addWidget(self._cursor_btn)

        self._silent_btn = QPushButton("Silent Mode")
        self._silent_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        self._silent_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._silent_btn.setCheckable(True)
        self._silent_btn.clicked.connect(self._on_silent_toggle)
        toggle_layout.addWidget(self._silent_btn)

        container_layout.addLayout(toggle_layout)

        # ─── System 1 & 2 Controls ────────────────────────────────────
        sys_layout = QHBoxLayout()
        sys_layout.setSpacing(5)

        guide_btn = QPushButton("Guide")
        guide_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        guide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        guide_btn.clicked.connect(self.ui_guide_requested.emit)
        sys_layout.addWidget(guide_btn)

        explain_btn = QPushButton("Explain")
        explain_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        explain_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        explain_btn.clicked.connect(self.ui_explain_requested.emit)
        sys_layout.addWidget(explain_btn)

        tour_btn = QPushButton("Tour")
        tour_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        tour_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tour_btn.clicked.connect(self.ui_tour_requested.emit)
        sys_layout.addWidget(tour_btn)

        self._linux_btn = QPushButton("Linux Mode")
        self._linux_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        self._linux_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._linux_btn.setCheckable(True)
        self._linux_btn.clicked.connect(self._on_linux_toggle)
        sys_layout.addWidget(self._linux_btn)

        container_layout.addLayout(sys_layout)

        # ─── Separator ────────────────────────────────────────────────
        container_layout.addWidget(self._make_separator())

        # ─── Permissions ──────────────────────────────────────────────
        self._perm_label = QLabel("Checking permissions...")
        self._perm_label.setFont(Fonts.small(10))
        self._perm_label.setStyleSheet("color: #636366;")
        self._perm_label.setWordWrap(True)
        container_layout.addWidget(self._perm_label)

        container_layout.addWidget(self._make_separator())

        # ─── Workspaces ───────────────────────────────────────────────
        ws_header = QLabel("Workspaces")
        ws_header.setFont(Fonts.small(11))
        ws_header.setStyleSheet("color: #636366;")
        container_layout.addWidget(ws_header)

        # Save new
        save_ws_layout = QHBoxLayout()
        self._ws_name_input = QLineEdit()
        self._ws_name_input.setPlaceholderText("Workspace name...")
        self._ws_name_input.setStyleSheet(Stylesheets.COMBOBOX) # reuse style
        save_ws_layout.addWidget(self._ws_name_input)
        
        save_ws_btn = QPushButton("Save")
        save_ws_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        save_ws_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_ws_btn.clicked.connect(self._on_save_workspace_clicked)
        save_ws_layout.addWidget(save_ws_btn)
        container_layout.addLayout(save_ws_layout)

        # Status Label
        self._ws_status_label = QLabel("")
        self._ws_status_label.setFont(Fonts.small(10))
        self._ws_status_label.setStyleSheet("color: #0A84FF;")
        self._ws_status_label.setWordWrap(True)
        self._ws_status_label.hide()
        container_layout.addWidget(self._ws_status_label)

        # Scroll area for list
        self._ws_scroll = QScrollArea()
        self._ws_scroll.setWidgetResizable(True)
        self._ws_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._ws_scroll.setMaximumHeight(150)
        
        self._ws_list_widget = QWidget()
        self._ws_list_widget.setStyleSheet("background: transparent;")
        self._ws_list_layout = QVBoxLayout(self._ws_list_widget)
        self._ws_list_layout.setContentsMargins(0, 0, 0, 0)
        self._ws_list_layout.setSpacing(5)
        self._ws_scroll.setWidget(self._ws_list_widget)
        
        container_layout.addWidget(self._ws_scroll)

        # ─── Quit Button ─────────────────────────────────────────────
        container_layout.addWidget(self._make_separator())

        quit_btn = QPushButton("Quit Clicky")
        quit_btn.setStyleSheet("""
            QPushButton {
                color: #FF453A;
                background: transparent;
                border: none;
                font-size: 12px;
                padding: 4px;
            }
            QPushButton:hover {
                color: #FF6961;
            }
        """)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.clicked.connect(self.quit_requested.emit)
        container_layout.addWidget(quit_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # ─── Version ─────────────────────────────────────────────────
        version = QLabel(f"v{config.APP_VERSION}")
        version.setFont(Fonts.small(9))
        version.setStyleSheet("color: #3A3A3C;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(version)

        main_layout.addWidget(container)

    def _make_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: rgba(68, 68, 70, 100); max-height: 1px;")
        return sep

    # ─── Public API ───────────────────────────────────────────────────

    def update_voice_state(self, state: str):
        self._voice_state = state
        state_text = {
            "idle": "Press Ctrl + Alt to talk",
            "listening": "🎙️ Listening...",
            "processing": "⏳ Thinking...",
            "responding": "🔊 Speaking...",
        }.get(state, state)
        self._state_label.setText(state_text)

        status = {
            "idle": ("Ready", "#34C759"),
            "listening": ("Listening", "#007AFF"),
            "processing": ("Processing", "#FF9F0A"),
            "responding": ("Speaking", "#007AFF"),
        }.get(state, ("Ready", "#34C759"))
        self._status_label.setText(status[0])
        self._status_label.setStyleSheet(f"color: {status[1]};")

    # ─── Workspaces ───────────────────────────────────────────────────

    def refresh_workspaces(self):
        """Reload the workspaces list from DB."""
        # Clear existing
        while self._ws_list_layout.count():
            child = self._ws_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        from storage import get_db
        try:
            snapshots = get_db().list_workspace_snapshots()
            for s in snapshots:
                row = QHBoxLayout()
                
                name_lbl = QLabel(s["name"])
                name_lbl.setStyleSheet("color: white;")
                row.addWidget(name_lbl, stretch=1)
                
                restore_btn = QPushButton("Restore")
                restore_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
                restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                restore_btn.clicked.connect(lambda checked=False, n=s["name"]: self._on_restore_workspace_clicked(n))
                row.addWidget(restore_btn)
                
                del_btn = QPushButton("✕")
                del_btn.setStyleSheet("color: #FF453A; background: transparent; border: none; font-size: 14px;")
                del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                del_btn.clicked.connect(lambda checked=False, n=s["name"]: self._on_delete_workspace_clicked(n))
                row.addWidget(del_btn)
                
                self._ws_list_layout.addLayout(row)
        except Exception as e:
            logger.error(f"Failed to load workspaces: {e}")

    def _on_save_workspace_clicked(self):
        name = self._ws_name_input.text().strip()
        if name:
            from executors.workspace_actions import handle_save_workspace
            handle_save_workspace(name)
            self._ws_name_input.clear()
            self.refresh_workspaces()

    def _on_restore_workspace_clicked(self, name: str):
        from executors.workspace_actions import handle_restore_workspace
        self._ws_status_label.setText("Restoring...")
        self._ws_status_label.show()
        handle_restore_workspace(name)
        # Status callback will hide/update it

    def _on_delete_workspace_clicked(self, name: str):
        from storage import get_db
        get_db().delete_workspace_snapshot(name)
        self.refresh_workspaces()

    def update_workspace_status(self, text: str):
        """Called by WorkspaceManager status_callback."""
        self._ws_status_label.setText(text)
        self._ws_status_label.show()
        if "complete" in text.lower() or "failed" in text.lower() or "saved" in text.lower():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, self._ws_status_label.hide)

    def update_permissions(self, perms: dict):
        self._permissions = perms
        if perms.get("microphone", False):
            self._perm_label.setText("✓ Microphone access granted")
            self._perm_label.setStyleSheet("color: #34C759; font-size: 10px;")
        else:
            self._perm_label.setText("✗ Microphone access needed — check Settings → Privacy → Microphone")
            self._perm_label.setStyleSheet("color: #FF453A; font-size: 10px;")

    def update_cursor_enabled(self, enabled: bool):
        self._is_cursor_enabled = enabled
        self._cursor_btn.setText("Hide Clicky" if enabled else "Show Clicky")

    def show_near_tray(self, tray_geometry=None):
        """Position and show the panel near the system tray area."""
        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        self.adjustSize()
        # Position near bottom-right (where tray typically is on Windows)
        x = screen_geo.right() - self.width() - 8
        y = screen_geo.bottom() - self.height() - 8
        if tray_geometry and not tray_geometry.isNull():
            x = tray_geometry.x() - self.width() // 2
            y = tray_geometry.y() - self.height() - 8
        x = max(screen_geo.x(), min(x, screen_geo.right() - self.width()))
        y = max(screen_geo.y(), min(y, screen_geo.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()

    def _on_model_changed(self, index):
        model = self._model_combo.currentData()
        if model:
            self.model_changed.emit(model)

    def _on_cursor_toggle(self):
        self._is_cursor_enabled = not self._is_cursor_enabled
        self.cursor_toggled.emit(self._is_cursor_enabled)
        if self._is_cursor_enabled:
            self._cursor_btn.setText("Hide Clicky")
            self._cursor_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        else:
            self._cursor_btn.setText("Show Clicky")
            self._cursor_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)

    def _on_silent_toggle(self, checked: bool):
        if checked:
            self._silent_btn.setStyleSheet(Stylesheets.BUTTON_PRIMARY)
        else:
            self._silent_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        self.silent_mode_toggled.emit(checked)

    def sync_silent_state(self, enabled: bool):
        """Update the button state if silent mode was toggled via hotkey."""
        self._silent_btn.setChecked(enabled)
        if enabled:
            self._silent_btn.setStyleSheet(Stylesheets.BUTTON_PRIMARY)
        else:
            self._silent_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)

    def _on_linux_toggle(self, checked: bool):
        if checked:
            self._linux_btn.setStyleSheet(Stylesheets.BUTTON_PRIMARY)
        else:
            self._linux_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)
        self.linux_mode_toggled.emit(checked)
        
    def sync_linux_state(self, enabled: bool):
        """Update the button state if linux mode was toggled via hotkey."""
        self._linux_btn.setChecked(enabled)
        if enabled:
            self._linux_btn.setStyleSheet(Stylesheets.BUTTON_PRIMARY)
        else:
            self._linux_btn.setStyleSheet(Stylesheets.BUTTON_SECONDARY)

    def _populate_model_combo(self, provider: str):
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if provider == "groq":
            self._model_combo.addItem("Llama 4 Scout", config.DEFAULT_GROQ_MODEL)
        elif provider == "gemini":
            self._model_combo.addItem("Gemini 2.5 Flash", config.DEFAULT_GEMINI_MODEL)
            self._model_combo.addItem("Gemini 2.5 Pro", config.GEMINI_PRO_MODEL)
        else:
            self._model_combo.addItem("Sonnet 4.6", config.DEFAULT_CLAUDE_MODEL)
            self._model_combo.addItem("Opus 4.6", config.OPUS_CLAUDE_MODEL)
        self._model_combo.blockSignals(False)

    def set_providers(self, ai: str, tts: str, stt: str):
        self._ai_combo.blockSignals(True)
        self._ai_combo.setCurrentText(ai)
        self._ai_combo.blockSignals(False)
        self._populate_model_combo(ai)
        
        self._tts_combo.blockSignals(True)
        self._tts_combo.setCurrentText(tts)
        self._tts_combo.blockSignals(False)
        
        self._stt_combo.blockSignals(True)
        self._stt_combo.setCurrentText(stt)
        self._stt_combo.blockSignals(False)

    def _on_provider_changed(self, key: str, value: str):
        # Apply UI side effect immediately if it's the AI provider
        if key == "AI_PROVIDER":
            self._populate_model_combo(value)
            # Default model selection handled by signal to main
            new_model = self._model_combo.itemData(0)
            self.model_changed.emit(new_model)

        msg = QMessageBox()
        msg.setWindowTitle("Restart Required")
        msg.setText(f"You changed {key} to {value}. Restart Clicky now to apply deep configuration changes?")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStyleSheet("background-color: #2C2C2E; color: white;")
        
        btn_now = msg.addButton("Restart Now", QMessageBox.ButtonRole.AcceptRole)
        btn_later = msg.addButton("Restart Later", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        if msg.clickedButton() == btn_now:
            self.provider_changed.emit(key, value, True)
        else:
            self.provider_changed.emit(key, value, False)
