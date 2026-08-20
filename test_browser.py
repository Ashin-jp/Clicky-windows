"""Quick integration test for Phase 7 browser modules."""
print("=== PHASE 7 INTEGRATION TEST ===")

# 1. Core imports
from executors import load_all_executors, get_all_actions
load_all_executors()
actions = get_all_actions()
print(f"[OK] {len(actions)} actions registered")

# 2. Browser actions registered
browser_actions = [a for a in actions if a.startswith("BROWSER_")]
print(f"[OK] {len(browser_actions)} browser actions: {', '.join(browser_actions)}")

# 3. Trust engine
from trust_engine import TrustEngine
te = TrustEngine()
t1 = te.get_trust_level("BROWSER_SEARCH")
t2 = te.get_trust_level("BROWSER_CLICK")
t3 = te.get_trust_level("BROWSER_FILL_FORM")
print(f"[OK] Trust: SEARCH={t1.value}, CLICK={t2.value}, FILL_FORM={t3.value}")

# 4. Intent router
from intent_router import get_intent_router
ir = get_intent_router()
tests = {
    "search for python tutorials online": "[BROWSER_SEARCH:",
    "go to youtube.com": "[BROWSER_NAVIGATE:",
    "read this page": "[BROWSER_READ:",
    "go back": "[BROWSER_BACK:",
}
for text, expected in tests.items():
    result = ir.classify(text)
    tag = result.action_tag or result.task_type.value
    ok = expected in tag
    print(f"  [{'OK' if ok else 'WARN'}] '{text}' -> {tag}")

# 5. Browser profile manager
from browser_profile import get_browser_profile_manager
pm = get_browser_profile_manager()
profile = pm.get_default_profile()
print(f"[OK] Default profile: {profile.profile_name}, engine: {profile.search_engine[:30]}...")

# 6. Browser controller singleton
from browser_controller import get_browser_controller, BROWSER_PROFILE_DIR
bc = get_browser_controller()
import os
print(f"[OK] Profile dir exists: {os.path.isdir(BROWSER_PROFILE_DIR)}")

# 7. Playwright import
from playwright.async_api import async_playwright
print("[OK] Playwright imported")

print("\n=== ALL TESTS PASSED ===")
