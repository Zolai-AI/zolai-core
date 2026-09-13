"""Phrase repository for multi-word expressions."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.engine import Engine

from .base import BaseRepository


class PhraseRepository(BaseRepository):
    """Repository for phrases (phrases table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "phrases")

    def get_by_zo(self, zo: str) -> list[dict[str, Any]]:
        """Get phrase by exact Zolai text."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.zo == zo)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_zo_prefix(self, prefix: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get phrases by Zolai prefix."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zo.ilike(f"{prefix}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_zo_contains(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get phrases containing query in Zolai text."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zo.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_english(self, english: str) -> list[dict[str, Any]]:
        """Get phrase by exact English translation."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.english == english)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_english_contains(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get phrases containing query in English translation."""
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.english.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_myanmar(self, myanmar: str) -> list[dict[str, Any]]:
        """Get phrase by exact Myanmar translation."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.myanmar == myanmar)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_top_frequency(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get most frequent phrases."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_frequency_range(
        self, min_freq: int = 0, max_freq: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get phrases within frequency range."""
        with self._engine.connect() as conn:
            query = self.table.select().where(self.table.c.frequency >= min_freq)
            if max_freq is not None:
                query = query.where(self.table.c.frequency <= max_freq)
            query = query.order_by(self.table.c.frequency.desc()).limit(limit)
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def parse_examples(self, examples_json: str) -> list[dict[str, Any]]:
        """Parse examples JSON array to list of dicts."""
        try:
            return json.loads(examples_json) if examples_json else []
        except json.JSONDecodeError:
            return []

    def get_phrases_with_myanmar(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get phrases that have Myanmar translations."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.myanmar.isnot(None))
                    & (self.table.c.myanmar != "")
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_phrases_missing_myanmar(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get phrases missing Myanmar translations."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.myanmar.is_(None))
                    | (self.table.c.myanmar == "")
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]
