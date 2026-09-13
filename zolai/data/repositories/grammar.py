"""Grammar repository for grammar patterns."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class GrammarRepository(BaseRepository):
    """Repository for grammar patterns (grammar_patterns table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "grammar_patterns", id_column="id")

    def get_by_pattern_id(self, pattern_id: str) -> dict[str, Any] | None:
        """Get pattern by unique pattern_id."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(self.table.c.pattern_id == pattern_id)
            ).first()
        return self._row_to_dict(row) if row else None

    def get_by_pattern_name(self, pattern: str) -> list[dict[str, Any]]:
        """Get patterns by pattern name (partial match)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.pattern.ilike(f"%{pattern}%"))
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_function(self, function: str) -> list[dict[str, Any]]:
        """Get patterns by grammatical function."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.function == function)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_book(self, book: str) -> list[dict[str, Any]]:
        """Get patterns applicable to a specific book."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    (self.table.c.book == book) | (self.table.c.book == "")
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_frequency(self, min_freq: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Get patterns by minimum frequency."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.frequency >= min_freq)
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_description(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search in pattern description."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.description.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_functions(self) -> list[str]:
        """Get all unique grammatical functions."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT function FROM grammar_patterns "
                    "WHERE function IS NOT NULL AND function != '' ORDER BY function"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def get_books(self) -> list[str]:
        """Get all unique books referenced."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT book FROM grammar_patterns "
                    "WHERE book IS NOT NULL AND book != '' ORDER BY book"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def parse_examples(self, examples_json: str) -> list[str]:
        """Parse examples JSON array to list of verse refs."""
        try:
            return json.loads(examples_json) if examples_json else []
        except json.JSONDecodeError:
            return []

    def get_top_patterns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get most frequent grammar patterns."""
        return self.get_by_frequency(min_freq=1, limit=limit)

    def validate_pattern(self, zolai_text: str) -> list[dict[str, Any]]:
        """Find grammar patterns that match the given Zolai text."""
        # This is a simplified pattern matching - could be enhanced
        matches = []
        words = zolai_text.lower().split()

        for word in words:
            patterns = self.get_by_pattern_name(word)
            for p in patterns:
                if p not in matches:
                    matches.append(p)

        return matches


class WordCollocationRepository(BaseRepository):
    """Repository for word collocations (word_collocations table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "word_collocations")

    def get_by_word1(self, word1: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get collocations where word1 is the first word."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.word1 == word1)
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_word2(self, word2: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get collocations where word2 is the second word."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.word2 == word2)
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_word(self, word: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get all collocations involving a word."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where((self.table.c.word1 == word) | (self.table.c.word2 == word))
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_top_collocations(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get most frequent collocations."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_high_pmi(self, min_pmi: float = 3.0, limit: int = 100) -> list[dict[str, Any]]:
        """Get collocations with high PMI (pointwise mutual information)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.pmiproxy >= min_pmi)
                .order_by(self.table.c.pmiproxy.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]
