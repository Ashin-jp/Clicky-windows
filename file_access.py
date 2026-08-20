"""
file_access.py — Zone-Based File Access Control

Classifies file paths into access tiers and checks whether
Clicky should be allowed to read/write them:

  🟢 PUBLIC ZONES    — Silent allow (ClickyWorkspace, Desktop read, Downloads read)
  🟡 PROJECT ZONES   — Confirm once per session
  🔴 SYSTEM ZONES    — Always confirm, read-only with warning
  🚫 FORBIDDEN ZONES — Blocked entirely (System32, AppData secrets, SSH keys)
  ⚠️  SENSITIVE       — Extra scrutiny (files containing API keys, private keys)
"""

import logging
import os
import re
from enum import Enum
from pathlib import Path

from storage import WORKSPACE_DIR

logger = logging.getLogger(__name__)


class FileAccessTier(Enum):
    PUBLIC = "public"           # Silent allow
    PROJECT = "project"         # Confirm once per session
    SYSTEM = "system"           # Always confirm, read-only
    FORBIDDEN = "forbidden"     # Blocked
    SENSITIVE = "sensitive"     # Extra scrutiny


class FilePermission(Enum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"


# ─── Zone Definitions ─────────────────────────────────────────────────

def _get_user_home() -> Path:
    return Path.home()


def _normalize(p: str) -> str:
    """Normalize a path for comparison."""
    return str(Path(p).resolve()).lower().replace("/", "\\")


# Forbidden zones — BLOCKED entirely
FORBIDDEN_ZONES = [
    r"C:\\Windows\\System32",
    r"C:\\Windows\\SysWOW64",
    r"C:\\Windows\\security",
    r"\\\.ssh\\?",
    r"\\\.gnupg\\?",
    r"\\\.aws\\?",
    r"\\\AppData\\Roaming\\",
    r"\\\AppData\\Local\\Microsoft\\Credentials",
    r"\\\AppData\\Local\\Microsoft\\Vault",
]

# System zones — ALWAYS_CONFIRM, read-only
SYSTEM_ZONES = [
    r"C:\\Windows\\",
    r"C:\\Program Files\\",
    r"C:\\Program Files \(x86\)\\",
    r"C:\\ProgramData\\",
]

# Sensitive file patterns — content-level detection
SENSITIVE_CONTENT_PATTERNS = [
    rb"-----BEGIN\s+(RSA|DSA|EC|OPENSSH|PGP)\s+PRIVATE\s+KEY-----",
    rb"(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?\w{20,}",
    rb"(?:password|passwd|pwd)\s*[:=]\s*['\"]?\S{4,}",
    rb"AKIA[0-9A-Z]{16}",  # AWS Access Key
    rb"sk-[a-zA-Z0-9]{20,}",  # OpenAI API key pattern
    rb"gsk_[a-zA-Z0-9]{20,}",  # Groq API key pattern
    rb"AIzaSy[a-zA-Z0-9_-]{33}",  # Google API key pattern
]

SENSITIVE_FILE_NAMES = [
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials", "secrets.json", "secrets.yaml", "secrets.yml",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    ".netrc", ".npmrc", ".pypirc",
    "token.json", "tokens.json",
]


class FileAccessControl:
    """
    Evaluates whether Clicky should be allowed to access a file path,
    and with what level of confirmation required.
    """

    def __init__(self):
        self._user_home = _get_user_home()
        self._desktop = self._user_home / "Desktop"
        self._downloads = self._user_home / "Downloads"
        self._documents = self._user_home / "Documents"

        # Session-approved project folders
        self._approved_project_folders: set[str] = set()

        logger.info("FileAccessControl initialized")

    def classify_path(self, path: str) -> FileAccessTier:
        """
        Classify a file path into an access tier.
        """
        norm = _normalize(path)
        resolved = Path(path).resolve()

        # Check FORBIDDEN first
        for pattern in FORBIDDEN_ZONES:
            if re.search(pattern, norm, re.IGNORECASE):
                return FileAccessTier.FORBIDDEN

        # Check if it's a sensitive filename
        basename = resolved.name.lower()
        if basename in [s.lower() for s in SENSITIVE_FILE_NAMES]:
            return FileAccessTier.SENSITIVE

        # Check SYSTEM zones
        for pattern in SYSTEM_ZONES:
            if re.search(pattern, norm, re.IGNORECASE):
                return FileAccessTier.SYSTEM

        # Check PUBLIC zones
        workspace_norm = _normalize(str(WORKSPACE_DIR))
        if norm.startswith(workspace_norm):
            return FileAccessTier.PUBLIC

        desktop_norm = _normalize(str(self._desktop))
        downloads_norm = _normalize(str(self._downloads))
        if norm.startswith(desktop_norm) or norm.startswith(downloads_norm):
            return FileAccessTier.PUBLIC

        # Check approved project folders
        for folder in self._approved_project_folders:
            if norm.startswith(_normalize(folder)):
                return FileAccessTier.PROJECT

        # Everything else is PROJECT (needs one-time confirmation)
        return FileAccessTier.PROJECT

    def check_access(
        self, path: str, operation: FilePermission
    ) -> tuple[bool, FileAccessTier, str]:
        """
        Check if a file operation is allowed.

        Returns:
            (allowed, tier, message)
            - allowed: Whether the operation can proceed
            - tier: The access tier for UI decisions
            - message: Warning/block message if applicable
        """
        tier = self.classify_path(path)

        if tier == FileAccessTier.FORBIDDEN:
            return (
                False,
                tier,
                f"🚫 Access to this location is blocked for safety:\n{path}\n"
                "This path is in a protected zone (System32, SSH keys, credentials).",
            )

        if tier == FileAccessTier.SYSTEM:
            if operation in (FilePermission.WRITE, FilePermission.READ_WRITE):
                return (
                    False,
                    tier,
                    f"🔴 Writing to system directories is not allowed:\n{path}",
                )
            return (
                True,
                tier,
                f"⚠️ Reading from a system directory:\n{path}",
            )

        if tier == FileAccessTier.SENSITIVE:
            return (
                True,
                tier,
                f"⚠️ This file may contain sensitive data (API keys, passwords, private keys):\n"
                f"{Path(path).name}\nProceed with caution.",
            )

        if tier == FileAccessTier.PUBLIC:
            # Desktop/Downloads are read-only
            norm = _normalize(path)
            desktop_norm = _normalize(str(self._desktop))
            downloads_norm = _normalize(str(self._downloads))
            if (norm.startswith(desktop_norm) or norm.startswith(downloads_norm)):
                if operation in (FilePermission.WRITE, FilePermission.READ_WRITE):
                    return (
                        True,
                        FileAccessTier.PROJECT,  # Escalate to confirm for writes
                        f"Writing to {Path(path).parent.name}/",
                    )
            return (True, tier, "")

        # PROJECT tier
        return (True, tier, f"Accessing project file: {Path(path).name}")

    def approve_project_folder(self, folder: str):
        """Mark a folder as approved for this session."""
        self._approved_project_folders.add(_normalize(folder))
        logger.info(f"FileAccess: approved project folder '{folder}'")

    def check_content_sensitivity(self, content: bytes) -> bool:
        """
        Check if file content contains sensitive patterns.
        Returns True if sensitive data is detected.
        """
        for pattern in SENSITIVE_CONTENT_PATTERNS:
            if re.search(pattern, content):
                return True
        return False


# ─── Singleton ────────────────────────────────────────────────────────
_fac_instance: FileAccessControl | None = None


def get_file_access() -> FileAccessControl:
    global _fac_instance
    if _fac_instance is None:
        _fac_instance = FileAccessControl()
    return _fac_instance
