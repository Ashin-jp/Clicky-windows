"""
executors/communication_actions.py — Communication Actions

Handles: DRAFT_MESSAGE, READ_ALOUD, DICTATE
"""

import logging

import pyperclip

from executors import register_action, ActionResult

logger = logging.getLogger(__name__)


@register_action(
    "DRAFT_MESSAGE", "✉️ Draft Message",
    "Draft an email or message", "communication"
)
def handle_draft_message(params: str) -> ActionResult:
    """
    Draft a message based on context.
    Params: "context or instruction" — e.g., "reply accepting the offer"
    The AI generates the draft and it gets copied to clipboard.
    """
    context = params.strip()
    if not context:
        context = "the email or message visible on screen"

    instruction = (
        f"Draft a professional message based on: {context}\n\n"
        "Look at what's on the user's screen for context (emails, chats, etc). "
        "Write the draft ready to send. Keep it natural and appropriate for the context. "
        "The draft will be copied to the user's clipboard."
    )

    return ActionResult(
        success=True,
        message="Drafting message...",
        data=instruction,
        inject_context=True,
        context_label=f"[DRAFT MESSAGE] {context}",
    )


@register_action(
    "READ_ALOUD", "🔊 Read Aloud",
    "Read screen text aloud via TTS", "communication"
)
def handle_read_aloud(params: str) -> ActionResult:
    """
    Read text aloud. Params: "text to read" or empty (reads from screen).
    If text is provided, it's spoken directly.
    If empty, the AI will describe/read what's on screen.
    """
    text = params.strip()

    if text:
        # Direct TTS — text provided, just speak it
        return ActionResult(
            success=True,
            message="Reading aloud...",
            data=text,
            inject_context=False,  # Don't re-process through AI
        )
    else:
        # Ask AI to read the screen content
        instruction = (
            "Read the main text content visible on the user's screen aloud. "
            "Don't summarize — read the actual text as written. "
            "If it's an article, read the body. If it's code, describe what it does."
        )
        return ActionResult(
            success=True,
            message="Reading screen...",
            data=instruction,
            inject_context=True,
            context_label="[READ ALOUD from screen]",
        )


@register_action(
    "DICTATE", "🎤 Dictate",
    "Start voice dictation mode", "communication"
)
def handle_dictate(params: str) -> ActionResult:
    """
    Trigger dictation mode. This signals the companion manager
    to switch to dictation mode for the next input.
    Params: ignored
    """
    return ActionResult(
        success=True,
        message="Dictation mode activated. Speak now and your words will be typed out.",
        data="__TRIGGER_DICTATION__",
        inject_context=False,
    )

@register_action(
    "TEXT_TRANSFORM", "✍️ Transform Text",
    "Transforms highlighted text using AI", "communication"
)
def handle_text_transform(params: str) -> ActionResult:
    """
    Handle smart text transformations.
    Params is the trigger_name (e.g. 'formalize', 'simplify', 'undo')
    """
    trigger = params.strip()
    from smart_text_transformer import get_text_transformer
    transformer = get_text_transformer()
    
    # Needs to be run in background since router is blocking
    import threading
    
    def run_transform():
        if trigger == "undo":
            transformer.undo_last_transform()
        else:
            transformer.transform_text(trigger)
            
    threading.Thread(target=run_transform, daemon=True).start()
    
    return ActionResult(
        success=True,
        message=f"Transforming text: {trigger}...",
        data=f"__TEXT_TRANSFORM_TRIGGERED__{trigger}",
        inject_context=False,
    )
