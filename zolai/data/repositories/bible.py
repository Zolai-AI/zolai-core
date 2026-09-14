"""Bible repository for verses, context, and analysis."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class BibleRepository(BaseRepository):
    """Repository for Bible verses (bible_verses table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "bible_verses")

    def get_by_ref(self, ref: str) -> dict[str, Any] | None:
        """Get verse by canonical reference (e.g., 'GEN 1:1')."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(self.table.c.ref == ref)
            ).first()
        return self._row_to_dict(row) if row else None

    def get_by_book_chapter_verse(
        self, book: str, chapter: int, verse: int
    ) -> dict[str, Any] | None:
        """Get verse by book, chapter, verse."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(
                    (self.table.c.book == book)
                    & (self.table.c.chapter == chapter)
                    & (self.table.c.verse == verse)
                )
            ).first()
        return self._row_to_dict(row) if row else None

    def get_chapter(self, book: str, chapter: int) -> list[dict[str, Any]]:
        """Get all verses in a chapter."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where((self.table.c.book == book) & (self.table.c.chapter == chapter))
                .order_by(self.table.c.verse)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_book(self, book: str) -> list[dict[str, Any]]:
        """Get all verses in a book."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.book == book)
                .order_by(self.table.c.chapter, self.table.c.verse)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_zo(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search Zolai text (TDB77 and Tedim2010)."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.zo_tdb77.ilike(pattern))
                    | (self.table.c.zo_tedim2010.ilike(pattern))
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_en(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search English text (KJV)."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.en_kJV.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_myanmar(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search Myanmar text."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.myanmar.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_all(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search across all text columns."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.zo_tdb77.ilike(pattern))
                    | (self.table.c.zo_tedim2010.ilike(pattern))
                    | (self.table.c.en_kJV.ilike(pattern))
                    | (self.table.c.myanmar.ilike(pattern))
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_books(self) -> list[dict[str, Any]]:
        """Get list of all books with verse counts."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT book, COUNT(*) as verse_count, "
                    "MIN(chapter) as first_chapter, MAX(chapter) as last_chapter "
                    "FROM bible_verses GROUP BY book ORDER BY MIN(id)"
                )
            ).fetchall()
        return [
            {"book": row[0], "verse_count": row[1], "chapters": f"{row[2]}-{row[3]}"}
            for row in rows
        ]

    def get_verse_count(self, book: str | None = None) -> int:
        """Get total verse count, optionally filtered by book."""
        with self._engine.connect() as conn:
            if book:
                return conn.execute(
                    text("SELECT COUNT(*) FROM bible_verses WHERE book = :book"),
                    {"book": book},
                ).scalar_one()
            return conn.execute(text("SELECT COUNT(*) FROM bible_verses")).scalar_one()

    def get_parallel_verse(
        self, ref: str
    ) -> dict[str, str | None] | None:
        """Get parallel texts for a verse reference."""
        verse = self.get_by_ref(ref)
        if not verse:
            return None
        return {
            "ref": verse.get("ref"),
            "zo_tdb77": verse.get("zo_tdb77"),
            "zo_tedim2010": verse.get("zo_tedim2010"),
            "en_kJV": verse.get("en_kJV"),
            "myanmar": verse.get("myanmar"),
        }

    def random_verse(self) -> dict[str, Any] | None:
        """Get a random verse (for quizzes)."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM bible_verses ORDER BY RANDOM() LIMIT 1")
            ).first()
        return self._row_to_dict(row) if row else None


class BibleContextRepository(BaseRepository):
    """Repository for Bible context analysis (bible_analysis table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "bible_analysis")

    def get_book_analysis(self, book: str) -> list[dict[str, Any]]:
        """Get all context analyses for a book."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where((self.table.c.book == book) & (self.table.c.chapter.is_(None)))
                .order_by(self.table.c.analysis_type)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_chapter_analysis(
        self, book: str, chapter: int
    ) -> list[dict[str, Any]]:
        """Get context analyses for a specific chapter."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.book == book)
                    & (self.table.c.chapter == chapter)
                )
                .order_by(self.table.c.analysis_type)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_type(self, analysis_type: str) -> list[dict[str, Any]]:
        """Get all analyses of a specific type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.analysis_type == analysis_type)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_analysis_types(self) -> list[str]:
        """Get all unique analysis types."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT analysis_type FROM bible_analysis "
                    "WHERE analysis_type IS NOT NULL ORDER BY analysis_type"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def get_book_analysis(self, book: str) -> list[dict[str, Any]]:
        """Get all context analyses for a book."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where((self.table.c.book == book) & (self.table.c.chapter.is_(None)))
                .order_by(self.table.c.analysis_type)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_chapter_analysis(
        self, book: str, chapter: int
    ) -> list[dict[str, Any]]:
        """Get context analyses for a specific chapter."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.book == book)
                    & (self.table.c.chapter == chapter)
                )
                .order_by(self.table.c.analysis_type)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_type(self, analysis_type: str) -> list[dict[str, Any]]:
        """Get all analyses of a specific type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.analysis_type == analysis_type)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_analysis_types(self) -> list[str]:
        """Get all unique analysis types."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT analysis_type FROM bible_analysis "
                    "WHERE analysis_type IS NOT NULL ORDER BY analysis_type"
                )
            ).fetchall()
        return [row[0] for row in rows]
