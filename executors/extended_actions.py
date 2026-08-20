"""
executors/extended_actions.py — New action handlers for Phase 3+ features.

Registers: TRANSFORM_TEXT, FOCUS_MODE, SAVE_WORKSPACE, RESTORE_WORKSPACE,
REMEMBER, RUN_CODE, RESEARCH, READ_SCREEN, HEALTH_CHECK
"""

import logging

from executors import register_action, ActionResult

logger = logging.getLogger(__name__)


@register_action("TRANSFORM_TEXT", "✏️ Transform", "Transform clipboard/selected text", "knowledge")
def handle_transform_text(params: str) -> ActionResult:
    """Apply a text transformation to clipboard content."""
    from smart_text_transformer import get_text_transformer

    transform_type = params.strip().lower() if params else "summarize"
    transformer = get_text_transformer()
    result = transformer.transform_clipboard(transform_type)

    if result["success"]:
        return ActionResult(
            success=True,
            message=f"Text transformed ({transform_type}). Copied to clipboard.",
            data=result["result"],
        )
    return ActionResult(success=False, message=result.get("error", "Transform failed."))


@register_action("FOCUS_MODE", "🎯 Focus", "Activate focus mode for N minutes", "automation")
def handle_focus_mode(params: str) -> ActionResult:
    """Activate or deactivate focus mode."""
    from focus_mode import get_focus_mode

    fm = get_focus_mode()

    if params.strip().lower() in ("off", "stop", "end", "0"):
        fm.deactivate()
        return ActionResult(success=True, message="Focus mode deactivated.")

    try:
        minutes = int(params.strip()) if params.strip() else 25
    except ValueError:
        minutes = 25

    fm.activate(minutes)
    return ActionResult(success=True, message=f"Focus mode activated for {minutes} minutes.")


@register_action("SAVE_WORKSPACE", "💾 Save Layout", "Save current window layout", "automation")
def handle_save_workspace(params: str) -> ActionResult:
    """Save current window layout."""
    from workspace_manager import get_workspace_manager

    name = params.strip() if params else "default"
    wm = get_workspace_manager()
    summary = wm.save_workspace(name)
    return ActionResult(success=True, message=summary)


@register_action("RESTORE_WORKSPACE", "📐 Restore Layout", "Restore saved window layout", "automation")
def handle_restore_workspace(params: str) -> ActionResult:
    """Restore a saved window layout."""
    from workspace_manager import get_workspace_manager

    name = params.strip() if params else "default"
    wm = get_workspace_manager()
    summary = wm.restore_workspace(name)
    success = "Failed" not in summary and "No workspace" not in summary
    return ActionResult(success=success, message=summary)


@register_action("REMEMBER", "🧠 Remember", "Save content to knowledge base", "knowledge")
def handle_remember(params: str) -> ActionResult:
    """Save content to personal knowledge base."""
    from knowledge_base import get_knowledge_base

    content = params.strip() if params else ""
    if not content:
        return ActionResult(success=False, message="Nothing to remember.")

    # Get current app context
    source_app = ""
    try:
        from uia_helper import get_foreground_app_name
        source_app = get_foreground_app_name() or ""
    except Exception:
        pass

    kb = get_knowledge_base()
    msg = kb.remember(content, source_app)
    return ActionResult(success=True, message=msg)


@register_action("RUN_CODE", "▶️ Run Code", "Execute code from clipboard", "automation")
def handle_run_code(params: str) -> ActionResult:
    """Execute clipboard code in sandbox."""
    from code_sandbox import get_code_sandbox

    language = params.strip().lower() if params else "python"
    sandbox = get_code_sandbox()
    result = sandbox.execute_clipboard()
    summary = sandbox.format_result(result)

    if not result["success"] and result["stderr"]:
        # Get error interpretation
        try:
            import pyperclip
            code = pyperclip.paste()
            interpretation = sandbox.interpret_error(result["stderr"], code)
            summary += f"\n\nExplanation: {interpretation}"
        except Exception:
            pass

    return ActionResult(
        success=result["success"],
        message=summary,
        data=result.get("stdout", ""),
        inject_context=bool(result.get("stdout")),
        context_label="Code output",
    )


@register_action("RESEARCH", "🔬 Research", "Start background research on a topic", "knowledge")
def handle_research(params: str) -> ActionResult:
    """Start autonomous background research."""
    from research_agent import get_research_agent

    topic = params.strip() if params else ""
    if not topic:
        return ActionResult(success=False, message="Please specify a research topic.")

    agent = get_research_agent()
    msg = agent.research(topic)
    return ActionResult(success=True, message=msg)


@register_action("READ_SCREEN", "👁️ Read Screen", "Read visible screen content aloud", "screen")
def handle_read_screen(params: str) -> ActionResult:
    """Start or stop screen reading."""
    from screen_reader_mode import get_screen_reader

    reader = get_screen_reader()
    if reader.is_reading():
        reader.stop_reading()
        return ActionResult(success=True, message="Stopped reading.")

    reader.start_reading()
    return ActionResult(success=True, message="Reading screen content...")


@register_action("HEALTH_CHECK", "🏥 Health", "Check system health", "automation")
def handle_health_check(params: str) -> ActionResult:
    """Get system health report."""
    from health_monitor import get_health_monitor

    hm = get_health_monitor()
    summary = hm.get_health_summary_text()
    return ActionResult(success=True, message=summary)
