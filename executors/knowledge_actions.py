"""
executors/knowledge_actions.py — Learning & Knowledge Actions

Handles: EXPLAIN, TRANSLATE, GENERATE_CODE, QUIZ, STEP_GUIDE, SUMMARISE_SCREEN
Most of these work by injecting context back into the AI conversation
rather than performing system actions.
"""

import logging

import pyperclip

from executors import register_action, ActionResult

logger = logging.getLogger(__name__)


@register_action(
    "EXPLAIN", "💡 Explain", "Explain what's on screen", "knowledge"
)
def handle_explain(params: str) -> ActionResult:
    """
    Explain selected/visible content. The AI already has the screenshot,
    so this just injects an instruction to elaborate.
    Params: optional topic/focus area
    """
    topic = params.strip() if params.strip() else "what's visible on screen"
    return ActionResult(
        success=True,
        message="Explaining...",
        data=f"Please explain {topic} in detail. Reference what you see on the user's screen.",
        inject_context=True,
        context_label=f"[EXPLAIN REQUEST] {topic}",
    )


@register_action(
    "TRANSLATE", "🌍 Translate", "Translate text to another language", "knowledge"
)
def handle_translate(params: str) -> ActionResult:
    """
    Translate text. Params: "target_lang|text" or just "target_lang"
    If no text provided, translates whatever is visible on screen.
    """
    if "|" in params:
        target_lang, text = params.split("|", 1)
    else:
        target_lang = params.strip()
        text = ""

    target_lang = target_lang.strip()
    text = text.strip()

    if text:
        instruction = f"Translate the following to {target_lang}:\n\n{text}"
    else:
        instruction = (
            f"Translate the text visible on the user's screen to {target_lang}. "
            "Provide the translation in your response."
        )

    return ActionResult(
        success=True,
        message=f"Translating to {target_lang}...",
        data=instruction,
        inject_context=True,
        context_label=f"[TRANSLATE to {target_lang}]",
    )


@register_action(
    "GENERATE_CODE", "💻 Generate Code",
    "Generate code from description", "knowledge"
)
def handle_generate_code(params: str) -> ActionResult:
    """
    Generate code. Params: "language|description" or just "description"
    Copies result to clipboard.
    """
    if "|" in params:
        language, description = params.split("|", 1)
    else:
        language = ""
        description = params.strip()

    language = language.strip()
    description = description.strip()

    lang_hint = f" in {language}" if language else ""
    instruction = (
        f"Generate code{lang_hint} for: {description}\n\n"
        "Provide only the code, well-commented. The code will be copied to the user's clipboard."
    )

    return ActionResult(
        success=True,
        message=f"Generating code{lang_hint}...",
        data=instruction,
        inject_context=True,
        context_label=f"[GENERATE CODE{lang_hint}] {description}",
    )


@register_action(
    "QUIZ", "🧠 Quiz", "Generate quiz questions from screen content", "knowledge"
)
def handle_quiz(params: str) -> ActionResult:
    """
    Generate quiz questions about visible content.
    Params: optional topic focus
    """
    topic = params.strip() if params.strip() else "the content visible on screen"

    instruction = (
        f"Based on {topic}, generate 5 quiz questions to test understanding. "
        "For each question:\n"
        "1. Ask the question clearly\n"
        "2. Provide 4 multiple choice options (A-D)\n"
        "3. Mark the correct answer\n"
        "4. Give a brief explanation\n\n"
        "Make the questions progressively harder."
    )

    return ActionResult(
        success=True,
        message="Generating quiz...",
        data=instruction,
        inject_context=True,
        context_label=f"[QUIZ] {topic}",
    )


@register_action(
    "STEP_GUIDE", "📋 Step-by-Step Guide",
    "Break down a task into steps", "knowledge"
)
def handle_step_guide(params: str) -> ActionResult:
    """
    Generate a step-by-step guide.
    Params: "task description"
    """
    task = params.strip()
    if not task:
        return ActionResult(False, "No task specified for step guide")

    instruction = (
        f"Create a detailed step-by-step guide for: {task}\n\n"
        "Reference what's on the user's screen if relevant. "
        "Number each step. Include tips and warnings where appropriate."
    )

    return ActionResult(
        success=True,
        message="Creating guide...",
        data=instruction,
        inject_context=True,
        context_label=f"[STEP GUIDE] {task}",
    )


@register_action(
    "SUMMARISE_SCREEN", "📱 Summarise Screen",
    "Summarise what's visible on screen", "knowledge"
)
def handle_summarise_screen(params: str) -> ActionResult:
    """
    Summarise the current screen content.
    Params: optional focus area
    """
    focus = params.strip() if params.strip() else ""
    focus_hint = f" Focus on: {focus}." if focus else ""

    instruction = (
        "Give a concise one-paragraph summary of what's visible on the user's screen."
        f"{focus_hint} Be specific about what you see — apps, content, windows open."
    )

    return ActionResult(
        success=True,
        message="Summarising...",
        data=instruction,
        inject_context=True,
        context_label="[SUMMARISE SCREEN]",
    )
