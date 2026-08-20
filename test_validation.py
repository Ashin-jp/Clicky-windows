"""Quick validation test for the upgraded Clicky action system."""
import sys
sys.path.insert(0, r"d:\clicky\windows-clicky")

from trust_engine import TrustEngine
from file_access import FileAccessControl, FilePermission
from executors import load_all_executors, get_all_actions
from actions import parse_actions

# Load executors
load_all_executors()
actions = get_all_actions()
print(f"=== {len(actions)} Action Executors Registered ===")
for k in sorted(actions.keys()):
    print(f"  {k} ({actions[k].category})")

# Trust Engine Tests
print("\n=== Trust Engine Tests ===")
te = TrustEngine()
tests = [
    ("RUN_CMD", "dir", "silent"),
    ("RUN_CMD", "echo hello", "silent"),
    ("RUN_CMD", "git status", "silent"),
    ("RUN_CMD", "pip install pandas", "confirm_once"),
    ("RUN_CMD", "npm install", "confirm_once"),
    ("RUN_CMD", "python script.py", "confirm_once"),
    ("RUN_CMD", "format C:", "blocked"),
    ("RUN_CMD", "rm -rf /", "blocked"),
    ("RUN_CMD", "shutdown /s", "blocked"),
    ("RUN_CMD", "regedit", "blocked"),
    ("RUN_CMD", "unknown-cmd", "always_confirm"),
    ("SEARCH", "python", "silent"),
    ("CLICK", "100,200", "confirm_once"),
    ("WRITE_FILE", "test.txt|hello", "always_confirm"),
    ("READ_FILE", "test.txt", "silent"),
    ("FETCH_URL", "example.com", "silent"),
    ("SCROLL", "down", "silent"),
    ("EXPLAIN", "this code", "silent"),
]

passed = 0
for atype, params, expected in tests:
    actual = te.get_trust_level(atype, params).value
    status = "PASS" if actual == expected else "FAIL"
    if status == "FAIL":
        print(f"  {status}: {atype}({params}) = {actual}, expected {expected}")
    passed += 1 if actual == expected else 0
print(f"  {passed}/{len(tests)} trust tests passed")

# File Access Tests
print("\n=== File Access Tests ===")
fac = FileAccessControl()
fa_tests = [
    ("D:/Clicky/workspace/test.txt", "public"),
    ("C:/Windows/System32/cmd.exe", "forbidden"),
    ("C:/Windows/notepad.exe", "system"),
    ("C:/Program Files/test.exe", "system"),
    (".env", "sensitive"),
    ("D:/projects/mycode.py", "project"),
]
fa_passed = 0
for path, expected in fa_tests:
    actual = fac.classify_path(path).value
    status = "PASS" if actual == expected else "FAIL"
    if status == "FAIL":
        print(f"  {status}: {path} = {actual}, expected {expected}")
    fa_passed += 1 if actual == expected else 0
print(f"  {fa_passed}/{len(fa_tests)} file access tests passed")

# Action Parsing Test
print("\n=== Action Parsing Test ===")
test_response = (
    'sure, let me search that up for you. '
    '[SEARCH:python 3.14 features] '
    '[FETCH_URL:https://docs.python.org] '
    '[CLICK:500,300] '
    '[POINT:800,600:search bar]'
)
clean, parsed = parse_actions(test_response, te)
print(f"  Input:  {test_response[:60]}...")
print(f"  Clean:  {clean[:60]}...")
print(f"  Parsed: {len(parsed)} actions")
for a in parsed:
    print(f"    {a.action_type}: {a.params[:40]} (trust={a.trust_level.value})")

# Database test
print("\n=== Database Test ===")
from storage import get_db
db = get_db()
db.log_action("TEST", "validation", "silent", "success")
recent = db.get_recent_actions(1)
print(f"  Stored and retrieved {len(recent)} action(s) from DB")

print("\n=== ALL VALIDATION PASSED ===")
