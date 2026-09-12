"""Question Answering for Zolai using Gemini ensemble + Bible context."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class ZolaiQA:
    """Question Answering for Zolai using Gemini ensemble + Bible context."""

    def __init__(
        self,
        voter: Any = None,
        db_path: str | None = None,
    ):
        """Initialize QA.

        Args:
            voter: EnsembleVoter instance for Gemini integration (optional).
            db_path: Path to zolai.db for Bible context.
        """
        self.voter = voter
        self._db_path = db_path
        self._bible_cache: list[dict] | None = None

    def _ensure_loaded(self) -> None:
        """Lazy-load Bible verses for context."""
        if self._bible_cache is not None:
            return
        self._bible_cache = []

        db_path = self._db_path or os.environ.get(
            "ZOLAI_DB_PATH", str(
                __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent
                / "data" / "zolai.db"
            )
        )
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                "SELECT book_name, chapter, verse, zo_tdb77, en_kJV "
                "FROM bible_verses LIMIT 5000"
            ):
                self._bible_cache.append({
                    "book": row["book_name"] or "",
                    "chapter": row["chapter"],
                    "verse": row["verse"],
                    "zolai": row["zo_tdb77"] or "",
                    "english": row["en_kJV"] or "",
                })
            conn.close()
            logger.info("Loaded %d Bible verses for QA context", len(self._bible_cache))
        except Exception as e:
            logger.warning("Failed to load Bible for QA: %s", e)

    async def answer(
        self,
        question: str,
        context: str = "",
        use_bible: bool = True,
    ) -> dict:
        """Answer question about Zolai text using Gemini ensemble.

        Args:
            question: Question to answer.
            context: Additional context (optional).
            use_bible: Whether to search Bible for relevant verses.

        Returns:
            {"answer": "...", "confidence": 0.9, "source": "Bible GEN 1:1"}
        """
        if self.voter is None:
            return self._fallback_answer(question, context, use_bible)

        # Build Bible context if enabled
        bible_context = ""
        if use_bible:
            bible_context = self._search_bible_context(question)

        full_context = context
        if bible_context:
            full_context = f"{context}\n\nBible context:\n{bible_context}" if context else bible_context

        prompt = (
            "Answer this question about Zolai language or culture. "
            "Use the provided context if available.\n"
            f"Question: {question}\n"
        )
        if full_context:
            prompt += f"Context:\n{full_context}\n"
        prompt += (
            'Output JSON: {"answer": "...", "confidence": 0.9, '
            '"source": "Bible GEN 1:1 or context source"}'
        )

        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            data = result.get("result", result)
            return {
                "answer": data.get("answer", ""),
                "confidence": float(data.get("confidence", 0.5)),
                "source": data.get("source", ""),
            }
        except Exception as e:
            logger.warning("Gemini QA failed, using fallback: %s", e)
            return self._fallback_answer(question, context, use_bible)

    def search_bible(self, query: str, limit: int = 5) -> list[dict]:
        """Search Bible for relevant verses.

        Args:
            query: Search query.
            limit: Maximum results.

        Returns:
            List of matching verses.
        """
        self._ensure_loaded()

        if not self._bible_cache:
            return []

        query_lower = query.lower()
        results = []

        for verse in self._bible_cache:
            zolai = verse.get("zolai", "").lower()
            english = verse.get("english", "").lower()
            if query_lower in zolai or query_lower in english:
                results.append(verse)
                if len(results) >= limit:
                    break

        return results

    def _search_bible_context(self, question: str) -> str:
        """Search Bible and format context for QA prompt.

        Args:
            question: Question to search for.

        Returns:
            Formatted Bible context string.
        """
        verses = self.search_bible(question, limit=3)
        if not verses:
            return ""
        parts = []
        for v in verses:
            ref = f"{v['book']} {v['chapter']}:{v['verse']}"
            parts.append(f"[{ref}] {v['zolai']} — {v['english']}")
        return "\n".join(parts)

    def _fallback_answer(
        self,
        question: str,
        context: str,
        use_bible: bool,
    ) -> dict:
        """Fallback answer when Gemini is unavailable.

        Args:
            question: Question to answer.
            context: Additional context.
            use_bible: Whether to use Bible search.

        Returns:
            Answer dict.
        """
        # Try Bible search
        if use_bible:
            bible_context = self._search_bible_context(question)
            if bible_context:
                return {
                    "answer": f"Based on Bible context:\n{bible_context}",
                    "confidence": 0.4,
                    "source": "Bible search",
                }

        # Try context
        if context:
            return {
                "answer": f"Based on provided context: {context[:200]}",
                "confidence": 0.3,
                "source": "provided context",
            }

        return {
            "answer": "Ka thei kei hi. (I don't know.)",
            "confidence": 0.1,
            "source": "no context available",
        }

    async def answer_with_context(
        self,
        question: str,
        bible_book: str = "",
        bible_chapter: int = 0,
    ) -> dict:
        """Answer with specific Bible book/chapter context.

        Args:
            question: Question to answer.
            bible_book: Bible book name (e.g., "GEN").
            bible_chapter: Chapter number.

        Returns:
            Answer dict with verse references.
        """
        self._ensure_loaded()

        # Filter Bible cache by book/chapter
        context_verses = []
        for v in self._bible_cache:
            if bible_book and v.get("book", "") != bible_book:
                continue
            if bible_chapter and v.get("chapter", 0) != bible_chapter:
                continue
            context_verses.append(v)

        if not context_verses:
            return self.answer(question, use_bible=True)

        # Build context
        context_parts = []
        for v in context_verses[:10]:
            ref = f"{v['book']} {v['chapter']}:{v['verse']}"
            context_parts.append(f"[{ref}] {v['zolai']} — {v['english']}")
        context = "\n".join(context_parts)

        return self.answer(question, context=context, use_bible=False)


# Module-level singleton
_qa_instance: ZolaiQA | None = None


def get_qa(voter: Any = None, db_path: str | None = None) -> ZolaiQA:
    """Get or create the singleton QA instance."""
    global _qa_instance
    if _qa_instance is None:
        _qa_instance = ZolaiQA(voter=voter, db_path=db_path)
    return _qa_instance
