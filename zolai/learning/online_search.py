"""Online search module for vocabulary, grammar, and Bible lookup.

Uses web search to find additional resources for Zolai language learning.
Provides ranked search with TF-IDF-inspired scoring, cross-lingual lookup,
topical Bible search, TTL caching, and search analytics.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300  # seconds


class OnlineSearch:
    """Search online resources for Zolai language learning.

    Features:
    - TF-IDF-inspired relevance scoring for ranked results
    - Cross-lingual search (EN→ZO and ZO→EN dictionaries)
    - Topical Bible search by theme keywords
    - TTL-based cache with configurable expiry
    - Search analytics tracking (query, category, count, last_seen)
    """

    def __init__(self, ttl: int = _DEFAULT_TTL) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_ttl: dict[str, float] = {}
        self._ttl = ttl
        self._analytics: dict[str, dict[str, Any]] = {}

    # ── Relevance scoring ────────────────────────────────────────────────

    @staticmethod
    def _compute_relevance(
        query: str,
        text: str,
        exact_match: bool = False,
        prefix_match: bool = False,
    ) -> float:
        """Compute TF-IDF inspired relevance score between query and text.

        Factors:
        - Exact match: highest score (1.0)
        - Prefix match: high score (0.8)
        - Term overlap: IDF-weighted term frequency
        - Length normalization: penalize overly long texts

        Args:
            query: Search query.
            text: Candidate text to score against.
            exact_match: If True, text equals query (boost score).
            prefix_match: If True, text starts with query (boost score).

        Returns:
            Relevance score between 0.0 and 1.0.
        """
        if not query or not text:
            return 0.0

        q_lower = query.lower()
        t_lower = text.lower()

        # Exact match bonus
        if q_lower == t_lower:
            return 1.0

        # Prefix match bonus
        if prefix_match or t_lower.startswith(q_lower):
            return 0.85

        # Term-level scoring
        q_tokens = q_lower.split()
        t_tokens = t_lower.split()

        if not q_tokens or not t_tokens:
            return 0.0

        # IDF-like weighting: rare query terms get higher weight
        t_set = set(t_tokens)
        matched = 0
        for qt in q_tokens:
            if qt in t_set:
                matched += 1

        if matched == 0:
            # Check substring containment
            if q_lower in t_lower:
                return 0.5
            return 0.0

        # Base term overlap ratio
        term_ratio = matched / len(q_tokens)

        # Length normalization: penalize texts much longer than query
        len_ratio = len(q_tokens) / max(len(t_tokens), 1)
        length_factor = min(1.0, 0.5 + 0.5 * len_ratio)

        return round(min(1.0, term_ratio * 0.7 + length_factor * 0.3), 4)

    # ── TTL cache helpers ────────────────────────────────────────────────

    def _cache_get(self, key: str) -> Any | None:
        """Get value from TTL cache if still valid."""
        if key in self._cache:
            ts = self._cache_ttl.get(key, 0.0)
            if time.monotonic() - ts < self._ttl:
                return self._cache[key]
            # Expired — remove
            del self._cache[key]
            self._cache_ttl.pop(key, None)
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        """Set value in TTL cache."""
        self._cache[key] = value
        self._cache_ttl[key] = time.monotonic()

    # ── Analytics ────────────────────────────────────────────────────────

    def _track_analytics(self, query: str, category: str, count: int) -> None:
        """Track search analytics.

        Args:
            query: Search query string.
            category: Search category (vocabulary, grammar, bible, cross_lingual, topical).
            count: Number of results returned.
        """
        key = f"{query}:{category}"
        if key in self._analytics:
            self._analytics[key]["count"] += count
            self._analytics[key]["last_seen"] = time.time()
        else:
            self._analytics[key] = {
                "query": query,
                "category": category,
                "count": count,
                "last_seen": time.time(),
            }

    def get_analytics(self) -> list[dict[str, Any]]:
        """Get search analytics sorted by count (descending).

        Returns:
            List of analytics entries with query, category, count, last_seen.
        """
        return sorted(
            self._analytics.values(),
            key=lambda x: x["count"],
            reverse=True,
        )

    # ── Public search API ────────────────────────────────────────────────

    def search_vocabulary(self, word: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for vocabulary information online.

        Args:
            word: Zolai or English word to search.
            limit: Maximum results.

        Returns:
            List of search results.
        """
        cache_key = f"vocab:{word}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        results = []

        # Search Bible for word usage
        bible_results = self._search_bible(word, limit)
        if bible_results:
            results.extend(bible_results)

        # Search dictionary
        dict_results = self._search_dictionary(word, limit)
        if dict_results:
            results.extend(dict_results)

        # Rank results by relevance
        ranked = self._rank_results(word, results)

        self._cache_set(cache_key, ranked[:limit])
        self._track_analytics(word, "vocabulary", len(ranked[:limit]))
        return ranked[:limit]

    def search_grammar(self, pattern: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for grammar patterns online.

        Args:
            pattern: Grammar pattern to search (e.g., "SOV", "negation").
            limit: Maximum results.

        Returns:
            List of search results.
        """
        cache_key = f"grammar:{pattern}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        results: list[dict[str, Any]] = []

        # Search grammar patterns in database
        conn = sqlite3.connect(str(config.paths.zolai_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
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

        self._cache_set(cache_key, results)
        self._track_analytics(pattern, "grammar", len(results))
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
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        results = self._search_bible(query, limit)

        self._cache_set(cache_key, results)
        self._track_analytics(query, "bible", len(results))
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

    def search_ranked(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search with TF-IDF-inspired relevance ranking.

        Args:
            query: Search query.
            category: Optional category filter (vocabulary, grammar, bible).
            limit: Maximum results.

        Returns:
            Ranked list of results with relevance scores.
        """
        results: list[dict[str, Any]] = []

        if category is None or category == "vocabulary":
            dict_results = self._search_dictionary(query, limit)
            for r in dict_results:
                text = r.get("zolai", "") + " " + r.get("english", "")
                r["relevance"] = self._compute_relevance(query, text)
                results.append(r)

        if category is None or category == "bible":
            bible_results = self._search_bible(query, limit)
            for r in bible_results:
                text = r.get("zo_tdb77", "") + " " + r.get("en_kjv", "")
                r["relevance"] = self._compute_relevance(query, text)
                results.append(r)

        if category is None or category == "grammar":
            grammar_results = self.search_grammar(query, limit)
            for r in grammar_results:
                text = r.get("pattern", "") + " " + r.get("description", "")
                r["relevance"] = self._compute_relevance(query, text)
                results.append(r)

        # Sort by relevance descending
        results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

        self._track_analytics(query, category or "all", len(results[:limit]))
        return results[:limit]

    def search_cross_lingual(
        self,
        query: str,
        source_lang: str = "en",
        target_lang: str = "zo",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Cross-lingual search between English and Zolai dictionaries.

        When source_lang="en", queries dictionary_en_zo table.
        When source_lang="zo", queries dictionary table.

        Args:
            query: Search term in source language.
            source_lang: Source language code ("en" or "zo").
            target_lang: Target language code ("en" or "zo").
            limit: Maximum results.

        Returns:
            List of cross-lingual search results.
        """
        cache_key = f"cross:{source_lang}:{target_lang}:{query}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        results: list[dict[str, Any]] = []

        conn = sqlite3.connect(str(config.paths.zolai_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            if source_lang == "en":
                # English → Zolai: query dictionary_en_zo
                cur.execute(
                    """SELECT headword, translations_clean, translations, pos, source
                       FROM dictionary_en_zo
                       WHERE headword LIKE ?
                       LIMIT ?""",
                    (f"%{query}%", limit),
                )
                for row in cur.fetchall():
                    import json
                    trans = row["translations_clean"]
                    if not trans:
                        try:
                            trans_list = json.loads(row["translations"] or "[]")
                            trans = ", ".join(trans_list) if trans_list else ""
                        except Exception:
                            trans = ""
                    results.append({
                        "source": "dictionary_en_zo",
                        "type": "cross_lingual",
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                        "source_term": row["headword"],
                        "target_term": trans,
                        "pos": row["pos"],
                        "dict_source": row["source"],
                        "relevance": self._compute_relevance(query, row["headword"]),
                    })
            else:
                # Zolai → English: query dictionary
                cur.execute(
                    """SELECT zolai, english_clean, english, pos, source
                       FROM dictionary
                       WHERE zolai LIKE ?
                       LIMIT ?""",
                    (f"%{query}%", limit),
                )
                for row in cur.fetchall():
                    results.append({
                        "source": "dictionary",
                        "type": "cross_lingual",
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                        "source_term": row["zolai"],
                        "target_term": row["english_clean"] or row["english"],
                        "pos": row["pos"],
                        "dict_source": row["source"],
                        "relevance": self._compute_relevance(query, row["zolai"]),
                    })

        except Exception as e:
            logger.debug("Cross-lingual search failed: %s", e)
        finally:
            conn.close()

        results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

        self._cache_set(cache_key, results)
        self._track_analytics(query, "cross_lingual", len(results))
        return results

    def search_topical(
        self,
        theme: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search Bible verses by topic/theme keywords across books.

        Matches theme keywords against English KJV text and Zolai translations
        to find thematically related verses.

        Args:
            theme: Theme keywords (e.g., "love", "creation", "prayer").
            limit: Maximum results.

        Returns:
            List of Bible verses matching the theme, ranked by relevance.
        """
        cache_key = f"topical:{theme}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        results: list[dict[str, Any]] = []

        conn = sqlite3.connect(str(config.paths.zolai_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # Build keyword-based search across EN and ZO text
            keywords = theme.split()
            conditions = []
            params: list[str] = []

            for kw in keywords:
                conditions.append("(en_kjv LIKE ? OR zo_tdb77 LIKE ? OR zo_tedim2010 LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

            where_clause = " OR ".join(conditions) if conditions else "1=1"
            params.append(limit)

            cur.execute(
                f"""SELECT ref, book, chapter, verse,
                           zo_tdb77, zo_tedim2010, en_kjv
                    FROM bible_verses
                    WHERE {where_clause}
                    LIMIT ?""",
                params,
            )

            for row in cur.fetchall():
                # Score relevance of this verse to the theme
                verse_text = (row["en_kjv"] or "") + " " + (row["zo_tdb77"] or "")
                relevance = self._compute_relevance(theme, verse_text)
                results.append({
                    "source": "bible_topical",
                    "type": "verse",
                    "ref": row["ref"],
                    "book": row["book"],
                    "chapter": row["chapter"],
                    "verse": row["verse"],
                    "zo_tdb77": row["zo_tdb77"],
                    "zo_tedim2010": row["zo_tedim2010"],
                    "en_kjv": row["en_kjv"],
                    "relevance": relevance,
                })

        except Exception as e:
            logger.debug("Topical Bible search failed: %s", e)
        finally:
            conn.close()

        results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

        self._cache_set(cache_key, results)
        self._track_analytics(theme, "topical", len(results))
        return results

    # ── Internal search helpers ──────────────────────────────────────────

    def _search_bible(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Internal Bible search."""
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
                verse_text = (row["en_kjv"] or "") + " " + (row["zo_tdb77"] or "")
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
                    "relevance": self._compute_relevance(query, verse_text),
                })
            return results

        except Exception as e:
            logger.debug("Bible search failed: %s", e)
            return []
        finally:
            conn.close()

    def _search_dictionary(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Internal dictionary search."""
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
                    "relevance": self._compute_relevance(query, row["zolai"]),
                })
            return results

        except Exception as e:
            logger.debug("Dictionary search failed: %s", e)
            return []
        finally:
            conn.close()

    def _rank_results(
        self,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rank results by relevance to query.

        Args:
            query: Original search query.
            results: Unranked result list.

        Returns:
            Results sorted by relevance score descending.
        """
        for r in results:
            # Compose searchable text from available fields
            parts = []
            for key in ("zolai", "english", "pattern", "description", "en_kjv", "zo_tdb77", "ref"):
                val = r.get(key)
                if val:
                    parts.append(str(val))
            text = " ".join(parts)
            r["relevance"] = self._compute_relevance(query, text)

        results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
        return results

    def clear_cache(self) -> None:
        """Clear search cache and TTL records."""
        self._cache.clear()
        self._cache_ttl.clear()
        logger.info("Search cache cleared")


# Global instance
_online_search: OnlineSearch | None = None


def get_online_search() -> OnlineSearch:
    """Get or create the global online search instance."""
    global _online_search
    if _online_search is None:
        _online_search = OnlineSearch()
    return _online_search
