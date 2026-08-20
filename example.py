"""
example.py — Demo Script for Clicky Visual Finder

Simulates the two-pass LLM visual finder pipeline across different confidence
levels, saving the resulting highlighted screenshots for review.
"""

import os
import sys
from PIL import Image, ImageDraw
from unittest.mock import MagicMock, patch

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from visual_finder import VisualFinder, capture_screen, speak_feedback


def create_dummy_screenshot() -> Image.Image:
    """Create a dummy screenshot simulating a browser window for demo purposes."""
    img = Image.new("RGB", (1280, 720), color="#1C1C1E")
    draw = ImageDraw.Draw(img)
    
    # Draw mock browser bar
    draw.rectangle([0, 0, 1280, 60], fill="#2C2C2E")
    draw.rectangle([10, 15, 50, 45], fill="#FF453A") # Close button
    draw.rectangle([80, 15, 120, 45], fill="#007AFF") # Address bar start
    draw.rectangle([120, 15, 1100, 45], fill="#3A3A3C", outline="#48484A") # URL field
    draw.text((130, 23), "https://google.com", fill="#AEAEB2")
    
    # Draw search button
    draw.rectangle([1120, 15, 1200, 45], fill="#34C759") # Search Button (Green)
    draw.text((1140, 23), "Search", fill="#FFFFFF")
    
    # Draw middle area
    draw.rectangle([200, 150, 1080, 570], fill="#2C2C2E", outline="#3A3A3C")
    draw.text((450, 300), "Google", fill="#FFFFFF")
    draw.rectangle([350, 350, 930, 400], fill="#1C1C1E", outline="#48484A")
    draw.text((370, 365), "Search or type URL...", fill="#AEAEB2")
    
    # Save coordinate info
    img.info['display_x'] = 0
    img.info['display_y'] = 0
    img.info['display_width'] = 1280
    img.info['display_height'] = 720
    
    return img


def run_demo():
    print("=== Clicky Visual Finder Demo ===")
    
    # Use a dummy screenshot for testing/demo
    screenshot = create_dummy_screenshot()
    
    # Configure mock LLM client responses for different levels
    scenarios = [
        {
            "name": "Tight Highlight (High Confidence - Search Button)",
            "target": "green search button in browser bar",
            "first_pass_json": '{"quadrant": "top_right", "confidence": 0.95, "reasoning": "Search button is at top right"}',
            "second_pass_json": '{"x": 0.8, "y": 0.35, "confidence": 0.92, "description": "Green search button"}',
            "output_file": "demo_tight_highlight.png"
        },
        {
            "name": "Medium Highlight (Moderate Confidence - Google Text)",
            "target": "Google logo text in center",
            "first_pass_json": '{"quadrant": "top_left", "confidence": 0.85, "reasoning": "Google logo is in center, top-left quadrant"}',
            "second_pass_json": '{"x": 0.85, "y": 0.88, "confidence": 0.73, "description": "Google text logo"}',
            "output_file": "demo_medium_highlight.png"
        },
        {
            "name": "Broad Highlight (Low Coordinate Confidence - Entire Quadrant)",
            "target": "settings icon on screen",
            "first_pass_json": '{"quadrant": "bottom_right", "confidence": 0.90, "reasoning": "Settings typically at bottom right"}',
            "second_pass_json": '{"x": 0.5, "y": 0.5, "confidence": 0.55, "description": "Settings quadrant area"}',
            "output_file": "demo_broad_highlight.png"
        },
        {
            "name": "Verbal Fallback (Very Low Confidence - No Highlight)",
            "target": "invisible micro logo",
            "first_pass_json": '{"quadrant": "bottom_left", "confidence": 0.50, "reasoning": "Might be in bottom-left"}',
            "second_pass_json": '{"x": 0.5, "y": 0.5, "confidence": 0.25, "description": "Unknown tiny pixel"}',
            "output_file": "demo_verbal_fallback.png"
        }
    ]
    
    for idx, scenario in enumerate(scenarios, 1):
        print(f"\n--- Scenario {idx}: {scenario['name']} ---")
        print(f"Target Description: '{scenario['target']}'")
        
        # Instantiate Finder with Mock LLM
        mock_llm = MagicMock()
        first_res = MagicMock(text=scenario["first_pass_json"])
        second_res = MagicMock(text=scenario["second_pass_json"])
        mock_llm.chat.side_effect = [first_res, second_res]
        
        # Patch capture_screen to return our simulated browser screenshot
        with MagicMock() as mock_cap:
            mock_cap.return_value = screenshot
            with patch("visual_finder.capture_screen", mock_cap):
                finder = VisualFinder(llm_client=mock_llm)
                result = finder.locate(scenario["target"])
                
                print(f"Result Success: {result['success']}")
                print(f"Message: {result['message']}")
                print(f"Confidence: {result['confidence']:.2f}")
                print(f"Mapped Screen Coords: {result['screen_coords']}")
                
                if result['highlight_image']:
                    result['highlight_image'].save(scenario["output_file"])
                    print(f"Saved highlighted image to: {scenario['output_file']}")
                else:
                    print("No highlight image drawn (verbal-only fallback).")
                
                # Speak feedback aloud
                speak_feedback(result['message'])


if __name__ == "__main__":
    run_demo()
