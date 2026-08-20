"""
test_trust_engine.py — Tests for TrustEngine logic.

Covers: session trust clears between restarts, CONFIRM_ONCE fires once
        then silent, ALWAYS_CONFIRM fires every time, BLOCKED rejected
        before API call, key normalization deduplicates, per-app context trust.
"""
import unittest
import sqlite3
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class MockDB:
    """In-memory SQLite database mimicking ClickyDatabase for trust tests."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE session_trust (
                command_prefix TEXT PRIMARY KEY,
                approved_at TEXT NOT NULL,
                session_id TEXT NOT NULL,
                expiry_type TEXT DEFAULT 'session',
                app_context TEXT DEFAULT '',
                expires_at TEXT
            );
        """)

    def is_command_approved(self, prefix, session_id):
        row = self._conn.execute(
            "SELECT 1 FROM session_trust WHERE command_prefix=? AND session_id=?",
            (prefix, session_id),
        ).fetchone()
        return row is not None

    def approve_command(self, prefix, session_id, expiry_type="session", app_context=""):
        from datetime import datetime
        self._conn.execute(
            "INSERT OR REPLACE INTO session_trust (command_prefix, approved_at, session_id, expiry_type, app_context) "
            "VALUES (?, ?, ?, ?, ?)",
            (prefix, datetime.now().isoformat(), session_id, expiry_type, app_context),
        )
        self._conn.commit()

    def clear_session_trust(self):
        self._conn.execute("DELETE FROM session_trust")
        self._conn.commit()

    def log_action(self, action_type, params, trust_level, result):
        pass

    def close(self):
        self._conn.close()


class TestSessionTrustClearsBetweenRestarts(unittest.TestCase):
    """Session trust should clear between engine restarts."""

    def test_new_session_clears_old(self):
        db = MockDB()
        db.approve_command("RUN:notepad", "session_old")
        # Simulate restart — new session
        db.clear_session_trust()
        self.assertFalse(db.is_command_approved("RUN:notepad", "session_new"))

    def test_same_session_remembers(self):
        db = MockDB()
        session = "session_123"
        db.approve_command("RUN:notepad", session)
        self.assertTrue(db.is_command_approved("RUN:notepad", session))


class TestConfirmOnce(unittest.TestCase):
    """CONFIRM_ONCE fires once then silent."""

    def test_first_time_needs_confirm(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        # RUN is CONFIRM_ONCE by default
        trust = engine.get_trust_level("RUN")
        self.assertEqual(trust, TrustLevel.CONFIRM_ONCE)

    def test_after_approval_is_silent(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        # Approve
        engine.record_approval("RUN", "notepad")
        # Next check should return SILENT
        can_run, level, reason = engine.should_execute("RUN", "notepad")
        self.assertTrue(can_run)
        self.assertEqual(level, TrustLevel.SILENT)
        self.assertIn("Previously approved", reason)


class TestAlwaysConfirm(unittest.TestCase):
    """ALWAYS_CONFIRM fires every time."""

    def test_always_confirm_for_write_file(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        trust = engine.get_trust_level("WRITE_FILE")
        self.assertEqual(trust, TrustLevel.ALWAYS_CONFIRM)

    def test_always_confirm_does_not_remember(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        # Execute should_execute twice — both should return ALWAYS_CONFIRM
        can_run1, level1, _ = engine.should_execute("WRITE_FILE", "test.txt")
        can_run2, level2, _ = engine.should_execute("WRITE_FILE", "test.txt")
        self.assertEqual(level1, TrustLevel.ALWAYS_CONFIRM)
        self.assertEqual(level2, TrustLevel.ALWAYS_CONFIRM)


class TestBlockedBeforeAPI(unittest.TestCase):
    """BLOCKED actions rejected before any API call."""

    def test_blocked_command(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        can_run, level, reason = engine.should_execute("RUN_CMD", "format C:")
        self.assertFalse(can_run)
        self.assertEqual(level, TrustLevel.BLOCKED)
        self.assertIn("blocked", reason.lower())

    def test_blocked_rm_rf(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        can_run, level, _ = engine.should_execute("RUN_CMD", "rm -rf /")
        self.assertFalse(can_run)
        self.assertEqual(level, TrustLevel.BLOCKED)

    def test_blocked_empty_command(self):
        from trust_engine import TrustEngine, TrustLevel
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test_session"
        engine._db = MockDB()

        can_run, level, _ = engine.should_execute("RUN_CMD", "")
        self.assertFalse(can_run)
        self.assertEqual(level, TrustLevel.BLOCKED)


class TestKeyNormalization(unittest.TestCase):
    """Key normalization deduplicates malformed keys."""

    def test_duplicate_prefix_deduplicated(self):
        from trust_engine import TrustEngine
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test"
        engine._db = MockDB()

        key = engine._normalize_key("RUN:RUN")
        self.assertEqual(key, "RUN")

    def test_whitespace_stripped(self):
        from trust_engine import TrustEngine
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test"
        engine._db = MockDB()

        key = engine._normalize_key("  RUN:notepad  ")
        self.assertEqual(key, "RUN:notepad")

    def test_special_chars_removed(self):
        from trust_engine import TrustEngine
        engine = TrustEngine.__new__(TrustEngine)
        engine._session_id = "test"
        engine._db = MockDB()

        key = engine._normalize_key("RUN:note pad!")
        # Only word characters and colons survive
        self.assertNotIn("!", key)
        self.assertNotIn(" ", key)


class TestPerAppContextTrust(unittest.TestCase):
    """Per-app context trust stored correctly."""

    def test_trust_per_app(self):
        db = MockDB()
        db.approve_command("CLICK:submit", "session_1", app_context="chrome.exe")
        
        # Should find it for the matching app
        from datetime import datetime
        row = db._conn.execute(
            "SELECT 1 FROM session_trust WHERE command_prefix=? AND app_context=?",
            ("CLICK:submit", "chrome.exe")
        ).fetchone()
        self.assertIsNotNone(row)

    def test_different_app_not_approved(self):
        db = MockDB()
        db.approve_command("CLICK:submit", "session_1", app_context="chrome.exe")
        
        # Different app context should not match
        row = db._conn.execute(
            "SELECT 1 FROM session_trust WHERE command_prefix=? AND app_context=?",
            ("CLICK:submit", "notepad.exe")
        ).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
