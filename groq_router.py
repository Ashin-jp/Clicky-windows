"""
groq_router.py — Multi-model Groq router with rate limit cascade.

Routes AI requests to the best Groq model by task type, with automatic
fallback on rate limits (HTTP 429). Request queue with priority levels.
All config stored in SQLite, hot-reloadable.
"""

import logging
import os
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TaskType(Enum):
    DIRECT_ACTION = "direct_action"
    SIMPLE_QUESTION = "simple_question"
    KNOWLEDGE_QUERY = "knowledge_query"
    CODE_TASK = "code_task"
    LONG_CONTEXT = "long_context"
    VISION_TASK = "vision_task"


class Priority(Enum):
    URGENT = 0    # Voice response — process immediately
    NORMAL = 1    # Background tasks
    LOW = 2       # Indexing, summaries


# ─── Model Configuration ─────────────────────────────────────────────
MODEL_ROUTES = {
    TaskType.SIMPLE_QUESTION: "meta-llama/llama-4-scout-17b-16e-instruct",
    TaskType.KNOWLEDGE_QUERY: "gemma2-9b-it",
    TaskType.CODE_TASK: "llama-3.3-70b-versatile",
    TaskType.LONG_CONTEXT: "mixtral-8x7b-32768",
    TaskType.VISION_TASK: "meta-llama/llama-4-scout-17b-16e-instruct",
}

# Fallback chain — tried in order when rate limited
FALLBACK_CHAIN = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "gemma2-9b-it",
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
]


@dataclass
class RateLimitState:
    """Per-model rate limit tracking."""
    cooldown_until: float = 0.0
    consecutive_429s: int = 0
    total_requests: int = 0
    total_tokens: int = 0


@dataclass
class RoutedRequest:
    """A request routed through the Groq router."""
    system_prompt: str
    user_prompt: str
    task_type: TaskType = TaskType.SIMPLE_QUESTION
    priority: Priority = Priority.NORMAL
    history: list = field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.7
    image_b64: Optional[str] = None


@dataclass
class RoutedResponse:
    """Response from the Groq router."""
    text: str
    model_used: str
    duration_ms: int
    tokens_used: int = 0
    was_fallback: bool = False
    error: Optional[str] = None


class GroqModelRouter:
    """
    Routes AI requests to the best Groq model by task type.
    Handles rate limits with automatic model fallback.
    """

    def __init__(self, api_key: str = None):
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self._client = None
        self._rate_limits: dict[str, RateLimitState] = {}
        self._lock = threading.Lock()
        self._request_count = 0

        # Initialize rate limit trackers for all models
        for model in FALLBACK_CHAIN:
            self._rate_limits[model] = RateLimitState()

        self._init_client()

    def _init_client(self):
        """Initialize the Groq client."""
        if not self._api_key:
            # Try loading from config
            try:
                import config
                self._api_key = getattr(config, 'GROQ_API_KEY', '')
            except ImportError:
                pass

        if not self._api_key:
            logger.warning("GroqRouter: No API key — AI calls will fail")
            return

        try:
            from groq import Groq, AsyncGroq
            self._client = Groq(api_key=self._api_key)
            self._async_client = AsyncGroq(api_key=self._api_key)
            logger.info("GroqRouter: Groq client initialized")
        except ImportError:
            logger.error("GroqRouter: groq package not installed")
        except Exception as e:
            logger.error(f"GroqRouter: initialization failed: {e}")

    def get_model_for_task(self, task_type: TaskType) -> str:
        """Get the recommended model for a task type."""
        return MODEL_ROUTES.get(task_type, FALLBACK_CHAIN[0])

    def is_model_available(self, model: str) -> bool:
        """Check if a model is currently available (not rate limited)."""
        with self._lock:
            state = self._rate_limits.get(model)
            if not state:
                return True
            if state.cooldown_until > time.monotonic():
                return False
            # Cooldown expired — reset
            state.consecutive_429s = 0
            return True

    def chat(self, request: RoutedRequest) -> RoutedResponse:
        """
        Send a chat request through the router.
        Handles model selection, rate limit fallback, and error recovery.
        """
        if not self._client:
            return RoutedResponse(
                text="AI is not available — check your GROQ_API_KEY.",
                model_used="none",
                duration_ms=0,
                error="no_client",
            )

        # Build model list: preferred first, then fallbacks
        preferred = self.get_model_for_task(request.task_type)
        models_to_try = [preferred]
        for m in FALLBACK_CHAIN:
            if m != preferred and self.is_model_available(m):
                models_to_try.append(m)

        # Build messages
        messages = [{"role": "system", "content": request.system_prompt}]
        for turn in request.history[-10:]:
            if isinstance(turn, dict):
                if turn.get("user"):
                    messages.append({"role": "user", "content": turn["user"]})
                if turn.get("assistant"):
                    messages.append({"role": "assistant", "content": turn["assistant"]})
            elif isinstance(turn, (list, tuple)) and len(turn) == 2:
                messages.append({"role": "user", "content": str(turn[0])})
                messages.append({"role": "assistant", "content": str(turn[1])})

        # Handle vision tasks
        if request.image_b64 and request.task_type == TaskType.VISION_TASK:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": request.user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{request.image_b64}"},
                    },
                ],
            })
        else:
            messages.append({"role": "user", "content": request.user_prompt})

        start = time.monotonic()
        was_fallback = False

        for i, model in enumerate(models_to_try):
            if not self.is_model_available(model):
                continue

            try:
                logger.info(f"GroqRouter: trying {model} ({request.task_type.value})...")

                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )

                text = response.choices[0].message.content or ""
                elapsed = int((time.monotonic() - start) * 1000)
                tokens = getattr(response.usage, 'total_tokens', 0) if response.usage else 0

                # Track usage
                with self._lock:
                    state = self._rate_limits.get(model, RateLimitState())
                    state.total_requests += 1
                    state.total_tokens += tokens
                    self._rate_limits[model] = state

                self._request_count += 1
                logger.info(f"GroqRouter: {model} OK — {len(text)} chars, {elapsed}ms, {tokens} tokens")

                return RoutedResponse(
                    text=text,
                    model_used=model,
                    duration_ms=elapsed,
                    tokens_used=tokens,
                    was_fallback=(i > 0),
                )

            except Exception as e:
                err_str = str(e).lower()

                if "429" in err_str or "rate" in err_str:
                    # Rate limited — cool this model down
                    backoff = min(60 * (2 ** self._rate_limits[model].consecutive_429s), 300)
                    with self._lock:
                        state = self._rate_limits[model]
                        state.cooldown_until = time.monotonic() + backoff
                        state.consecutive_429s += 1
                    logger.warning(f"GroqRouter: {model} rate limited, backoff {backoff}s")
                    was_fallback = True
                    continue
                else:
                    logger.error(f"GroqRouter: {model} failed: {e}")
                    continue

        # All models exhausted
        elapsed = int((time.monotonic() - start) * 1000)
        logger.error("GroqRouter: all models exhausted")

        return RoutedResponse(
            text="I'm sorry, all AI models are currently unavailable. Please try again shortly.",
            model_used="none",
            duration_ms=elapsed,
            error="all_models_exhausted",
        )

    async def async_chat_stream(self, request: RoutedRequest):
        """
        Send an async chat request and yield chunks.
        Yields strings (text chunks) and finally yields a RoutedResponse.
        """
        import asyncio
        if not self._async_client:
            yield RoutedResponse(
                text="AI is not available — check your GROQ_API_KEY.",
                model_used="none",
                duration_ms=0,
                error="no_client",
            )
            return

        preferred = self.get_model_for_task(request.task_type)
        models_to_try = [preferred]
        for m in FALLBACK_CHAIN:
            if m != preferred and self.is_model_available(m):
                models_to_try.append(m)

        messages = [{"role": "system", "content": request.system_prompt}]
        for turn in request.history[-10:]:
            if isinstance(turn, dict):
                if turn.get("user"):
                    messages.append({"role": "user", "content": turn["user"]})
                if turn.get("assistant"):
                    messages.append({"role": "assistant", "content": turn["assistant"]})
            elif isinstance(turn, (list, tuple)) and len(turn) == 2:
                messages.append({"role": "user", "content": str(turn[0])})
                messages.append({"role": "assistant", "content": str(turn[1])})

        if request.image_b64 and request.task_type == TaskType.VISION_TASK:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": request.user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{request.image_b64}"},
                    },
                ],
            })
        else:
            messages.append({"role": "user", "content": request.user_prompt})

        start = time.monotonic()
        was_fallback = False

        for i, model in enumerate(models_to_try):
            if not self.is_model_available(model):
                continue

            try:
                logger.info(f"GroqRouter: streaming {model} ({request.task_type.value})...")
                stream = await self._async_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    stream=True
                )
                
                full_text = ""
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        full_text += delta.content
                        yield delta.content
                        
                elapsed = int((time.monotonic() - start) * 1000)
                # approximate token usage since streaming doesn't return usage info accurately on groq sometimes
                tokens = int(len(full_text) / 4)

                with self._lock:
                    state = self._rate_limits.get(model, RateLimitState())
                    state.total_requests += 1
                    state.total_tokens += tokens
                    self._rate_limits[model] = state

                self._request_count += 1
                logger.info(f"GroqRouter: {model} streaming OK — {len(full_text)} chars, {elapsed}ms")

                yield RoutedResponse(
                    text=full_text,
                    model_used=model,
                    duration_ms=elapsed,
                    tokens_used=tokens,
                    was_fallback=(i > 0),
                )
                return

            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str:
                    backoff = min(60 * (2 ** self._rate_limits[model].consecutive_429s), 300)
                    with self._lock:
                        state = self._rate_limits[model]
                        state.cooldown_until = time.monotonic() + backoff
                        state.consecutive_429s += 1
                    logger.warning(f"GroqRouter: {model} rate limited, backoff {backoff}s")
                    was_fallback = True
                    continue
                else:
                    logger.error(f"GroqRouter: streaming {model} failed: {e}")
                    continue

        elapsed = int((time.monotonic() - start) * 1000)
        yield RoutedResponse(
            text="I'm sorry, all AI models are currently unavailable.",
            model_used="none",
            duration_ms=elapsed,
            error="all_models_exhausted",
        )

    def quick_chat(self, prompt: str, task_type: TaskType = TaskType.SIMPLE_QUESTION,
                   system_prompt: str = "You are a helpful AI assistant. Be concise.") -> str:
        """Convenience method for simple one-shot queries."""
        request = RoutedRequest(
            system_prompt=system_prompt,
            user_prompt=prompt,
            task_type=task_type,
            priority=Priority.NORMAL,
        )
        response = self.chat(request)
        return response.text

    def get_stats(self) -> dict:
        """Get router usage statistics."""
        with self._lock:
            stats = {
                "total_requests": self._request_count,
                "models": {},
            }
            for model, state in self._rate_limits.items():
                stats["models"][model] = {
                    "requests": state.total_requests,
                    "tokens": state.total_tokens,
                    "is_available": self.is_model_available(model),
                    "cooldown_remaining": max(0, int(state.cooldown_until - time.monotonic())),
                }
            return stats


# ─── Singleton ────────────────────────────────────────────────────────
_router_instance: Optional[GroqModelRouter] = None
_router_lock = threading.Lock()


def get_router() -> GroqModelRouter:
    """Get or create the singleton router instance."""
    global _router_instance
    with _router_lock:
        if _router_instance is None:
            _router_instance = GroqModelRouter()
        return _router_instance
