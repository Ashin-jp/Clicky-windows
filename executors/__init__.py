"""
executors/__init__.py — Action Executor Registry

Provides a decorator-based registration system for action executors.
Each executor module registers its handlers, and the dispatcher
routes [ACTION:type:params] tags to the correct handler.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Result returned by an action executor."""
    success: bool
    message: str = ""
    data: Any = None                # Arbitrary data (e.g., file contents, search results)
    inject_context: bool = False    # If True, `data` should be fed back to the AI as context
    context_label: str = ""         # Label for the injected context


@dataclass
class ActionDefinition:
    """Metadata for a registered action type."""
    action_type: str
    handler: Callable[[str], ActionResult]
    display_prefix: str             # Emoji + label for confirmation dialog
    description: str                # Human-readable description
    category: str                   # Category: screen, file, web, knowledge, automation, communication


# ─── Global Registry ──────────────────────────────────────────────────
_action_registry: dict[str, ActionDefinition] = {}


def register_action(
    action_type: str,
    display_prefix: str,
    description: str,
    category: str,
):
    """
    Decorator to register an action handler.

    Usage:
        @register_action("CLICK", "🖱️ Click", "Click at coordinates", "screen")
        def handle_click(params: str) -> ActionResult:
            ...
    """
    def decorator(func: Callable[[str], ActionResult]):
        _action_registry[action_type.upper()] = ActionDefinition(
            action_type=action_type.upper(),
            handler=func,
            display_prefix=display_prefix,
            description=description,
            category=category,
        )
        logger.debug(f"Registered action: {action_type}")
        return func
    return decorator


def get_action(action_type: str) -> ActionDefinition | None:
    """Look up a registered action by type."""
    return _action_registry.get(action_type.upper())


def get_all_actions() -> dict[str, ActionDefinition]:
    """Get all registered actions."""
    return dict(_action_registry)


def get_actions_by_category(category: str) -> list[ActionDefinition]:
    """Get all actions in a specific category."""
    return [a for a in _action_registry.values() if a.category == category]


def execute_registered_action(action_type: str, params: str) -> ActionResult:
    """Execute a registered action by type."""
    definition = get_action(action_type)
    if definition is None:
        return ActionResult(
            success=False,
            message=f"Unknown action type: {action_type}",
        )
    try:
        return definition.handler(params)
    except Exception as e:
        logger.error(f"Action {action_type} failed: {e}", exc_info=True)
        return ActionResult(
            success=False,
            message=f"Action failed: {e}",
        )


def load_all_executors():
    """
    Import all executor modules to trigger their @register_action decorators.
    Call this once during app initialization.
    """
    # Phase 1
    from executors import screen_actions  # noqa: F401

    # Phase 2
    from executors import file_actions    # noqa: F401
    from executors import web_actions     # noqa: F401

    # Phase 3
    from executors import knowledge_actions    # noqa: F401
    from executors import communication_actions  # noqa: F401

    # Phase 4 — Extended actions
    from executors import extended_actions  # noqa: F401
    from executors import app_actions       # noqa: F401
    from executors import browser_actions   # noqa: F401
    from executors import workspace_actions  # noqa: F401

    logger.info(f"Loaded {len(_action_registry)} action executors")
