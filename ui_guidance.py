"""
ui_guidance.py — Universal UI Guidance System

Finds elements on screen, points at them, explains them, and gives app tours.
Strictly uses window-relative percentage coordinates for multi-monitor and scaling resilience.
"""

import logging
import json
import time
import win32gui
import pyautogui
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple, List

from storage import get_db
from groq_router import get_router
from uia_helper import get_app_tree_as_text, get_element_at_cursor
from screen_capture import capture_region

from action_confirm_dialog import TrustLevel

logger = logging.getLogger(__name__)

# Constants for Category
CAT_STANDARD_WIN32 = "STANDARD_WIN32"
CAT_ELECTRON = "ELECTRON"
CAT_GPU_RENDERED = "GPU_RENDERED"
CAT_WEB_APP = "WEB_APP"
CAT_TERMINAL = "TERMINAL"
CAT_UNKNOWN = "UNKNOWN"

@dataclass
class WindowBounds:
    left: int
    top: int
    width: int
    height: int

class ApplicationCategoryClassifier:
    def __init__(self):
        self.db = get_db()

    def classify_app(self, exe_name: Optional[str], hwnd: int) -> str:
        """Classify a foreground application into a known category."""
        exe_lower = (exe_name or "").lower()
        
        # 1. SQLite lookup
        cursor = self.db._conn.cursor()
        cursor.execute("SELECT category FROM app_categories WHERE exe_name = ?", (exe_lower,))
        row = cursor.fetchone()
        if row:
            return row["category"]
            
        # 2. Window class name heuristics
        try:
            class_name = (win32gui.GetClassName(hwnd) or "").lower()
            if "chrome" in class_name or "mozilla" in class_name:
                self._save_category(exe_lower, class_name, CAT_WEB_APP)
                return CAT_WEB_APP
            if "console" in class_name or "tty" in class_name:
                self._save_category(exe_lower, class_name, CAT_TERMINAL)
                return CAT_TERMINAL
        except Exception:
            class_name = "unknown"

        # 3. UIA availability test
        uia_tree = get_app_tree_as_text(max_depth=2)
        if uia_tree and len(uia_tree) > 200:
            self._save_category(exe_lower, class_name, CAT_STANDARD_WIN32, uia_support=True)
            return CAT_STANDARD_WIN32

        # 4. Default to UNKNOWN
        self._save_category(exe_lower, class_name, CAT_UNKNOWN)
        return CAT_UNKNOWN

    def _save_category(self, exe_name: str, window_class: str, category: str, uia_support: bool = False):
        try:
            self.db._conn.execute(
                "INSERT OR REPLACE INTO app_categories (exe_name, window_class, category, uia_support) VALUES (?, ?, ?, ?)",
                (exe_name, window_class, category, int(uia_support))
            )
            self.db._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save app category: {e}")

class UIGuidanceSystem:
    def __init__(self):
        self.classifier = ApplicationCategoryClassifier()
        self.db = get_db()
        self.groq = get_router()

    async def _vision_query(self, screenshot_bytes: bytes, prompt: str) -> str:
        """Send a screenshot + prompt to the AI for analysis via Groq vision."""
        import base64
        from groq_router import RoutedRequest, TaskType, Priority
        b64_image = base64.b64encode(screenshot_bytes).decode('ascii')
        req = RoutedRequest(
            system_prompt="You are a UI analysis assistant. Return only valid JSON when asked.",
            user_prompt=prompt,
            task_type=TaskType.VISION_TASK,
            priority=Priority.URGENT,
            image_b64=b64_image,
        )
        response = self.groq.chat(req)
        return response.text

    def get_window_bounds(self, hwnd: int) -> Optional[WindowBounds]:
        try:
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            return WindowBounds(left, top, right - left, bottom - top)
        except Exception as e:
            logger.error(f"Failed to get window rect for HWND {hwnd}: {e}")
            return None

    def bring_to_foreground(self, hwnd: int):
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"Could not bring window {hwnd} to foreground: {e}")

    def get_app_knowledge(self, exe_name: str) -> str:
        cursor = self.db._conn.cursor()
        cursor.execute("SELECT ui_fact FROM app_knowledge WHERE app_exe = ? ORDER BY high_confidence DESC", (exe_name.lower(),))
        facts = [row[0] for row in cursor.fetchall()]
        if not facts:
            return "No previous knowledge about this app."
        return "\\n".join(facts)

    def save_ui_knowledge(self, app_exe: str, fact: str, source: str = "ai"):
        cursor = self.db._conn.cursor()
        cursor.execute("SELECT id, access_count FROM app_knowledge WHERE app_exe = ? AND ui_fact = ?", (app_exe.lower(), fact))
        row = cursor.fetchone()
        if row:
            new_count = row[1] + 1
            is_high_conf = 1 if new_count > 10 else 0
            cursor.execute("UPDATE app_knowledge SET access_count = ?, high_confidence = ? WHERE id = ?", (new_count, is_high_conf, row[0]))
        else:
            cursor.execute(
                "INSERT INTO app_knowledge (app_exe, ui_fact, tags, source, created_at, access_count, high_confidence) VALUES (?, ?, '', ?, ?, 1, 0)",
                (app_exe.lower(), fact, source, datetime.now().isoformat())
            )
        self.db._conn.commit()

    async def guide_to(self, target_description: str, exe_name: str, hwnd: int, companion_manager) -> dict:
        """Finds target element, yielding coordinates and steps."""
        self.bring_to_foreground(hwnd)
        bounds = self.get_window_bounds(hwnd)
        if not bounds:
            return {"found": False, "not_visible_reason": "Could not get window bounds."}

        category = self.classifier.classify_app(exe_name, hwnd)
        
        # Web app handling
        if category == CAT_WEB_APP:
            # For WEB_APP, we would ideally route to browser_controller. 
            # If not possible via direct DOM, fallback to vision.
            from browser_controller import get_browser_controller
            ctrl = get_browser_controller()
            if ctrl and ctrl._page and not ctrl._page.is_closed():
                # Try DOM finding (this assumes a method `find_element_by_description` exists or we fallback to groq with vision)
                pass # Fallback to standard vision flow for this implementation

        # Crop screenshot to window bounds
        screenshot = capture_region((bounds.left, bounds.top, bounds.width, bounds.height))
        
        uia_context = ""
        if category == CAT_STANDARD_WIN32:
            tree = get_app_tree_as_text(max_depth=4)
            if tree and len(tree) > 200:
                uia_context = tree
        elif category == CAT_ELECTRON:
            # Minimal UIA
            tree = get_app_tree_as_text(max_depth=1)
            uia_context = tree if tree else ""
        elif category == CAT_TERMINAL:
            # Read terminal text
            uia_context = get_app_tree_as_text(max_depth=3)

        knowledge = self.get_app_knowledge(exe_name)

        prompt = f"""You are the Universal UI Guidance System.
Your task is to find the element matching this description: "{target_description}"

Application: {exe_name} (Category: {category})
Known facts about this app:
{knowledge}

UIA Tree Information:
{uia_context}

INSTRUCTIONS:
1. Return ONLY valid JSON, no markdown formatting.
2. Provide 'x_percent' and 'y_percent' as floats between 0.0 and 1.0 relative to the provided cropped image bounds. NEVER provide absolute screen coordinates.
3. If app is UNKNOWN, describe visible UI regions rather than guessing names.
4. If menu navigation is required, return 'navigation_path' as an ordered list of steps.

JSON FORMAT:
{{
  "found": boolean,
  "x_percent": float,
  "y_percent": float,
  "confidence": float (0-1),
  "explanation": "One sentence explaining what it is",
  "navigation_path": ["step 1", "step 2"] (or empty list),
  "not_visible_reason": "reason if found is false"
}}
"""
        response_text = await self._vision_query(screenshot, prompt)
        try:
            # Strip markdown code blocks if any
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            result = json.loads(response_text)
        except Exception as e:
            logger.error(f"Failed to parse guidance JSON: {e}")
            return {"found": False, "not_visible_reason": "AI returned invalid JSON."}

        # Coordinate conversion
        if result.get("found"):
            xp = result.get("x_percent", 0.0)
            yp = result.get("y_percent", 0.0)
            # CRITICAL: Convert relative percentage to absolute screen coordinates
            result["screen_x"] = int(bounds.left + (xp * bounds.width))
            result["screen_y"] = int(bounds.top + (yp * bounds.height))
            
            # Save history
            self.db._conn.execute(
                "INSERT INTO guidance_history (app_exe, request, found, confidence, timestamp) VALUES (?, ?, 1, ?, ?)",
                (exe_name.lower(), target_description, result.get("confidence", 0), datetime.now().isoformat())
            )
            self.db._conn.commit()

        return result

    async def explain_element_at_cursor(self) -> str:
        x, y = pyautogui.position()
        element = get_element_at_cursor()
        
        context = ""
        if element:
            context = f"Name: {element.name}, Type: {element.control_type}, AutomationId: {element.automation_id}"
            
        screenshot = capture_region((x - 150, y - 150, 300, 300))
        prompt = f"""Explain the UI element at the center of this image in plain language. 
Available UIA Context: {context}
What is this button or element and what does it do? Be concise."""
        
        explanation = await self._vision_query(screenshot, prompt)
        return explanation

    async def app_tour(self, exe_name: str, hwnd: int, focus_area: Optional[str] = None) -> list:
        self.bring_to_foreground(hwnd)
        bounds = self.get_window_bounds(hwnd)
        if not bounds:
            return []
            
        screenshot = capture_region((bounds.left, bounds.top, bounds.width, bounds.height))
        prompt = """Identify 5-7 major UI regions in this application. 
Return ONLY JSON format:
{
  "regions": [
    {"name": "Toolbar", "purpose": "Contains main tools", "x_percent": 0.5, "y_percent": 0.1}
  ]
}"""
        response_text = await self._vision_query(screenshot, prompt)
        try:
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(response_text)
            regions = data.get("regions", [])
            for r in regions:
                r["screen_x"] = int(bounds.left + (r.get("x_percent", 0.5) * bounds.width))
                r["screen_y"] = int(bounds.top + (r.get("y_percent", 0.5) * bounds.height))
                self.save_ui_knowledge(exe_name, f"Region {r['name']} at {r['x_percent']},{r['y_percent']} - {r['purpose']}", "tour")
            return regions
        except Exception as e:
            logger.error(f"Tour parsing failed: {e}")
            return []

    async def first_seen_onboarding(self, exe_name: str, hwnd: int):
        bounds = self.get_window_bounds(hwnd)
        if not bounds:
            return
            
        screenshot = capture_region((bounds.left, bounds.top, bounds.width, bounds.height))
        prompt = f"This is an application ({exe_name}) I have not seen before. Identify the major UI regions visible and describe each one's likely purpose based on its visual appearance. Return a JSON list of objects with 'fact' string."
        
        response_text = await self._vision_query(screenshot, prompt)
        try:
            data = json.loads(response_text.replace("```json", "").replace("```", "").strip())
            for item in data:
                fact = item.get("fact", "")
                if fact:
                    self.save_ui_knowledge(exe_name, fact, "ai_onboarding")
            
            # Mark as categorized
            self.classifier._save_category(exe_name.lower(), "unknown", CAT_UNKNOWN)
        except Exception:
            pass
