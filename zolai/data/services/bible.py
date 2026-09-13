"""Bible service for verse lookup, context, and study."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..repositories import (
    BibleContextRepository,
    BibleRepository,
)


class BibleService:
    """Service for Bible verse operations."""

    def __init__(self, engine: Engine) -> None:
        self.repo = BibleRepository(engine)
        self.engine = engine

    def get_verse(self, ref: str) -> dict[str, Any] | None:
        """Get a single verse by reference."""
        return self.repo.get_by_ref(ref)

    def get_verse_by_book_chapter_verse(
        self, book: str, chapter: int, verse: int
    ) -> dict[str, Any] | None:
        """Get a verse by book, chapter, verse."""
        return self.repo.get_by_book_chapter_verse(book, chapter, verse)

    def get_chapter(self, book: str, chapter: int) -> list[dict[str, Any]]:
        """Get all verses in a chapter."""
        return self.repo.get_chapter(book, chapter)

    def get_book(self, book: str) -> list[dict[str, Any]]:
        """Get all verses in a book."""
        return self.repo.get_book(book)

    def search_zo(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search Zolai Bible text."""
        return self.repo.search_zo(query, limit=limit)

    def search_en(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search English Bible text."""
        return self.repo.search_en(query, limit=limit)

    def search_myanmar(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search Myanmar Bible text."""
        return self.repo.search_myanmar(query, limit=limit)

    def search_all(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search all Bible text columns."""
        return self.repo.search_all(query, limit=limit)

    def get_parallel(self, ref: str) -> dict[str, str | None] | None:
        """Get parallel texts for a verse."""
        return self.repo.get_parallel_verse(ref)

    def get_books(self) -> list[dict[str, Any]]:
        """Get all books with metadata."""
        return self.repo.get_books()

    def get_verse_count(self, book: str | None = None) -> int:
        """Get verse count."""
        return self.repo.get_verse_count(book)

    def random_verse(self) -> dict[str, Any] | None:
        """Get a random verse."""
        return self.repo.random_verse()

    def get_books_list(self) -> list[str]:
        """Get list of book codes."""
        books = self.repo.get_books()
        return [b["book"] for b in books]

    def get_stats(self) -> dict[str, Any]:
        """Get Bible statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM bible_verses")).scalar()
            books = conn.execute(
                text("SELECT COUNT(DISTINCT book) FROM bible_verses")
            ).scalar()

        return {
            "total_verses": total or 0,
            "total_books": books or 0,
        }


class BibleContextService:
    """Service for Bible context analysis."""

    def __init__(self, engine: Engine) -> None:
        self.repo = BibleContextRepository(engine)

    def get_book_analysis(self, book: str) -> list[dict[str, Any]]:
        """Get all context analyses for a book."""
        return self.repo.get_book_analysis(book)

    def get_chapter_analysis(
        self, book: str, chapter: int
    ) -> list[dict[str, Any]]:
        """Get context analyses for a chapter."""
        return self.repo.get_chapter_analysis(book, chapter)

    def get_analysis_types(self) -> list[str]:
        """Get all analysis types."""
        return self.repo.get_analysis_types()

    def get_by_type(self, analysis_type: str) -> list[dict[str, Any]]:
        """Get all analyses of a specific type."""
        return self.repo.get_by_type(analysis_type)
