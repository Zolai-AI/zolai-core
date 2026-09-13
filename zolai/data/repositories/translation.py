"""Translation repository for translation pairs and alignment."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class TranslationRepository(BaseRepository):
    """Repository for translation pairs (translations table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "translations")

    def get_by_direction(
        self, direction: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get translation pairs by direction."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.direction == direction)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_source(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search in source language."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.source.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_target(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search in target language."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.target.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_both(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search in both source and target."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.source.ilike(pattern))
                    | (self.table.c.target.ilike(pattern))
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_reference(self, reference: str) -> list[dict[str, Any]]:
        """Get translations by Bible reference."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.reference == reference)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_high_confidence(
        self, min_confidence: float = 0.9, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get high-confidence translation pairs."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.confidence >= min_confidence)
                .order_by(self.table.c.confidence.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_directions(self) -> list[str]:
        """Get all unique translation directions."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT direction FROM translations "
                    "WHERE direction IS NOT NULL ORDER BY direction"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def count_by_direction(self) -> dict[str, int]:
        """Get count of pairs per direction."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT direction, COUNT(*) as cnt FROM translations "
                    "GROUP BY direction ORDER BY cnt DESC"
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}


class WordAlignmentRepository(BaseRepository):
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
