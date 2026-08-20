import logging
import threading
import time
import json
import hashlib
from datetime import datetime
from typing import Optional, Callable

from storage import get_db
from watchdog_system import get_watchdog

logger = logging.getLogger(__name__)

# Browser executables for RUN action categorization
_BROWSER_EXES = {"chrome", "msedge", "firefox", "chromium", "brave", "vivaldi", "opera"}


def _categorize_action(action_type: str) -> str:
    """Categorize an action type for more specific pattern matching.
    
    RUN + browser exe → RUN_BROWSER
    RUN + other app → RUN_APP
    BROWSER_* → BROWSER_ACTION
    FILE_* → FILE_ACTION
    Others → keep action_type
    """
    upper = action_type.upper()
    if upper == "RUN":
        # Check if the action was for a browser
        lower = action_type.lower()
        if any(b in lower for b in _BROWSER_EXES):
            return "RUN_BROWSER"
        return "RUN_APP"
    if upper.startswith("BROWSER_"):
        return "BROWSER_ACTION"
    if upper.startswith("FILE_"):
        return "FILE_ACTION"
    return upper


class SuggestionEngine:
    """
    Background daemon thread that runs on startup to detect repeated 
    action patterns in the last 30 days and proactively suggest macros.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._on_suggestion_callback: Optional[Callable] = None
        self._has_run_analysis = False
        logger.info("SuggestionEngine: initialized")

    def set_suggestion_callback(self, callback: Callable):
        """Callback takes (sequence_tuple, hash_key, description)"""
        self._on_suggestion_callback = callback

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        
        get_watchdog().register(name="suggestion_engine", heartbeat_interval=30.0)
        self._running.set()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SuggestionEngineThread",
            daemon=True
        )
        self._thread.start()
        logger.info("SuggestionEngine: started")

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        get_watchdog().unregister("suggestion_engine")
        logger.info("SuggestionEngine: stopped")

    def _run_loop(self):
        while self._running.is_set():
            get_watchdog().heartbeat("suggestion_engine")
            
            if not self._has_run_analysis:
                self._has_run_analysis = True
                try:
                    self._analyze_patterns()
                except Exception as e:
                    logger.error(f"SuggestionEngine: pattern analysis failed: {e}")

            # Sleep in chunks to allow clean shutdown
            for _ in range(30):
                if not self._running.is_set():
                    return
                time.sleep(1.0)

    def _analyze_patterns(self):
        """Analyze last 30 days of actions for repeated sequences."""
        db = get_db()
        history = db.get_action_history_recent(days=30)
        if not history:
            return

        # 1. Group into sessions based on time gaps > 30 mins
        sessions = []
        current_session = []
        last_time = None

        for row in history:
            try:
                dt = datetime.fromisoformat(row["executed_at"])
                if last_time and (dt - last_time).total_seconds() > 1800:
                    if len(current_session) >= 3:
                        sessions.append(current_session)
                    current_session = []
                current_session.append(_categorize_action(row["action_type"]))
                last_time = dt
            except Exception:
                continue

        if current_session and len(current_session) >= 3:
            sessions.append(current_session)

        # 2. Extract sequences of length 3 to 5
        # Maps sequence tuple to set of session indices
        sequence_occurrences = {}
        
        for session_idx, session_actions in enumerate(sessions):
            n = len(session_actions)
            for length in range(3, 6):
                for i in range(n - length + 1):
                    seq = tuple(session_actions[i:i + length])
                    if seq not in sequence_occurrences:
                        sequence_occurrences[seq] = set()
                    sequence_occurrences[seq].add(session_idx)

        # 3. Find qualified patterns (appears in >= 3 different sessions)
        for seq, session_indices in sequence_occurrences.items():
            if len(session_indices) >= 3:
                # Hash the sequence to uniquely identify it
                hash_key = hashlib.md5(",".join(seq).encode()).hexdigest()
                
                # Check if we already suggested this
                if not db.get_suggestion_outcome(hash_key):
                    desc = " then ".join(seq)
                    logger.info(f"SuggestionEngine: Found qualified pattern: {desc}")
                    
                    if self._on_suggestion_callback:
                        self._on_suggestion_callback(seq, hash_key, desc)
                        
                    # Only make one suggestion per startup to avoid spam
                    break

    def generate_macro_definition(self, sequence: tuple) -> str:
        """Use Groq to generate a JSON macro definition for the sequence."""
        from groq_router import get_router, RoutedRequest, TaskType, Priority
        
        router = get_router()
        system_prompt = (
            "You are an expert automation engineer. The user frequently performs the following sequence "
            "of categorized actions: " + ", ".join(sequence) + ". "
            "Categories: RUN_BROWSER=launch a web browser, RUN_APP=launch a desktop app, "
            "BROWSER_ACTION=browser automation (search/navigate/click), FILE_ACTION=file operations. "
            "Write a Clicky OS CHAIN macro definition in valid JSON to automate this. "
            "The JSON must have a 'name', 'description', and a list of 'actions' (each with 'action_type' and 'params'). "
            "For params, use sensible defaults or placeholders like '<ENTER_VALUE_HERE>'. "
            "Output ONLY valid JSON, no markdown formatting or explanation."
        )
        
        req = RoutedRequest(
            system_prompt=system_prompt,
            user_prompt="Generate the JSON macro definition.",
            task_type=TaskType.CODE_TASK,
            priority=Priority.NORMAL
        )
        
        # We need a synchronous call or we can use quick_chat
        try:
            resp = router.quick_chat(
                prompt="Generate the JSON macro definition.",
                task_type=TaskType.CODE_TASK,
                system_prompt=system_prompt
            )
            # Try to strip markdown code blocks if present
            clean_json = resp.replace("```json", "").replace("```", "").strip()
            return clean_json
        except Exception as e:
            logger.error(f"SuggestionEngine: failed to generate macro: {e}")
            return ""

# Singleton
_instance: Optional[SuggestionEngine] = None

def get_suggestion_engine() -> SuggestionEngine:
    global _instance
    if _instance is None:
        _instance = SuggestionEngine()
    return _instance
