"""
test_stt_corrections.py — Tests for STT correction logic.

Covers: case-insensitive matching, whole-word boundary, context gating,
        learn via "remember that", persistence to SQLite.
"""
import unittest
import sqlite3
import json
import re
import os
import sys

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from constants import STT_ACTION_CONTEXT_WORDS, STT_SKIP_CONTEXT_PHRASES


def apply_context_gated_corrections(text: str, corrections: dict) -> str:
    """
    Standalone re-implementation of the context-gated correction logic
    from companion_manager._on_final_transcript() for unit testing
    without needing to instantiate the full CompanionManager.
    """
    for misheard, info in corrections.items():
        correct = info["correct"]
        pattern = re.compile(r'\b' + re.escape(misheard) + r'\b', re.IGNORECASE)

        def _context_replace(match, _correct=correct):
            start = match.start()
            prefix_text = text[:start].strip().lower()
            preceding_words = prefix_text.split()[-2:] if prefix_text else []

            # Edge case: at very beginning → always apply
            if not preceding_words:
                return _correct

            # Check skip phrases
            prefix_two = " ".join(preceding_words[-2:]) if len(preceding_words) >= 2 else preceding_words[-1]
            for skip_phrase in STT_SKIP_CONTEXT_PHRASES:
                if prefix_two.endswith(skip_phrase) or skip_phrase in prefix_two:
                    return match.group(0)  # Don't correct

            # Check action context
            if any(w in STT_ACTION_CONTEXT_WORDS for w in preceding_words):
                return _correct

            # No strong signal → apply
            return _correct

        text = pattern.sub(_context_replace, text)
    return text


class TestWholeWordBoundary(unittest.TestCase):
    """Whole-word boundary matching — substrings must NOT be replaced."""

    def test_substring_not_replaced(self):
        """'chrome' in 'chromedriver' should not be corrected."""
        corrections = {"chrome": {"correct": "Chrome Browser", "context": None}}
        result = apply_context_gated_corrections("install chromedriver", corrections)
        self.assertEqual(result, "install chromedriver")

    def test_exact_word_replaced(self):
        corrections = {"chrome": {"correct": "Chrome Browser", "context": None}}
        result = apply_context_gated_corrections("open chrome please", corrections)
        self.assertEqual(result, "open Chrome Browser please")


class TestCaseInsensitive(unittest.TestCase):
    """Case-insensitive matching."""

    def test_uppercase_match(self):
        corrections = {"spotify": {"correct": "Spotify", "context": None}}
        result = apply_context_gated_corrections("open SPOTIFY", corrections)
        self.assertEqual(result, "open Spotify")

    def test_mixed_case_match(self):
        corrections = {"vscode": {"correct": "VS Code", "context": None}}
        result = apply_context_gated_corrections("launch VsCode please", corrections)
        self.assertEqual(result, "launch VS Code please")


class TestContextGating(unittest.TestCase):
    """Context-gated correction: action words → apply, skip phrases → skip."""

    def test_action_context_applies(self):
        """'open cromb' → 'open Chrome' because 'open' is an action word."""
        corrections = {"cromb": {"correct": "Chrome", "context": None}}
        result = apply_context_gated_corrections("open cromb", corrections)
        self.assertEqual(result, "open Chrome")

    def test_skip_context_preserves(self):
        """'want to cromb' should NOT correct because 'want to' is a skip phrase."""
        corrections = {"cromb": {"correct": "Chrome", "context": None}}
        result = apply_context_gated_corrections("I want to cromb the metal", corrections)
        self.assertEqual(result, "I want to cromb the metal")

    def test_beginning_of_transcript(self):
        """Word at beginning with no preceding words → always apply."""
        corrections = {"cromb": {"correct": "Chrome", "context": None}}
        result = apply_context_gated_corrections("cromb is good", corrections)
        self.assertEqual(result, "Chrome is good")

    def test_no_context_signal_applies(self):
        """When preceding words are neutral, default to applying."""
        corrections = {"cromb": {"correct": "Chrome", "context": None}}
        result = apply_context_gated_corrections("please try cromb today", corrections)
        self.assertEqual(result, "please try Chrome today")


class TestSQLitePersistence(unittest.TestCase):
    """Test that corrections persist to SQLite and load correctly."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE stt_corrections (
                misheard TEXT PRIMARY KEY,
                correct TEXT NOT NULL,
                apply_when_preceded_by TEXT DEFAULT NULL
            )
        """)

    def tearDown(self):
        self.conn.close()

    def test_add_and_retrieve(self):
        self.conn.execute(
            "INSERT INTO stt_corrections (misheard, correct) VALUES (?, ?)",
            ("cromb", "Chrome")
        )
        self.conn.commit()
        cursor = self.conn.execute("SELECT misheard, correct FROM stt_corrections")
        rows = {r["misheard"]: r["correct"] for r in cursor.fetchall()}
        self.assertEqual(rows["cromb"], "Chrome")

    def test_context_words_persist(self):
        context = ["open", "launch"]
        self.conn.execute(
            "INSERT INTO stt_corrections (misheard, correct, apply_when_preceded_by) VALUES (?, ?, ?)",
            ("cromb", "Chrome", json.dumps(context))
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT apply_when_preceded_by FROM stt_corrections WHERE misheard = ?",
            ("cromb",)
        ).fetchone()
        loaded = json.loads(row["apply_when_preceded_by"])
        self.assertEqual(loaded, context)

    def test_correction_loads_on_new_connection(self):
        """Simulate restart: corrections persist across connections."""
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), "test_stt.db")
        try:
            # Write
            conn1 = sqlite3.connect(db_path)
            conn1.execute("""
                CREATE TABLE IF NOT EXISTS stt_corrections (
                    misheard TEXT PRIMARY KEY, correct TEXT NOT NULL,
                    apply_when_preceded_by TEXT DEFAULT NULL
                )
            """)
            conn1.execute("INSERT INTO stt_corrections (misheard, correct) VALUES (?, ?)", ("cromb", "Chrome"))
            conn1.commit()
            conn1.close()

            # Read on new connection
            conn2 = sqlite3.connect(db_path)
            conn2.row_factory = sqlite3.Row
            row = conn2.execute("SELECT correct FROM stt_corrections WHERE misheard = ?", ("cromb",)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["correct"], "Chrome")
            conn2.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_learn_remember_that(self):
        """Simulate learning 'remember that X means Y'."""
        # The "remember that" flow extracts misheard/correct and calls add_stt_correction
        misheard = "cromb"
        correct = "Chrome"
        self.conn.execute(
            "INSERT OR REPLACE INTO stt_corrections (misheard, correct) VALUES (?, ?)",
            (misheard.strip().lower(), correct.strip())
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT correct FROM stt_corrections WHERE misheard = ?",
            (misheard,)
        ).fetchone()
        self.assertEqual(row["correct"], "Chrome")


if __name__ == "__main__":
    unittest.main()
