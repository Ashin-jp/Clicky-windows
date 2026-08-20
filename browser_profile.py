"""
browser_profile.py — Browser profile and form-fill data management.

Stores the user's preferred Chrome profile name, search engine, and
basic form-fill data (name, email — never passwords) in SQLite.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from storage import get_db

logger = logging.getLogger(__name__)


@dataclass
class BrowserProfile:
    """User's browser profile settings."""
    profile_name: str = "Default"
    search_engine: str = "https://www.google.com/search?q="
    form_data: dict = field(default_factory=dict)
    is_default: bool = True


class BrowserProfileManager:
    """Manages browser profiles and form-fill data in SQLite."""

    def __init__(self):
        self._db = get_db()
        self._ensure_table()

    def _ensure_table(self):
        """Create browser_profiles table if it doesn't exist."""
        self._db._conn.execute("""
            CREATE TABLE IF NOT EXISTS browser_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL DEFAULT 'Default',
                search_engine TEXT NOT NULL DEFAULT 'https://www.google.com/search?q=',
                form_data TEXT DEFAULT '{}',
                is_default INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._db._conn.commit()

    def get_default_profile(self) -> BrowserProfile:
        """Get the default browser profile, creating one if none exists."""
        row = self._db._conn.execute(
            "SELECT profile_name, search_engine, form_data, is_default "
            "FROM browser_profiles WHERE is_default = 1 LIMIT 1"
        ).fetchone()

        if row:
            form_data = {}
            try:
                form_data = json.loads(row["form_data"]) if row["form_data"] else {}
            except (json.JSONDecodeError, TypeError):
                pass
            return BrowserProfile(
                profile_name=row["profile_name"],
                search_engine=row["search_engine"],
                form_data=form_data,
                is_default=bool(row["is_default"]),
            )

        # Create default profile
        profile = BrowserProfile()
        self.save_profile(profile)
        return profile

    def save_profile(self, profile: BrowserProfile):
        """Save or update a browser profile."""
        form_json = json.dumps(profile.form_data)

        existing = self._db._conn.execute(
            "SELECT id FROM browser_profiles WHERE profile_name = ?",
            (profile.profile_name,),
        ).fetchone()

        if existing:
            self._db._conn.execute(
                "UPDATE browser_profiles SET search_engine=?, form_data=?, "
                "is_default=?, updated_at=datetime('now') WHERE id=?",
                (profile.search_engine, form_json, int(profile.is_default), existing["id"]),
            )
        else:
            self._db._conn.execute(
                "INSERT INTO browser_profiles (profile_name, search_engine, form_data, is_default) "
                "VALUES (?, ?, ?, ?)",
                (profile.profile_name, profile.search_engine, form_json, int(profile.is_default)),
            )
        self._db._conn.commit()
        logger.info(f"BrowserProfile: saved '{profile.profile_name}'")

    def update_form_data(self, key: str, value: str):
        """Update a single form-fill field (name, email, phone, etc.)."""
        profile = self.get_default_profile()
        profile.form_data[key] = value
        self.save_profile(profile)

    def get_form_data(self) -> dict:
        """Get all form-fill data for the default profile."""
        return self.get_default_profile().form_data

    def set_search_engine(self, engine_url: str):
        """Update the default search engine URL."""
        profile = self.get_default_profile()
        profile.search_engine = engine_url
        self.save_profile(profile)


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[BrowserProfileManager] = None


def get_browser_profile_manager() -> BrowserProfileManager:
    global _instance
    if _instance is None:
        _instance = BrowserProfileManager()
    return _instance
