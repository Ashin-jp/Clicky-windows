"""
constants.py — Shared constants for Clicky.

Single source of truth for keyword dictionaries and configuration
values used across multiple modules.
"""

# ─── Action Keywords ──────────────────────────────────────────────────
# Used by intent_router.py for classification scoring and by
# companion_manager.py for pending-action conflict detection.
# Higher weight = stronger signal for action intent.

ACTION_KEYWORDS = {
    # App launching
    "open": 3, "launch": 3, "start": 3, "run": 3, "close": 3,
    "minimize": 3, "maximize": 3, "switch": 2,
    # File operations
    "create": 2, "delete": 3, "rename": 2, "move": 2, "copy": 2,
    "save": 2, "find": 2, "search": 2,
    # System
    "shutdown": 3, "restart": 3, "lock": 3, "mute": 3, "unmute": 3,
    "volume": 2, "brightness": 2, "screenshot": 3,
    # Automation
    "click": 3, "type": 2, "scroll": 2, "press": 3,
    "record": 2, "macro": 2, "schedule": 2,
    # Focus/workspace
    "focus": 2, "workspace": 2, "timer": 2,
}

# Words that suggest an STT correction should be applied (action context)
STT_ACTION_CONTEXT_WORDS = {
    "open", "launch", "run", "start", "close", "switch",
    "find", "search", "click", "press", "mute", "unmute",
}

# Words that suggest an STT correction should NOT be applied (non-action context)
STT_SKIP_CONTEXT_PHRASES = {
    "want to", "need to", "going to", "will", "can",
    "would", "should", "could", "might", "may",
}
