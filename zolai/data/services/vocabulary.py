"""Vocabulary service for vocab entries and word usage profiles."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..repositories import (
    VocabularyRepository,
    WordUsageRepository,
)


class VocabularyService:
    """Service for vocabulary index operations."""

    def __init__(self, engine: Engine) -> None:
        self.repo = VocabularyRepository(engine)
        self.engine = engine

    def get_by_headword(self, headword: str) -> list[dict[str, Any]]:
        """Get vocabulary entries by headword."""
        return self.repo.get_by_headword(headword)

    def get_by_prefix(self, prefix: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get vocabulary entries by prefix."""
        return self.repo.get_by_headword_prefix(prefix, limit=limit)

    def get_top_words(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get most frequent vocabulary words."""
        return self.repo.get_top_words(limit=limit)

    def get_by_frequency(
        self, min_freq: int = 0, max_freq: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get words within frequency range."""
        return self.repo.get_by_frequency_range(min_freq, max_freq, limit=limit)

    def get_by_book(self, book: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get words appearing in a specific book."""
        return self.repo.get_by_book(book, limit=limit)

    def get_by_pos(self, pos: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get words by part of speech."""
        return self.repo.get_by_pos(pos, limit=limit)

    def search_english(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search by English translation."""
        return self.repo.search_english(query, limit=limit)

    def get_entry_with_parsed(self, headword: str) -> dict[str, Any] | None:
        """Get vocabulary entry with parsed books and examples."""
        entries = self.repo.get_by_headword(headword)
        if not entries:
            return None

        entry = entries[0].copy()
        entry["books_parsed"] = self.repo.parse_books(entry.get("books", "[]"))
        entry["examples_parsed"] = self.repo.parse_examples(entry.get("examples", "[]"))
        return entry

    def get_stats(self) -> dict[str, Any]:
        """Get vocabulary statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM zolai_vocabulary")).scalar()
            total_freq = conn.execute(
                text("SELECT SUM(frequency) FROM zolai_vocabulary")
            ).scalar()
            with_pos = conn.execute(
                text("SELECT COUNT(*) FROM zolai_vocabulary WHERE pos != '' AND pos IS NOT NULL")
            ).scalar()

        return {
            "total_entries": total or 0,
            "total_frequency": total_freq or 0,
            "with_pos": with_pos or 0,
        }


class WordUsageService:
    """Service for word usage profiles."""

    def __init__(self, engine: Engine) -> None:
        self.repo = WordUsageRepository(engine)

    def get_by_word(self, word: str) -> list[dict[str, Any]]:
        """Get usage profiles for a word."""
        return self.repo.get_by_word(word)

    def get_by_word_book(self, word: str, book: str) -> dict[str, Any] | None:
        """Get usage profile for a word in a specific book."""
        return self.repo.get_by_word_book(word, book)

    def get_words_in_book(self, book: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get all words with usage data in a book."""
        return self.repo.get_words_in_book(book, limit=limit)

    def get_meaning_shifts(self, word: str) -> list[dict[str, Any]]:
        """Get detected meaning shifts for a word."""
        return self.repo.get_meaning_shifts(word)

    def get_co_occurring(self, word: str, book: str) -> list[dict[str, Any]]:
        """Get co-occurring words for a word in a book."""
        return self.repo.get_co_occurring(word, book)

    def get_all_words(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Get all unique words with usage data."""
        return self.repo.get_all_words(limit=limit)

    def get_context_translation(
        self, word: str, book: str
    ) -> dict[str, Any] | None:
        """Get context-aware translation for a word in a specific book."""
        profile = self.repo.get_by_word_book(word, book)
        if not profile:
            return None

        result = {
            "word": word,
            "book": book,
            "total_freq": profile.get("total_freq", 0),
            "translations": [],
        }

        try:
            meaning_shifts = json.loads(profile.get("meaning_shifts", "[]"))
            for shift in meaning_shifts:
                if shift.get("books") and book in shift["books"]:
                    result["translations"].append({
                        "translation": shift.get("translation"),
                        "books": shift.get("books"),
                    })
        except json.JSONDecodeError:
            pass

        return result
