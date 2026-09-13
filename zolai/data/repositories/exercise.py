"""Exercise repository for training exercises."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class ExerciseRepository(BaseRepository):
    """Repository for training exercises (training_exercises table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "training_exercises", id_column="id")

    def get_by_type(self, exercise_type: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get exercises by type (negation, question, pronoun, error_correction, conditional)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.exercise_type == exercise_type)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_difficulty(
        self, difficulty: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get exercises by difficulty (easy, medium, hard)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.difficulty == difficulty)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_type_and_difficulty(
        self, exercise_type: str, difficulty: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get exercises by type and difficulty."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.exercise_type == exercise_type)
                    & (self.table.c.difficulty == difficulty)
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_random(
        self, exercise_type: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get random exercises, optionally filtered by type."""
        with self._engine.connect() as conn:
            if exercise_type:
                rows = conn.execute(
                    text(
                        "SELECT * FROM training_exercises "
                        "WHERE exercise_type = :type ORDER BY RANDOM() LIMIT :limit"
                    ),
                    {"type": exercise_type, "limit": limit},
                ).fetchall()
            else:
                rows = conn.execute(
                    text("SELECT * FROM training_exercises ORDER BY RANDOM() LIMIT :limit"),
                    {"limit": limit},
                ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_types(self) -> list[str]:
        """Get all unique exercise types."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT exercise_type FROM training_exercises "
                    "WHERE exercise_type IS NOT NULL ORDER BY exercise_type"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def get_difficulties(self) -> list[str]:
        """Get all unique difficulty levels."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT difficulty FROM training_exercises "
                    "WHERE difficulty IS NOT NULL ORDER BY difficulty"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def count_by_type(self) -> dict[str, int]:
        """Get count of exercises per type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT exercise_type, COUNT(*) as cnt FROM training_exercises "
                    "GROUP BY exercise_type ORDER BY cnt DESC"
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def search_zolai(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search exercises by Zolai text."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zolai.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_english(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search exercises by English text."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.english.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_source(self, source: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get exercises by source file."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.source == source)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]
