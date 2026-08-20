import logging
import time
import pyperclip
from typing import Optional

from storage import get_db

logger = logging.getLogger(__name__)


class SmartTextTransformer:
    """
    Handles voice-triggered text transformations.
    Grabs selected text via Ctrl+C, applies LLM transformation,
    and replaces clipboard contents with the result.
    """

    def __init__(self, tts_callback=None):
        self._tts_callback = tts_callback
        logger.info("SmartTextTransformer: initialized")

    def _get_source_text(self) -> str:
        """Attempt to get selected text by simulating Ctrl+C, fallback to clipboard."""
        import pyautogui
        
        # Save current clipboard to restore if Ctrl+C fails to grab new text
        old_clipboard = pyperclip.paste()
        
        # Simulate Ctrl+C to copy selected text
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.1) # Wait for clipboard to update
        
        new_clipboard = pyperclip.paste()
        
        if new_clipboard and new_clipboard != old_clipboard:
            return new_clipboard
            
        # Fallback to existing clipboard content
        return old_clipboard

    def transform_text(self, trigger_name: str) -> bool:
        """
        Execute a text transformation.
        Returns True on success.
        """
        db = get_db()
        prompt_instruction = db.get_transform_prompt(trigger_name)
        if not prompt_instruction:
            logger.error(f"Transformer: no prompt found for '{trigger_name}'")
            return False

        source_text = self._get_source_text()
        if not source_text or not source_text.strip():
            self._notify("No text selected or copied to transform.")
            return False

        logger.info(f"Transformer: applying '{trigger_name}' to {len(source_text)} chars")

        # LLM Routing
        # If text is < 300 chars and we have a local model, we'd use it here.
        # Since local 1B isn't fully integrated, we route to Groq router.
        from groq_router import get_router, TaskType
        router = get_router()
        
        system_prompt = (
            f"You are a text transformation assistant. {prompt_instruction} "
            "Output ONLY the transformed text. Do not add any conversational filler, "
            "quotes, or markdown code blocks around the output."
        )

        try:
            # Quick chat is synchronous but fast
            transformed = router.quick_chat(
                prompt=source_text,
                task_type=TaskType.SIMPLE_QUESTION,
                system_prompt=system_prompt
            )
            
            # Clean up potential markdown formatting that Groq sometimes returns
            if transformed.startswith("```"):
                lines = transformed.split("\n")
                if len(lines) > 2:
                    transformed = "\n".join(lines[1:-1])
            transformed = transformed.strip()
            
            if not transformed:
                self._notify("Transformation resulted in empty text.")
                return False

            # Save to history for undo
            db.save_transform_history(source_text, transformed, trigger_name)
            
            # Place in clipboard
            pyperclip.copy(transformed)
            
            # Optionally simulate Ctrl+V to replace the highlighted text directly
            import pyautogui
            pyautogui.hotkey('ctrl', 'v')
            
            self._notify(f"Text {trigger_name}d and updated.")
            return True

        except Exception as e:
            logger.error(f"Transformer: execution failed: {e}")
            self._notify("Sorry, the text transformation failed.")
            return False

    def undo_last_transform(self) -> bool:
        """Undo the last text transformation by restoring original text to clipboard."""
        db = get_db()
        last_transform = db.get_last_transform()
        
        if not last_transform:
            self._notify("No recent transformations to undo.")
            return False
            
        original_text = last_transform["original_text"]
        pyperclip.copy(original_text)
        
        import pyautogui
        pyautogui.hotkey('ctrl', 'v')
        
        # Remove from history so we don't undo it again
        db._conn.execute("DELETE FROM transform_history WHERE id = ?", (last_transform["id"],))
        db._conn.commit()
        
        self._notify("Transformation undone.")
        return True

    def _notify(self, message: str):
        if self._tts_callback:
            try:
                # We need to run it in the event loop if it's an async TTS call
                import asyncio
                loop = asyncio.get_event_loop()
                if asyncio.iscoroutinefunction(self._tts_callback):
                    loop.create_task(self._tts_callback(message))
                else:
                    self._tts_callback(message)
            except Exception as e:
                logger.debug(f"Transformer: TTS notify failed: {e}")
        else:
            logger.info(f"Transformer Notify: {message}")

# Singleton
_instance: Optional[SmartTextTransformer] = None

def get_text_transformer() -> SmartTextTransformer:
    global _instance
    if _instance is None:
        _instance = SmartTextTransformer()
    return _instance
