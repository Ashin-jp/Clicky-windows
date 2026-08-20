"""
code_sandbox.py — Isolated Python code execution.

Executes clipboard code in a subprocess with timeout protection,
captures stdout/stderr, and sends errors to Groq for interpretation.
"""

import hashlib
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CodeSandbox:
    """Isolated code execution with error interpretation."""

    def __init__(self, timeout: int = 30):
        self._timeout = timeout
        self._db = None
        self._venv_python = self._detect_venv()
        logger.info("CodeSandbox: initialized")

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def _detect_venv(self) -> str:
        """Detect active virtual environment."""
        import os
        venv = os.environ.get("VIRTUAL_ENV")
        if venv:
            venv_python = Path(venv) / "Scripts" / "python.exe"
            if venv_python.exists():
                logger.info(f"CodeSandbox: using venv at {venv}")
                return str(venv_python)
        return sys.executable

    def execute(self, code: str, language: str = "python") -> dict:
        """
        Execute code in an isolated subprocess.
        Returns dict with: stdout, stderr, return_code, duration_ms, success
        """
        if language != "python":
            return {"stdout": "", "stderr": f"Language '{language}' not supported yet.",
                    "return_code": -1, "duration_ms": 0, "success": False}

        start = time.monotonic()
        try:
            result = subprocess.run(
                [self._venv_python, "-c", code],
                capture_output=True, text=True,
                timeout=self._timeout,
                cwd=str(Path.home()),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            elapsed = int((time.monotonic() - start) * 1000)

            output = {
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:5000],
                "return_code": result.returncode,
                "duration_ms": elapsed,
                "success": result.returncode == 0,
            }

        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - start) * 1000)
            output = {
                "stdout": "", "stderr": f"Execution timed out after {self._timeout} seconds.",
                "return_code": -1, "duration_ms": elapsed, "success": False,
            }

        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            output = {
                "stdout": "", "stderr": str(e),
                "return_code": -1, "duration_ms": elapsed, "success": False,
            }

        # Log to database
        self._log_execution(code, output)
        return output

    def execute_clipboard(self) -> dict:
        """Execute code from clipboard."""
        try:
            import pyperclip
            code = pyperclip.paste()
        except Exception as e:
            return {"stdout": "", "stderr": f"Cannot read clipboard: {e}",
                    "return_code": -1, "duration_ms": 0, "success": False}

        if not code or not code.strip():
            return {"stdout": "", "stderr": "Clipboard is empty.",
                    "return_code": -1, "duration_ms": 0, "success": False}

        return self.execute(code)

    def interpret_error(self, stderr: str, code: str) -> str:
        """Send error to Groq for interpretation and fix suggestion."""
        try:
            from groq_router import get_router, RoutedRequest, TaskType, Priority
            router = get_router()

            prompt = (f"The following Python code produced an error.\n\n"
                      f"Code:\n```python\n{code[:2000]}\n```\n\n"
                      f"Error:\n```\n{stderr[:1000]}\n```\n\n"
                      f"Explain the error briefly and suggest a fix. "
                      f"Show the corrected code only if the fix is simple.")

            request = RoutedRequest(
                system_prompt="You are a Python debugging expert. Be concise.",
                user_prompt=prompt,
                task_type=TaskType.CODE_TASK,
                priority=Priority.NORMAL,
                max_tokens=500,
            )
            response = router.chat(request)
            return response.text

        except Exception as e:
            logger.debug(f"CodeSandbox: error interpretation failed: {e}")
            return f"Could not interpret error: {e}"

    def format_result(self, result: dict) -> str:
        """Format execution result for TTS/display."""
        if result["success"]:
            stdout = result["stdout"].strip()
            if stdout:
                # Truncate for TTS
                preview = stdout[:200]
                if len(stdout) > 200:
                    preview += "... (truncated)"
                return f"Code executed successfully. Output: {preview}"
            return "Code executed successfully with no output."
        else:
            return f"Code failed: {result['stderr'][:200]}"

    def _log_execution(self, code: str, result: dict):
        """Log execution to database."""
        try:
            db = self._get_db()
            code_hash = hashlib.md5(code.encode()).hexdigest()[:16]
            db.log_action(
                action_type="RUN_CODE",
                params=f"hash={code_hash},len={len(code)}",
                trust_level="sandbox",
                result=f"rc={result['return_code']},ms={result['duration_ms']}",
            )
        except Exception:
            pass


_instance: Optional[CodeSandbox] = None

def get_code_sandbox() -> CodeSandbox:
    global _instance
    if _instance is None:
        _instance = CodeSandbox()
    return _instance
