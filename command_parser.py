"""
command_parser.py — Pure-function command splitting.

Splits user transcripts containing "and" or "then" into separate
commands, with guards against false splits.
"""

import re
from constants import ACTION_KEYWORDS


def split_commands(text: str) -> list[str]:
    """
    Split a transcript into multiple commands on "and" or "then" delimiters.

    Rules:
    - "then" always splits into sequential commands.
    - "and" only splits if both the left side AND the right side contain
      an action keyword. This prevents "open chrome and edge" from splitting
      (right side "edge" has no action keyword).
    - Empty segments after splitting are discarded.
    - If no split applies, returns a single-element list with the original text.

    Args:
        text: The raw user transcript.

    Returns:
        List of command strings, length >= 1.
    """
    if not text or not text.strip():
        return [text or ""]

    text = text.strip()

    # ── "then" delimiter: always splits ───────────────────────────────
    if re.search(r'\bthen\b', text, re.IGNORECASE):
        parts = re.split(r'\bthen\b', text, flags=re.IGNORECASE)
        commands = [p.strip() for p in parts if p.strip()]
        if commands:
            return commands

    # ── "and" delimiter: conditional split ────────────────────────────
    if re.search(r'\band\b', text, re.IGNORECASE):
        parts = re.split(r'\band\b', text, flags=re.IGNORECASE)
        if len(parts) >= 2:
            left = parts[0].strip()
            right = " and ".join(parts[1:]).strip()  # rejoin remaining

            left_has_action = _has_action_keyword(left)
            right_has_action = _has_action_keyword(right)

            if left_has_action and right_has_action:
                commands = [p.strip() for p in parts if p.strip()]
                return commands if commands else [text]

    # No split
    return [text]


def _has_action_keyword(segment: str) -> bool:
    """Check if a text segment contains at least one action keyword."""
    words = set(segment.lower().split())
    return bool(words & set(ACTION_KEYWORDS.keys()))
