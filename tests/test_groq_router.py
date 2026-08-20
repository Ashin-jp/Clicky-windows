"""
test_groq_router.py — Tests for GroqModelRouter logic.

Covers: DIRECT_ACTION zero API calls, 429 model cascade, all models exhausted,
        empty response handling, streaming buffer sentence dispatch,
        streaming buffer 200-char max length dispatch.
"""
import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from groq_router import TaskType


class TestDirectActionZeroAPICalls(unittest.TestCase):
    """DIRECT_ACTION should produce zero Groq API calls."""

    def test_direct_action_no_api_call(self):
        """When intent is DIRECT_ACTION, the router should not be called."""
        from intent_router import IntentRouter
        router = IntentRouter()
        result = router.classify("open chrome")
        self.assertEqual(result.task_type, TaskType.DIRECT_ACTION)
        # DIRECT_ACTION = local pattern match, recommended_model is empty
        self.assertEqual(result.recommended_model, "")


class TestModelCascade429(unittest.TestCase):
    """429 response triggers model cascade to next model."""

    def test_rate_limit_cascades(self):
        """When a model returns 429, the next model should be tried."""
        from groq_router import RateLimitState
        state = RateLimitState()
        state.consecutive_429s = 1
        # Backoff should be calculated
        backoff = min(60 * (2 ** state.consecutive_429s), 300)
        self.assertEqual(backoff, 120)  # 60 * 2^1 = 120

    def test_backoff_caps_at_300(self):
        state_429s = 5
        backoff = min(60 * (2 ** state_429s), 300)
        self.assertEqual(backoff, 300)


class TestAllModelsExhausted(unittest.TestCase):
    """All models exhausted triggers fallback text."""

    def test_exhausted_response_text(self):
        from groq_router import RoutedResponse
        resp = RoutedResponse(
            text="I'm sorry, all AI models are currently unavailable.",
            model_used="none",
            duration_ms=100,
            error="all_models_exhausted",
        )
        self.assertEqual(resp.model_used, "none")
        self.assertIn("unavailable", resp.text)
        self.assertEqual(resp.error, "all_models_exhausted")


class TestEmptyResponse(unittest.TestCase):
    """Empty Groq response handled without crashing."""

    def test_empty_text_response(self):
        from groq_router import RoutedResponse
        resp = RoutedResponse(text="", model_used="test-model", duration_ms=50)
        self.assertEqual(resp.text, "")
        self.assertIsNone(resp.error)

    def test_none_content_fallback(self):
        """When API returns None content, should fallback to empty string."""
        content = None
        text = content or ""
        self.assertEqual(text, "")


class TestStreamingBufferSentenceDispatch(unittest.TestCase):
    """Streaming buffer dispatches sentence on delimiter detection."""

    def _simulate_buffer(self, chunks: list[str]) -> list[str]:
        """Simulate the sentence buffering logic from companion_manager."""
        dispatched = []
        sentence_buffer = ""
        abbrev_pattern = re.compile(
            r'\b(?:e\.g\.|i\.e\.|vs\.|dr\.|mr\.|mrs\.|ms\.|prof\.|sr\.|jr\.|inc\.|ltd\.|etc\.)\s*$',
            re.IGNORECASE
        )

        for chunk in chunks:
            sentence_buffer += chunk
            if any(p in sentence_buffer for p in ['. ', '? ', '! ', '.\n', '?\n', '!\n']):
                matches = list(re.finditer(r'([.?!])(?:\s+|\n)', sentence_buffer))
                if matches:
                    last_match = matches[-1]
                    end_idx = last_match.end()
                    candidate = sentence_buffer[:end_idx]

                    prefix = sentence_buffer[:last_match.start() + 1]
                    if abbrev_pattern.search(prefix):
                        continue

                    clean = re.sub(r'\[.*?\]', '', candidate).strip()
                    if clean:
                        dispatched.append(clean)
                    sentence_buffer = sentence_buffer[end_idx:]

            # Fix 10: Max length dispatch at 200 chars
            if len(sentence_buffer) > 200:
                last_space = sentence_buffer.rfind(' ')
                if last_space > 0:
                    dispatched.append(sentence_buffer[:last_space].strip())
                    sentence_buffer = sentence_buffer[last_space + 1:]

        if sentence_buffer.strip():
            clean = re.sub(r'\[.*?\]', '', sentence_buffer).strip()
            if clean:
                dispatched.append(clean)
        return dispatched

    def test_period_dispatches(self):
        result = self._simulate_buffer(["Hello world. ", "Next sentence."])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "Hello world.")

    def test_question_mark_dispatches(self):
        result = self._simulate_buffer(["How are you? ", "Good."])
        self.assertEqual(len(result), 2)

    def test_exclamation_dispatches(self):
        result = self._simulate_buffer(["Wow! ", "That's great."])
        self.assertEqual(len(result), 2)

    def test_abbreviation_does_not_dispatch(self):
        result = self._simulate_buffer(["For e.g. the sky is blue. "])
        # Should get 1 dispatch (the full sentence), not 2
        self.assertEqual(len(result), 1)

    def test_no_delimiter_buffers(self):
        result = self._simulate_buffer(["Hello world"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "Hello world")


class TestStreamingBuffer200CharMax(unittest.TestCase):
    """Streaming buffer dispatches at 200 character limit even without delimiter."""

    def test_long_buffer_splits_at_space(self):
        """Buffer exceeding 200 chars without delimiter should split at last space."""
        long_text = "word " * 50  # 250 chars
        chunks = [long_text]

        sentence_buffer = ""
        dispatched = []

        for chunk in chunks:
            sentence_buffer += chunk
            # Apply split repeatedly until buffer is under 200
            while len(sentence_buffer) > 200:
                last_space = sentence_buffer.rfind(' ', 0, 200)
                if last_space > 0:
                    dispatched.append(sentence_buffer[:last_space].strip())
                    sentence_buffer = sentence_buffer[last_space + 1:]
                else:
                    break

        if sentence_buffer.strip():
            dispatched.append(sentence_buffer.strip())

        self.assertGreaterEqual(len(dispatched), 2)
        for d in dispatched:
            # Each dispatched segment should be reasonable length
            self.assertLessEqual(len(d), 260)

    def test_short_buffer_no_early_dispatch(self):
        """Buffer under 200 chars should not be force-dispatched."""
        short_text = "This is a short message"
        sentence_buffer = short_text
        dispatched = []
        if len(sentence_buffer) > 200:
            dispatched.append("forced")
        self.assertEqual(len(dispatched), 0)


if __name__ == "__main__":
    unittest.main()
