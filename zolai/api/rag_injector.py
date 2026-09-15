"""
RAG Injector — Build structured context from SQLite for AI prompts.

Queries 7 tables in priority order, extracts keywords from user message,
and formats results as a structured context block truncated to max_tokens.
"""

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Common stop words to filter from keyword extraction
STOP_WORDS = {
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "as", "into", "through", "during", "before",
    "after", "above", "below", "between", "out", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "can", "will", "just", "don", "should", "now",
    "i", "you", "he", "she", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "what", "which",
    "who", "whom", "this", "that", "these", "those", "am", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "having", "do",
    "does", "did", "doing", "would", "could", "might", "must", "shall",
    "may", "need", "dare", "ought", "used", "go", "going", "went",
    "give", "tell", "said", "say", "like", "know", "think", "want",
}


def _extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    """
    Extract keywords from user message.

    Splits on whitespace/punctuation, filters stop words, and takes top N.
    """
    # Split on non-alpha characters, keep Zolai-compatible words
    words = re.findall(r"[a-zA-Z]+", text.lower())
    # Filter stop words and very short words
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:max_keywords]


class RAGInjector:
    """Build structured RAG context from the Zolai SQLite database."""

    def __init__(self, db_path: str):
        """
        Args:
            db_path: Path to zolai.db.
        """
        self.db_path = db_path

    async def build_context(self, user_message: str, max_tokens: int = 800) -> str:
        """
        Build structured context from database tables.

        Queries 7 tables in priority order:
        1. dictionary (word/definition lookup)
        2. bible_verses (parallel verse search)
        3. grammar_patterns (pattern matching)
        4. phrases (multi-word expressions)
        5. proverbs (proverb lookup)
        6. translations (sentence pairs)
        7. word_usage (word usage profiles)

        Returns formatted context string truncated to max_tokens budget.
        """
        keywords = _extract_keywords(user_message)
        if not keywords:
            return ""

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            results: list[str] = []

            # 1. Dictionary lookup (highest priority)
            for kw in keywords[:3]:
                rows = conn.execute(
                    """SELECT zolai, english_clean, pos, source
                       FROM dictionary
                       WHERE zolai LIKE ? OR english_clean LIKE ?
                       AND is_deleted = 0
                       LIMIT 3""",
                    (f"%{kw}%", f"%{kw}%"),
                ).fetchall()
                for row in rows:
                    results.append(
                        f"[dict] {row['zolai']} = {row['english_clean']} "
                        f"({row['pos'] or 'pos?'} | {row['source']})"
                    )

            # 2. Bible verses (parallel EN/ZO)
            for kw in keywords[:2]:
                rows = conn.execute(
                    """SELECT ref, zo_tdb77, en_kJV
                       FROM bible_verses
                       WHERE zo_tdb77 LIKE ? OR en_kJV LIKE ?
                       LIMIT 3""",
                    (f"%{kw}%", f"%{kw}%"),
                ).fetchall()
                for row in rows:
                    results.append(
                        f"[bible] {row['ref']}: ZO={row['zo_tdb77'][:80]} | EN={row['en_kJV'][:80]}"
                    )

            # 3. Grammar patterns
            for kw in keywords[:2]:
                rows = conn.execute(
                    """SELECT pattern, description, function
                       FROM grammar_patterns
                       WHERE pattern LIKE ? OR description LIKE ?
                       LIMIT 2""",
                    (f"%{kw}%", f"%{kw}%"),
                ).fetchall()
                for row in rows:
                    results.append(
                        f"[grammar] {row['pattern']}: {row['description'][:60]}"
                    )

            # 4. Phrases
            for kw in keywords[:2]:
                rows = conn.execute(
                    """SELECT phrase, translation
                       FROM phrases
                       WHERE phrase LIKE ? OR translation LIKE ?
                       LIMIT 2""",
                    (f"%{kw}%", f"%{kw}%"),
                ).fetchall()
                for row in rows:
                    results.append(f"[phrase] {row['phrase']} = {row['translation']}")

            # 5. Proverbs
            for kw in keywords[:2]:
                rows = conn.execute(
                    """SELECT text, source
                       FROM proverbs
                       WHERE text LIKE ?
                       LIMIT 2""",
                    (f"%{kw}%",),
                ).fetchall()
                for row in rows:
                    results.append(f"[proverb] {row['text'][:100]} ({row['source']})")

            # 6. Translations (sentence pairs)
            for kw in keywords[:2]:
                rows = conn.execute(
                    """SELECT source, target, source_lang
                       FROM translations
                       WHERE source LIKE ? OR target LIKE ?
                       LIMIT 2""",
                    (f"%{kw}%", f"%{kw}%"),
                ).fetchall()
                for row in rows:
                    results.append(
                        f"[trans] {row['source_lang']}: {row['source'][:60]} → {row['target'][:60]}"
                    )

            # 7. Word usage (per-book profiles)
            for kw in keywords[:2]:
                rows = conn.execute(
                    """SELECT word, book, frequency
                       FROM word_usage
                       WHERE word LIKE ?
                       LIMIT 2""",
                    (f"%{kw}%",),
                ).fetchall()
                for row in rows:
                    results.append(
                        f"[usage] {row['word']} in {row['book']}: freq={row['frequency']}"
                    )

            conn.close()

            # Format and truncate to token budget (rough: 1 token ≈ 4 chars)
            max_chars = max_tokens * 4
            formatted = "\n".join(results)
            if len(formatted) > max_chars:
                formatted = formatted[:max_chars] + "\n... [truncated]"

            return formatted

        except Exception as e:
            logger.error("RAG context build failed: %s", e)
            return ""
