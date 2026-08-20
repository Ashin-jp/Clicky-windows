"""
companion_manager.py — Central State Machine (v2)

Owns the full push-to-talk pipeline with extended action system:
hotkey → mic → transcription → screenshot → AI → TTS → overlay animation
+ trust engine + executor registry + context injection + automation subsystems
+ silent mode (text I/O via chat overlay for when user can't speak/hear).
"""

import asyncio
import logging
import time
import traceback

from PySide6.QtCore import QObject, Signal, QTimer, QSettings

from analytics import ClickyAnalytics
from audio_capture import AudioCapture
from global_hotkey import GlobalHotkey
from overlay_window import OverlayWindowManager
from point_parser import parse_pointing_coordinates, map_screenshot_to_screen_coordinates
from screen_capture import capture_all_screens
from trust_engine import TrustEngine, TrustLevel
from groq_router import TaskType
from storage import get_db
import config
import permissions

logger = logging.getLogger(__name__)

# ─── Task Tracking (Fix 2) ───────────────────────────────────────────
_tracked_tasks: set[asyncio.Task] = set()


def create_tracked_task(coro, name: str = "unnamed") -> asyncio.Task:
    """
    Create an asyncio task with exception tracking.
    Every exception is logged with full traceback and task name.
    The task reference is stored to prevent GC and allow shutdown awaiting.
    """
    task = asyncio.ensure_future(coro)
    _tracked_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _tracked_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error(
                f"Tracked task '{name}' failed:\n"
                f"{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
            )

    task.add_done_callback(_on_done)
    return task


ACTIONS_PROMPT = """\
device actions:
you can take actions on the user's device. a trust system handles confirmation — safe actions run silently, risky ones need user approval. don't hesitate to use actions when they'd help.

available actions (append these tags AFTER your spoken text, BEFORE any POINT tag):

SCREEN INTERACTION:
- [CLICK:x,y] — click at screen coordinates (left click)
- [CLICK:x,y,right] — right-click at coordinates
- [SCROLL:direction] or [SCROLL:direction,amount] — scroll up/down/left/right
- [DRAG:x1,y1,x2,y2] — drag from point A to point B
- [RIGHTCLICK:x,y] — right-click at coordinates (opens context menu)
- [SCREENSHOT_REGION:x,y,w,h] — capture a specific region for closer analysis
- [SAVE_SCREENSHOT:filename] — take a full screenshot and save to Pictures/Screenshots

LEGACY (still supported):
- [SEARCH:query] — google search
- [OPEN:url] — open URL in browser
- [TYPE:text] — type text at cursor position
- [HOTKEY:keys] — press keyboard shortcut (keys separated by +)
- [RUN:app] — launch a windows app

FILE & SYSTEM:
- [OPEN_FILE:path] — open a file or folder with the system default app
- [CREATE_FILE:path|content] — create a new file (relative paths go to clicky workspace)
- [READ_FILE:path] — read file contents (will be fed back to you for analysis)
- [WRITE_FILE:path|content] — write content to a file
- [SEARCH_FILES:directory|pattern] — find files matching a glob pattern
- [RUN_CMD:command] — run a terminal command and get the output back

WEB:
- [FETCH_URL:url] — fetch webpage text (content comes back to you for analysis)
- [DOWNLOAD:url] or [DOWNLOAD:url|save_path] — download a file
- [SUMMARISE_PAGE:url] — fetch and summarise a webpage

KNOWLEDGE (these re-prompt you with specific instructions):
- [EXPLAIN:topic] — explain something visible on screen in detail
- [TRANSLATE:language|text] — translate text (or screen content if no text given)
- [GENERATE_CODE:language|description] — generate code, copies to clipboard
- [QUIZ:topic] — generate quiz questions from screen content
- [STEP_GUIDE:task] — create a step-by-step guide
- [SUMMARISE_SCREEN:focus] — summarise what's visible on screen

COMMUNICATION:
- [DRAFT_MESSAGE:context] — draft an email/message based on screen context
- [READ_ALOUD:text] — read text aloud (or screen text if empty)
- [DICTATE:] — activate voice dictation mode

AUTOMATION:
- [RECORD_MACRO:name] — start recording actions as a replayable macro
- [STOP_RECORDING:] — stop recording the current macro
- [PLAY_MACRO:name] — replay a saved macro
- [WATCH_FOLDER:path] — monitor a folder for new files
- [SCHEDULE_TASK:time|action_type|params] — schedule an action (time as HH:MM)
- [CHAIN:action1_type:params|action2_type:params] — run multiple actions in sequence

EXTENDED:
- [TRANSFORM_TEXT:type] — transform clipboard text (formalize, simplify, bullet_points, summarize, shorten, expand, email_format, translate:lang)
- [FOCUS_MODE:minutes] — activate focus mode for N minutes
- [SAVE_WORKSPACE:name] — save current window layout
- [RESTORE_WORKSPACE:name] — restore a saved layout
- [REMEMBER:content] — save content to your personal knowledge base
- [RUN_CODE:language] — execute code from clipboard
- [RESEARCH:topic] — start background research
- [READ_SCREEN:] — read screen content aloud
- [HEALTH_CHECK:] — report system health
- [CLOSE_APP:name] — close a running app (e.g., [CLOSE_APP:Chrome])
- [SWITCH_TO_APP:name] — bring an app to foreground
- [LIST_OPEN_APPS:] — read aloud open apps
- [RESTART_APP:name] — close and reopen an app
- [APP_VOLUME:app|action] — adjust volume (mute, unmute, lower, raise)

BROWSER (uses a dedicated Playwright browser — never touches the user's own Chrome):
- [BROWSER_SEARCH:query] — open browser, search the web, read top results
- [BROWSER_NAVIGATE:url] — navigate to a URL
- [BROWSER_CLICK:element_description] — click an element on the page by its text or label
- [BROWSER_TYPE:field_description|text] — type text into a form field
- [BROWSER_READ:] — extract and read the main content of the current page
- [BROWSER_SCROLL:direction] or [BROWSER_SCROLL:direction|amount] — scroll page
- [BROWSER_TAB:action|target] — tab management (new, close, list, switch|name)
- [BROWSER_BACK:] — go back in browser history
- [BROWSER_FORWARD:] — go forward in browser history
- [BROWSER_SCREENSHOT:] — screenshot the current browser tab
- [BROWSER_FILL_FORM:context] — auto-fill form fields from saved profile data

trust levels: SEARCH, OPEN, SCROLL, READ_FILE, FETCH_URL, EXPLAIN, TRANSLATE, QUIZ, STEP_GUIDE, SUMMARISE_SCREEN, SUMMARISE_PAGE, DRAFT_MESSAGE, READ_ALOUD, SCREENSHOT_REGION, SEARCH_FILES, GENERATE_CODE, HEALTH_CHECK, READ_SCREEN, TRANSFORM_TEXT, REMEMBER, SWITCH_TO_APP, LIST_OPEN_APPS, APP_VOLUME, BROWSER_SEARCH, BROWSER_NAVIGATE, BROWSER_READ, BROWSER_SCROLL, BROWSER_TAB, BROWSER_BACK, BROWSER_FORWARD, BROWSER_SCREENSHOT run silently. TYPE, HOTKEY, RUN, CLICK, DRAG, RIGHTCLICK, DOWNLOAD, DICTATE, RECORD_MACRO, PLAY_MACRO, WATCH_FOLDER, SCHEDULE_TASK, CHAIN, CREATE_FILE, FOCUS_MODE, SAVE_WORKSPACE, RESTORE_WORKSPACE, RESEARCH, RUN_CODE, CLOSE_APP, RESTART_APP, BROWSER_CLICK, BROWSER_TYPE need one-time confirmation. WRITE_FILE, RUN_CMD, BROWSER_FILL_FORM always need confirmation. dangerous system commands are blocked automatically.

when to use actions:
- user says "search for python tutorials" → use BROWSER_SEARCH
- user says "click that button" → use CLICK with coordinates from screenshot
- user says "open my downloads folder" → use OPEN_FILE
- user says "what's in this file?" → use READ_FILE then explain
- user says "run pip install requests" → use RUN_CMD
- user says "translate this to spanish" → use TRANSLATE
- user says "save this code to a file" → use CREATE_FILE
- user says "focus mode for 25 minutes" → use FOCUS_MODE
- user says "how is my computer" → use HEALTH_CHECK
- user says "research quantum computing" → use RESEARCH
- user says "remember this" → use REMEMBER with the last AI response
- user says "go to youtube.com" → use BROWSER_NAVIGATE
- user says "read this page" → use BROWSER_READ
- user says "click the sign in button" → use BROWSER_CLICK
- user says "fill out this form" → use BROWSER_FILL_FORM

don't use actions when the user is just asking a question you can answer directly. only act when they explicitly or implicitly want you to do something on their device.

you can combine multiple action tags. they execute in order.
"""

SYSTEM_PROMPT = f"""\
you're clicky, a powerful AI companion that lives in the user's system tray on windows. the user just spoke to you via push-to-talk and you can see their screen(s). your reply will be spoken aloud via text-to-speech, so write the way you'd actually talk. this is an ongoing conversation — you remember everything they've said before.

rules:
- default to one or two sentences. be direct and dense. BUT if the user asks you to explain more, go deeper, or elaborate, then go all out — give a thorough, detailed explanation with no length limit.
- all lowercase, casual, warm. no emojis.
- write for the ear, not the eye. short sentences. no lists, bullet points, markdown, or formatting — just natural speech.
- don't use abbreviations or symbols that sound weird read aloud. write "for example" not "e.g.", spell out small numbers.
- if the user's question relates to what's on their screen, reference specific things you see.
- if the screenshot doesn't seem relevant to their question, just answer the question directly.
- you can help with anything — coding, writing, general knowledge, brainstorming.
- never say "simply" or "just".
- don't read out code verbatim. describe what the code does or what needs to change conversationally.
- focus on giving a thorough, useful explanation. don't end with simple yes/no questions like "want me to explain more?" or "should i show you?" — those are dead ends that force the user to just say yes.
- instead, when it fits naturally, end by planting a seed — mention something bigger or more ambitious they could try, a related concept that goes deeper, or a next-level technique that builds on what you just explained. make it something worth coming back for, not a question they'd just nod to. it's okay to not end with anything extra if the answer is complete on its own.
- if you receive multiple screen images, the one labeled "primary focus" is where the cursor is — prioritize that one but reference others if relevant.

element pointing:
you have a small blue triangle cursor that can fly to and point at things on screen. use it whenever pointing would genuinely help the user — if they're asking how to do something, looking for a menu, trying to find a button, or need help navigating an app, point at the relevant element. err on the side of pointing rather than not pointing, because it makes your help way more useful and concrete.

don't point at things when it would be pointless — like if the user asks a general knowledge question, or the conversation has nothing to do with what's on screen. but if there's a specific UI element, menu, button, or area on screen that's relevant to what you're helping with, point at it.

when you point, append a coordinate tag at the very end of your response, AFTER your spoken text. the screenshot images are labeled with their pixel dimensions. use those dimensions as the coordinate space. the origin (0,0) is the top-left corner of the image. x increases rightward, y increases downward.

format: [POINT:x,y:label] where x,y are integer pixel coordinates in the screenshot's coordinate space, and label is a short 1-3 word description of the element. if the element is on the cursor's screen you can omit the screen number. if the element is on a DIFFERENT screen, append :screenN where N is the screen number from the image label.

if pointing wouldn't help, append [POINT:none].

{ACTIONS_PROMPT}
"""

SILENT_PROMPT = f"""\
you're clicky, a powerful AI companion that lives in the user's system tray on windows. the user is in SILENT MODE — they typed their message (they can't speak) and they'll READ your reply (they can't hear). so write for the eye, not the ear.

rules:
- be concise and well-structured. use short paragraphs.
- all lowercase, casual, warm. no emojis.
- you can use simple formatting: dashes for lists, blank lines between paragraphs.
- if the user's question relates to what's on their screen, reference specific things you see.
- you can help with anything — coding, writing, general knowledge, brainstorming.
- never say "simply" or "just".
- for code, show key snippets but keep it brief. don't dump entire files.
- don't end with filler questions. plant a seed or end cleanly.

element pointing:
same as voice mode — use [POINT:x,y:label] to fly the cursor to relevant UI elements.
if pointing wouldn't help, use [POINT:none].

{ACTIONS_PROMPT}
"""

class CompanionManager(QObject):
    """Central state machine for the Clicky companion."""

    voice_state_changed = Signal(str)
    audio_power_changed = Signal(float)
    permissions_changed = Signal(dict)
    overlay_visibility_changed = Signal(bool)
    transcript_received = Signal(str)
    silent_mode_changed = Signal(bool)  # Emitted when silent mode is toggled
    linux_mode_changed = Signal(bool)   # Emitted when linux mode is toggled
    workspace_status_changed = Signal(str) # Emitted for workspace UI updates
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._settings = QSettings("Clicky", "WindowsClicky")
        self._voice_state = "idle"
        self._is_overlay_visible = False
        self._last_transcript = None
        self._active_mode = "none"  # "ai", "dictation", "silent", or "none"
        self._is_silent_mode = False
        # Database & Thread State
        from storage import get_db
        self._db = get_db()
        self._conversation_history: list[tuple[str, str]] = self._db.load_messages()
        self._current_response_task = None
        self._event_loop = None

        # Suggestion Engine State
        self._pending_suggestion_sequence = None
        self._pending_suggestion_hash = None
        
        # Pending Action State (Fix 4)
        self._pending_action_context = {}

        # Provider selection from DB
        self._ai_provider = self._db.get_config("AI_PROVIDER", config.AI_PROVIDER)
        self._tts_provider = self._db.get_config("TTS_PROVIDER", config.TTS_PROVIDER)
        self._stt_provider = self._db.get_config("STT_PROVIDER", config.STT_PROVIDER)

        if self._ai_provider == "groq":
            default_model = config.DEFAULT_GROQ_MODEL
        elif self._ai_provider == "gemini":
            default_model = config.DEFAULT_GEMINI_MODEL
        else:
            default_model = config.DEFAULT_CLAUDE_MODEL
        self._selected_model = self._settings.value("selectedModel", default_model)

        self._is_cursor_enabled = self._settings.value("isCursorEnabled", True, type=bool)
        self._has_completed_onboarding = self._settings.value("hasCompletedOnboarding", False, type=bool)

        # Core components
        self.hotkey = GlobalHotkey()
        self.audio = AudioCapture()
        self.overlay = OverlayWindowManager()
        self.ai_client = self._create_ai_client()
        self.tts = self._create_tts_client()
        self._transcription_provider = None
        self._active_session = None

        # v2: Trust engine & subsystems
        self.trust_engine = TrustEngine()
        self.macro_recorder = None
        self.folder_watcher = None
        self.task_scheduler = None

        # v3: New subsystems
        from ambient_context import get_ambient_context
        from clipboard_monitor import get_clipboard_monitor
        from health_monitor import get_health_monitor
        from watchdog_system import get_watchdog
        from intent_router import get_intent_router

        self._ambient_context = get_ambient_context()
        self._clipboard_monitor = get_clipboard_monitor()
        self._health_monitor = get_health_monitor()
        self._watchdog = get_watchdog()
        self._intent_router = get_intent_router()

        # Systems 1 & 2
        from ui_guidance import UIGuidanceSystem
        from linux_assistant import LinuxCommandAssistant
        self.ui_guidance = UIGuidanceSystem()
        self.linux_assistant = LinuxCommandAssistant()
        self._linux_mode_active = False
        self._ambient_context.linux_error_callback = self._on_linux_ambient_error

        # Silent mode: chat overlay
        from chat_overlay import ChatOverlay
        self.chat_overlay = ChatOverlay()
        self.chat_overlay.message_submitted.connect(self._on_silent_message)
        self.chat_overlay.closed.connect(self._on_silent_closed)
        self.chat_overlay.stop_requested.connect(self._cancel_current_operation)

        # Load executor registry
        from executors import load_all_executors
        load_all_executors()

        # Wire signals
        self.hotkey.shortcut_pressed.connect(self._on_shortcut_pressed)
        self.hotkey.shortcut_released.connect(self._on_shortcut_released)
        self.hotkey.dictation_pressed.connect(self._on_dictation_pressed)
        self.hotkey.dictation_released.connect(self._on_dictation_released)
        self.hotkey.silent_mode_triggered.connect(self._on_silent_mode_toggle)
        self.audio.audio_buffer_ready.connect(self._on_audio_buffer)
        self.audio.audio_power_changed.connect(self._on_audio_power)

        # Phase 5 hotkeys
        self.hotkey.focus_mode_triggered.connect(self._on_focus_mode_hotkey)
        self.hotkey.visual_finder_triggered.connect(self._on_visual_finder_hotkey)
        self.hotkey.screen_read_triggered.connect(self._on_screen_read_hotkey)
        self.hotkey.health_check_triggered.connect(self._on_health_check_hotkey)
        self.hotkey.macro_record_triggered.connect(self._on_macro_record_hotkey)
        self.hotkey.save_workspace_triggered.connect(self._on_save_workspace_hotkey)

        # System 1 & 2 hotkeys
        self.hotkey.ui_guide_triggered.connect(self._on_ui_guide_hotkey)
        self.hotkey.ui_explain_triggered.connect(self._on_ui_explain_hotkey)
        self.hotkey.ui_tour_triggered.connect(self._on_ui_tour_hotkey)
        self.hotkey.linux_mode_triggered.connect(self._on_linux_mode_hotkey)

        # Permission polling
        self._perm_timer = QTimer(self)
        self._perm_timer.timeout.connect(self._refresh_permissions)
        self._perm_timer.start(3000)

        # TTS polling
        self._tts_poll_timer = QTimer(self)
        self._tts_poll_timer.timeout.connect(self._check_tts_finished)
        self._tts_poll_timer.start(200)

        ClickyAnalytics.initialize()

        # Start background subsystems
        self._start_subsystems()

    @property
    def voice_state(self) -> str:
        return self._voice_state

    @property
    def selected_model(self) -> str:
        return str(self._selected_model)

    @property
    def is_overlay_visible(self) -> bool:
        return self._is_overlay_visible

    @property
    def is_cursor_enabled(self) -> bool:
        return bool(self._is_cursor_enabled)

    @property
    def ai_provider(self) -> str:
        return self._ai_provider

    @property
    def tts_provider(self) -> str:
        return self._tts_provider

    @property
    def stt_provider(self) -> str:
        return self._stt_provider

    def apply_provider_change(self, config_key: str, new_value: str, restart_now: bool):
        """Save a provider change to DB and optionally restart."""
        self._db.set_config(config_key, new_value)
        if restart_now:
            logger.info(f"Saving conversation and restarting to apply {config_key}={new_value}")
            self._db.save_messages(self._conversation_history)
            self.restart_requested.emit()
        else:
            logger.info(f"Saved {config_key}={new_value} for next launch")

    def _create_ai_client(self):
        model = str(self._selected_model)
        if self._ai_provider == "groq":
            from groq_api import GroqAPI
            return GroqAPI(model=model)
        elif self._ai_provider == "gemini":
            from gemini_api import GeminiAPI
            return GeminiAPI(model=model)
        else:
            from claude_api import ClaudeAPI
            return ClaudeAPI(model=model)

    def _create_tts_client(self):
        if self._tts_provider == "edge":
            from edge_tts_client import EdgeTTSClient
            return EdgeTTSClient(voice=config.EDGE_TTS_VOICE)
        else:
            from elevenlabs_tts import ElevenLabsTTSClient
            return ElevenLabsTTSClient()

    def _create_stt_provider(self):
        if self._stt_provider == "google_free":
            from google_stt_provider import GoogleFreeSTTProvider
            return GoogleFreeSTTProvider()
        else:
            from transcription_provider import TranscriptionProviderFactory
            return TranscriptionProviderFactory.make_default_provider()

    def set_selected_model(self, model: str):
        self._selected_model = model
        self._settings.setValue("selectedModel", model)
        self.ai_client.model = model

    def set_cursor_enabled(self, enabled: bool):
        self._is_cursor_enabled = enabled
        self._settings.setValue("isCursorEnabled", enabled)
        if enabled:
            self.overlay.show_overlay()
            self._is_overlay_visible = True
        else:
            self.overlay.hide_overlay()
            self._is_overlay_visible = False
        self.overlay_visibility_changed.emit(self._is_overlay_visible)

    def start(self):
        """Initialize all components and start listening."""
        logger.info("CompanionManager: starting (v2 — extended actions)")
        self._refresh_permissions()
        self.hotkey.start()

        try:
            self._transcription_provider = self._create_stt_provider()
        except Exception as e:
            logger.error(f"Failed to init transcription provider: {e}")

        # Initialize automation subsystems
        try:
            from macro_recorder import MacroRecorder
            from folder_watcher import FolderWatcher
            from task_scheduler import TaskScheduler

            self.macro_recorder = MacroRecorder()
            self.folder_watcher = FolderWatcher()
            self.task_scheduler = TaskScheduler()

            # Wire folder watcher events
            self.folder_watcher.file_event.connect(self._on_folder_event)
            # Wire scheduled task triggers
            self.task_scheduler.task_triggered.connect(self._on_scheduled_task)

            # Suggestion Engine & Text Transformer
            from suggestion_engine import get_suggestion_engine
            from smart_text_transformer import get_text_transformer
            
            self.suggestion_engine = get_suggestion_engine()
            self.suggestion_engine.set_suggestion_callback(self._on_proactive_suggestion)
            self.suggestion_engine.start()
            
            # Init transformer (it doesn't have a background thread, just needs to exist)
            get_text_transformer()

            # Workspace Manager
            from workspace_manager import get_workspace_manager
            self.workspace_manager = get_workspace_manager()
            self.workspace_manager.set_tts_callback(lambda msg: create_tracked_task(self.tts.speak_text(msg), "workspace_tts"))
            self.workspace_manager.set_status_callback(self.workspace_status_changed.emit)

            logger.info("Automation subsystems initialized")
        except Exception as e:
            logger.warning(f"Automation subsystems init failed: {e}")

        if self._has_completed_onboarding and self._is_cursor_enabled:
            self.overlay.show_overlay()
            self._is_overlay_visible = True
            self.overlay_visibility_changed.emit(True)
        elif not self._has_completed_onboarding:
            self._has_completed_onboarding = True
            self._settings.setValue("hasCompletedOnboarding", True)
            self.overlay.show_overlay()
            self._is_overlay_visible = True
            self.overlay_visibility_changed.emit(True)

    def stop(self):
        self.hotkey.stop()
        self.audio.stop()
        self.overlay.hide_overlay()
        self._perm_timer.stop()
        self._tts_poll_timer.stop()
        if self._current_response_task:
            self._current_response_task.cancel()
        if self.folder_watcher:
            self.folder_watcher.stop()
        if self.task_scheduler:
            self.task_scheduler.stop()
        # Stop v3 subsystems
        self._ambient_context.stop()
        self._clipboard_monitor.stop()
        self._health_monitor.stop()
        self._watchdog.stop()

    def _start_subsystems(self):
        """Start all background subsystems."""
        try:
            # Note: _ambient_context is intentionally started lazily upon first user interaction
            # to prevent UIA initialization blocking the startup sequence.
            self._clipboard_monitor.start()
            self._health_monitor.start()
            self._watchdog.start()

            # Register components with watchdog
            self._watchdog.register("ambient_context", heartbeat_interval=35,
                                    restart_fn=self._ambient_context.start)
            self._watchdog.register("clipboard_monitor", heartbeat_interval=10,
                                    restart_fn=self._clipboard_monitor.start)
            self._watchdog.register("health_monitor", heartbeat_interval=15,
                                    restart_fn=self._health_monitor.start)

            # Wire TTS callbacks (thread-safe — schedules onto event loop)
            def tts_notify(text):
                loop = self._event_loop
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(
                        lambda t=text: create_tracked_task(self.tts.speak_text(t), "tts_notify")
                    )

            self._health_monitor.set_tts_callback(tts_notify)
            self._watchdog.set_tts_callback(tts_notify)
            
            # Wire BrowserController TTS callback
            from browser_controller import get_browser_controller
            get_browser_controller(tts_notify)

            # Clipboard suggestions
            self._clipboard_monitor.set_suggestion_callback(tts_notify)

            # DB maintenance
            self._db.maybe_vacuum()

            logger.info("Subsystems started: ambient, clipboard, health, watchdog")
        except Exception as e:
            logger.error(f"Subsystem startup error: {e}")

    # ─── Phase 5 Hotkey Handlers ──────────────────────────────────────

    def _on_visual_finder_hotkey(self):
        """Ctrl+Shift+V — Trigger visual finder with clipboard contents."""
        self._ambient_context.start()
        
        import pyperclip
        target_description = pyperclip.paste().strip()
        
        if not target_description:
            logger.info("VisualFinder: clipboard is empty. Skipping.")
            create_tracked_task(self.tts.speak_text("Clipboard is empty. Copy description to find."), "vf_empty_tts")
            return
            
        logger.info(f"VisualFinder: hotkey triggered for target '{target_description}'")
        create_tracked_task(self.tts.speak_text(f"Searching for {target_description} on screen"), "vf_start_tts")
        
        async def run_visual_finder():
            from visual_finder import VisualFinder
            from groq_router import get_router
            import asyncio
            
            finder = VisualFinder(llm_client=get_router())
            loop = asyncio.get_event_loop()
            
            # Execute vision LLM lookup in background thread pool to prevent GUI freeze
            result = await loop.run_in_executor(None, finder.locate, target_description, False)
            
            # Speak visual finder text result
            await self.tts.speak_text(result['message'])
            
        create_tracked_task(run_visual_finder(), "visual_finder_hotkey_task")

    def _on_focus_mode_hotkey(self):
        """Ctrl+Shift+F — Toggle focus mode."""
        self._ambient_context.start()
        from focus_mode import get_focus_mode
        fm = get_focus_mode()
        if fm.is_active():
            fm.deactivate()
        else:
            fm.activate(25)

    def _on_screen_read_hotkey(self):
        """Ctrl+Shift+S — Toggle screen reading."""
        self._ambient_context.start()
        from screen_reader_mode import get_screen_reader
        reader = get_screen_reader()
        if reader.is_reading():
            reader.stop_reading()
        else:
            def tts_cb(text):
                create_tracked_task(self.tts.speak_text(text), "screen_read_tts")
            reader.set_tts_callback(tts_cb)
            reader.start_reading()

    def _on_health_check_hotkey(self):
        """Ctrl+Shift+H — Health check report."""
        self._ambient_context.start()
        summary = self._health_monitor.get_health_summary_text()
        create_tracked_task(self.tts.speak_text(summary), "health_check_tts")

    def _on_macro_record_hotkey(self):
        """Ctrl+Shift+R — Toggle macro recording."""
        self._ambient_context.start()
        if self.macro_recorder and hasattr(self.macro_recorder, 'is_recording') and self.macro_recorder.is_recording:
            self.macro_recorder.stop_recording()
            create_tracked_task(self.tts.speak_text("Macro recording stopped."), "macro_stop_tts")
        else:
            create_tracked_task(self.tts.speak_text("Macro recording is not yet active."), "macro_inactive_tts")

    def _on_save_workspace_hotkey(self):
        """Ctrl+Shift+W — Save current workspace."""
        self._ambient_context.start()
        from workspace_manager import get_workspace_manager
        wm = get_workspace_manager()
        summary = wm.save_workspace("quick_save")
        create_tracked_task(self.tts.speak_text(summary), "workspace_save_tts")

    # ─── System 1 & 2 Hotkey Handlers ─────────────────────────────────

    def _on_ui_guide_hotkey(self):
        """Ctrl+Shift+G — Prompt user for what to guide to."""
        self._ambient_context.start()
        if self.audio.is_recording:
            return
        self._active_mode = "ai"
        self.tts.stop_playback()
        self._set_voice_state("listening")
        self.audio.start()
        create_tracked_task(self.tts.speak_text("What are you looking for?"), "ui_guide_prompt")
        create_tracked_task(self._start_transcription(), "hotkey_transcription")

    def _on_ui_explain_hotkey(self):
        """Ctrl+Shift+E — Explain element at cursor."""
        self._ambient_context.start()
        async def explain_task():
            self._set_voice_state("processing")
            explanation = await self.ui_guidance.explain_element_at_cursor()
            create_tracked_task(self.tts.speak_text(explanation), "ui_explain_tts")
            self._set_voice_state("idle")
        create_tracked_task(explain_task(), "ui_explain_hotkey_task")

    def _on_ui_tour_hotkey(self):
        """Ctrl+Shift+T — Give an app tour."""
        self._ambient_context.start()
        async def tour_task():
            self._set_voice_state("processing")
            ctx = self._ambient_context.get_current_context()
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            regions = await self.ui_guidance.app_tour(ctx.active_app, hwnd)
            if not regions:
                create_tracked_task(self.tts.speak_text("I couldn't identify any major regions in this app."), "ui_tour_fail")
            else:
                for r in regions:
                    self.overlay.pulse_at(r["screen_x"], r["screen_y"], 2000)
                    self.overlay.point_and_label(r["screen_x"], r["screen_y"], r["name"])
                    await self.tts.speak_text(f"{r['name']}: {r['purpose']}")
            self._set_voice_state("idle")
        create_tracked_task(tour_task(), "ui_tour_hotkey_task")

    def _on_linux_mode_hotkey(self):
        """Ctrl+Shift+L — Toggle Linux mode context."""
        self._ambient_context.start()
        self.set_linux_mode(not self._linux_mode_active)

    def set_linux_mode(self, enabled: bool):
        self._linux_mode_active = enabled
        self._ambient_context.linux_mode_active = enabled
        msg = "Linux mode enabled. I will now track your WSL context." if enabled else "Linux mode disabled."
        create_tracked_task(self.tts.speak_text(msg), "linux_toggle_tts")
        # Ensure we have the signal to sync UI state
        if hasattr(self, 'linux_mode_changed'):
            self.linux_mode_changed.emit(enabled)

    def _on_linux_ambient_error(self, error_signature: str):
        """Called by ambient context when a Linux error is detected."""
        if getattr(self, "_last_linux_error_prompt_time", 0) > time.monotonic() - 60:
            return  # Throttle to once per minute
        self._last_linux_error_prompt_time = time.monotonic()
        
        msg = f"I noticed a {error_signature} in your terminal. Want me to take a look?"
        create_tracked_task(self.tts.speak_text(msg), "linux_error_prompt")

    def _set_voice_state(self, state: str):
        self._voice_state = state
        self.voice_state_changed.emit(state)
        if state == "listening" and getattr(self, "_active_mode", "") == "dictation":
            self.overlay.set_voice_state("dictating")
        else:
            self.overlay.set_voice_state(state)

    # ─── Shortcut Handlers ────────────────────────────────────────────

    def _on_shortcut_pressed(self):
        if self.audio.is_recording:
            return
        logger.info("PTT: AI pressed")
        self._active_mode = "ai"
        ClickyAnalytics.track_push_to_talk_started()
        
        # Start ambient context on first interaction to lazily load UIA
        self._ambient_context.start()
        if self._current_response_task:
            self._current_response_task.cancel()
            self._current_response_task = None
        self.tts.stop_playback()
        self.overlay.cancel_navigation()
        if not self._is_overlay_visible:
            self.overlay.show_overlay()
            self._is_overlay_visible = True
        self._set_voice_state("listening")
        self.audio.start()
        create_tracked_task(self._start_transcription(), "hotkey_transcription")

    def _on_shortcut_released(self):
        if self._active_mode != "ai":
            return
        logger.info("PTT: AI released")
        ClickyAnalytics.track_push_to_talk_released()
        self.audio.stop()
        self._set_voice_state("processing")
        if self._active_session:
            create_tracked_task(self._active_session.request_final_transcript(), "hotkey_final_transcript")

    def _on_dictation_pressed(self):
        if self.audio.is_recording:
            return
        logger.info("PTT: Dictation pressed")
        self._active_mode = "dictation"
        
        # Start ambient context on first interaction to lazily load UIA
        self._ambient_context.start()
        if self._current_response_task:
            self._current_response_task.cancel()
            self._current_response_task = None
        self.tts.stop_playback()
        self.overlay.cancel_navigation()
        if not self._is_overlay_visible:
            self.overlay.show_overlay()
            self._is_overlay_visible = True
        self._set_voice_state("listening")
        self.audio.start()
        create_tracked_task(self._start_transcription(), "silent_transcription")

    def _on_proactive_suggestion(self, seq, hash_key, desc):
        """Callback from SuggestionEngine."""
        self._pending_suggestion_sequence = seq
        self._pending_suggestion_hash = hash_key
        msg = f"I noticed you frequently do {desc} in sequence. Would you like me to create an automated macro for this?"
        
        def do_speak():
            create_tracked_task(self.tts.speak_text(msg), "suggestion_tts")
            self._set_voice_state("listening")
            self.audio.start()
            create_tracked_task(self._start_transcription(), "suggestion_transcription")
            
        # Run on main thread
        loop = self._event_loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(do_speak)

    def _on_dictation_released(self):
        if self._active_mode != "dictation":
            return
        logger.info("PTT: Dictation released")
        self.audio.stop()
        self._set_voice_state("processing")
        if self._active_session:
            create_tracked_task(self._active_session.request_final_transcript(), "silent_final_transcript")

    def _on_audio_buffer(self, pcm16_data: bytes):
        if self._active_session:
            self._active_session.append_audio_buffer(pcm16_data)

    def _on_audio_power(self, level: float):
        self.audio_power_changed.emit(level)
        self.overlay.set_audio_power(level)

    # ─── Transcription ────────────────────────────────────────────────

    async def _start_transcription(self):
        if not self._transcription_provider:
            logger.error("No transcription provider")
            self._set_voice_state("idle")
            return
        try:
            self._active_session = await self._transcription_provider.start_streaming_session(
                keyterms=["Clicky", "Claude", "Anthropic", "Windows"],
                on_transcript_update=self._on_transcript_update,
                on_final_transcript_ready=self._on_final_transcript,
                on_error=self._on_transcription_error,
            )
        except Exception as e:
            logger.error(f"Failed to start transcription: {e}")
            self._set_voice_state("idle")

    def _on_transcript_update(self, text: str):
        self._last_transcript = text

    def _on_final_transcript(self, text: str):
        # ─── Fix 1: Context-Gated STT Correction Layer ───────────────────────
        corrections = self._db.get_stt_corrections()
        if corrections:
            import re
            from constants import STT_ACTION_CONTEXT_WORDS, STT_SKIP_CONTEXT_PHRASES
            for misheard, info in corrections.items():
                correct = info["correct"]
                context = info.get("context")  # list of context words or None
                pattern = re.compile(r'\b' + re.escape(misheard) + r'\b', re.IGNORECASE)
                
                def _context_replace(match):
                    start = match.start()
                    # Get the two words before the match
                    prefix_text = text[:start].strip().lower()
                    preceding_words = prefix_text.split()[-2:] if prefix_text else []
                    
                    # Edge case: word at very beginning of transcript
                    # Default to applying the correction (likely a noun/app name)
                    if not preceding_words:
                        return correct
                    
                    # Check if preceded by a skip-phrase (non-action context)
                    prefix_two = " ".join(preceding_words[-2:]) if len(preceding_words) >= 2 else preceding_words[-1]
                    for skip_phrase in STT_SKIP_CONTEXT_PHRASES:
                        if prefix_two.endswith(skip_phrase) or skip_phrase in prefix_two:
                            return match.group(0)  # Don't correct
                    
                    # Check if preceded by action-suggesting words
                    if any(w in STT_ACTION_CONTEXT_WORDS for w in preceding_words):
                        return correct
                    
                    # No strong signal either way — apply the correction
                    return correct
                
                text = pattern.sub(_context_replace, text)

        self._last_transcript = text
        self.transcript_received.emit(text)
        logger.info(f"Transcript: {text}")

        if not text.strip():
            self._set_voice_state("idle")
            self._active_mode = "none"
            create_tracked_task(self.tts.speak_text("I didn't catch that, please try again."), "empty_stt_tts")
            return

        # Update ambient context with conversation
        self._ambient_context.update_conversation("user", text)

        if getattr(self, "_active_mode", "") == "dictation":
            import pyautogui
            logger.info("Dictation: typing text out")
            self._set_voice_state("responding")
            def do_type():
                pyautogui.write(text, interval=0.01)
                self._set_voice_state("idle")
                self._active_mode = "none"
                if not self._is_cursor_enabled and self._is_overlay_visible:
                    QTimer.singleShot(1000, self._maybe_hide_overlay)
            QTimer.singleShot(100, do_type)
        else:
            self._process_user_message(text, silent=False)

    def _split_sequential_commands(self, text: str) -> list[str]:
        """Split a command into sequential parts using 'and', 'then', 'next', etc."""
        import re
        from intent_router import ACTION_KEYWORDS
        
        # Split on delimiters while keeping them in the resulting list
        parts = re.split(r'\b(and\s+then|then|after\s+that|next|and)\b', text, flags=re.IGNORECASE)
        
        commands = [parts[0].strip()]
        
        for i in range(1, len(parts), 2):
            delim = parts[i].lower()
            next_part = parts[i+1].strip()
            
            if not next_part:
                continue
                
            if delim == "and":
                # Conditionally split on 'and'
                left_side = commands[-1].lower()
                right_side = next_part.lower()
                
                left_has_action = any(re.search(r'\b' + kw + r'\b', left_side) for kw in ACTION_KEYWORDS)
                right_has_action = any(re.search(r'\b' + kw + r'\b', right_side) for kw in ACTION_KEYWORDS)
                right_word_count = len(right_side.split())
                
                if left_has_action and right_has_action and right_word_count > 1:
                    commands.append(next_part)
                else:
                    commands[-1] = f"{commands[-1]} and {next_part}"
            else:
                # Unconditional split for other delimiters
                commands.append(next_part)
                
        return [c for c in commands if c]

    def _process_user_message(self, text: str, silent: bool):
        """Unified message processing for voice and silent mode."""
        
        # ─── Fix 3: Reconstitute pending action with expiry ─────────────────
        if self._pending_action_context:
            import time as _time
            from constants import ACTION_KEYWORDS as _ak
            created_at = self._pending_action_context.get("created_at", 0)
            elapsed = _time.time() - created_at
            
            # Condition 1: discard if older than 30 seconds
            if elapsed > 30:
                logger.info(f"Pending action expired ({elapsed:.0f}s > 30s), discarding")
                self._pending_action_context = {}
            # Condition 2: discard if new transcript contains action keywords
            elif any(kw in text.lower().split() for kw in _ak):
                logger.info(f"New transcript contains action keyword, treating as new command")
                self._pending_action_context = {}
            else:
                # Both conditions pass — reconstitute
                action_type = self._pending_action_context.get("action_type")
                prefix = self._pending_action_context.get("prefix", "search for")
                logger.info(f"Reconstituting pending action '{action_type}' with new input: '{text}'")
                text = f"{prefix} {text}"
                self._pending_action_context = {}
            
        # ─── Fix 2: STT Correction 'Remember' Intent ─────────────────────────
        import re
        m = re.match(r"(?:remember\s+(?:that\s+)?)?when\s+i\s+say\s+[\"']?(.+?)[\"']?\s+(?:you\s+should\s+hear|it\s+means|change\s+it\s+to)\s+[\"']?(.+?)[\"']?$", text.strip(), re.IGNORECASE)
        if m:
            misheard = m.group(1).strip()
            correct = m.group(2).strip()
            if self._db.add_stt_correction(misheard, correct):
                create_tracked_task(self.tts.speak_text(f"Got it. When you say {misheard}, I will hear {correct}."), "stt_correct_ack")
            return
        
        # Handle pending suggestion confirmation
        if self._pending_suggestion_sequence:
            text_lower = text.lower().strip()
            if "yes" in text_lower or "yeah" in text_lower or "sure" in text_lower or "do it" in text_lower:
                seq = self._pending_suggestion_sequence
                hash_key = self._pending_suggestion_hash
                self._db.save_suggestion_outcome(hash_key or "", " then ".join(seq), True)
                
                msg = "Great! Let me write that macro for you..."
                create_tracked_task(self.tts.speak_text(msg), "macro_gen_tts")
                
                # Generate in background
                def gen_macro():
                    from suggestion_engine import get_suggestion_engine
                    macro_json = get_suggestion_engine().generate_macro_definition(seq)
                    if macro_json:
                        import json
                        try:
                            # Try parsing to validate
                            parsed = json.loads(macro_json)
                            # Let's save it directly using the macro recorder logic
                            if "name" in parsed and "actions" in parsed:
                                self._db.save_macro(
                                    name=parsed["name"],
                                    actions=parsed["actions"],
                                    description=parsed.get("description", "Auto-generated macro")
                                )
                                done_msg = f"I have saved a new macro named '{parsed['name']}'."
                                loop = self._event_loop
                                if loop and loop.is_running():
                                    loop.call_soon_threadsafe(
                                        lambda: create_tracked_task(self.tts.speak_text(done_msg), "macro_done_tts")
                                    )
                        except Exception as e:
                            logger.error(f"Failed to parse generated macro: {e}")
                            
                import threading
                threading.Thread(target=gen_macro, daemon=True).start()
                
            elif "no" in text_lower or "nah" in text_lower or "stop" in text_lower or "cancel" in text_lower:
                self._db.save_suggestion_outcome(self._pending_suggestion_hash or "", " then ".join(self._pending_suggestion_sequence), False)
                create_tracked_task(self.tts.speak_text("Okay, I won't bother you about that sequence again."), "macro_cancel_tts")
            else:
                create_tracked_task(self.tts.speak_text("I didn't catch a clear yes or no. Canceling macro suggestion."), "macro_cancel_tts")

            self._pending_suggestion_sequence = None
            self._pending_suggestion_hash = None
            self._set_voice_state("idle")
            return

        # ─── Fix 3: Multi-step Delimiter Expansion ───────────────────────────
        parts = self._split_sequential_commands(text)
        
        if len(parts) > 1:
            logger.info(f"Multi-step command detected: {parts}")
            create_tracked_task(self._process_sequential_commands(parts, silent), "sequential_commands")
            return

        self._process_single_command(text, silent)

    async def _process_sequential_commands(self, parts: list[str], silent: bool):
        self._last_action_failed = False
        import asyncio
        for i, part in enumerate(parts):
            logger.info(f"Executing step {i+1}/{len(parts)}: {part}")
            
            self._process_single_command(part, silent)
            
            # Wait for AI response task to complete
            while self._current_response_task and not self._current_response_task.done():
                await asyncio.sleep(0.5)
                
            # Allow time for execution to finish
            await asyncio.sleep(1.0)
            
            if getattr(self, "_last_action_failed", False):
                logger.error(f"Step {i+1} failed, aborting sequence.")
                create_tracked_task(self.tts.speak_text(f"Step {i+1} failed. Stopping sequence."), "seq_fail_tts")
                break
                
        self._set_voice_state("idle")

    def _process_single_command(self, text: str, silent: bool):
        from browser_controller import get_browser_controller
        ctrl = get_browser_controller()
        current_url = ""
        is_browser_active = False

        if ctrl and ctrl._ready and ctrl._page and not ctrl._page.is_closed():
            is_browser_active = True
            current_url = ctrl._page.url

        # Try local intent classification first (zero API cost)
        intent = self._intent_router.classify(text, current_url=current_url, is_browser_active=is_browser_active)

        if intent.task_type == TaskType.DIRECT_ACTION and intent.action_tag and intent.confidence > 0.8:
            # Direct action — execute locally without AI call
            logger.info(f"IntentRouter: direct action {intent.action_tag}")
            self._execute_intent_action(intent)
        elif intent.confidence < 0.3 and not any(k in text.lower() for k in ["screen", "see", "look", "show", "image", "what's on", "read screen"]):
            # Fix 1: Below threshold, ask for clarification instead of guessing with vision
            msg = "I'm not sure what you mean. Could you rephrase?"
            if silent and self.chat_overlay.is_open():
                self.chat_overlay.show_response(msg)
            else:
                create_tracked_task(self.tts.speak_text(msg), "clarify_tts")
            self._set_voice_state("idle")
        else:
            # Route to AI with enriched context
            ClickyAnalytics.track_user_message_sent(text)
            self._current_response_task = create_tracked_task(self._send_to_ai(text, silent=silent), "send_to_ai")

    def _execute_intent_action(self, intent):
        """Execute a locally-classified direct action."""
        from executors import execute_registered_action
        import re

        tag = intent.action_tag  # e.g. "[RUN:notepad]"
        match = re.match(r'\[(\w+):(.*?)\]', tag)
        if not match:
            # Fallback to AI
            self._current_response_task = create_tracked_task(self._send_to_ai(intent.raw_text), "intent_fallback_ai")
            return

        action_type = match.group(1)
        params = match.group(2)

        # ─── Fix 4: Incomplete Command Catch ─────────────────────────────────
        if action_type in ("BROWSER_SEARCH", "SITE_SEARCH", "RESEARCH"):
            query = params.split('|')[-1] if '|' in params else params
            query = query.strip()
            stop_words = {"for", "the", "a", "an", "to", "on", "in"}
            if not query or query.lower() in stop_words:
                import time as _time
                self._pending_action_context = {
                    "action_type": action_type,
                    "prefix": intent.raw_text.strip(),
                    "created_at": _time.time(),
                }
                create_tracked_task(self.tts.speak_text("What would you like to search for?"), "incomplete_command_tts")
                self._set_voice_state("idle")
                return

        # Trust check
        should_run, trust_level, reason = self.trust_engine.should_execute(action_type, params)

        if not should_run:
            # Needs confirmation — use existing trust engine flow
            from actions import parse_actions
            spoken_text, pending = parse_actions(tag, self.trust_engine)
            if pending:
                self._execute_pending_actions(pending)
                create_tracked_task(self.tts.speak_text(f"Running {action_type.lower()}."), "action_confirm_tts")
            self._set_voice_state("idle")
        else:
            # Safe action — execute immediately
            result = execute_registered_action(action_type, params)
            response_text = result.message if result.message else "Done."
            create_tracked_task(self.tts.speak_text(response_text), "action_result_tts")
            self._set_voice_state("idle")

            # Log
            self._db.log_action(action_type, params, trust_level.value,
                                "ok" if result.success else "fail")

    def _on_transcription_error(self, error):
        logger.error(f"Transcription error: {error}")
        self._set_voice_state("idle")

    # ─── AI Response Pipeline ─────────────────────────────────────────

    async def _send_to_ai(self, transcript: str, silent: bool = False):
        self._set_voice_state("processing")

        # Check for pending browser login
        from browser_controller import get_browser_controller
        ctrl = get_browser_controller()
        if ctrl and getattr(ctrl, "_login_done_event", None) and not ctrl._login_done_event.is_set():
            if transcript.strip().lower() in ("done", "done.", "ok", "ready", "yes"):
                ctrl.signal_login_done()
                self._set_voice_state("idle")
                if silent and self.chat_overlay.is_open():
                    self.chat_overlay.show_response("Login confirmed. Proceeding...")
                else:
                    await self.tts.speak_text("Login confirmed. Proceeding...")
                return

        # Use a reading-optimized prompt in silent mode
        prompt = SILENT_PROMPT if silent else SYSTEM_PROMPT

        try:
            screen_captures = capture_all_screens()
            images = []
            for cap in screen_captures:
                dim_info = f" (image dimensions: {cap.screenshot_width}x{cap.screenshot_height} pixels)"
                images.append((cap.image_data, cap.label + dim_info))

            history = list(self._conversation_history)

            # Enrich transcript with ambient context
            enriched_transcript = transcript
            try:
                ctx = self._ambient_context.get_current_context()
                ctx_str = ctx.to_context_string()
                
                # Check terminal state for Linux mode auto-routing
                is_terminal = ctx.active_app.lower() in ("cmd.exe", "windowsterminal.exe", "powershell.exe", "wsl.exe", "ubuntu.exe")
                
                if self._linux_mode_active:
                    wsl_ctx = self.linux_assistant.get_wsl_context()
                    ctx_str += f" | [WSL Context] Distro: {wsl_ctx.get('distro')} | User: {wsl_ctx.get('whoami')} | PWD: {wsl_ctx.get('pwd')}"

                if ctx_str:
                    enriched_transcript = f"{ctx_str}\n\nUser said: {transcript}"
                    
                # Inject intent route if found
                intent = self._intent_router.classify(transcript, current_url="", is_terminal=is_terminal)
                if intent.task_type == TaskType.DIRECT_ACTION and intent.action_tag:
                    enriched_transcript += f"\n\nSystem: Detected implicit user intent: {intent.action_tag}. Process this action."
            except Exception as e:
                logger.debug(f"Context enrichment failed: {e}")

            # Prepend knowledge base context if relevant
            try:
                from knowledge_base import get_knowledge_base
                kb = get_knowledge_base()
                kb_context = kb.get_context_for_query(transcript)
                if kb_context:
                    enriched_transcript = f"{kb_context}\n\n{enriched_transcript}"
            except Exception:
                pass

            if self._ai_provider == "groq":
                from groq_router import get_router, RoutedRequest, Priority
                router = get_router()
                
                b64_image = None
                if images:
                    import base64
                    img_data = images[0][0] if isinstance(images[0], tuple) else None
                    if img_data:
                        b64_image = base64.b64encode(img_data).decode('ascii')

                req = RoutedRequest(
                    system_prompt=prompt,
                    user_prompt=enriched_transcript,
                    task_type=TaskType.VISION_TASK if b64_image else TaskType.SIMPLE_QUESTION,
                    priority=Priority.URGENT,
                    max_tokens=getattr(config, "GROQ_MAX_TOKENS", 1024),
                    image_b64=b64_image,
                    history=history
                )
                
                tts_queue = asyncio.Queue(maxsize=10)
                playback_task = None
                
                if not silent:
                    async def tts_worker():
                        while True:
                            sentence = await tts_queue.get()
                            if sentence is None:  # Sentinel
                                tts_queue.task_done()
                                break
                            try:
                                await self.tts.speak_text(sentence)
                            except Exception as e:
                                logger.error(f"TTS stream error: {e}")
                            tts_queue.task_done()
                            
                    playback_task = create_tracked_task(tts_worker(), "tts_stream_worker")
                    self._set_voice_state("responding")
                    
                full_response = ""
                duration = 0
                sentence_buffer = ""
                _stream_stalled = False
                
                import re
                abbrev_pattern = re.compile(r'\b(?:e\.g\.|i\.e\.|vs\.|dr\.|mr\.|mrs\.|ms\.|prof\.|sr\.|jr\.|inc\.|ltd\.|etc\.)\s*$', re.IGNORECASE)

                # Fix 1 + Fix 4: Streaming with inactivity watchdog + hard timeout
                _last_chunk_time = asyncio.get_event_loop().time()

                async def _inactivity_watchdog():
                    """Fix 4: Cancel stream if no chunk arrives for 10 seconds."""
                    nonlocal _stream_stalled
                    while True:
                        await asyncio.sleep(2)
                        elapsed = asyncio.get_event_loop().time() - _last_chunk_time
                        if elapsed > 10:
                            logger.warning(f"Streaming watchdog: no chunk for {elapsed:.1f}s, treating as stalled")
                            _stream_stalled = True
                            return

                async def _stream_groq():
                    """Inner coroutine for the streaming loop."""
                    nonlocal full_response, duration, sentence_buffer, _last_chunk_time

                    async for chunk in router.async_chat_stream(req):
                        _last_chunk_time = asyncio.get_event_loop().time()
                        if isinstance(chunk, str):
                            full_response += chunk
                            if not silent:
                                sentence_buffer += chunk
                                if any(p in sentence_buffer for p in ['. ', '? ', '! ', '.\n', '?\n', '!\n']):
                                    matches = list(re.finditer(r'([.?!])(?:\s+|\n)', sentence_buffer))
                                    if matches:
                                        last_match = matches[-1]
                                        end_idx = last_match.end()
                                        candidate = sentence_buffer[:end_idx]
                                        
                                        prefix = sentence_buffer[:last_match.start() + 1]
                                        if abbrev_pattern.search(prefix):
                                            continue 
                                            
                                        clean_sentence = re.sub(r'\[.*?\]', '', candidate).strip()
                                        if clean_sentence:
                                            await tts_queue.put(clean_sentence)
                                        sentence_buffer = sentence_buffer[end_idx:]
                                
                                # Fix 10: Max buffer length dispatch (200 chars)
                                if len(sentence_buffer) > 200:
                                    last_space = sentence_buffer.rfind(' ')
                                    if last_space > 0:
                                        overflow = sentence_buffer[:last_space].strip()
                                        clean_overflow = re.sub(r'\[.*?\]', '', overflow).strip()
                                        if clean_overflow:
                                            await tts_queue.put(clean_overflow)
                                        sentence_buffer = sentence_buffer[last_space + 1:]
                        else:
                            duration = chunk.duration_ms / 1000.0

                # Run streaming + watchdog concurrently, wrapped in 30s hard timeout
                watchdog_task = asyncio.ensure_future(_inactivity_watchdog())
                stream_task = asyncio.ensure_future(_stream_groq())

                try:
                    # Fix 1: Hard 30-second timeout on the entire streaming operation
                    done, pending = await asyncio.wait_for(
                        asyncio.wait({stream_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED),
                        timeout=30.0
                    )

                    # If watchdog fired first (stalled stream), cancel the stream task
                    if _stream_stalled and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        logger.info("Streaming watchdog: stream cancelled due to inactivity")

                    # If stream finished first, cancel the watchdog
                    if not watchdog_task.done():
                        watchdog_task.cancel()
                        try:
                            await watchdog_task
                        except (asyncio.CancelledError, Exception):
                            pass

                    # Re-raise any exception from the stream task
                    if stream_task.done() and stream_task.exception():
                        raise stream_task.exception()

                except asyncio.TimeoutError:
                    # Hard 30s timeout — cancel both tasks
                    for t in (stream_task, watchdog_task):
                        if not t.done():
                            t.cancel()
                            try:
                                await t
                            except (asyncio.CancelledError, Exception):
                                pass
                    raise  # Re-raise to be caught by outer handler

                # Flush remaining sentence buffer
                if not silent and sentence_buffer.strip():
                    clean_sentence = re.sub(r'\[.*?\]', '', sentence_buffer).strip()
                    if clean_sentence:
                        await tts_queue.put(clean_sentence)
                        
                if not silent and playback_task:
                    await tts_queue.put(None)
                    await playback_task
                    self._set_voice_state("idle")
                    
            else:
                full_response, duration = await self.ai_client.analyze_image_streaming(
                    images=images,
                    system_prompt=prompt,
                    conversation_history=history,
                    user_prompt=enriched_transcript,
                )

            # Parse POINT tag
            result = parse_pointing_coordinates(full_response)
            spoken_text = result.spoken_text

            # Parse ACTION tags with trust classification
            from actions import parse_actions
            spoken_text, pending_actions = parse_actions(spoken_text, self.trust_engine)

            # Handle element pointing (works in both modes)
            if result.coordinate:
                self._set_voice_state("idle")
                px, py = result.coordinate
                target_cap = None
                if result.screen_number and 1 <= result.screen_number <= len(screen_captures):
                    target_cap = screen_captures[result.screen_number - 1]
                else:
                    target_cap = next((c for c in screen_captures if c.is_cursor_screen), None)
                if target_cap:
                    sx, sy = map_screenshot_to_screen_coordinates(
                        px, py,
                        target_cap.screenshot_width, target_cap.screenshot_height,
                        target_cap.display_x, target_cap.display_y,
                        target_cap.display_width, target_cap.display_height,
                    )
                    self.overlay.navigate_to_element(sx, sy, result.element_label or "")
                    ClickyAnalytics.track_element_pointed(result.element_label)

            # Save to history
            self._conversation_history.append((transcript, spoken_text))
            if len(self._conversation_history) > getattr(config, "MAX_CONVERSATION_HISTORY", 50):
                self._conversation_history = self._conversation_history[-getattr(config, "MAX_CONVERSATION_HISTORY", 50):]

            ClickyAnalytics.track_ai_response_received(spoken_text)

            if silent:
                if self.chat_overlay.is_open():
                    self.chat_overlay.show_response(spoken_text)
                self._set_voice_state("idle")
            else:
                if self._ai_provider != "groq" and spoken_text.strip():
                    try:
                        await self.tts.speak_text(spoken_text)
                        self._set_voice_state("responding")
                    except Exception as e:
                        logger.error(f"TTS error: {e}")
                        self._set_voice_state("idle")

            # Execute actions with trust-aware confirmation
            if pending_actions:
                self._execute_pending_actions(pending_actions)

        except asyncio.CancelledError:
            logger.info("_send_to_ai: cancelled")
            self._set_voice_state("idle")
        except asyncio.TimeoutError:
            # Fix 1: Hard 30s timeout on AI calls
            logger.warning("_send_to_ai: timed out after 30s")
            self._set_voice_state("idle")
            if silent and self.chat_overlay.is_open():
                self.chat_overlay.show_response("Request timed out. Please try again.")
            else:
                create_tracked_task(self.tts.speak_text("That took too long, please try again."), "timeout_tts")
        except Exception as e:
            logger.error(f"Response error: {e}")
            ClickyAnalytics.track_response_error(str(e))
            self._set_voice_state("idle")
            if silent and self.chat_overlay.is_open():
                self.chat_overlay.show_response(f"something went wrong: {e}")

    def _execute_pending_actions(self, actions: list):
        """Execute parsed ACTION tags with trust-level-aware confirmation."""
        from action_confirm_dialog import confirm_action, show_blocked_action
        from actions import execute_action

        def _run_actions():
            context_results = []

            for action in actions:
                can_exec, trust, reason = self.trust_engine.should_execute(
                    action.action_type, action.params
                )

                if not can_exec:
                    show_blocked_action(action.display_text, reason)
                    self.trust_engine.log_execution(
                        action.action_type, action.params, trust, "blocked"
                    )
                    continue

                # Show confirmation if needed
                if trust in (TrustLevel.CONFIRM_ONCE, TrustLevel.ALWAYS_CONFIRM):
                    if not confirm_action(action.display_text, trust):
                        logger.info(f"User denied action: {action.action_type}")
                        self.trust_engine.log_execution(
                            action.action_type, action.params, trust, "denied"
                        )
                        break

                    # Remember approval for CONFIRM_ONCE
                    if trust == TrustLevel.CONFIRM_ONCE:
                        self.trust_engine.record_approval(action.action_type, action.params)

                # Execute
                result = execute_action(action)
                self.trust_engine.log_execution(
                    action.action_type, action.params,
                    trust if trust != TrustLevel.SILENT else TrustLevel.SILENT,
                    "success" if result.success else f"failed: {result.message}",
                )

                if not result.success:
                    self._last_action_failed = True
                    if action.action_type.startswith("BROWSER_") or action.action_type == "SITE_SEARCH" or action.action_type == "RUN":
                        logger.error(f"Action {action.action_type} failed: {result.message}. Aborting action chain.")
                        msg = f"Task aborted because {action.action_type.lower()} failed: {result.message}"
                        if getattr(self, "_is_silent_mode", False) and self.chat_overlay.is_open():
                            self.chat_overlay.show_response(msg)
                        else:
                            create_tracked_task(self.tts.speak_text(msg), "abort_tts")
                        break

                # Optional: slight delay between successful browser actions to ensure UI is ready
                if action.action_type in ("BROWSER_NAVIGATE", "SITE_SEARCH"):
                    import time
                    time.sleep(1.0)

                # Record in macro if recording
                if self.macro_recorder and self.macro_recorder.is_recording:
                    self.macro_recorder.record_step(action.action_type, action.params)

                # Collect context injection results
                if result.inject_context and result.data:
                    context_results.append((result.context_label, result.data))

            # If any actions produced context, feed back to AI
            if context_results:
                self._inject_context_and_respond(context_results)

        QTimer.singleShot(500, _run_actions)

    def _inject_context_and_respond(self, context_items: list[tuple[str, str]]):
        """Feed action results back into the AI for follow-up response."""
        context_parts = []
        for label, data in context_items:
            if isinstance(data, bytes):
                context_parts.append(f"[{label}: binary data, {len(data)} bytes]")
            else:
                context_parts.append(f"[{label}]\n{data}")

        combined = "\n\n".join(context_parts)
        follow_up = f"Here is the result of the action(s) I just performed:\n\n{combined}\n\nPlease analyze and respond to this."

        self._current_response_task = create_tracked_task(
            self._send_to_ai(follow_up, silent=getattr(self, "_is_silent_mode", False)), "context_inject_ai"
        )

    # ─── Automation Event Handlers ────────────────────────────────────

    def _on_folder_event(self, event):
        """Handle folder watcher events."""
        logger.info(f"Folder event: {event}")
        # Notify via TTS
        msg = f"hey, a file was {event.event_type} in the watched folder: {event.path}"
        create_tracked_task(self.tts.speak_text(msg), "folder_event_tts")

    def _on_scheduled_task(self, name: str, action_type: str, params: str):
        """Handle scheduled task triggers."""
        logger.info(f"Scheduled task fired: {name} → {action_type}:{params}")
        from actions import ParsedAction, execute_action
        action = ParsedAction(
            action_type=action_type, params=params,
            display_text=f"⏰ Scheduled: {name}", trust_level=TrustLevel.CONFIRM_ONCE,
        )
        result = execute_action(action)
        if result.inject_context and result.data:
            self._inject_context_and_respond([(result.context_label, result.data)])

    # ─── Silent Mode ──────────────────────────────────────────────────

    def _on_silent_mode_toggle(self):
        """Toggle silent mode: open/close the chat overlay."""
        # Start ambient context on first interaction to lazily load UIA
        self._ambient_context.start()
        
        if self.chat_overlay.is_open():
            self.chat_overlay.hide()
            self._is_silent_mode = False
            logger.info("Silent mode: OFF")
        else:
            # Cancel any voice activity
            if self._current_response_task:
                self._current_response_task.cancel()
                self._current_response_task = None
            self.tts.stop_playback()
            self._set_voice_state("idle")

            self._is_silent_mode = True
            self.chat_overlay.show_at_cursor()
            logger.info("Silent mode: ON (Ctrl+Shift+Alt)")

        self.silent_mode_changed.emit(self._is_silent_mode)

    def _on_silent_message(self, text: str):
        """Handle a typed message from the chat overlay."""
        logger.info(f"Silent message: {text}")
        self.chat_overlay.show_processing()
        self._process_user_message(text, silent=True)

    def _on_silent_closed(self):
        """Handle chat overlay being dismissed."""
        self._is_silent_mode = False
        self.silent_mode_changed.emit(False)
        logger.info("Silent mode: closed")

    def _cancel_current_operation(self):
        """Cancel the active AI task, stop TTS, and reset state to idle.
        Connected to chat_overlay.stop_requested signal."""
        logger.info("Cancel requested: cancelling current operation")
        # Cancel the active asyncio task
        if self._current_response_task:
            self._current_response_task.cancel()
            self._current_response_task = None
        # Stop any TTS playback
        self.tts.stop_playback()
        # Reset state
        self._set_voice_state("idle")
        # Update chat overlay if open
        if self.chat_overlay.is_open():
            self.chat_overlay._set_status("idle")

    def set_silent_mode(self, enabled: bool):
        """Programmatically enable/disable silent mode."""
        if enabled and not self.chat_overlay.is_open():
            self._on_silent_mode_toggle()
        elif not enabled and self.chat_overlay.is_open():
            self._on_silent_mode_toggle()

    # ─── TTS & Overlay ────────────────────────────────────────────────

    def _check_tts_finished(self):
        if self._voice_state == "responding" and not self.tts.is_playing:
            self._set_voice_state("idle")
            if not self._is_cursor_enabled and self._is_overlay_visible:
                QTimer.singleShot(1000, self._maybe_hide_overlay)

    def _maybe_hide_overlay(self):
        if not self._is_cursor_enabled and self._voice_state == "idle":
            self.overlay.hide_overlay()
            self._is_overlay_visible = False
            self.overlay_visibility_changed.emit(False)

    def _refresh_permissions(self):
        status = permissions.get_permission_status()
        self.permissions_changed.emit(status)
