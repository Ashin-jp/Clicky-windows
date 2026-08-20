"""
site_search.py — Universal site search profiles and detection.

Maintains a SQLite-backed database of per-domain search profiles
(URL patterns, selectors, result wait selectors) and provides
auto-detection strategies for unknown sites.
"""

import json
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from storage import get_db

logger = logging.getLogger(__name__)

# ─── Pre-populated Profiles ──────────────────────────────────────────
DEFAULT_PROFILES = [
    {
        "domain": "youtube.com",
        "url_pattern": "https://www.youtube.com/results?search_query=QUERY",
        "selector_list": ["input#search", "input[name='search_query']", "[placeholder='Search']"],
        "result_wait": "ytd-video-renderer, ytd-rich-item-renderer",
        "requires_login": False,
    },
    {
        "domain": "google.com",
        "url_pattern": "https://www.google.com/search?q=QUERY",
        "selector_list": ["textarea[name='q']", "input[name='q']", "[role='combobox']"],
        "result_wait": "#search, #rso",
        "requires_login": False,
    },
    {
        "domain": "reddit.com",
        "url_pattern": "https://www.reddit.com/search/?q=QUERY",
        "selector_list": ["input[name='q']", "#search-input"],
        "result_wait": None,
        "requires_login": False,
    },
    {
        "domain": "github.com",
        "url_pattern": "https://github.com/search?q=QUERY",
        "selector_list": ["input[name='q']", "#query-builder-test"],
        "result_wait": None,
        "requires_login": False,
    },
    {
        "domain": "wikipedia.org",
        "url_pattern": "https://en.wikipedia.org/wiki/Special:Search?search=QUERY",
        "selector_list": ["input[name='search']", "#searchInput"],
        "result_wait": ".mw-search-results",
        "requires_login": False,
    },
    {
        "domain": "amazon.com",
        "url_pattern": "https://www.amazon.com/s?k=QUERY",
        "selector_list": ["input#twotabsearchtextbox", "input[name='field-keywords']"],
        "result_wait": ".s-result-item",
        "requires_login": False,
    },
    {
        "domain": "twitter.com",
        "url_pattern": "https://twitter.com/search?q=QUERY",
        "selector_list": ["input[data-testid='SearchBox_Search_Input']"],
        "result_wait": None,
        "requires_login": False,
    },
    {
        "domain": "x.com",
        "url_pattern": "https://x.com/search?q=QUERY",
        "selector_list": ["input[data-testid='SearchBox_Search_Input']"],
        "result_wait": None,
        "requires_login": False,
    },
    {
        "domain": "stackoverflow.com",
        "url_pattern": "https://stackoverflow.com/search?q=QUERY",
        "selector_list": ["input[name='q']", ".s-input__search"],
        "result_wait": ".s-post-summary",
        "requires_login": False,
    },
    {
        "domain": "netflix.com",
        "url_pattern": None,
        "selector_list": ["input[data-uia='search-box']", "input[type='search']"],
        "result_wait": None,
        "requires_login": True,
    },
    {
        "domain": "mail.google.com",
        "url_pattern": None,
        "selector_list": ["input[placeholder='Search mail']", "input[aria-label='Search mail']"],
        "result_wait": None,
        "requires_login": True,
    },
]

# Common name → domain mapping
SITE_ALIASES = {
    "youtube": "youtube.com",
    "yt": "youtube.com",
    "google": "google.com",
    "reddit": "reddit.com",
    "github": "github.com",
    "gh": "github.com",
    "wikipedia": "wikipedia.org",
    "wiki": "wikipedia.org",
    "amazon": "amazon.com",
    "twitter": "twitter.com",
    "x": "x.com",
    "stackoverflow": "stackoverflow.com",
    "stack overflow": "stackoverflow.com",
    "netflix": "netflix.com",
    "gmail": "mail.google.com",
}


class SiteSearchManager:
    """Manages per-domain search profiles in SQLite."""

    def __init__(self):
        self._db = get_db()
        self._ensure_table()
        self._seed_defaults()

    def _ensure_table(self):
        self._db._conn.execute("""
            CREATE TABLE IF NOT EXISTS site_search_profiles (
                domain TEXT PRIMARY KEY,
                selector_list TEXT DEFAULT '[]',
                url_pattern TEXT,
                result_wait_selector TEXT,
                requires_login INTEGER DEFAULT 0,
                last_verified TEXT
            )
        """)
        self._db._conn.commit()

    def _seed_defaults(self):
        """Insert default profiles if table is empty."""
        count = self._db._conn.execute(
            "SELECT COUNT(*) FROM site_search_profiles"
        ).fetchone()[0]
        if count > 0:
            return

        for p in DEFAULT_PROFILES:
            self._db._conn.execute(
                "INSERT OR IGNORE INTO site_search_profiles "
                "(domain, selector_list, url_pattern, result_wait_selector, requires_login) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    p["domain"],
                    json.dumps(p["selector_list"]),
                    p.get("url_pattern"),
                    p.get("result_wait"),
                    int(p.get("requires_login", False)),
                ),
            )
        self._db._conn.commit()
        logger.info(f"SiteSearch: seeded {len(DEFAULT_PROFILES)} default profiles")

    def get_profile(self, domain: str) -> Optional[dict]:
        """Get search profile for a domain. Tries exact match then suffix match."""
        # Exact match
        row = self._db._conn.execute(
            "SELECT * FROM site_search_profiles WHERE domain = ?", (domain,)
        ).fetchone()

        # Suffix match (e.g., "www.youtube.com" matches "youtube.com")
        if not row:
            row = self._db._conn.execute(
                "SELECT * FROM site_search_profiles WHERE ? LIKE '%' || domain",
                (domain,),
            ).fetchone()

        if row:
            return {
                "domain": row["domain"],
                "selector_list": json.loads(row["selector_list"]) if row["selector_list"] else [],
                "url_pattern": row["url_pattern"],
                "result_wait": row["result_wait_selector"],
                "requires_login": bool(row["requires_login"]),
            }
        return None

    def save_profile(self, domain: str, selector: str, url_pattern: str = None,
                     result_wait: str = None):
        """Save or update a search profile after successful search."""
        existing = self.get_profile(domain)
        if existing:
            # Prepend new selector to priority list if not already first
            selectors = existing["selector_list"]
            if selector and selector not in selectors:
                selectors.insert(0, selector)
            self._db._conn.execute(
                "UPDATE site_search_profiles SET selector_list=?, url_pattern=?, "
                "result_wait_selector=?, last_verified=datetime('now') WHERE domain=?",
                (json.dumps(selectors), url_pattern or existing.get("url_pattern"),
                 result_wait or existing.get("result_wait"), domain),
            )
        else:
            self._db._conn.execute(
                "INSERT INTO site_search_profiles "
                "(domain, selector_list, url_pattern, result_wait_selector, last_verified) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (domain, json.dumps([selector] if selector else []), url_pattern, result_wait),
            )
        self._db._conn.commit()

    def resolve_domain(self, name: str) -> str:
        """Resolve a common site name to its domain."""
        return SITE_ALIASES.get(name.lower().strip(), name.lower().strip())

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract domain from a URL."""
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            return parsed.netloc.lstrip("www.")
        except Exception:
            return url


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[SiteSearchManager] = None


def get_site_search() -> SiteSearchManager:
    global _instance
    if _instance is None:
        _instance = SiteSearchManager()
    return _instance
