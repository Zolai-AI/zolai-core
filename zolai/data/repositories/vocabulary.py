"""Vocabulary repository for vocab entries and word usage profiles."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class VocabularyRepository(BaseRepository):
    """Repository for vocabulary index (vocab table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "vocab", id_column="id")

    def get_by_headword(self, headword: str) -> list[dict[str, Any]]:
        """Get vocabulary entries by headword (case-insensitive)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.headword) == headword.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_headword_prefix(self, prefix: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get vocabulary entries by headword prefix."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.headword.ilike(f"{prefix}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_frequency_range(
        self, min_freq: int = 0, max_freq: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get vocabulary entries within frequency range."""
        with self._engine.connect() as conn:
            query = self.table.select().where(self.table.c.frequency >= min_freq)
            if max_freq is not None:
                query = query.where(self.table.c.frequency <= max_freq)
            query = query.order_by(self.table.c.frequency.desc()).limit(limit)
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_top_words(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get most frequent vocabulary words."""
        return self.get_by_frequency_range(min_freq=1, limit=limit)

    def get_by_book(self, book: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get words appearing in a specific book."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.books.ilike(f"%{book}%"))
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_pos(self, pos: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get words by part of speech."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.pos.ilike(f"%{pos}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_english(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search by English translation."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.english.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def parse_books(self, books_json: str) -> list[str]:
        """Parse books JSON array to list."""
        try:
            return json.loads(books_json) if books_json else []
        except json.JSONDecodeError:
            return []

    def parse_examples(self, examples_json: str) -> list[Any]:
        """Parse examples JSON array to list."""
        try:
            return json.loads(examples_json) if examples_json else []
        except json.JSONDecodeError:
            return []


class WordUsageRepository(BaseRepository):
    """Repository for word usage profiles (word_usage table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "word_usage")

    def get_by_word(self, word: str) -> list[dict[str, Any]]:
        """Get usage profiles for a word."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.word == word)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_word_book(self, word: str, book: str) -> dict[str, Any] | None:
        """Get usage profile for a word in a specific book."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(
                    (self.table.c.word == word) & (self.table.c.book == book)
                )
            ).first()
        return self._row_to_dict(row) if row else None

    def get_words_in_book(self, book: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get all words with usage data in a specific book."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.book == book)
                .order_by(self.table.c.total_freq.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_meaning_shifts(self, word: str) -> list[dict[str, Any]]:
        """Get detected meaning shifts for a word across books."""
        profile = self.get_by_word(word)
        if not profile:
            return []
        try:
            import json
            return json.loads(profile[0].get("meaning_shifts", "[]"))
        except json.JSONDecodeError:
            return []

    def get_co_occurring(self, word: str, book: str) -> list[dict[str, Any]]:
        """Get co-occurring words for a word in a book."""
        profile = self.get_by_word_book(word, book)
        if not profile:
            return []
        try:
            import json
            return json.loads(profile[0].get("co_occurring_words", "[]"))
        except json.JSONDecodeError:
            return []

    def get_all_words(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Get all unique words with usage data."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT word FROM word_usage ORDER BY word LIMIT :limit"
                ),
                {"limit": limit},
            ).fetchall()
        return [{"word": row[0]} for row in rows]
