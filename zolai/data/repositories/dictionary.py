"""Dictionary repository for Zolai↔English↔Myanmar entries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class DictionaryRepository(BaseRepository):
    """Repository for Zolai→English dictionary (dictionary table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "dictionary")

    def lookup_exact(self, zolai: str) -> list[dict[str, Any]]:
        """Exact case-insensitive lookup by Zolai headword."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.zolai) == zolai.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def lookup_prefix(self, zolai: str, limit: int = 10) -> list[dict[str, Any]]:
        """Prefix match on Zolai headword."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zolai.ilike(f"{zolai}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def lookup_contains(self, zolai: str, limit: int = 20) -> list[dict[str, Any]]:
        """Substring match on Zolai headword."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zolai.ilike(f"%{zolai}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_english(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search by English translation (exact + contains)."""
        with self._engine.connect() as conn:
            # Exact on english_clean first
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.english_clean) == query.lower()
                )
            ).fetchall()
            if rows:
                return [self._row_to_dict(row) for row in rows]

            # Contains on english_clean
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.english_clean.ilike(f"%{query}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_myanmar(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search by Myanmar translation."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.myanmar.ilike(f"%{query}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_all(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search across Zolai, English, and Myanmar."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.zolai.ilike(f"%{query}%"))
                    | (self.table.c.english_clean.ilike(f"%{query}%"))
                    | (self.table.c.myanmar.ilike(f"%{query}%"))
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_source(self, source: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries by source dictionary."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.source == source)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_sources(self) -> list[str]:
        """Get all unique source values."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT source FROM dictionary WHERE source IS NOT NULL AND source != ''"
                )
            ).fetchall()
        return [row[0] for row in rows]

    def get_missing_myanmar(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries missing Myanmar translation."""
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

    def get_missing_english_clean(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries missing clean English translation."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.english_clean.is_(None))
                    | (self.table.c.english_clean == "")
                )
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def update_myanmar(self, zolai: str, myanmar: str, user: str = "system") -> bool:
        """Update Myanmar translation for a Zolai word."""
        entry = self.lookup_exact(zolai)
        if not entry:
            return False
        return self.update(entry[0]["id"], {"myanmar": myanmar}, user=user)

    def update_english_clean(
        self, zolai: str, english_clean: str, user: str = "system"
    ) -> bool:
        """Update clean English translation for a Zolai word."""
        entry = self.lookup_exact(zolai)
        if not entry:
            return False
        return self.update(entry[0]["id"], {"english_clean": english_clean}, user=user)

    def count_by_source(self) -> dict[str, int]:
        """Get count of entries per source."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source, COUNT(*) as cnt FROM dictionary "
                    "WHERE source IS NOT NULL GROUP BY source ORDER BY cnt DESC"
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}


class DictionaryEnZoRepository(BaseRepository):
    """Repository for English→Zolai dictionary (dictionary_en_zo table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "dictionary_en_zo", id_column="id")

    def lookup_exact(self, headword: str) -> list[dict[str, Any]]:
        """Exact case-insensitive lookup by English headword."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.headword) == headword.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def lookup_prefix(self, headword: str, limit: int = 10) -> list[dict[str, Any]]:
        """Prefix match on English headword."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.headword.ilike(f"{headword}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def lookup_contains(self, headword: str, limit: int = 20) -> list[dict[str, Any]]:
        """Substring match on English headword."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.headword.ilike(f"%{headword}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_translations(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search in translations_clean column."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.translations_clean.ilike(f"%{query}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_pos(self, pos: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get entries by part of speech."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.pos.ilike(f"%{pos}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_missing_myanmar(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries missing Myanmar translation."""
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

    def update_myanmar(self, headword: str, myanmar: str, user: str = "system") -> bool:
        """Update Myanmar translation for an English headword."""
        entries = self.lookup_exact(headword)
        if not entries:
            return False
        return self.update(entries[0]["id"], {"myanmar": myanmar}, user=user)
