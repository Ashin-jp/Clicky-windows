import asyncio
import os
import sys

# Add current path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

async def test_fixes():
    from companion_manager import CompanionManager
    cm = CompanionManager()
    
    # 1. Empty transcript test
    print("Test 1: Empty transcript")
    cm._on_final_transcript("   ")
    await asyncio.sleep(1)
    print("Voice state after empty:", cm._voice_state)
    
    # 2. Then splitting test (should split)
    print("\nTest 2: Valid then-splitting")
    cm._process_user_message("open notepad then search google", silent=True)
    await asyncio.sleep(1)
    
    # 3. Then splitting test (should NOT split)
    print("\nTest 3: Invalid then-splitting")
    cm._process_user_message("if this works then great", silent=True)
    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(test_fixes())
