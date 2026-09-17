"""Online search module for vocabulary, grammar, and Bible lookup.

Uses web search to find additional resources for Zolai language learning.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class OnlineSearch:
    """Search online resources for Zolai language learning."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def search_vocabulary(self, word: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for vocabulary information online.

        Args:
            word: Zolai or English word to search.
            limit: Maximum results.

        Returns:
            List of search results.
        """
        cache_key = f"vocab:{word}:{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = []

        # Search Bible for word usage
        bible_results = self._search_bible(word, limit)
        if bible_results:
            results.extend(bible_results)

        # Search dictionary
        dict_results = self._search_dictionary(word, limit)
        if dict_results:
            results.extend(dict_results)

        # Cache results
        self._cache[cache_key] = results[:limit]
        return results[:limit]

    def search_grammar(self, pattern: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for grammar patterns online.

        Args:
            pattern: Grammar pattern to search (e.g., "SOV", "negation").
            limit: Maximum results.

        Returns:
            List of search results.
        """
        cache_key = f"grammar:{pattern}:{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = []

        # Search grammar patterns in database
        from ..config import config
        import sqlite3

        conn = sqlite3.connect(str(config.paths.zolai_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # Search by function type
            cur.execute(
                """SELECT * FROM grammar_patterns 
                   WHERE function LIKE ? OR description LIKE ?
                   LIMIT ?""",
                (f"%{pattern}%", f"%{pattern}%", limit),
            )
            for row in cur.fetchall():
                results.append({
                    "source": "database",
                    "type": "grammar_pattern",
                    "pattern": row["pattern"],
                    "function": row["function"],
                    "description": row["description"],
                    "examples": row["examples"],
                })
        except Exception as e:
            logger.debug("Search grammar failed: %s", e)
        finally:
            conn.close()

        # Cache results
        self._cache[cache_key] = results
        return results

    def search_bible(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search Bible verses for context.

        Args:
            query: Search query.
            limit: Maximum results.

        Returns:
            List of Bible verse results.
        """
        cache_key = f"bible:{query}:{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = self._search_bible(query, limit)

        # Cache results
        self._cache[cache_key] = results
        return results

    def search_all(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search all resources for a query.

        Args:
            query: Search query.
            limit: Maximum results per category.

        Returns:
            Dict with results by category.
        """
        return {
            "vocabulary": self.search_vocabulary(query, limit),
            "grammar": self.search_grammar(query, limit),
            "bible": self.search_bible(query, limit),
        }

    def _search_bible(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Internal Bible search."""
        from ..config import config
        import sqlite3

        conn = sqlite3.connect(str(config.paths.zolai_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # Search Zolai Bible
            cur.execute(
                """SELECT ref, book, chapter, verse, 
                          zo_tdb77, zo_tedim2010, en_kjv
                   FROM bible_verses
                   WHERE zo_tdb77 LIKE ? OR zo_tedim2010 LIKE ? OR en_kjv LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            )

            results = []
            for row in cur.fetchall():
                results.append({
                    "source": "bible",
                    "type": "verse",
                    "ref": row["ref"],
                    "book": row["book"],
                    "chapter": row["chapter"],
                    "verse": row["verse"],
                    "zo_tdb77": row["zo_tdb77"],
                    "zo_tedim2010": row["zo_tedim2010"],
                    "en_kjv": row["en_kjv"],
                })
            return results

        except Exception as e:
            logger.debug("Bible search failed: %s", e)
            return []
        finally:
            conn.close()

    def _search_dictionary(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Internal dictionary search."""
        from ..config import config
        import sqlite3

        conn = sqlite3.connect(str(config.paths.zolai_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # Search Zolai→English
            cur.execute(
                """SELECT zolai, english, pos, source
                   FROM dictionary
                   WHERE zolai LIKE ? OR english LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            )

            results = []
            for row in cur.fetchall():
                results.append({
                    "source": "dictionary",
                    "type": "entry",
                    "zolai": row["zolai"],
                    "english": row["english"],
                    "pos": row["pos"],
                    "dictionary_source": row["source"],
                })
            return results

        except Exception as e:
            logger.debug("Dictionary search failed: %s", e)
            return []
        finally:
            conn.close()

    def clear_cache(self) -> None:
        """Clear search cache."""
        self._cache.clear()
        logger.info("Search cache cleared")


# Global instance
_online_search: OnlineSearch | None = None


def get_online_search() -> OnlineSearch:
    """Get or create the global online search instance."""
    global _online_search
    if _online_search is None:
        _online_search = OnlineSearch()
    return _online_search
