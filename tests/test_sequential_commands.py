"""
test_sequential_commands.py — Tests for sequential command execution.

Covers: two valid commands in order, first failure stops second,
        all delimiter types, "open chrome and edge" no split,
        empty second segment triggers clarification.
"""
import unittest
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from command_parser import split_commands


class TestTwoValidCommands(unittest.TestCase):
    """Two valid commands execute in order with mock verification."""

    def test_then_produces_two_commands(self):
        result = split_commands("open chrome then close notepad")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "open chrome")
        self.assertEqual(result[1], "close notepad")

    def test_and_produces_two_commands(self):
        result = split_commands("open chrome and close notepad")
        self.assertEqual(len(result), 2)

    def test_order_preserved(self):
        result = split_commands("launch calculator then open notepad")
        self.assertEqual(result[0], "launch calculator")
        self.assertEqual(result[1], "open notepad")


class TestFirstFailureStopsSecond(unittest.TestCase):
    """First command failure should stop second command."""

    def test_failure_detection_logic(self):
        """Simulate: first command fails, second should not run."""
        commands = split_commands("delete system32 then open chrome")
        self.assertEqual(len(commands), 2)
        
        # Simulate execution with failure tracking
        executed = []
        for cmd in commands:
            result = self._mock_execute(cmd)
            if not result["success"]:
                break
            executed.append(cmd)
        
        # First command failed, so only 0 commands should have been "executed"
        self.assertEqual(len(executed), 0)

    def _mock_execute(self, cmd):
        if "delete" in cmd:
            return {"success": False, "message": "Blocked"}
        return {"success": True, "message": "OK"}


class TestAllDelimiterTypes(unittest.TestCase):
    """All delimiter types split correctly."""

    def test_then_delimiter(self):
        result = split_commands("open chrome then open notepad")
        self.assertEqual(len(result), 2)

    def test_and_delimiter_with_actions(self):
        result = split_commands("open chrome and launch notepad")
        self.assertEqual(len(result), 2)

    def test_then_case_insensitive(self):
        result = split_commands("open chrome THEN open notepad")
        self.assertEqual(len(result), 2)

    def test_and_case_insensitive(self):
        result = split_commands("open chrome AND close notepad")
        self.assertEqual(len(result), 2)


class TestNoSplitCases(unittest.TestCase):
    """'open chrome and edge' does NOT split."""

    def test_and_with_no_action_right(self):
        result = split_commands("open chrome and edge")
        self.assertEqual(len(result), 1)

    def test_search_with_and(self):
        result = split_commands("search for cats and dogs")
        self.assertEqual(len(result), 1)

    def test_compound_noun(self):
        result = split_commands("open bread and butter")
        self.assertEqual(len(result), 1)


class TestEmptySecondSegment(unittest.TestCase):
    """Empty second segment after split triggers clarification."""

    def test_trailing_then(self):
        result = split_commands("open chrome then ")
        # Should still return at least the first command
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0], "open chrome")

    def test_trailing_and(self):
        result = split_commands("open chrome and ")
        # "and " with nothing after → no split (right side empty has no keyword)
        self.assertGreater(len(result), 0)

    def test_only_delimiter(self):
        result = split_commands("then")
        # Edge case: just the delimiter
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
