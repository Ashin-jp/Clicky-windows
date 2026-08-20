"""
test_intent_router.py — Tests for IntentRouter classification and command_parser splitting.

Covers: DIRECT_ACTION, BROWSER_SEARCH, GUIDE_TO, EXPLAIN_ELEMENT,
        incomplete commands, "and"/"then" splitting, BLOCKED classification.
"""
import unittest
from unittest.mock import patch, MagicMock

# Import after conftest has set up mocks
from intent_router import IntentRouter, IntentResult
from groq_router import TaskType
from command_parser import split_commands
from constants import ACTION_KEYWORDS


class TestIntentRouterDirectAction(unittest.TestCase):
    """DIRECT_ACTION for open/run/click commands."""

    def setUp(self):
        self.router = IntentRouter()

    def test_open_chrome(self):
        result = self.router.classify("open chrome")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("RUN", result.action_tag)
        self.assertGreater(result.confidence, 0.8)

    def test_run_notepad(self):
        result = self.router.classify("run notepad")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("RUN", result.action_tag)

    def test_launch_calculator(self):
        result = self.router.classify("launch calculator")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("RUN", result.action_tag)

    def test_click_on_submit(self):
        result = self.router.classify("click on 'submit'")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("BROWSER_CLICK", result.action_tag)

    def test_close_app(self):
        result = self.router.classify("close spotify")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("CLOSE_APP", result.action_tag)


class TestIntentRouterBrowserSearch(unittest.TestCase):
    """BROWSER_SEARCH for site-specific searches."""

    def setUp(self):
        self.router = IntentRouter()

    def test_search_youtube(self):
        result = self.router.classify("search for cats on youtube")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("SITE_SEARCH", result.action_tag or "")

    def test_search_generic(self):
        result = self.router.classify("search for python tutorials")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("BROWSER_SEARCH", result.action_tag)

    def test_google_something(self):
        result = self.router.classify("google machine learning")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("BROWSER_SEARCH", result.action_tag)


class TestIntentRouterGuideTo(unittest.TestCase):
    """GUIDE_TO for where-is queries."""

    def setUp(self):
        self.router = IntentRouter()

    def test_where_is_save_button(self):
        result = self.router.classify("where is the save button")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("GUIDE_TO", result.action_tag)

    def test_show_me_where(self):
        result = self.router.classify("show me where the settings menu is")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("GUIDE_TO", result.action_tag)

    def test_locate_toolbar(self):
        result = self.router.classify("locate the toolbar")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("GUIDE_TO", result.action_tag)


class TestIntentRouterExplainElement(unittest.TestCase):
    """EXPLAIN_ELEMENT for what-is queries."""

    def setUp(self):
        self.router = IntentRouter()

    def test_what_is_this(self):
        result = self.router.classify("what is this")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("EXPLAIN_ELEMENT", result.action_tag)

    def test_what_does_this_do(self):
        result = self.router.classify("what does this do")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        self.assertIn("EXPLAIN_ELEMENT", result.action_tag)


class TestIntentRouterIncomplete(unittest.TestCase):
    """Incomplete / stop-word-only queries."""

    def setUp(self):
        self.router = IntentRouter()

    def test_empty_string(self):
        result = self.router.classify("")
        self.assertEqual(result.confidence, 0.0)

    def test_whitespace_only(self):
        result = self.router.classify("   ")
        self.assertEqual(result.confidence, 0.0)

    def test_none_input(self):
        result = self.router.classify(None)
        self.assertEqual(result.confidence, 0.0)


class TestIntentRouterActionKeywordsFromConstants(unittest.TestCase):
    """Verify ACTION_KEYWORDS is imported from constants, not local."""

    def test_action_keywords_matches(self):
        """intent_router.ACTION_KEYWORDS should be the exact same object as constants.ACTION_KEYWORDS."""
        from intent_router import ACTION_KEYWORDS as ir_kw
        from constants import ACTION_KEYWORDS as const_kw
        self.assertIs(ir_kw, const_kw)


class TestCommandParser(unittest.TestCase):
    """Tests for the split_commands pure function."""

    def test_single_command(self):
        result = split_commands("open chrome")
        self.assertEqual(result, ["open chrome"])

    def test_then_splits(self):
        result = split_commands("open chrome then open notepad")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "open chrome")
        self.assertEqual(result[1], "open notepad")

    def test_and_splits_when_both_have_action(self):
        """'and' splits when both sides contain action keywords."""
        result = split_commands("open chrome and close notepad")
        self.assertEqual(len(result), 2)
        self.assertIn("open chrome", result[0])
        self.assertIn("close notepad", result[1])

    def test_and_does_not_split_single_word_right(self):
        """'open chrome and edge' should NOT split — 'edge' has no action keyword."""
        result = split_commands("open chrome and edge")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "open chrome and edge")

    def test_then_with_three_segments(self):
        result = split_commands("open chrome then search for cats then close firefox")
        self.assertEqual(len(result), 3)

    def test_empty_input(self):
        result = split_commands("")
        self.assertEqual(result, [""])

    def test_none_input(self):
        result = split_commands(None)
        self.assertEqual(result, [""])

    def test_no_delimiter(self):
        result = split_commands("open the calculator please")
        self.assertEqual(len(result), 1)

    def test_and_with_no_action_right(self):
        """'search for dogs and cats' should NOT split — 'cats' is not an action."""
        result = split_commands("search for dogs and cats")
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
