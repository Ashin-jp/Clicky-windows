"""
knowledge_base.py — Curated personal knowledge store.

BM25 text search before Groq calls. Auto-tagged by app context.
Voice commands: remember, recall, forget, export.
"""

import logging
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Personal knowledge store with BM25 search."""

    def __init__(self):
        self._db = None
        self._bm25 = None
        self._corpus: list[dict] = []
        self._last_index_time: float = 0
        logger.info("KnowledgeBase: initialized")

    def _get_db(self):
        if self._db is None:
            from storage import get_db
            self._db = get_db()
        return self._db

    def _rebuild_index(self):
        """Rebuild BM25 index from database."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.debug("KnowledgeBase: rank-bm25 not installed, using DB search only")
            return

        try:
            entries = self._get_db().get_all_knowledge()
            self._corpus = entries
            if not entries:
                self._bm25 = None
                return

            tokenized = [self._tokenize(e["content"] + " " + e.get("tags", "")) for e in entries]
            self._bm25 = BM25Okapi(tokenized)
            self._last_index_time = time.monotonic()
        except Exception as e:
            logger.debug(f"KnowledgeBase: index rebuild failed: {e}")

    def _tokenize(self, text: str) -> list[str]:
        return text.lower().split()

    def remember(self, content: str, source_app: str = "") -> str:
        """Save content to knowledge base. Returns confirmation."""
        tags = self._auto_tag(source_app)
        try:
            entry_id = self._get_db().save_knowledge(content, tags, source_app)
            self._rebuild_index()
            return f"Got it, I'll remember that. (entry #{entry_id})"
        except Exception as e:
            logger.error(f"KnowledgeBase: save failed: {e}")
            return f"Sorry, I couldn't save that: {e}"

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """Search knowledge base. Returns top matches."""
        # Rebuild index if stale (>60s)
        if time.monotonic() - self._last_index_time > 60:
            self._rebuild_index()

        # Try BM25 first
        if self._bm25 and self._corpus:
            try:
                tokens = self._tokenize(query)
                scores = self._bm25.get_scores(tokens)
                ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

                results = []
                for idx, score in ranked[:top_k]:
                    if score > 0:
                        entry = self._corpus[idx].copy()
                        entry["score"] = float(score)
                        results.append(entry)

                if results:
                    return results
            except Exception as e:
                logger.debug(f"KnowledgeBase: BM25 search failed: {e}")

        # Fallback to SQLite LIKE search
        try:
            return self._get_db().search_knowledge(query, top_k)
        except Exception:
            return []

    def should_use_local(self, query: str, threshold: float = 0.75) -> bool:
        """Check if local knowledge can answer this query."""
        results = self.search(query, top_k=1)
        if results and results[0].get("score", 0) > threshold:
            return True
        return False

    def get_context_for_query(self, query: str) -> str:
        """Get knowledge context to prepend to AI calls."""
        results = self.search(query, top_k=3)
        if not results:
            return ""
        parts = ["[Relevant knowledge from your notes:]"]
        for r in results:
            content = r.get("content", "")[:500]
            parts.append(f"- {content}")
        return "\n".join(parts)

    def forget(self, query: str) -> str:
        """Delete matching knowledge entries."""
        results = self.search(query, top_k=10)
        if not results:
            return f"I don't have any knowledge about '{query}'."

        deleted = 0
        for r in results:
            try:
                self._get_db().delete_knowledge(r["id"])
                deleted += 1
            except Exception:
                pass

        self._rebuild_index()
        return f"Forgotten {deleted} entries about '{query}'."

    def export_to_markdown(self) -> str:
        """Export all knowledge to a markdown file."""
        from pathlib import Path
        entries = self._get_db().get_all_knowledge()
        if not entries:
            return "No knowledge entries to export."

        docs_dir = Path.home() / "Documents"
        docs_dir.mkdir(exist_ok=True)
        filepath = docs_dir / "clicky_knowledge_export.md"

        lines = ["# Clicky Knowledge Base Export\n"]
        for e in entries:
            lines.append(f"## Entry #{e['id']}")
            lines.append(f"**Tags:** {e.get('tags', 'none')}")
            lines.append(f"**Source:** {e.get('source_app', 'unknown')}")
            lines.append(f"**Created:** {e.get('created_at', '')}\n")
            lines.append(e["content"])
            lines.append("\n---\n")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return f"Exported {len(entries)} entries to {filepath}"

    def _auto_tag(self, source_app: str) -> str:
        """Generate tags based on source app context."""
        app_lower = (source_app or "").lower()
        tags = []
        if any(x in app_lower for x in ("code", "pycharm", "idea", "vim", "studio")):
            tags.append("coding")
        elif any(x in app_lower for x in ("chrome", "firefox", "edge", "brave")):
            tags.append("web")
        elif any(x in app_lower for x in ("word", "docs", "writer")):
            tags.append("writing")
        elif any(x in app_lower for x in ("excel", "sheets", "calc")):
            tags.append("data")
        elif any(x in app_lower for x in ("terminal", "cmd", "powershell", "wt")):
            tags.append("terminal")
        return ",".join(tags)


_instance: Optional[KnowledgeBase] = None

def get_knowledge_base() -> KnowledgeBase:
    global _instance
    if _instance is None:
        _instance = KnowledgeBase()
    return _instance
