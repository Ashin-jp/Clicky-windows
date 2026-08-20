"""
executors/file_actions.py — System & File Actions

Handles: OPEN_FILE, CREATE_FILE, READ_FILE, WRITE_FILE, SEARCH_FILES, RUN_CMD
Integrates with file_access.py for zone-based access control.
"""

import glob
import logging
import os
import subprocess
import time
from pathlib import Path

from executors import register_action, ActionResult
from file_access import get_file_access, FilePermission
from storage import WORKSPACE_DIR

logger = logging.getLogger(__name__)

# Maximum file size to read into AI context (chars)
MAX_FILE_READ_SIZE = 50_000
# Maximum command output to capture
MAX_CMD_OUTPUT = 10_000


@register_action(
    "OPEN_FILE", "📂 Open", "Open a file or folder", "file"
)
def handle_open_file(params: str) -> ActionResult:
    """
    Open a file or folder using the system default handler.
    Params: "path"
    """
    path = params.strip().strip('"').strip("'")
    if not path:
        return ActionResult(False, "No path specified")

    resolved = Path(path).resolve()
    if not resolved.exists():
        return ActionResult(False, f"Path not found: {path}")

    try:
        os.startfile(str(resolved))
        logger.info(f"Action: opened {resolved}")
        return ActionResult(True, f"Opened {resolved.name}")
    except Exception as e:
        return ActionResult(False, f"Failed to open: {e}")


@register_action(
    "CREATE_FILE", "📝 Create File", "Create a new file with content", "file"
)
def handle_create_file(params: str) -> ActionResult:
    """
    Create a new file. Params: "path|content"
    Uses | as separator between path and content.
    If no path separtor, creates in workspace.
    """
    if "|" in params:
        path_str, content = params.split("|", 1)
    else:
        # Auto-place in workspace
        path_str = params.strip()
        content = ""

    path_str = path_str.strip().strip('"').strip("'")

    # If relative path, place in workspace
    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE_DIR / p

    resolved = p.resolve()

    # Check access
    fac = get_file_access()
    allowed, tier, msg = fac.check_access(str(resolved), FilePermission.WRITE)
    if not allowed:
        return ActionResult(False, msg)

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info(f"Action: created file {resolved} ({len(content)} chars)")
        return ActionResult(True, f"Created {resolved.name} ({len(content)} chars)")
    except Exception as e:
        return ActionResult(False, f"Failed to create file: {e}")


@register_action(
    "READ_FILE", "📖 Read File", "Read file contents into AI context", "file"
)
def handle_read_file(params: str) -> ActionResult:
    """
    Read a file and inject contents into AI conversation.
    Params: "path"
    """
    path = params.strip().strip('"').strip("'")
    if not path:
        return ActionResult(False, "No path specified")

    resolved = Path(path).resolve()
    if not resolved.exists():
        return ActionResult(False, f"File not found: {path}")

    if not resolved.is_file():
        return ActionResult(False, f"Not a file: {path}")

    # Check access
    fac = get_file_access()
    allowed, tier, msg = fac.check_access(str(resolved), FilePermission.READ)
    if not allowed:
        return ActionResult(False, msg)

    try:
        # Check file size
        size = resolved.stat().st_size
        if size > MAX_FILE_READ_SIZE * 2:  # Rough byte estimate
            return ActionResult(
                False,
                f"File too large ({size // 1024}KB). Max is ~{MAX_FILE_READ_SIZE // 1000}K chars.",
            )

        content = resolved.read_text(encoding="utf-8", errors="replace")

        # Check for sensitive content
        if fac.check_content_sensitivity(content.encode("utf-8", errors="replace")):
            logger.warning(f"Sensitive content detected in {resolved.name}")

        # Truncate if still too long
        if len(content) > MAX_FILE_READ_SIZE:
            content = content[:MAX_FILE_READ_SIZE] + f"\n\n... [truncated, file has {len(content)} chars total]"

        logger.info(f"Action: read file {resolved.name} ({len(content)} chars)")
        return ActionResult(
            success=True,
            message=f"Read {resolved.name}",
            data=content,
            inject_context=True,
            context_label=f"Contents of {resolved.name}",
        )
    except Exception as e:
        return ActionResult(False, f"Failed to read file: {e}")


@register_action(
    "WRITE_FILE", "💾 Write File", "Write content to a file", "file"
)
def handle_write_file(params: str) -> ActionResult:
    """
    Write content to a file. Params: "path|content"
    """
    if "|" not in params:
        return ActionResult(False, "Write requires path|content format")

    path_str, content = params.split("|", 1)
    path_str = path_str.strip().strip('"').strip("'")

    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE_DIR / p

    resolved = p.resolve()

    # Check access
    fac = get_file_access()
    allowed, tier, msg = fac.check_access(str(resolved), FilePermission.WRITE)
    if not allowed:
        return ActionResult(False, msg)

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info(f"Action: wrote file {resolved} ({len(content)} chars)")
        return ActionResult(True, f"Saved {resolved.name} ({len(content)} chars)")
    except Exception as e:
        return ActionResult(False, f"Failed to write file: {e}")


@register_action(
    "SEARCH_FILES", "🔍 Search Files", "Search for files by name or pattern", "file"
)
def handle_search_files(params: str) -> ActionResult:
    """
    Search for files. Params: "directory|pattern" or just "pattern"
    (defaults to workspace directory).
    """
    if "|" in params:
        directory, pattern = params.split("|", 1)
        directory = directory.strip().strip('"').strip("'")
    else:
        directory = str(WORKSPACE_DIR)
        pattern = params.strip()

    search_dir = Path(directory).resolve()
    if not search_dir.exists():
        return ActionResult(False, f"Directory not found: {directory}")

    # Check access
    fac = get_file_access()
    allowed, tier, msg = fac.check_access(str(search_dir), FilePermission.READ)
    if not allowed:
        return ActionResult(False, msg)

    try:
        # Use glob pattern
        if "**" in pattern:
            matches = list(search_dir.glob(pattern))
        elif "*" in pattern or "?" in pattern:
            matches = list(search_dir.rglob(pattern))
        else:
            # Search by substring in filename
            matches = [
                p for p in search_dir.rglob("*")
                if pattern.lower() in p.name.lower() and p.is_file()
            ]

        # Limit results
        total = len(matches)
        matches = matches[:50]

        results = []
        for m in matches:
            rel = m.relative_to(search_dir) if m.is_relative_to(search_dir) else m
            size = m.stat().st_size if m.is_file() else 0
            results.append(f"  {rel} ({size // 1024}KB)" if size > 1024 else f"  {rel}")

        result_text = f"Found {total} file(s) in {search_dir.name}/:\n" + "\n".join(results)
        if total > 50:
            result_text += f"\n  ... and {total - 50} more"

        logger.info(f"Action: searched {search_dir} for '{pattern}' → {total} results")
        return ActionResult(
            success=True,
            message=f"Found {total} file(s)",
            data=result_text,
            inject_context=True,
            context_label=f"File search results for '{pattern}'",
        )
    except Exception as e:
        return ActionResult(False, f"Search failed: {e}")


@register_action(
    "RUN_CMD", "⚡ Run Command", "Execute a terminal command", "file"
)
def handle_run_cmd(params: str) -> ActionResult:
    """
    Execute a shell command and return output.
    Params: "command"
    Trust level is determined by trust_engine.py based on command content.
    """
    command = params.strip()
    if not command:
        return ActionResult(False, "No command specified")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(WORKSPACE_DIR),
        )

        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR]: {result.stderr}"

        # Truncate
        if len(output) > MAX_CMD_OUTPUT:
            output = output[:MAX_CMD_OUTPUT] + f"\n... [truncated, {len(output)} chars total]"

        status = "✓" if result.returncode == 0 else f"✗ (exit code {result.returncode})"
        logger.info(f"Action: ran command '{command[:50]}' → {status}")

        return ActionResult(
            success=result.returncode == 0,
            message=f"Command {status}",
            data=f"$ {command}\n{output}",
            inject_context=True,
            context_label=f"Command output: {command[:60]}",
        )
    except subprocess.TimeoutExpired:
        return ActionResult(False, f"Command timed out after 30s: {command[:60]}")
    except Exception as e:
        return ActionResult(False, f"Command failed: {e}")
