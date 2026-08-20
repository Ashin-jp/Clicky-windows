"""
research_agent.py — Multi-step autonomous research as background task.

Generates search queries via Groq, fetches web results, synthesizes
a coherent answer, and saves to knowledge base.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class ResearchAgent:
    """Background autonomous research agent."""

    def __init__(self):
        self._tts_callback: Optional[Callable] = None
        self._db = None
        self._active_tasks: dict[str, threading.Thread] = {}
        logger.info("ResearchAgent: initialized")

    def set_tts_callback(self, callback: Callable):
        self._tts_callback = callback

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def research(self, topic: str) -> str:
        """Start a background research task. Returns acknowledgment."""
        if topic in self._active_tasks and self._active_tasks[topic].is_alive():
            return f"Already researching '{topic}'."

        thread = threading.Thread(target=self._research_task, args=(topic,),
                                  name=f"Research-{topic[:20]}", daemon=True)
        self._active_tasks[topic] = thread
        thread.start()

        return f"Starting research on '{topic}'. I'll notify you when done."

    def _research_task(self, topic: str):
        """Execute multi-step research pipeline."""
        try:
            from groq_router import get_router, RoutedRequest, TaskType, Priority

            router = get_router()
            self._notify(f"Researching '{topic}'...")

            # Step 1: Generate 3 diverse search queries
            query_prompt = (f"Generate exactly 3 diverse web search queries to research: {topic}\n"
                            f"Output only the queries, one per line. No numbering.")
            query_req = RoutedRequest(
                system_prompt="Generate search queries. Output only queries, one per line.",
                user_prompt=query_prompt,
                task_type=TaskType.SIMPLE_QUESTION,
                priority=Priority.LOW, max_tokens=200, temperature=0.8,
            )
            query_resp = router.chat(query_req)
            queries = [q.strip() for q in query_resp.text.strip().split("\n") if q.strip()][:3]

            if not queries:
                queries = [topic]

            logger.info(f"ResearchAgent: generated {len(queries)} queries for '{topic}'")

            # Step 2: Fetch web results
            all_content = []
            for query in queries:
                try:
                    content = self._web_search(query)
                    if content:
                        all_content.append(content)
                except Exception as e:
                    logger.debug(f"ResearchAgent: search failed for '{query}': {e}")

            if not all_content:
                self._notify(f"Couldn't find web results for '{topic}'. Try a different query.")
                return

            # Step 3: Synthesize
            combined = "\n\n---\n\n".join(all_content)[:8000]
            synth_prompt = (f"Based on the following web search results about '{topic}', "
                            f"write a coherent, comprehensive summary. "
                            f"Mention the number of sources used.\n\n{combined}")

            synth_req = RoutedRequest(
                system_prompt="You are a research assistant. Synthesize web results into a clear summary.",
                user_prompt=synth_prompt,
                task_type=TaskType.LONG_CONTEXT,
                priority=Priority.LOW, max_tokens=1024, temperature=0.5,
            )
            synth_resp = router.chat(synth_req)
            summary = synth_resp.text

            # Step 4: Save to knowledge base
            try:
                from knowledge_base import get_knowledge_base
                kb = get_knowledge_base()
                kb.remember(f"Research on '{topic}':\n\n{summary}", source_app="research_agent")
            except Exception:
                pass

            # Step 5: Notify
            # TTS preview (first 300 chars)
            preview = summary[:300].replace("\n", " ")
            self._notify(f"Research on {topic} complete. {preview}")

            logger.info(f"ResearchAgent: completed research on '{topic}' ({len(summary)} chars)")

        except Exception as e:
            logger.error(f"ResearchAgent: research failed for '{topic}': {e}")
            self._notify(f"Research on '{topic}' failed: {str(e)[:100]}")
        finally:
            self._active_tasks.pop(topic, None)

    def _web_search(self, query: str) -> Optional[str]:
        """Execute a web search and extract text from top results."""
        try:
            import httpx
            from bs4 import BeautifulSoup

            # Use DuckDuckGo HTML search (no API key needed)
            url = "https://html.duckduckgo.com/html/"
            resp = httpx.post(url, data={"q": query}, timeout=10.0,
                              headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract result URLs
            links = []
            for a in soup.select("a.result__a"):
                href = a.get("href", "")
                if href and href.startswith("http"):
                    links.append(href)

            # Fetch top 2 result pages
            content_parts = []
            for link in links[:2]:
                try:
                    page_resp = httpx.get(link, timeout=8.0,
                                          headers={"User-Agent": "Mozilla/5.0"},
                                          follow_redirects=True)
                    page_soup = BeautifulSoup(page_resp.text, "html.parser")

                    # Remove scripts/styles
                    for tag in page_soup(["script", "style", "nav", "header", "footer"]):
                        tag.decompose()

                    text = page_soup.get_text(separator="\n", strip=True)
                    # Take first 2000 chars
                    if text and len(text) > 100:
                        content_parts.append(f"Source: {link}\n{text[:2000]}")
                except Exception:
                    pass

            return "\n\n".join(content_parts) if content_parts else None

        except Exception as e:
            logger.debug(f"ResearchAgent: web search error: {e}")
            return None

    def _notify(self, text: str):
        logger.info(f"ResearchAgent: {text[:100]}")
        if self._tts_callback:
            try:
                self._tts_callback(text)
            except Exception:
                pass


_instance: Optional[ResearchAgent] = None

def get_research_agent() -> ResearchAgent:
    global _instance
    if _instance is None:
        _instance = ResearchAgent()
    return _instance
