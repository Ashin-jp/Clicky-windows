"""
linux_assistant.py — Linux Teaching and Assistance System

Provides bidirectional communication with WSL, interactive Linux lessons,
command explanation, error auto-detection, and safety checking.
"""

import logging
import subprocess
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from storage import get_db
from action_confirm_dialog import TrustLevel

logger = logging.getLogger(__name__)

# Cache distro per session
_cached_distro = None

def get_wsl_distro() -> str:
    """Fetch the WSL distro from /etc/os-release and cache it."""
    global _cached_distro
    if _cached_distro is not None:
        return _cached_distro

    try:
        result = subprocess.run(
            ['wsl', '-e', 'bash', '-c', 'cat /etc/os-release'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("ID="):
                    _cached_distro = line.split("=")[1].strip('"').strip("'")
                    break
    except Exception as e:
        logger.error(f"Failed to detect WSL distro: {e}")
        
    if not _cached_distro:
        _cached_distro = "unknown"
        
    return _cached_distro

@dataclass
class SafetyResult:
    is_safe: bool
    trust_level: TrustLevel
    reason: str

def classify_linux_command_safety(command: str) -> SafetyResult:
    """Classify safety of a Linux command before TrustEngine runs."""
    cmd_lower = command.lower().strip()
    
    # Very dangerous patterns (Blocked)
    blocked_patterns = [
        r"rm\s+-r[fF]?\s+/", r"rm\s+-r[fF]?\s+~", r"dd\s+if=/dev/zero",
        r"mkfs\s+/dev/sda", r":\(\)\{\s*:\|:&\s*\};:"
    ]
    for p in blocked_patterns:
        if re.search(p, cmd_lower):
            return SafetyResult(False, TrustLevel.ALWAYS_CONFIRM, "Command blocked: Potentially destroys filesystem.")

    # Danger (Always Confirm)
    danger_patterns = [
        r"^rm\s+", r"sudo\s+", r"^dd\s+", r"^mkfs", r"^fdisk", 
        r"wget.*\|\s*bash", r"curl.*\|\s*sh", r"chmod\s+777", r"chown\s+-R\s+root"
    ]
    for p in danger_patterns:
        if re.search(p, cmd_lower):
            return SafetyResult(True, TrustLevel.ALWAYS_CONFIRM, "Dangerous command: requires explicit confirmation.")

    # Caution (Confirm Once)
    caution_cmds = [
        "mkdir", "touch", "cp", "mv", "chmod", "chown", "apt install", 
        "apt remove", "pip install", "git", "nano", "vim", "python"
    ]
    for c in caution_cmds:
        if cmd_lower.startswith(c) or f" {c} " in f" {cmd_lower} ":
            return SafetyResult(True, TrustLevel.CONFIRM_ONCE, "Modifies state: requires confirmation.")

    # Safe (Silent)
    safe_cmds = [
        "pwd", "ls", "cd", "cat", "echo", "whoami", "uname", "df", "free", 
        "ps", "top", "history", "which", "find", "grep", "head", "tail", "wc", "man"
    ]
    
    # If the base command is in the safe list, it's safe
    base_cmd = cmd_lower.split(" ")[0]
    if base_cmd in safe_cmds:
        return SafetyResult(True, TrustLevel.SILENT, "Safe read-only command.")

    # Default to Caution for unknown commands
    return SafetyResult(True, TrustLevel.CONFIRM_ONCE, "Unknown command: requires confirmation.")


class LinuxCommandAssistant:
    def __init__(self):
        self.db = get_db()
        
    def execute_wsl_command(self, command: str, timeout: int = 30) -> tuple[str, str, int]:
        """Run a command directly in WSL and return stdout, stderr, return_code."""
        safety = classify_linux_command_safety(command)
        if not safety.is_safe:
            return "", safety.reason, -1
            
        logger.info(f"Executing WSL command: {command}")
        try:
            result = subprocess.run(
                ['wsl', '-e', 'bash', '-c', command],
                capture_output=True, text=True, timeout=timeout
            )
            self.db.save_linux_command(command, result.stdout + result.stderr, result.returncode)
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            logger.warning(f"WSL command timed out: {command}")
            return "", "Timeout expired.", -1
        except Exception as e:
            logger.error(f"WSL command failed: {e}")
            return "", str(e), -1

    def get_wsl_context(self) -> dict:
        """Gather safe context from WSL environment."""
        context = {
            "distro": get_wsl_distro(),
            "pwd": "",
            "whoami": "",
            "ls_la": "",
            "shell": "",
            "uname": "",
            "df_h": ""
        }
        
        cmds = {
            "pwd": "pwd",
            "whoami": "whoami",
            "ls_la": "ls -la | head -n 20",
            "shell": "echo $SHELL",
            "uname": "uname -a",
            "df_h": "df -h | grep -v tmpfs"
        }
        
        for key, cmd in cmds.items():
            stdout, _, rc = self.execute_wsl_command(cmd, timeout=5)
            if rc == 0:
                context[key] = stdout.strip()
                
        return context

    def read_wsl_file(self, path: str) -> str:
        """Read a file via wsl cat."""
        # Extremely basic sanitization to prevent arbitrary command injection via backticks or semicolons
        safe_path = path.replace(";", "").replace("`", "").replace("$", "")
        stdout, stderr, rc = self.execute_wsl_command(f"cat '{safe_path}'", timeout=10)
        return stdout if rc == 0 else stderr

    def wsl_autocomplete(self, partial_command: str) -> list[str]:
        """Get bash completions for a partial command."""
        safe_partial = partial_command.replace("'", "'\\''")
        stdout, _, rc = self.execute_wsl_command(f"compgen -c '{safe_partial}'", timeout=5)
        if rc == 0:
            return stdout.splitlines()
        return []

    # AI Integration methods (These generate prompts for the main Groq router)
    def suggest_command(self, task_description: str) -> str:
        # Prompt construction handled by AI pipeline
        pass

    def explain_command(self, command_string: str) -> str:
        pass

    def explain_error(self, error_text: str) -> str:
        pass

    def interactive_lesson(self, topic: str):
        pass

    def command_history_analysis(self) -> str:
        history = self.db.get_command_history(50)
        # Format for AI analysis
        return "\\n".join([f"[{h['executed_at']}] exit:{h['exit_code']} cmd:{h['command']}" for h in history])
