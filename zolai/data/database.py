"""Unified data access layer for Zolai.

Provides DatabaseManager with SQLite and PostgreSQL backends.
Lazy-connection pattern: database opens on first query.

Usage:
    from zolai.data.database import get_manager
    mgr = get_manager()          # SQLite default
    mgr = get_manager("postgresql://...")  # PostgreSQL
    results = mgr.lookup_word("pasian")
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.engine import Engine

from .models import MODEL_REGISTRY, Base

logger = logging.getLogger(__name__)

# JSON column name → Python json.loads before returning
_JSON_COLS: dict[str, set[str]] = {
    "dictionary": {"english"},
    "bible_verses": set(),
    "grammar_patterns": {"examples"},
    "phrases": {"examples"},
    "vocab": {"books", "examples"},
    "translations": set(),
    "word_usage": {"meaning_shifts", "co_occurring_words"},
    "provenance": set(),
}


class DatabaseManager:
    """Unified database access with lazy connection."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or "sqlite:///zolai.db"
        self._engine: Engine | None = None
        self._metadata: MetaData | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            connect_args: dict[str, Any] = {}
            if self._db_url.startswith("sqlite"):
                connect_args = {"check_same_thread": False}
            self._engine = create_engine(
                self._db_url,
                echo=False,
                connect_args=connect_args,
            )
            if self._db_url.startswith("sqlite"):
                with self._engine.connect() as conn:
                    conn.execute(text("PRAGMA journal_mode=WAL"))
                    conn.commit()
        return self._engine

    @property
    def metadata(self) -> MetaData:
        if self._metadata is None:
            self._metadata = MetaData()
            Base.metadata.reflect(bind=self.engine)
            self._metadata = Base.metadata
        return self._metadata

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def init_db(self) -> None:
        """Create all tables (idempotent)."""
        Base.metadata.create_all(self.engine)
        logger.info("Database initialized: %s", self._db_url)

    def table_names(self) -> list[str]:
        """Return sorted list of table names."""
        return sorted(Base.metadata.tables.keys())

    # ------------------------------------------------------------------
    # Lookup methods
    # ------------------------------------------------------------------
    def lookup_word(self, word: str) -> list[dict[str, Any]]:
        """Search dictionary by Zolai headword (case-insensitive)."""
        table = Table("dictionary", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    table.c.zolai.ilike(f"%{word}%")
                )
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def search_bible(self, query: str) -> list[dict[str, Any]]:
        """Search Bible verses by Zolai or English text (LIKE match)."""
        table = Table("bible_verses", self.metadata, autoload_with=self.engine)
        pattern = f"%{query}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.zo_tdb77.ilike(pattern))
                    | (table.c.zo_tedim2010.ilike(pattern))
                    | (table.c.en_kJV.ilike(pattern))
                ).limit(100)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def match_phrase(self, word: str) -> list[dict[str, Any]]:
        """Find phrases containing a word (case-insensitive LIKE)."""
        table = Table("phrases", self.metadata, autoload_with=self.engine)
        pattern = f"%{word}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.zo.ilike(pattern))
                    | (table.c.english.ilike(pattern))
                ).limit(50)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def get_grammar(self, pattern: str) -> list[dict[str, Any]]:
        """Look up grammar patterns by name or description."""
        table = Table("grammar_patterns", self.metadata, autoload_with=self.engine)
        pat = f"%{pattern}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.pattern.ilike(pat))
                    | (table.c.description.ilike(pat))
                ).limit(50)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def get_vocab(self, word: str) -> list[dict[str, Any]]:
        """Look up vocabulary entries by headword."""
        table = Table("vocab", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    table.c.headword.ilike(f"%{word}%")
                )
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def get_translations(
        self, word: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Look up translation pairs containing a word."""
        table = Table("translations", self.metadata, autoload_with=self.engine)
        pat = f"%{word}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.source.ilike(pat))
                    | (table.c.target.ilike(pat))
                ).limit(limit)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    # ------------------------------------------------------------------
    # Count / aggregate
    # ------------------------------------------------------------------
    def count(self, table_name: str) -> int:
        """Return row count for a table."""
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            )
            return result.scalar_one()

    def counts(self) -> dict[str, int]:
        """Return row counts for all tables."""
        return {name: self.count(name) for name in self.table_names()}

    # ------------------------------------------------------------------
    # Bulk insert
    # ------------------------------------------------------------------
    def insert_many(self, table_name: str, records: list[dict[str, Any]]) -> int:
        """Bulk insert records into a table.

        JSON-serialises list/dict columns automatically.
        Returns count of inserted rows.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        json_cols = _JSON_COLS.get(table_name, set())
        cleaned = []
        for rec in records:
            row = {}
            for k, v in rec.items():
                if k in json_cols and isinstance(v, (list, dict)):
                    row[k] = json.dumps(v, ensure_ascii=False)
                elif k in json_cols and isinstance(v, str):
                    # Already a string — keep as-is
                    row[k] = v
                else:
                    row[k] = v
            cleaned.append(row)
        with self.engine.begin() as conn:
            conn.execute(table.insert(), cleaned)
        return len(cleaned)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_table(self, table_name: str) -> list[dict[str, Any]]:
        """Export all rows from a table as list of dicts.

        Deserialises JSON columns back to Python objects.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        json_cols = _JSON_COLS.get(table_name, set())
        with self.engine.connect() as conn:
            rows = conn.execute(table.select()).fetchall()
        result = []
        for row in rows:
            d = self._row_to_dict(row, table)
            for col in json_cols:
                if col in d and isinstance(d[col], str):
                    try:
                        d[col] = json.loads(d[col])
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: Any, table: Table) -> dict[str, Any]:
        """Convert a Row to a plain dict, skipping internal id."""
        cols = [c.name for c in table.columns if c.name != "id"]
        return {col: getattr(row, col, None) for col in cols}

    def dispose(self) -> None:
        """Release connection pool resources."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._metadata = None


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------
_manager: DatabaseManager | None = None


def get_manager(db_url: str | None = None) -> DatabaseManager:
    """Get or create a DatabaseManager singleton.

    If db_url is provided, creates a new manager (replaces any existing).
    If not provided and no singleton exists, uses default SQLite.
    """
    global _manager
    if db_url is not None or _manager is None:
        if _manager is not None:
            _manager.dispose()
        _manager = DatabaseManager(db_url)
    return _manager


def init_db(db_url: str | None = None) -> DatabaseManager:
    """Create tables and return the manager."""
    mgr = get_manager(db_url)
    mgr.init_db()
    return mgr
