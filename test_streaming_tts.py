import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

async def test_streaming():
    from companion_manager import CompanionManager
    
    cm = CompanionManager()
    
    # We want to force groq streaming
    cm._ai_provider = "groq"
    
    print("Testing streaming TTS response...")
    
    # Trigger processing. We'll use silent=False to test the TTS queue.
    cm._process_user_message("explain how python asyncio works in 3 paragraphs", silent=False)
    
    print("Waiting for streaming to complete...")
    # Keep event loop running so the stream has time to process and TTS can play
    for _ in range(60):
        await asyncio.sleep(1)
        if cm._voice_state == "idle":
            print("Finished.")
            break

if __name__ == "__main__":
    try:
        # PySide6 components in CompanionManager might need QApplication
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        
        # CompanionManager does some timer/signals stuff, so we run event loop via PySide
        # However, asyncio.run is simpler if we avoid Qt conflicts or just use QTimer.
        # But we hit an issue last time. Let's just create QApplication then run.
        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_streaming())
    except Exception as e:
        print(f"Error: {e}")
