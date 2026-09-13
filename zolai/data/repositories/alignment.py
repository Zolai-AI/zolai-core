"""Alignment repository for word alignments."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class AlignmentRepository(BaseRepository):
    """Repository for word-level alignments (word_alignments table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "word_alignments")

    def get_by_ref(self, ref: str) -> list[dict[str, Any]]:
        """Get all word alignments for a verse reference."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.ref == ref)
                .order_by(self.table.c.position)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_zolai_word(self, zolai_word: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get alignments for a specific Zolai word."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zolai_word == zolai_word)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_english_word(
        self, english_word: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get alignments for a specific English word."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.english_word == english_word)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_aligned_pairs(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Get unique Zolai-English word pairs with frequencies."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT zolai_word, english_word, COUNT(*) as freq "
                    "FROM word_alignments "
                    "GROUP BY zolai_word, english_word "
                    "ORDER BY freq DESC LIMIT :limit"
                ),
                {"limit": limit},
            ).fetchall()
        return [
            {"zolai_word": row[0], "english_word": row[1], "frequency": row[2]}
            for row in rows
        ]

    def get_refs_for_word(self, zolai_word: str, limit: int = 100) -> list[str]:
        """Get verse references where a Zolai word appears."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT ref FROM word_alignments "
                    "WHERE zolai_word = :word LIMIT :limit"
                ),
                {"word": zolai_word, "limit": limit},
            ).fetchall()
        return [row[0] for row in rows]

    def get_alignment_stats(self) -> dict[str, Any]:
        """Get alignment statistics."""
        with self._engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM word_alignments")).scalar()
            unique_zolai = conn.execute(
                text("SELECT COUNT(DISTINCT zolai_word) FROM word_alignments")
            ).scalar()
            unique_english = conn.execute(
                text("SELECT COUNT(DISTINCT english_word) FROM word_alignments")
            ).scalar()
            unique_refs = conn.execute(
                text("SELECT COUNT(DISTINCT ref) FROM word_alignments")
            ).scalar()

        return {
            "total_alignments": total or 0,
            "unique_zolai_words": unique_zolai or 0,
            "unique_english_words": unique_english or 0,
            "unique_references": unique_refs or 0,
        }
