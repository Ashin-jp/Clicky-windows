"""
intent_router.py — Local intent classifier (zero API calls).

Classifies user transcripts into task types and extracts action parameters.
Uses keyword trie + regex patterns for fast, offline classification.
DirectAction detection extracts parameters from natural language.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from groq_router import TaskType

def _get_known_site_url(name: str) -> Optional[str]:
    sites = {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "reddit": "https://www.reddit.com",
        "github": "https://github.com",
        "wikipedia": "https://www.wikipedia.org",
        "amazon": "https://www.amazon.com",
        "twitter": "https://twitter.com",
        "x": "https://x.com",
        "stackoverflow": "https://stackoverflow.com",
        "stack overflow": "https://stackoverflow.com",
        "netflix": "https://www.netflix.com",
        "gmail": "https://mail.google.com",
    }
    return sites.get(name.lower().strip())

KNOWN_SITES_PATTERN = r"(youtube|google|reddit|github|wikipedia|amazon|twitter|x|stackoverflow|stack overflow|netflix|gmail)"

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    """Result of intent classification."""
    task_type: TaskType
    recommended_model: str
    confidence: float
    extracted_params: dict = field(default_factory=dict)
    action_tag: Optional[str] = None  # Pre-built [ACTION:...] tag for DIRECT_ACTION
    raw_text: str = ""


# ─── Keyword Categories ──────────────────────────────────────────────
# Higher weight = stronger signal for that category

from constants import ACTION_KEYWORDS  # Single source of truth

CODE_KEYWORDS = {
    "code": 3, "program": 2, "script": 2, "function": 3, "class": 2,
    "debug": 3, "error": 2, "bug": 2, "fix": 2, "refactor": 2,
    "python": 3, "javascript": 3, "rust": 2, "java": 2, "html": 2, "css": 2,
    "api": 2, "database": 2, "sql": 2, "regex": 2,
    "implement": 2, "write": 1, "generate": 1,
    "compile": 2, "build": 1, "test": 1, "unittest": 2,
}

KNOWLEDGE_KEYWORDS = {
    "explain": 3, "what": 2, "why": 2, "how": 2, "define": 3,
    "difference": 2, "compare": 2, "versus": 2, "pros": 2, "cons": 2,
    "history": 2, "meaning": 2, "concept": 2, "theory": 2,
    "summarize": 3, "summary": 3, "overview": 2, "breakdown": 2,
    "teach": 2, "learn": 1, "understand": 1, "study": 2,
    "quiz": 3, "test me": 2, "flashcard": 2,
}

LONG_CONTEXT_KEYWORDS = {
    "read": 2, "document": 3, "file": 1, "article": 3, "paper": 3,
    "research": 3, "analyze": 2, "analysis": 3, "report": 2,
    "review": 2, "essay": 2, "book": 2, "chapter": 2,
    "entire": 2, "whole": 2, "full": 2, "complete": 2,
}

VISION_KEYWORDS = {
    "screen": 3, "see": 2, "look": 2, "show": 1, "display": 2,
    "image": 3, "picture": 3, "photo": 3, "chart": 3, "graph": 3,
    "diagram": 3, "table": 2, "ui": 2, "interface": 2,
    "what's on": 3, "describe": 2, "read screen": 3,
}

# ─── Direct Action Patterns ──────────────────────────────────────────
# (regex_pattern, action_tag_template, params_extractor)

DIRECT_ACTION_PATTERNS = [
    # Browser: search for X online (MUST be before generic SEARCH)
    (r"(?:search|google|look\s+up|find)\s+(?:for\s+)?(.+?)(?:\s+on(?:line|\s+the\s+web))\s*$",
     lambda m: f"[BROWSER_SEARCH:{m.group(1).strip()}]", "BROWSER_SEARCH"),

    # Browser: go to domain (MUST be before generic open/RUN)
    (r"(?:go\s+to|visit|navigate\s+to|open)\s+((?:[\w-]+\.)+(?:com|org|net|io|dev|ai|co|edu|gov|app)\S*)\s*$",
     lambda m: f"[BROWSER_NAVIGATE:{m.group(1).strip()}]", "BROWSER_NAVIGATE"),

    # Browser: go to known site
    (r"(?:go\s+to|visit|navigate\s+to|open)\s+" + KNOWN_SITES_PATTERN + r"(?:\s+please)?\s*$",
     lambda m: f"[BROWSER_NAVIGATE:{_get_known_site_url(m.group(1))}]", "BROWSER_NAVIGATE"),

    # Browser: search for known site (e.g. "search for YouTube" -> navigate to YouTube)
    (r"(?:search|google|look\s+up|find)\s+(?:for\s+)?" + KNOWN_SITES_PATTERN + r"(?:\s+please)?\s*$",
     lambda m: f"[BROWSER_NAVIGATE:{_get_known_site_url(m.group(1))}]", "BROWSER_NAVIGATE"),

    # Web search with specific site: "search for X on Y"
    (r"(?:search|look\s+up|find)\s+(?:for\s+)?(.+?)\s+on\s+([a-zA-Z0-9.\s]+)(?:\s+please)?\s*$",
     lambda m: f"[SITE_SEARCH:{m.group(2).strip()}|{m.group(1).strip()}]", "SITE_SEARCH"),
    
    # Web search on specific site reversed: "search Y for X"
    (r"search\s+([a-zA-Z0-9.\s]+)\s+for\s+(.+?)(?:\s+please)?\s*$",
     lambda m: f"[SITE_SEARCH:{m.group(1).strip()}|{m.group(2).strip()}]", "SITE_SEARCH"),

    # Search current site explicitly: "search for X on this site", "search this site for X"
    (r"(?:search|look\s+up|find)\s+(?:for\s+)?(.+?)\s+(?:on\s+this\s+site|here)(?:\s+please)?\s*$",
     lambda m: f"[SITE_SEARCH:current|{m.group(1).strip()}]", "SITE_SEARCH"),
    (r"search\s+(?:this\s+site|here)\s+for\s+(.+?)(?:\s+please)?\s*$",
     lambda m: f"[SITE_SEARCH:current|{m.group(1).strip()}]", "SITE_SEARCH"),

    # App launching (generic — fires after browser patterns)
    (r"(?:open|launch|start|run)\s+(.+?)(?:\s+please)?$",
     lambda m: f"[RUN:{_normalize_app(m.group(1))}]", "RUN"),

    # Close app
    (r"(?:close|exit|quit|kill)\s+(.+?)(?:\s+please)?$",
     lambda m: f"[CLOSE_APP:{m.group(1).strip()}]", "CLOSE_APP"),

    # Web search (generic — no site specified)
    (r"(?:search|google|look\s+up|find)\s+(?:for\s+)?(.+?)$",
     lambda m: f"[BROWSER_SEARCH:{m.group(1).strip()}]", "BROWSER_SEARCH"),

    # Open URL (explicit http/www)
    (r"(?:go to|open|visit|navigate to)\s+((?:https?://|www\.).+?)$",
     lambda m: f"[BROWSER_NAVIGATE:{m.group(1).strip()}]", "BROWSER_NAVIGATE"),

    # Type text (requires quotes or explicit "type out/in")
    (r"(?:type|enter|input)\s+[\"'](.+?)[\"']$",
     lambda m: f"[TYPE:{m.group(1).strip()}]", "TYPE"),
    (r"(?:type\s+out|type\s+in)\s+(.+?)$",
     lambda m: f"[TYPE:{m.group(1).strip()}]", "TYPE"),

    # Workspaces
    (r"(?:save|capture)\s+(?:the\s+)?(?:current\s+)?workspace\s+as\s+(.+?)$",
     lambda m: f"[SAVE_WORKSPACE:{m.group(1).strip()}]", "SAVE_WORKSPACE"),
    (r"(?:restore|load|open)\s+(?:the\s+)?workspace\s+(.+?)$",
     lambda m: f"[RESTORE_WORKSPACE:{m.group(1).strip()}]", "RESTORE_WORKSPACE"),
    (r"(?:delete|remove)\s+(?:the\s+)?workspace\s+(.+?)$",
     lambda m: f"[DELETE_WORKSPACE:{m.group(1).strip()}]", "DELETE_WORKSPACE"),
    (r"(?:list|show)\s+(?:my\s+)?(?:saved\s+)?workspaces?$",
     lambda m: "[LIST_WORKSPACES:none]", "LIST_WORKSPACES"),

    # Text transformations
    (r"(?:make\s+this|rewrite\s+this)\s+(?:more\s+)?formal\s*$",
     lambda m: "[TEXT_TRANSFORM:formalize]", "TEXT_TRANSFORM"),
    (r"(?:simplify|make\s+this\s+simpler|explain\s+simply)\s*$",
     lambda m: "[TEXT_TRANSFORM:simplify]", "TEXT_TRANSFORM"),
    (r"(?:convert\s+to|make\s+into)\s+bullet\s+points\s*$",
     lambda m: "[TEXT_TRANSFORM:bullet_points]", "TEXT_TRANSFORM"),
    (r"(?:extract|get)\s+action\s+items\s*$",
     lambda m: "[TEXT_TRANSFORM:action_items]", "TEXT_TRANSFORM"),
    (r"(?:summarize|give\s+me\s+a\s+summary)\s*$",
     lambda m: "[TEXT_TRANSFORM:summarize]", "TEXT_TRANSFORM"),
    (r"(?:make\s+this|format\s+as)\s+(?:an\s+)?email\s*$",
     lambda m: "[TEXT_TRANSFORM:email_format]", "TEXT_TRANSFORM"),
    (r"(?:undo|revert)\s+(?:last\s+)?transform(?:ation)?\s*$",
     lambda m: "[TEXT_TRANSFORM:undo]", "TEXT_TRANSFORM"),

    # Volume
    (r"(?:mute)\s+(.+?)(?:\s+audio|\s+sound|\s+volume)?(?:\s+please)?$",
     lambda m: f"[APP_VOLUME:{m.group(1).strip()}|mute]", "APP_VOLUME"),
    (r"(?:unmute)\s+(.+?)(?:\s+audio|\s+sound|\s+volume)?(?:\s+please)?$",
     lambda m: f"[APP_VOLUME:{m.group(1).strip()}|unmute]", "APP_VOLUME"),
    (r"(?:lower|decrease)\s+(?:the\s+)?volume\s+(?:for|on)\s+(.+?)(?:\s+please)?$",
     lambda m: f"[APP_VOLUME:{m.group(1).strip()}|lower]", "APP_VOLUME"),
    (r"(?:raise|increase)\s+(?:the\s+)?volume\s+(?:for|on)\s+(.+?)(?:\s+please)?$",
     lambda m: f"[APP_VOLUME:{m.group(1).strip()}|raise]", "APP_VOLUME"),

    # Switch app
    (r"(?:switch\s+to|go\s+to|bring\s+up)\s+(?:my\s+)?(.+?)(?:\s+please)?$",
     lambda m: f"[SWITCH_TO_APP:{m.group(1).strip()}]", "SWITCH_TO_APP"),

    # Restart app
    (r"restart\s+(.+?)(?:\s+please)?$",
     lambda m: f"[RESTART_APP:{m.group(1).strip()}]", "RESTART_APP"),

    # List apps
    (r"(?:what|which)\s+apps\s+(?:do\s+i\s+have|are)\s+open",
     lambda m: "[LIST_OPEN_APPS:]", "LIST_OPEN_APPS"),

    # Screenshot
    (r"(?:take\s+)?(?:a\s+)?screenshot",
     lambda m: "[SCREENSHOT:]", "SCREENSHOT"),

    # Timer
    (r"(?:set\s+)?(?:a\s+)?timer\s+(?:for\s+)?(\d+)\s+(minute|second|hour)s?",
     lambda m: f"[SCHEDULE_TASK:timer_{m.group(1)}_{m.group(2)}]", "SCHEDULE_TASK"),

    # Focus mode
    (r"(?:focus\s+mode|start\s+focus|begin\s+focus)\s+(?:for\s+)?(\d+)\s*(?:minute|min)s?",
     lambda m: f"[FOCUS_MODE:{m.group(1)}]", "FOCUS_MODE"),

    # Read screen
    (r"(?:read\s+(?:the\s+)?screen|what(?:'s|\s+is)\s+on\s+(?:my\s+)?screen)",
     lambda m: "[READ_SCREEN:]", "READ_SCREEN"),

    # Health check
    (r"(?:how\s+is\s+my\s+computer|system\s+health|health\s+check|run\s+diagnostics)",
     lambda m: "[HEALTH_CHECK:]", "HEALTH_CHECK"),

    # Save workspace
    (r"save\s+workspace\s+(?:as\s+)?(.+)",
     lambda m: f"[SAVE_WORKSPACE:{m.group(1).strip()}]", "SAVE_WORKSPACE"),

    # Restore workspace
    (r"(?:restore|load)\s+workspace\s+(.+)",
     lambda m: f"[RESTORE_WORKSPACE:{m.group(1).strip()}]", "RESTORE_WORKSPACE"),

    # Remember
    (r"remember\s+(?:that\s+)?(.+)",
     lambda m: f"[REMEMBER:{m.group(1).strip()}]", "REMEMBER"),

    # Run code
    (r"(?:run|execute)\s+(?:this\s+|the\s+)?code",
     lambda m: "[RUN_CODE:python]", "RUN_CODE"),

    # Transform text
    (r"(?:make\s+(?:it|this)\s+)?(formal|simple|shorter|longer|bullet\s*points?|summarize)",
     lambda m: f"[TRANSFORM_TEXT:{_normalize_transform(m.group(1))}]", "TRANSFORM_TEXT"),

    # Research
    (r"research\s+(.+)",
     lambda m: f"[RESEARCH:{m.group(1).strip()}]", "RESEARCH"),

    # Browser: read this page
    (r"(?:read|summarize|summarise)\s+(?:this\s+|the\s+|current\s+)?(?:web\s*)?page",
     lambda m: "[BROWSER_READ:]", "BROWSER_READ"),

    # Browser: go back / go forward
    (r"go\s+back(?:\s+in\s+the\s+browser)?",
     lambda m: "[BROWSER_BACK:]", "BROWSER_BACK"),
    (r"go\s+forward(?:\s+in\s+the\s+browser)?",
     lambda m: "[BROWSER_FORWARD:]", "BROWSER_FORWARD"),

    # Browser: click on X (on webpage)
    (r"click\s+(?:on\s+)?(?:the\s+)?[\"'](.+?)[\"']",
     lambda m: f"[BROWSER_CLICK:{m.group(1).strip()}]", "BROWSER_CLICK"),

    # UI Guidance: GUIDE_TO
    (r"(?:where\s+is|how\s+do\s+i\s+find|find|where\s+can\s+i|i\s+can't\s+find|help\s+me\s+find|show\s+me\s+where|locate)\s+(.+?)(?:\s+in\s+this\s+app)?$",
     lambda m: f"[GUIDE_TO:{m.group(1).strip()}]", "GUIDE_TO"),

    # UI Guidance: EXPLAIN_ELEMENT
    (r"(what\s+is\s+this|what\s+does\s+this\s+do|explain\s+this|what's\s+this\s+button|what\s+is\s+this\s+option|what\s+does\s+this\s+button\s+do)",
     lambda m: "[EXPLAIN_ELEMENT:]", "EXPLAIN_ELEMENT"),

    # UI Guidance: APP_TOUR
    (r"(show\s+me\s+around|give\s+me\s+a\s+tour|what\s+are\s+the\s+main\s+parts)",
     lambda m: "[APP_TOUR:]", "APP_TOUR"),

    # UI Guidance: REMEMBER_UI
    (r"remember\s+that\s+(.+)",
     lambda m: f"[REMEMBER_UI:{m.group(1).strip()}]", "REMEMBER_UI"),

    # Linux Assistant (Non-terminal specific patterns)
    (r"(teach\s+me\s+linux|i'm\s+new\s+to\s+linux|how\s+do\s+i\s+use\s+linux)",
     lambda m: "[LINUX_INTERACTIVE_LESSON:]", "LINUX_INTERACTIVE_LESSON"),
    (r"(what\s+went\s+wrong|why\s+did\s+that\s+fail|i\s+got\s+an\s+error)",
     lambda m: "[LINUX_ERROR_EXPLAIN:]", "LINUX_ERROR_EXPLAIN"),
    (r"how\s+do\s+i\s+(.+?)\s+in\s+linux",
     lambda m: f"[LINUX_SUGGEST_COMMAND:{m.group(1).strip()}]", "LINUX_SUGGEST_COMMAND"),
    (r"what\s+does\s+this\s+command\s+do",
     lambda m: "[LINUX_EXPLAIN_COMMAND:]", "LINUX_EXPLAIN_COMMAND"),
]

# Compiled patterns for speed
_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), gen, tag) for p, gen, tag in DIRECT_ACTION_PATTERNS]


def _normalize_app(name: str) -> str:
    """Normalize app names to executable names."""
    name = name.strip().lower()
    app_map = {
        "notepad": "notepad", "calculator": "calc", "paint": "mspaint",
        "explorer": "explorer", "file explorer": "explorer",
        "command prompt": "cmd", "cmd": "cmd", "terminal": "wt",
        "powershell": "powershell", "task manager": "taskmgr",
        "settings": "ms-settings:", "control panel": "control",
        "chrome": "chrome", "firefox": "firefox", "edge": "msedge",
        "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
        "vs code": "code", "vscode": "code", "visual studio code": "code",
        "spotify": "spotify", "discord": "discord", "slack": "slack",
        "steam": "steam", "obs": "obs64",
    }
    return app_map.get(name, name)


def _normalize_transform(transform: str) -> str:
    """Normalize transformation names."""
    transform = transform.strip().lower()
    norm = {
        "formal": "formalize", "simple": "simplify",
        "shorter": "shorten", "longer": "expand",
        "bullet points": "bullet_points", "bulletpoints": "bullet_points",
        "summarize": "summarize",
    }
    return norm.get(transform, transform)


def _keyword_score(text: str, keywords: dict) -> float:
    """Score text against a keyword dictionary. Returns normalized score."""
    words = set(text.lower().split())
    total = 0
    matches = 0
    for keyword, weight in keywords.items():
        if keyword in text.lower():  # Use 'in' for multi-word keywords
            total += weight
            matches += 1
    if matches == 0:
        return 0.0
    return min(total / 10.0, 1.0)


class IntentRouter:
    """
    Local intent classifier — classifies user input without API calls.
    """

    def __init__(self, confidence_threshold: float = 0.3):
        self.confidence_threshold = confidence_threshold
        logger.info("IntentRouter: initialized")

    def classify(self, text: str, current_url: str = "", is_terminal: bool = False, is_browser_active: bool = False) -> IntentResult:
        """
        Classify a user transcript into a task type.

        Priority order:
        1. Try direct action pattern matching first
        2. Score against keyword categories
        3. Default to SIMPLE_QUESTION if nothing matches well
        """
        if not text or not text.strip():
            return IntentResult(
                task_type=TaskType.SIMPLE_QUESTION,
                recommended_model="meta-llama/llama-4-scout-17b-16e-instruct",
                confidence=0.0,
                raw_text=text or "",
            )

        clean_text = text.strip()
        t_lower = clean_text.lower()
        
        # ─── Fix 5: Browser Search Context Pre-check ─────────────────
        if is_browser_active:
            import re
            # Negative check: don't intercept if file/folder context words are present
            negative_words = ["file", "folder", "document", "notes", "code", "log", "logs"]
            has_negative = any(nw in t_lower for nw in negative_words)
            
            if not has_negative and ("search" in t_lower or "find" in t_lower):
                m = re.match(r"(?:search|find)(?:\s+for)?\s+(.+?)(?:\s+on\s+(.+))?$", t_lower)
                if m:
                    query = m.group(1).strip()
                    domain = "current"
                    # If "on youtube" was specified, handle it. Otherwise default to current site.
                    if m.group(2):
                        domain = m.group(2).strip()
                        
                    return IntentResult(
                        task_type=TaskType.DIRECT_ACTION,
                        recommended_model="meta-llama/llama-4-scout-17b-16e-instruct",
                        confidence=1.0,
                        action_tag=f"[SITE_SEARCH:{domain}|{query}]",
                        raw_text=text,
                    )

        # ─── Linux Terminal Context Catch-All ────────────────────────
        if is_terminal:
            linux_keywords = [
                "how do i", "what does", "what is", "explain", "help me", 
                "i don't understand", "command", "error", "permission", 
                "install", "find", "delete", "run"
            ]
            if any(k in t_lower for k in linux_keywords):
                return IntentResult(
                    task_type=TaskType.DIRECT_ACTION,
                    recommended_model="",
                    confidence=0.95,
                    action_tag=f"[LINUX_ASSIST:{clean_text}]",
                    raw_text=clean_text,
                )

        # ─── Fix 1: Highest Priority Search Overrides ────────────────
        def _quick_search_result(domain, query):
            return IntentResult(
                task_type=TaskType.DIRECT_ACTION,
                recommended_model="meta-llama/llama-4-scout-17b-16e-instruct",
                confidence=1.0,
                action_tag=f"[SITE_SEARCH:{domain}|{query}]",
                raw_text=text,
            )

        # "search athiradi", "search for athiradi" -> youtube
        import re
        m = re.match(r"(?:search|find)(?:\s+for)?\s+(athiradi)", t_lower)
        if m:
            return _quick_search_result("youtube.com", "athiradi")

        # YouTube specific high-priority
        yt_patterns = [
            r"(?:search|look\s+up|find)\s+(?:for\s+)?(.+?)\s+on\s+youtube\s*$",
            r"search\s+youtube\s+for\s+(.+?)\s*$",
            r"^youtube\s+(.+?)\s*$",
        ]
        for p in yt_patterns:
            m = re.match(p, t_lower)
            if m:
                return _quick_search_result("youtube.com", m.group(1).strip())

        # URL aware: "search for X" on youtube.com
        if "youtube.com" in current_url:
            m = re.match(r"(?:search|find|look\s+up)(?:\s+for)?\s+(.+?)\s*$", t_lower)
            if m and not re.search(r"on\s+[a-z]+", t_lower):
                return _quick_search_result("youtube.com", m.group(1).strip())

        # Known sites detection (from site_search_profiles)
        try:
            from storage import Storage
            db = Storage()
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT domain FROM site_search_profiles")
                known_domains = [row[0] for row in cursor.fetchall()]
        except Exception:
            known_domains = []

        for domain in known_domains:
            name = domain.split('.')[0] # e.g. youtube from youtube.com
            # search X on [name]
            if re.match(rf"(?:search|look\s+up|find)\s+(?:for\s+)?(.+?)\s+on\s+{name}\s*$", t_lower):
                return _quick_search_result(domain, re.match(rf"(?:search|look\s+up|find)\s+(?:for\s+)?(.+?)\s+on\s+{name}\s*$", t_lower).group(1).strip())
            # search [name] for X
            if re.match(rf"search\s+{name}\s+for\s+(.+?)\s*$", t_lower):
                return _quick_search_result(domain, re.match(rf"search\s+{name}\s+for\s+(.+?)\s*$", t_lower).group(1).strip())


        # ─── Step 1: Direct Action Pattern Matching ────────────────
        for pattern, tag_gen, action_type in _COMPILED_PATTERNS:
            match = pattern.search(clean_text)
            if match:
                try:
                    action_tag = tag_gen(match)
                    params = {"action_type": action_type}
                    for i, group in enumerate(match.groups(), 1):
                        params[f"param_{i}"] = group

                    logger.debug(f"IntentRouter: DirectAction match: {action_tag}")
                    return IntentResult(
                        task_type=TaskType.DIRECT_ACTION,
                        recommended_model="",
                        confidence=0.95,
                        extracted_params=params,
                        action_tag=action_tag,
                        raw_text=clean_text,
                    )
                except Exception as e:
                    logger.debug(f"IntentRouter: pattern match error: {e}")
                    continue

        # ─── Step 2: Keyword Scoring ───────────────────────────────
        scores = {
            TaskType.DIRECT_ACTION: _keyword_score(clean_text, ACTION_KEYWORDS),
            TaskType.CODE_TASK: _keyword_score(clean_text, CODE_KEYWORDS),
            TaskType.KNOWLEDGE_QUERY: _keyword_score(clean_text, KNOWLEDGE_KEYWORDS),
            TaskType.LONG_CONTEXT: _keyword_score(clean_text, LONG_CONTEXT_KEYWORDS),
            TaskType.VISION_TASK: _keyword_score(clean_text, VISION_KEYWORDS),
        }

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        # If action keywords win but no direct pattern match, still route to AI
        # but with action-aware model
        if best_type == TaskType.DIRECT_ACTION:
            best_type = TaskType.SIMPLE_QUESTION

        # Low confidence → default to simple question
        if best_score < self.confidence_threshold:
            best_type = TaskType.SIMPLE_QUESTION
            best_score = 0.5

        # Get model for this task type
        from groq_router import MODEL_ROUTES
        model = MODEL_ROUTES.get(best_type, "meta-llama/llama-4-scout-17b-16e-instruct")

        logger.debug(f"IntentRouter: {best_type.value} (confidence={best_score:.2f}, model={model})")

        return IntentResult(
            task_type=best_type,
            recommended_model=model,
            confidence=best_score,
            raw_text=clean_text,
        )


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[IntentRouter] = None


def get_intent_router() -> IntentRouter:
    """Get or create the singleton IntentRouter."""
    global _instance
    if _instance is None:
        _instance = IntentRouter()
    return _instance
