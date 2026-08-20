"""
executors/workspace_actions.py — Workspace management actions.
"""

import logging
from executors import register_action, ActionResult
from workspace_manager import get_workspace_manager
from storage import get_db

logger = logging.getLogger(__name__)

@register_action(
    "SAVE_WORKSPACE", "💾 Save Workspace",
    "Save current window layout", "system"
)
def handle_save_workspace(params: str) -> ActionResult:
    """Save the current workspace layout."""
    name = params.strip()
    if not name:
        return ActionResult(success=False, message="Workspace name required.")
        
    try:
        manager = get_workspace_manager()
        msg = manager.save_workspace(name)
        return ActionResult(success=True, message=msg, data=name)
    except Exception as e:
        logger.error(f"Failed to save workspace: {e}")
        return ActionResult(success=False, message=str(e))


@register_action(
    "RESTORE_WORKSPACE", "🔄 Restore Workspace",
    "Restore a saved window layout", "system"
)
def handle_restore_workspace(params: str) -> ActionResult:
    """Restore a saved workspace layout."""
    name = params.strip()
    if not name:
        return ActionResult(success=False, message="Workspace name required.")
        
    try:
        manager = get_workspace_manager()
        
        # Check window count for safety
        snapshot = get_db().get_workspace_snapshot(name)
        if snapshot:
            import json
            windows = json.loads(snapshot["snapshot_json"])
            if len(windows) > 10:
                # To be completely safe, maybe log it, but the confirm_once level handles the actual trust
                pass

        # Since restoration takes time and uses sleep(), we run it in a background thread
        import threading
        def _run_restore():
            manager.restore_workspace(name)
            
        threading.Thread(target=_run_restore, daemon=True).start()
        
        return ActionResult(success=True, message=f"Restoring workspace '{name}'...", data=name)
    except Exception as e:
        logger.error(f"Failed to restore workspace: {e}")
        return ActionResult(success=False, message=str(e))


@register_action(
    "DELETE_WORKSPACE", "🗑️ Delete Workspace",
    "Delete a saved window layout", "system"
)
def handle_delete_workspace(params: str) -> ActionResult:
    """Delete a saved workspace layout."""
    name = params.strip()
    if not name:
        return ActionResult(success=False, message="Workspace name required.")
        
    try:
        deleted = get_db().delete_workspace_snapshot(name)
        if deleted:
            return ActionResult(success=True, message=f"Workspace '{name}' deleted.", data=name)
        else:
            return ActionResult(success=False, message=f"Workspace '{name}' not found.")
    except Exception as e:
        logger.error(f"Failed to delete workspace: {e}")
        return ActionResult(success=False, message=str(e))

@register_action(
    "LIST_WORKSPACES", "📋 List Workspaces",
    "List all saved workspace layouts", "system"
)
def handle_list_workspaces(params: str) -> ActionResult:
    """List all saved workspace layouts and read via TTS."""
    try:
        snapshots = get_db().list_workspace_snapshots()
        if not snapshots:
            msg = "You have no saved workspaces."
        else:
            names = [s["name"] for s in snapshots]
            if len(names) == 1:
                msg = f"You have one saved workspace: {names[0]}."
            else:
                msg = f"You have {len(names)} saved workspaces: " + ", ".join(names) + "."
                
        # Send to TTS using the manager
        manager = get_workspace_manager()
        manager._notify_tts(msg)
        
        return ActionResult(success=True, message=msg, data=names)
    except Exception as e:
        logger.error(f"Failed to list workspaces: {e}")
        return ActionResult(success=False, message=str(e))

