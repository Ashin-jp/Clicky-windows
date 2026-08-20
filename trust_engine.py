"""
trust_engine.py — 4-Tier Trust Model

Classifies every action into one of four trust levels:
  Level 1 — SILENT:  Safe, read-only → runs immediately
  Level 2 — CONFIRM_ONCE: Medium-risk → confirm first time per session
  Level 3 — ALWAYS_CONFIRM: High-risk → must confirm every time
  Level 4 — BLOCKED: Dangerous → refused with explanation

Also handles command-level trust classification for RUN_CMD.
"""

import logging
import re
import uuid
from enum import Enum

from storage import get_db

logger = logging.getLogger(__name__)


class TrustLevel(Enum):
    SILENT = "silent"                  # Auto-approve, no UI
    CONFIRM_ONCE = "confirm_once"      # Confirm first time, remember for session
    ALWAYS_CONFIRM = "always_confirm"  # Must confirm every time
    BLOCKED = "blocked"                # Refused entirely


# ─── Action Type Trust Mappings ───────────────────────────────────────
# Each action type has a default trust level. Some may be overridden
# based on parameters (e.g., OPEN_FILE is SILENT for safe paths but
# ALWAYS_CONFIRM for system paths).

ACTION_TRUST_DEFAULTS: dict[str, TrustLevel] = {
    # Screen Interaction (Phase 1)
    "CLICK":              TrustLevel.CONFIRM_ONCE,
    "SCROLL":             TrustLevel.SILENT,
    "DRAG":               TrustLevel.CONFIRM_ONCE,
    "RIGHTCLICK":         TrustLevel.CONFIRM_ONCE,
    "SCREENSHOT_REGION":  TrustLevel.SILENT,
    "SAVE_SCREENSHOT":    TrustLevel.SILENT,

    # Existing actions
    "SEARCH":             TrustLevel.SILENT,
    "OPEN":               TrustLevel.SILENT,
    "TYPE":               TrustLevel.CONFIRM_ONCE,
    "HOTKEY":             TrustLevel.CONFIRM_ONCE,
    "RUN":                TrustLevel.CONFIRM_ONCE,

    # System & File (Phase 2)
    "OPEN_FILE":          TrustLevel.SILENT,        # Elevated by file_access for system paths
    "CREATE_FILE":        TrustLevel.CONFIRM_ONCE,
    "READ_FILE":          TrustLevel.SILENT,         # Elevated by file_access for sensitive files
    "WRITE_FILE":         TrustLevel.ALWAYS_CONFIRM,
    "SEARCH_FILES":       TrustLevel.SILENT,
    "RUN_CMD":            TrustLevel.ALWAYS_CONFIRM, # Overridden by command trust classifier

    # Web & API (Phase 2)
    "FETCH_URL":          TrustLevel.SILENT,
    "DOWNLOAD":           TrustLevel.CONFIRM_ONCE,
    "SUMMARISE_PAGE":     TrustLevel.SILENT,

    # Knowledge (Phase 3)
    "EXPLAIN":            TrustLevel.SILENT,
    "TRANSLATE":          TrustLevel.SILENT,
    "GENERATE_CODE":      TrustLevel.SILENT,
    "QUIZ":               TrustLevel.SILENT,
    "STEP_GUIDE":         TrustLevel.SILENT,
    "SUMMARISE_SCREEN":   TrustLevel.SILENT,

    # Communication (Phase 3)
    "DRAFT_MESSAGE":      TrustLevel.SILENT,
    "READ_ALOUD":         TrustLevel.SILENT,
    "DICTATE":            TrustLevel.CONFIRM_ONCE,

    # Automation (Phase 4)
    "RECORD_MACRO":       TrustLevel.CONFIRM_ONCE,
    "PLAY_MACRO":         TrustLevel.CONFIRM_ONCE,
    "STOP_RECORDING":     TrustLevel.SILENT,
    "WATCH_FOLDER":       TrustLevel.CONFIRM_ONCE,
    "SCHEDULE_TASK":      TrustLevel.CONFIRM_ONCE,
    "CHAIN":              TrustLevel.CONFIRM_ONCE,

    # Extended (Phase 5)
    "TEXT_TRANSFORM":     TrustLevel.SILENT,
    "FOCUS_MODE":         TrustLevel.CONFIRM_ONCE,
    "SAVE_WORKSPACE":     TrustLevel.SILENT,
    "RESTORE_WORKSPACE":  TrustLevel.CONFIRM_ONCE,
    "DELETE_WORKSPACE":   TrustLevel.CONFIRM_ONCE,
    "LIST_WORKSPACES":    TrustLevel.SILENT,
    "REMEMBER":           TrustLevel.SILENT,
    "RUN_CODE":           TrustLevel.CONFIRM_ONCE,
    "RESEARCH":           TrustLevel.CONFIRM_ONCE,
    "READ_SCREEN":        TrustLevel.SILENT,
    "HEALTH_CHECK":       TrustLevel.SILENT,

    # App Lifecycle (Phase 6)
    "CLOSE_APP":          TrustLevel.CONFIRM_ONCE,
    "SWITCH_TO_APP":      TrustLevel.SILENT,
    "LIST_OPEN_APPS":     TrustLevel.SILENT,
    "RESTART_APP":        TrustLevel.CONFIRM_ONCE,
    "APP_VOLUME":         TrustLevel.SILENT,

    # UI Guidance (System 1)
    "GUIDE_TO":           TrustLevel.SILENT,
    "EXPLAIN_ELEMENT":    TrustLevel.SILENT,
    "GUIDED_CLICK":       TrustLevel.CONFIRM_ONCE,
    "APP_TOUR":           TrustLevel.SILENT,
    "REMEMBER_UI":        TrustLevel.SILENT,

    # Linux Assistant (System 2)
    "LINUX_ASSIST":               TrustLevel.SILENT,
    "LINUX_INTERACTIVE_LESSON":   TrustLevel.SILENT,
    "LINUX_ERROR_EXPLAIN":        TrustLevel.SILENT,
    "LINUX_SUGGEST_COMMAND":      TrustLevel.SILENT,
    "LINUX_EXPLAIN_COMMAND":      TrustLevel.SILENT,

    # Browser Automation (Phase 7)
    "BROWSER_SEARCH":     TrustLevel.SILENT,
    "BROWSER_NAVIGATE":   TrustLevel.SILENT,
    "BROWSER_CLICK":      TrustLevel.CONFIRM_ONCE,
    "BROWSER_TYPE":       TrustLevel.CONFIRM_ONCE,
    "BROWSER_READ":       TrustLevel.SILENT,
    "BROWSER_SCROLL":     TrustLevel.SILENT,
    "BROWSER_TAB":        TrustLevel.SILENT,
    "BROWSER_BACK":       TrustLevel.SILENT,
    "BROWSER_FORWARD":    TrustLevel.SILENT,
    "BROWSER_SCREENSHOT": TrustLevel.SILENT,
    "BROWSER_FILL_FORM":  TrustLevel.ALWAYS_CONFIRM,
}


# ─── Command Trust Classification (for RUN_CMD) ──────────────────────

# Level 1: Safe, read-only commands → SILENT
SAFE_COMMAND_PREFIXES = [
    "dir", "echo", "type", "findstr", "where", "whoami",
    "hostname", "ipconfig", "ping", "nslookup", "tracert",
    "systeminfo", "tasklist", "wmic", "ver",
    "git status", "git log", "git diff", "git branch",
    "python --version", "python -V", "python -c",
    "node --version", "node -v", "npm --version", "npm list",
    "pip list", "pip show", "pip --version",
    "cargo --version", "rustc --version",
    "java -version", "javac -version",
    "dotnet --version", "dotnet --info",
    "code --version",
    "cls", "date /t", "time /t", "set",
]

# Level 2: Medium-risk, modifying commands → CONFIRM_ONCE
MEDIUM_COMMAND_PREFIXES = [
    "pip install", "pip uninstall",
    "npm install", "npm uninstall", "npm update", "npm init",
    "git add", "git commit", "git push", "git pull", "git checkout",
    "git merge", "git rebase", "git stash",
    "python ", "python3 ", "py ",
    "node ", "npx ",
    "cargo build", "cargo run", "cargo test",
    "dotnet build", "dotnet run", "dotnet test",
    "mkdir", "md", "copy", "xcopy", "move", "ren", "rename",
    "cd ",
    "start ", "explorer",
]

# Level 4: Blocked commands → NEVER
BLOCKED_COMMAND_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"format\s+[A-Z]:",
    r"del\s+/[FfSsQq].*[A-Z]:\\",
    r"rd\s+/[Ss]\s+[A-Z]:\\",
    r"rmdir\s+/[Ss]\s+[A-Z]:\\",
    r"shutdown\s+/[pPsSrR]",
    r"reg\s+delete",
    r"regedit",
    r"bcdedit",
    r"sfc\s+/scannow",
    r"dism",
    r"diskpart",
    r"cipher\s+/[wW]",
    r"net\s+user\s+.*\s+/add",
    r"net\s+localgroup\s+administrators",
    r"powershell.*-ep\s+bypass",
    r"Invoke-WebRequest.*\|\s*iex",
    r"DownloadString.*\|\s*iex",
    r"Set-ExecutionPolicy\s+Unrestricted",
]


class TrustEngine:
    """
    Evaluates trust levels for actions and manages session-based
    trust escalation/de-escalation.
    """

    def __init__(self):
        self._session_id = uuid.uuid4().hex[:12]
        self._db = get_db()
        # Clear stale session approvals from previous runs
        self._db.clear_session_trust()
        logger.info(f"TrustEngine initialized (session: {self._session_id})")

    @property
    def session_id(self) -> str:
        return self._session_id

    def get_trust_level(self, action_type: str, params: str = "") -> TrustLevel:
        """
        Determine the trust level for an action.
        May be elevated based on params (e.g., RUN_CMD checks command content).
        """
        action_upper = action_type.upper()

        # Special handling for RUN_CMD — classify by command content
        if action_upper == "RUN_CMD":
            return self._classify_command(params)

        # Default from the mapping
        return ACTION_TRUST_DEFAULTS.get(action_upper, TrustLevel.ALWAYS_CONFIRM)

    def _normalize_key(self, key: str) -> str:
        """Normalize trust key to prevent duplicates."""
        import re
        # Strip whitespace and special characters
        key = re.sub(r'[^\w:]', '', key.strip())
        # Deduplicate WORD:WORD pattern
        key = re.sub(r'^([^:]+):\1$', r'\1', key)
        return key

    def should_execute(self, action_type: str, params: str = "") -> tuple[bool, TrustLevel, str]:
        """
        Determine whether an action should execute.

        Returns:
            (can_execute, trust_level, reason)
            - can_execute: True if action can proceed (possibly after confirmation)
            - trust_level: The trust level to use for UI decisions
            - reason: Human-readable explanation if blocked
        """
        trust = self.get_trust_level(action_type, params)

        if trust == TrustLevel.BLOCKED:
            reason = self._get_block_reason(action_type, params)
            return (False, trust, reason)

        if trust == TrustLevel.SILENT:
            return (True, trust, "")

        if trust == TrustLevel.CONFIRM_ONCE:
            # Check if already approved this session
            raw_key = f"{action_type}:{self._get_approval_key(action_type, params)}"
            key = self._normalize_key(raw_key)
            if self._db.is_command_approved(key, self._session_id):
                return (True, TrustLevel.SILENT, "Previously approved this session")
            return (True, trust, "")

        # ALWAYS_CONFIRM
        return (True, trust, "")

    def record_approval(self, action_type: str, params: str = ""):
        """Record that the user approved a CONFIRM_ONCE action for this session."""
        raw_key = f"{action_type}:{self._get_approval_key(action_type, params)}"
        key = self._normalize_key(raw_key)
        self._db.approve_command(key, self._session_id)
        logger.info(f"Trust: approved '{key}' for session")

    def log_execution(self, action_type: str, params: str, trust_level: TrustLevel, result: str):
        """Log an action execution to the audit trail."""
        self._db.log_action(action_type, params, trust_level.value, result)

    def _get_approval_key(self, action_type: str, params: str) -> str:
        """
        Generate a session-approval key. For RUN_CMD, use the command prefix.
        For other actions, use the action type itself.
        """
        if action_type.upper() == "RUN_CMD":
            # Approve by command prefix (first word)
            prefix = params.strip().split()[0].lower() if params.strip() else ""
            return prefix
        return action_type.upper()

    def _classify_command(self, command: str) -> TrustLevel:
        """Classify a shell command into a trust level."""
        cmd = command.strip().lower()

        if not cmd:
            return TrustLevel.BLOCKED

        # Check blocked patterns first
        for pattern in BLOCKED_COMMAND_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return TrustLevel.BLOCKED

        # Check safe prefixes
        for prefix in SAFE_COMMAND_PREFIXES:
            if cmd.startswith(prefix.lower()):
                return TrustLevel.SILENT

        # Check medium-risk prefixes
        for prefix in MEDIUM_COMMAND_PREFIXES:
            if cmd.startswith(prefix.lower()):
                return TrustLevel.CONFIRM_ONCE

        # Unknown commands default to ALWAYS_CONFIRM
        return TrustLevel.ALWAYS_CONFIRM

    def _get_block_reason(self, action_type: str, params: str) -> str:
        """Generate a human-readable reason for blocking an action."""
        if action_type.upper() == "RUN_CMD":
            return (
                f"This command has been blocked for safety: \"{params}\"\n"
                "It matches a pattern known to be destructive or dangerous."
            )
        return f"Action '{action_type}' with these parameters is not allowed."
