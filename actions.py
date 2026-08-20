"""
actions.py — Action Parser & Dispatcher (v2)

Parses [ACTION:type:params] tags from AI responses and routes them
through the trust engine and executor registry. Replaces the original
hardcoded action system with a modular, extensible architecture.

Action flow:
  AI Response → parse_actions() → TrustEngine.should_execute()
    → SILENT: execute immediately
    → CONFIRM_ONCE: show dialog, remember for session
    → ALWAYS_CONFIRM: show dialog every time
    → BLOCKED: refuse with explanation
"""

import logging
import re
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass

import pyautogui

import config
from trust_engine import TrustEngine, TrustLevel
from executors import (
    ActionResult,
    get_action,
    execute_registered_action,
    load_all_executors,
)

logger = logging.getLogger(__name__)

# Safety: small delay before automation actions
pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True  # Move mouse to corner to abort


@dataclass
class ParsedAction:
    """A single parsed action from the AI response."""
    action_type: str    # SEARCH, OPEN, TYPE, HOTKEY, RUN, CLICK, SCROLL, etc.
    params: str         # The action parameter
    display_text: str   # Human-readable description for confirmation
    trust_level: TrustLevel = TrustLevel.ALWAYS_CONFIRM  # Populated by trust engine


# ─── Expanded action pattern ─────────────────────────────────────────
# Matches [ACTION_TYPE:params] for all registered + legacy action types
ALL_ACTION_TYPES = (
    # Legacy (Phase 0)
    "SEARCH|OPEN|TYPE|HOTKEY|RUN"
    # Screen Interaction (Phase 1)
    "|CLICK|SCROLL|DRAG|RIGHTCLICK|SCREENSHOT_REGION|SAVE_SCREENSHOT"
    # System & File (Phase 2)
    "|OPEN_FILE|CREATE_FILE|READ_FILE|WRITE_FILE|SEARCH_FILES|RUN_CMD"
    # Web & API (Phase 2)
    "|FETCH_URL|DOWNLOAD|SUMMARISE_PAGE"
    # Knowledge (Phase 3)
    "|EXPLAIN|TRANSLATE|GENERATE_CODE|QUIZ|STEP_GUIDE|SUMMARISE_SCREEN"
    # Communication (Phase 3)
    "|DRAFT_MESSAGE|READ_ALOUD|DICTATE"
    # Automation (Phase 4)
    "|RECORD_MACRO|PLAY_MACRO|STOP_RECORDING"
    "|WATCH_FOLDER|SCHEDULE_TASK|CHAIN"
    # App Lifecycle (Phase 6)
    "|CLOSE_APP|SWITCH_TO_APP|LIST_OPEN_APPS|RESTART_APP|APP_VOLUME"
    # Browser (Phase 7)
    "|BROWSER_SEARCH|BROWSER_NAVIGATE|BROWSER_CLICK|BROWSER_TYPE"
    "|BROWSER_READ|BROWSER_SCROLL|BROWSER_TAB|BROWSER_BACK|BROWSER_FORWARD"
    "|BROWSER_SCREENSHOT|BROWSER_FILL_FORM"
    # System 3 (Smart Transformer)
    "|TEXT_TRANSFORM"
    # Workspace
    "|SAVE_WORKSPACE|RESTORE_WORKSPACE|DELETE_WORKSPACE|LIST_WORKSPACES"
)

ACTION_PATTERN = re.compile(
    r'\[(?P<type>' + ALL_ACTION_TYPES + r'):(?P<params>[^\]]+)\]',
    re.IGNORECASE,
)

# Display prefixes for action types (used by confirmation dialog)
DISPLAY_PREFIXES = {
    # Legacy
    "SEARCH":           "🔍 Search",
    "OPEN":             "🌐 Open",
    "TYPE":             "⌨️ Type",
    "HOTKEY":           "⌨️ Press",
    "RUN":              "🚀 Launch",
    # Screen
    "CLICK":            "🖱️ Click",
    "SCROLL":           "🔄 Scroll",
    "DRAG":             "↔️ Drag",
    "RIGHTCLICK":       "🖱️ Right-Click",
    "SCREENSHOT_REGION":"📸 Screenshot",
    "SAVE_SCREENSHOT":  "📸 Save Screenshot",
    # File
    "OPEN_FILE":        "📂 Open File",
    "CREATE_FILE":      "📝 Create File",
    "READ_FILE":        "📖 Read File",
    "WRITE_FILE":       "💾 Write File",
    "TEXT_TRANSFORM":   "✍️ Transform Text",
    "SAVE_WORKSPACE":   "💾 Save Workspace",
    "RESTORE_WORKSPACE":"🔄 Restore Workspace",
    "DELETE_WORKSPACE": "🗑️ Delete Workspace",
    "LIST_WORKSPACES":  "📋 List Workspaces",
    "SEARCH_FILES":     "🔍 Search Files",
    "RUN_CMD":          "⚡ Run Command",
    # Web
    "FETCH_URL":        "🌐 Fetch URL",
    "DOWNLOAD":         "⬇️ Download",
    "SUMMARISE_PAGE":   "📄 Summarise",
    # Knowledge
    "EXPLAIN":          "💡 Explain",
    "TRANSLATE":        "🌍 Translate",
    "GENERATE_CODE":    "💻 Generate Code",
    "QUIZ":             "🧠 Quiz",
    "STEP_GUIDE":       "📋 Step Guide",
    "SUMMARISE_SCREEN": "📱 Summarise",
    # Communication
    "DRAFT_MESSAGE":    "✉️ Draft",
    "READ_ALOUD":       "🔊 Read Aloud",
    "DICTATE":          "🎤 Dictate",
    # Automation
    "RECORD_MACRO":     "⏺️ Record Macro",
    "PLAY_MACRO":       "▶️ Play Macro",
    "STOP_RECORDING":   "⏹️ Stop Recording",
    "WATCH_FOLDER":     "👁️ Watch Folder",
    "SCHEDULE_TASK":    "⏰ Schedule",
    "CHAIN":            "🔗 Chain Actions",
    # App Lifecycle
    "CLOSE_APP":        "❌ Close App",
    "SWITCH_TO_APP":    "🔄 Switch App",
    "LIST_OPEN_APPS":   "📋 List Apps",
    "RESTART_APP":      "🔄 Restart App",
    "APP_VOLUME":       "🔊 App Volume",
    # Browser
    "BROWSER_SEARCH":   "🔍 Web Search",
    "BROWSER_NAVIGATE": "🌐 Navigate",
    "BROWSER_CLICK":    "👆 Click",
    "BROWSER_TYPE":     "⌨️ Type",
    "BROWSER_READ":     "📖 Read Page",
    "BROWSER_SCROLL":   "📜 Scroll",
    "BROWSER_TAB":      "🗂️ Tabs",
    "BROWSER_BACK":     "⬅️ Back",
    "BROWSER_FORWARD":  "➡️ Forward",
    "BROWSER_SCREENSHOT":"📸 Screenshot",
    "BROWSER_FILL_FORM":"📝 Fill Form",
    # UI Guidance
    "GUIDE_TO":         "🎯 Guide To",
    "EXPLAIN_ELEMENT":  "💡 Explain UI",
    "GUIDED_CLICK":     "🖱️ Guided Click",
    "APP_TOUR":         "🗺️ App Tour",
    "REMEMBER_UI":      "💾 Remember UI",
    # Linux Assistant
    "LINUX_ASSIST":             "🐧 Linux Assist",
    "LINUX_INTERACTIVE_LESSON": "🎓 Linux Lesson",
    "LINUX_ERROR_EXPLAIN":      "⚠️ Explain Error",
    "LINUX_SUGGEST_COMMAND":    "💡 Suggest Cmd",
    "LINUX_EXPLAIN_COMMAND":    "📖 Explain Cmd",
}


def parse_actions(text: str, trust_engine: TrustEngine = None) -> tuple[str, list[ParsedAction]]:
    """
    Parse [ACTION:type:params] tags from AI response text.

    Args:
        text: Raw AI response text
        trust_engine: Optional TrustEngine for classifying trust levels

    Returns:
        (clean_text_without_tags, list_of_parsed_actions)
    """
    actions = []

    for match in ACTION_PATTERN.finditer(text):
        action_type = match.group("type").upper()
        params = match.group("params").strip()

        prefix = DISPLAY_PREFIXES.get(action_type, action_type)
        display = f'{prefix}: "{params}"' if len(params) < 80 else f'{prefix}: "{params[:77]}..."'

        # Classify trust level
        trust = TrustLevel.ALWAYS_CONFIRM
        if trust_engine:
            trust = trust_engine.get_trust_level(action_type, params)

        actions.append(ParsedAction(
            action_type=action_type,
            params=params,
            display_text=display,
            trust_level=trust,
        ))

    # Remove action tags from the spoken text
    clean_text = ACTION_PATTERN.sub("", text).strip()

    return clean_text, actions


def execute_action(action: ParsedAction) -> ActionResult:
    """
    Execute a single action through the executor registry.
    Falls back to legacy handlers for backward compatibility.
    """
    action_type = action.action_type.upper()

    # Check if a registered executor exists
    if get_action(action_type):
        result = execute_registered_action(action_type, action.params)
        logger.info(f"Action executed: {action_type} → {'✓' if result.success else '✗'} {result.message}")
        return result

    # Legacy fallback for the original 5 action types
    try:
        if action_type == "SEARCH":
            return _legacy_search(action.params)
        elif action_type == "OPEN":
            return _legacy_open(action.params)
        elif action_type == "TYPE":
            return _legacy_type(action.params)
        elif action_type == "HOTKEY":
            return _legacy_hotkey(action.params)
        elif action_type == "RUN":
            return _legacy_run(action.params)
        else:
            logger.warning(f"Unknown action type: {action_type}")
            return ActionResult(False, f"Unknown action: {action_type}")
    except Exception as e:
        logger.error(f"Action failed ({action_type}): {e}")
        return ActionResult(False, f"Action failed: {e}")


# ─── Legacy Handlers (backward compatibility) ────────────────────────

def _legacy_search(query: str) -> ActionResult:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}"
    webbrowser.open(url)
    logger.info(f"Action: searched for '{query}'")
    return ActionResult(True, f"Searched for '{query}'")


def _legacy_open(url: str) -> ActionResult:
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    webbrowser.open(url)
    logger.info(f"Action: opened {url}")
    return ActionResult(True, f"Opened {url}")


def _legacy_type(text: str) -> ActionResult:
    time.sleep(0.5)
    pyautogui.write(text, interval=0.02)
    logger.info(f"Action: typed '{text[:30]}...'")
    return ActionResult(True, f"Typed text")


def _legacy_hotkey(keys: str) -> ActionResult:
    key_list = [k.strip().lower() for k in keys.split("+")]
    pyautogui.hotkey(*key_list)
    logger.info(f"Action: pressed {'+'.join(key_list)}")
    return ActionResult(True, f"Pressed {'+'.join(key_list)}")


def _legacy_run(app_name: str) -> ActionResult:
    import subprocess
    app_map = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "calc": "calc.exe",
        "terminal": "wt.exe",
        "cmd": "cmd.exe",
        "explorer": "explorer.exe",
        "paint": "mspaint.exe",
        "settings": "ms-settings:",
        "task manager": "taskmgr.exe",
        "taskmgr": "taskmgr.exe",
    }
    resolved = app_map.get(app_name.lower(), app_name)

    if resolved.startswith("ms-"):
        import os
        os.startfile(resolved)
    else:
        subprocess.Popen(resolved, shell=True)

    logger.info(f"Action: launched {resolved}")
    return ActionResult(True, f"Launched {resolved}")
