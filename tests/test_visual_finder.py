"""
test_visual_finder.py — Unit Tests for VisualFinder

Tests the two-pass zoom locator, coordinate mapper, response strategy selector,
quadrant splitter, and mock LLM vision fallback flows.
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import visual_finder
from visual_finder import (
    split_into_quadrants,
    map_to_screen_coordinates,
    determine_response_strategy,
    VisualFinder,
)


class TestVisualFinder(unittest.TestCase):

    def setUp(self):
        # Create a mock 1920x1080 screen image
        self.mock_screen = Image.new("RGB", (1920, 1080), color="black")
        self.mock_screen.info['display_x'] = 100
        self.mock_screen.info['display_y'] = 200
        self.mock_screen.info['display_width'] = 1920
        self.mock_screen.info['display_height'] = 1080

    def test_quadrant_splitting(self):
        """Verify quadrant splitter returns correct sizes and bounds."""
        quads = split_into_quadrants(self.mock_screen)
        
        self.assertEqual(quads['top_left'].size, (960, 540))
        self.assertEqual(quads['top_right'].size, (960, 540))
        self.assertEqual(quads['bottom_left'].size, (960, 540))
        self.assertEqual(quads['bottom_right'].size, (960, 540))
        
        bounds = quads['quadrant_bounds']
        self.assertEqual(bounds['top_left'], (0, 0, 960, 540))
        self.assertEqual(bounds['top_right'], (960, 0, 1920, 540))

    def test_coordinate_mapping(self):
        """Test mapping crop percentage coordinates back to screen pixels."""
        # Crop: top_right quadrant bounds: (960, 0, 1920, 540)
        # Relative coordinates inside crop: x=0.5, y=0.5 (center of crop)
        # Display offset: (100, 200)
        quadrant_bounds = (960, 0, 1920, 540)
        relative_coords = {"x": 0.5, "y": 0.5}
        display_offset = (100, 200)
        
        screen_x, screen_y = map_to_screen_coordinates(
            quadrant_bounds,
            relative_coords,
            display_offset
        )
        
        # Crop center: 960 + 0.5*(1920-960) = 1440. Local.
        # Global: 1440 + 100 = 1540
        self.assertEqual(screen_x, 1540)
        
        # Crop center: 0 + 0.5*(540-0) = 270. Local.
        # Global: 270 + 200 = 470
        self.assertEqual(screen_y, 470)

    def test_response_strategy(self):
        """Verify correct strategy selection based on confidence ratings."""
        self.assertEqual(determine_response_strategy(0.95), "tight")
        self.assertEqual(determine_response_strategy(0.80), "tight")
        self.assertEqual(determine_response_strategy(0.70), "medium")
        self.assertEqual(determine_response_strategy(0.50), "broad")
        self.assertEqual(determine_response_strategy(0.30), "verbal")

    @patch("visual_finder.capture_screen")
    def test_visual_finder_high_confidence(self, mock_capture):
        """Test successful identification with tight highlight."""
        mock_capture.return_value = self.mock_screen
        
        # Mock LLM Client
        mock_llm = MagicMock()
        
        # First Pass Response (Quadrant Selection)
        first_pass_response = MagicMock()
        first_pass_response.text = '{"quadrant": "top_right", "confidence": 0.95, "reasoning": "Contains Chrome icon"}'
        
        # Second Pass Response (Coordinate Pinpointing)
        second_pass_response = MagicMock()
        second_pass_response.text = '{"x": 0.2, "y": 0.3, "confidence": 0.9, "description": "The Chrome address bar"}'
        
        # Route mock calls to chat
        mock_llm.chat.side_effect = [first_pass_response, second_pass_response]
        
        finder = VisualFinder(llm_client=mock_llm)
        result = finder.locate("Chrome address bar")
        
        self.assertTrue(result['success'])
        self.assertEqual(result['confidence'], 0.9)
        self.assertFalse(result['fallback_used'])
        self.assertIsNotNone(result['highlight_image'])
        
        # Map: top_right crop bounds (960, 0, 1920, 540)
        # x = 960 + 0.2 * 960 = 1152. Global: 1152 + 100 = 1252
        # y = 0 + 0.3 * 540 = 162. Global: 162 + 200 = 362
        self.assertEqual(result['screen_coords'], (1252, 362))

    @patch("visual_finder.capture_screen")
    def test_visual_finder_low_confidence_quadrant(self, mock_capture):
        """Test fallback when first pass quadrant selection is low confidence."""
        mock_capture.return_value = self.mock_screen
        mock_llm = MagicMock()
        
        # Low confidence first pass (conf = 0.3)
        first_pass_response = MagicMock()
        first_pass_response.text = '{"quadrant": "bottom_left", "confidence": 0.3, "reasoning": "Not sure"}'
        
        second_pass_response = MagicMock()
        second_pass_response.text = '{"x": 0.5, "y": 0.5, "confidence": 0.8, "description": "Center of quadrant"}'
        
        mock_llm.chat.side_effect = [first_pass_response, second_pass_response]
        
        finder = VisualFinder(llm_client=mock_llm)
        result = finder.locate("Start Menu Button")
        
        # Even though first pass conf is low, second pass coordinate conf is high (0.8)
        self.assertTrue(result['success'])
        self.assertTrue(result['fallback_used'])

    @patch("visual_finder.capture_screen")
    def test_visual_finder_verbal_fallback(self, mock_capture):
        """Test final verbal fallback when coordinate locating has very low confidence (<0.4)."""
        mock_capture.return_value = self.mock_screen
        mock_llm = MagicMock()
        
        first_pass = MagicMock()
        first_pass.text = '{"quadrant": "top_left", "confidence": 0.9, "reasoning": "High confidence"}'
        
        second_pass = MagicMock()
        second_pass.text = '{"x": 0.5, "y": 0.5, "confidence": 0.3, "description": "Unable to pinpoint"}'
        
        mock_llm.chat.side_effect = [first_pass, second_pass]
        
        finder = VisualFinder(llm_client=mock_llm)
        result = finder.locate("Tiny symbol in taskbar")
        
        self.assertFalse(result['success'])
        self.assertIsNone(result['highlight_image'])
        self.assertIn("not confident enough", result['message'].lower())

    @patch("visual_finder.capture_screen")
    def test_performance_benchmark(self, mock_capture):
        """Test that execution finishes well within performance limits."""
        mock_capture.return_value = self.mock_screen
        mock_llm = MagicMock()
        
        first_pass = MagicMock()
        first_pass.text = '{"quadrant": "top_left", "confidence": 0.8, "reasoning": "Quick"}'
        second_pass = MagicMock()
        second_pass.text = '{"x": 0.1, "y": 0.1, "confidence": 0.85, "description": "Fast"}'
        
        mock_llm.chat.side_effect = [first_pass, second_pass]
        
        finder = VisualFinder(llm_client=mock_llm)
        
        start_time = time.time()
        result = finder.locate("Start Button")
        end_time = time.time()
        
        duration = end_time - start_time
        self.assertLess(duration, 3.0, f"Performance test failed: took {duration:.2f}s (max 3s)")


if __name__ == "__main__":
    unittest.main()
