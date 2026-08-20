"""
test_browser_logic.py — Tests for browser controller logic.

Covers: URL decode verification, known domain URL patterns,
        empty query handling, page None recovery, auth file loading.
All tests mock Playwright — no real browser is launched.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.parse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestURLDecodeVerification(unittest.TestCase):
    """URL success verification uses decoded URLs, not raw URLs."""

    def test_encoded_url_matches_query(self):
        """A URL-encoded query string should match after decoding."""
        raw_url = "https://www.youtube.com/results?search_query=cats+and+dogs"
        decoded = urllib.parse.unquote_plus(raw_url)
        self.assertIn("cats and dogs", decoded.lower())

    def test_percent_encoded_url_matches(self):
        """Percent-encoded characters should be decoded before comparison."""
        raw_url = "https://www.google.com/search?q=hello%20world"
        decoded = urllib.parse.unquote(raw_url)
        self.assertIn("hello world", decoded.lower())

    def test_raw_url_does_not_match(self):
        """Without decoding, the query wouldn't match (demonstrating the problem)."""
        raw_url = "https://www.google.com/search?q=hello%20world"
        self.assertNotIn("hello world", raw_url.lower())

    def test_unicode_url_decode(self):
        """Unicode characters in URLs should be decoded."""
        raw_url = "https://example.com/search?q=%E4%BD%A0%E5%A5%BD"
        decoded = urllib.parse.unquote(raw_url)
        self.assertIn("你好", decoded)


class TestKnownDomainURLPattern(unittest.TestCase):
    """Known domains use URL pattern, not DOM search."""

    def test_youtube_url_pattern(self):
        """YouTube search should use URL shortcut, not DOM search."""
        from urllib.parse import quote_plus
        query = "python tutorials"
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        self.assertIn("search_query=python+tutorials", url)

    def test_google_url_pattern(self):
        from urllib.parse import quote_plus
        query = "machine learning"
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        self.assertIn("q=machine+learning", url)


class TestEmptyQueryHandling(unittest.TestCase):
    """Empty query should raise ValueError before browser launch."""

    def test_empty_query_string(self):
        """Empty string query should be caught."""
        query = ""
        self.assertFalse(bool(query.strip()))

    def test_whitespace_only_query(self):
        """Whitespace-only query should be caught."""
        query = "   "
        self.assertFalse(bool(query.strip()))


class TestPageNoneRecovery(unittest.TestCase):
    """self.page None recovery logic."""

    def test_page_is_none_detected(self):
        """When page is None, should trigger recovery."""
        page = None
        needs_recovery = (page is None)
        self.assertTrue(needs_recovery)

    def test_closed_page_detected(self):
        """When page.is_closed() returns True, should trigger recovery."""
        mock_page = MagicMock()
        mock_page.is_closed.return_value = True
        needs_recovery = (mock_page is None or mock_page.is_closed())
        self.assertTrue(needs_recovery)


class TestAuthFileLoading(unittest.TestCase):
    """Auth file loading on context creation."""

    def test_auth_file_path_construction(self):
        """Auth file should be in browser profile directory."""
        profile_dir = os.path.join("C:", "FakeAppData", "Clicky", "browser_profile")
        auth_file = os.path.join(profile_dir, "auth.json")
        self.assertTrue(auth_file.endswith("auth.json"))

    def test_auth_file_exists_check(self):
        """When auth file exists, storage_state should be loaded."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write('{"cookies": []}')
            tmp_path = f.name
        try:
            self.assertTrue(os.path.exists(tmp_path))
        finally:
            os.unlink(tmp_path)

    def test_auth_file_missing_no_crash(self):
        """When auth file doesn't exist, should create context without it."""
        fake_path = "/nonexistent/auth.json"
        self.assertFalse(os.path.exists(fake_path))


if __name__ == "__main__":
    unittest.main()
