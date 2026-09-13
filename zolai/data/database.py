"""Unified data access layer for Zolai.

Provides DatabaseManager with SQLite and PostgreSQL backends.
Lazy-connection pattern: database opens on first query.

Usage:
    from zolai.data.database import get_manager
    mgr = get_manager()          # SQLite default
    mgr = get_manager("postgresql://...")  # PostgreSQL
    results = mgr.lookup_word("pasian")

CLI:
    python -m zolai.data.database status
    python -m zolai.data.database backup
    python -m zolai.data.database restore
    python -m zolai.data.database query "pasian"
    python -m zolai.data.database stats
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from ..config import config
from .models import Base, DataAuditLog

logger = logging.getLogger(__name__)

# JSON column name → Python json.loads before returning
_JSON_COLS: dict[str, set[str]] = {
    "dictionary": {"english"},
    "dictionary_en_zo": {"translations"},
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
        self._pool_config: dict[str, Any] = {}

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
        self._ensure_columns()
        logger.info("Database initialized: %s", self._db_url)

    def _ensure_columns(self) -> None:
        """Add missing columns to existing tables (SQLite safe)."""
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(self.engine)

        # Provenance: add version, status, updated_at, change_log
        existing = {c["name"] for c in inspector.get_columns("provenance")}
        alters = {
            "version": (
                "ALTER TABLE provenance ADD COLUMN version "
                "VARCHAR NOT NULL DEFAULT '1.0'"
            ),
            "status": (
                "ALTER TABLE provenance ADD COLUMN status "
                "VARCHAR NOT NULL DEFAULT 'active'"
            ),
            "updated_at": (
                "ALTER TABLE provenance ADD COLUMN updated_at "
                "VARCHAR NOT NULL DEFAULT ''"
            ),
            "change_log": (
                "ALTER TABLE provenance ADD COLUMN change_log "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        }
        with self.engine.connect() as conn:
            for col, sql in alters.items():
                if col not in existing:
                    conn.execute(text(sql))
            conn.commit()

    def table_names(self) -> list[str]:
        """Return sorted list of table names."""
        return sorted(Base.metadata.tables.keys())

    # ------------------------------------------------------------------
    # Transaction & Session Management
    # ------------------------------------------------------------------
    @contextmanager
    def transaction(self):
        """Context manager for database transactions.

        Usage:
            with mgr.transaction() as conn:
                conn.execute(...)
                conn.execute(...)  # both in same transaction
        """
        with self.engine.begin() as conn:
            yield conn

    def execute_in_transaction(self, fn):
        """Execute a function within a transaction.

        Args:
            fn: Function that receives a connection and returns a value.

        Returns:
            The return value of fn.
        """
        with self.engine.begin() as conn:
            return fn(conn)

    @contextmanager
    def session(self) -> Session:
        """Context-managed SQLAlchemy session with automatic commit/rollback."""
        session = Session(self.engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_session(self) -> Session:
        """Get a new SQLAlchemy session (caller must manage commit/close)."""
        return Session(self.engine)

    # ------------------------------------------------------------------
    # Optimistic Locking Helpers
    # ------------------------------------------------------------------
    def get_with_version(
        self, table_name: str, entity_id: int
    ) -> tuple[dict[str, Any], int] | tuple[None, None]:
        """Get a record with its version for optimistic locking.

        Returns:
            Tuple of (record_dict, version) or (None, None) if not found.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        version_col = "version" if "version" in table.columns else None

        with self.engine.connect() as conn:
            if version_col:
                row = conn.execute(
                    table.select().where(table.c.id == entity_id)
                ).first()
                if row:
                    return self._row_to_dict(row, table), getattr(row, version_col, 1)
            else:
                row = conn.execute(
                    table.select().where(table.c.id == entity_id)
                ).first()
                if row:
                    return self._row_to_dict(row, table), 1
        return None, None

    def update_with_version(
        self,
        table_name: str,
        entity_id: int,
        data: dict[str, Any],
        expected_version: int,
        user: str = "system",
    ) -> bool:
        """Update a record with optimistic locking.

        Args:
            table_name: Target table
            entity_id: Primary key
            data: New column values
            expected_version: Expected version number
            user: User for audit log

        Returns:
            True if updated, False if not found or version mismatch.

        Raises:
            ValueError: If version mismatch (concurrent modification).
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        version_col = "version" if "version" in table.columns else None

        with self.engine.begin() as conn:
            # Get current record and version
            current = conn.execute(
                table.select().where(table.c.id == entity_id)
            ).first()

            if not current:
                return False

            current_version = getattr(current, version_col, 1) if version_col else 1

            if current_version != expected_version:
                raise ValueError(
                    f"Optimistic lock failed: expected version {expected_version}, "
                    f"found {current_version}. Record was modified concurrently."
                )

            old_data = self._row_to_dict(current, table)

            # Prepare update data with incremented version
            update_data = {**data}
            if version_col:
                update_data[version_col] = current_version + 1

            conn.execute(
                table.update()
                .where(table.c.id == entity_id)
                .values(update_data)
            )

            # Log to audit
            audit_table = Table("data_audit_log", self.metadata, autoload_with=self.engine)
            conn.execute(
                audit_table.insert(),
                {
                    "table_name": table_name,
                    "row_id": entity_id,
                    "field": "update",
                    "old_value": json.dumps(old_data, ensure_ascii=False),
                    "new_value": json.dumps(update_data, ensure_ascii=False),
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "reason": f"optimistic_update:{user}",
                },
            )

        return True

    # ------------------------------------------------------------------
    # Enhanced Bulk Operations
    # ------------------------------------------------------------------
    def bulk_upsert(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        conflict_columns: list[str],
        user: str = "system",
    ) -> dict[str, int]:
        """Bulk upsert (insert or update on conflict) records.

        Args:
            table_name: Target table
            records: List of record dicts
            conflict_columns: Columns that define uniqueness for conflict resolution
            user: User for audit log

        Returns:
            Dict with 'inserted', 'updated', 'errors' counts.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        json_cols = _JSON_COLS.get(table_name, set())

        inserted = 0
        updated = 0
        errors = 0

        with self.engine.begin() as conn:
            for record in records:
                try:
                    # Clean JSON columns
                    cleaned = {}
                    for k, v in record.items():
                        if k in json_cols and isinstance(v, (list, dict)):
                            cleaned[k] = json.dumps(v, ensure_ascii=False)
                        elif k in json_cols and isinstance(v, str):
                            cleaned[k] = v
                        else:
                            cleaned[k] = v

                    # Check for existing record
                    where_clause = []
                    for col in conflict_columns:
                        if col in cleaned:
                            where_clause.append(table.c[col] == cleaned[col])

                    if where_clause:
                        from sqlalchemy import and_
                        existing = conn.execute(
                            table.select().where(and_(*where_clause))
                        ).first()

                        if existing:
                            # Update existing
                            old_data = self._row_to_dict(existing, table)
                            update_data = {k: v for k, v in cleaned.items() if k not in conflict_columns}
                            if "version" in table.columns:
                                update_data["version"] = getattr(existing, "version", 1) + 1

                            conn.execute(
                                table.update()
                                .where(and_(*where_clause))
                                .values(update_data)
                            )

                            conn.execute(
                                Table("data_audit_log", self.metadata, autoload_with=self.engine).insert(),
                                {
                                    "table_name": table_name,
                                    "row_id": getattr(existing, "id", 0),
                                    "field": "bulk_upsert_update",
                                    "old_value": json.dumps(old_data, ensure_ascii=False),
                                    "new_value": json.dumps(update_data, ensure_ascii=False),
                                    "changed_at": datetime.now(timezone.utc).isoformat(),
                                    "reason": f"bulk_upsert:{user}",
                                },
                            )
                            updated += 1
                            continue

                    # Insert new
                    if "version" in table.columns:
                        cleaned["version"] = 1
                    result = conn.execute(table.insert(), cleaned)
                    entity_id = result.inserted_primary_key[0]

                    conn.execute(
                        Table("data_audit_log", self.metadata, autoload_with=self.engine).insert(),
                        {
                            "table_name": table_name,
                            "row_id": entity_id,
                            "field": "bulk_upsert_insert",
                            "old_value": None,
                            "new_value": json.dumps(cleaned, ensure_ascii=False),
                            "changed_at": datetime.now(timezone.utc).isoformat(),
                            "reason": f"bulk_upsert:{user}",
                        },
                    )
                    inserted += 1

                except Exception as exc:
                    logger.error("Bulk upsert error for record %s: %s", record, exc)
                    errors += 1

        return {"inserted": inserted, "updated": updated, "errors": errors}

    def bulk_delete(
        self,
        table_name: str,
        entity_ids: list[int],
        user: str = "system",
        soft: bool = True,
    ) -> int:
        """Bulk delete (soft or hard) records by IDs.

        Args:
            table_name: Target table
            entity_ids: List of primary key IDs to delete
            user: User for audit log
            soft: If True, soft delete (set is_deleted=1); else hard delete

        Returns:
            Number of records deleted.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        has_soft_delete = "is_deleted" in table.columns

        if not entity_ids:
            return 0

        deleted = 0
        with self.engine.begin() as conn:
            for entity_id in entity_ids:
                current = conn.execute(
                    table.select().where(table.c.id == entity_id)
                ).first()

                if not current:
                    continue

                old_data = self._row_to_dict(current, table)

                if soft and has_soft_delete:
                    conn.execute(
                        table.update()
                        .where(table.c.id == entity_id)
                        .values(
                            {
                                "is_deleted": 1,
                                "deleted_at": datetime.now(timezone.utc).isoformat(),
                                "deleted_by": user,
                            }
                        )
                    )
                    operation = "soft_delete"
                else:
                    conn.execute(table.delete().where(table.c.id == entity_id))
                    operation = "delete"

                conn.execute(
                    Table("data_audit_log", self.metadata, autoload_with=self.engine).insert(),
                    {
                        "table_name": table_name,
                        "row_id": entity_id,
                        "field": operation,
                        "old_value": json.dumps(old_data, ensure_ascii=False),
                        "new_value": json.dumps({"deleted_by": user}, ensure_ascii=False),
                        "changed_at": datetime.now(timezone.utc).isoformat(),
                        "reason": f"bulk_{operation}:{user}",
                    },
                )
                deleted += 1

        return deleted

    # ------------------------------------------------------------------
    # PostgreSQL Connection Pooling Enhancements
    # ------------------------------------------------------------------
    def configure_pool(
        self,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 30,
        pool_recycle: int = 3600,
    ) -> None:
        """Configure connection pool for PostgreSQL (must be called before first use).

        Args:
            pool_size: Number of connections to maintain
            max_overflow: Additional connections allowed
            pool_timeout: Seconds to wait for connection
            pool_recycle: Seconds before recycling connections
        """
        if self._engine is not None:
            logger.warning("Pool configuration ignored: engine already created")
            return

        if not self._db_url.startswith("postgresql"):
            logger.warning("Pool configuration only applies to PostgreSQL")
            return

        self._pool_config = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
            "pool_pre_ping": True,
            "poolclass": QueuePool,
        }

    def _create_engine_with_pool(self) -> Engine:
        """Create engine with configured pool settings."""
        if self._db_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
            pool_kwargs = {}
        else:
            connect_args = {}
            pool_kwargs = getattr(self, "_pool_config", {
                "pool_size": 5,
                "max_overflow": 10,
                "pool_pre_ping": True,
                "poolclass": QueuePool,
            })

        engine = create_engine(
            self._db_url,
            echo=False,
            connect_args=connect_args,
            **pool_kwargs,
        )

        if self._db_url.startswith("sqlite"):
            with engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA busy_timeout=30000"))
                conn.execute(text("PRAGMA synchronous=NORMAL"))
                conn.commit()

        return engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = self._create_engine_with_pool()
        return self._engine

    # ------------------------------------------------------------------
    # Lookup methods
    # ------------------------------------------------------------------
    def lookup_word(self, word: str) -> list[dict[str, Any]]:
        """Search dictionary by Zolai headword — exact match first, then LIKE fallback."""
        table = Table("dictionary", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            # Try exact match first (case-insensitive)
            rows = conn.execute(
                table.select().where(
                    func.lower(table.c.zolai) == word.lower()
                )
            ).fetchall()
            if rows:
                return [self._row_to_dict(row, table) for row in rows]
            # Fallback: substring match
            rows = conn.execute(
                table.select().where(
                    table.c.zolai.ilike(f"%{word}%")
                ).limit(10)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def search_dictionary(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search dictionary by Zolai or English (exact + LIKE)."""
        table = Table("dictionary", self.metadata, autoload_with=self.engine)
        pattern = f"%{query}%"
        with self.engine.connect() as conn:
            # Try exact match first (Zolai)
            rows = conn.execute(
                table.select().where(
                    func.lower(table.c.zolai) == query.lower()
                )
            ).fetchall()
            if rows:
                return [self._row_to_dict(row, table) for row in rows]
            # Try exact match (English)
            rows = conn.execute(
                table.select().where(
                    func.lower(table.c.english_clean) == query.lower()
                )
            ).fetchall()
            if rows:
                return [self._row_to_dict(row, table) for row in rows]
            # Fallback: substring match on Zolai or English
            rows = conn.execute(
                table.select().where(
                    (table.c.zolai.ilike(pattern))
                    | (table.c.english_clean.ilike(pattern))
                ).limit(limit)
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


    def get_bible_words(self) -> set[str]:
        """Return a set of all words appearing in the Bible (zo_tdb77 and zo_tedim2010 columns).

        Words are extracted by splitting on whitespace and punctuation,
        lowercased, and filtered to alphabetic tokens only.

        This is used by the ZVS validator to check if historical forms
        actually appear in the Bible database.
        """
        table = Table("bible_verses", self.metadata, autoload_with=self.engine)
        words: set[str] = set()
        # Use a simple regex to extract words
        word_pattern = re.compile(r"[a-zA-Z]+")
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().with_only_columns(
                    table.c.zo_tdb77, table.c.zo_tedim2010
                ).where(
                    (table.c.zo_tdb77.isnot(None)) | (table.c.zo_tedim2010.isnot(None))
                )
            ).fetchall()
            for row in rows:
                for col_val in row:
                    if col_val:
                        for word in word_pattern.findall(col_val):
                            words.add(word.lower())
        return words

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

    def lookup_english(self, word: str) -> list[dict[str, Any]]:
        """Search EN→ZO dictionary by English headword — exact match first, then LIKE."""
        table = Table("dictionary_en_zo", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    func.lower(table.c.headword) == word.lower()
                )
            ).fetchall()
            if rows:
                return [self._row_to_dict(row, table) for row in rows]
            rows = conn.execute(
                table.select().where(
                    table.c.headword.ilike(f"%{word}%")
                ).limit(10)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    # ------------------------------------------------------------------
    # Myanmar / Burmese lookups
    # ------------------------------------------------------------------
    def lookup_myanmar(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search dictionary + bible_verses by Myanmar text.

        Returns combined results from both tables.
        """
        results: list[dict[str, Any]] = []
        pattern = f"%{query}%"

        # Search dictionary (myanmar column)
        dict_table = Table(
            "dictionary", self.metadata, autoload_with=self.engine
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                dict_table.select().where(
                    dict_table.c.myanmar.ilike(pattern)
                ).limit(limit)
            ).fetchall()
            for row in rows:
                results.append({
                    "type": "dictionary",
                    "zolai": getattr(row, "zolai", None),
                    "myanmar": getattr(row, "myanmar", None),
                    "english": getattr(row, "english", None),
                    "source": getattr(row, "source", None),
                })

        # Search bible_verses (myanmar column)
        bible_table = Table(
            "bible_verses", self.metadata, autoload_with=self.engine
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                bible_table.select().where(
                    bible_table.c.myanmar.ilike(pattern)
                ).limit(limit)
            ).fetchall()
            for row in rows:
                results.append({
                    "type": "bible",
                    "ref": getattr(row, "ref", None),
                    "myanmar": getattr(row, "myanmar", None),
                    "zo_tdb77": getattr(row, "zo_tdb77", None),
                    "en_kjv": getattr(row, "en_kjv", None),
                })
        return results

    def translate_zo_my(self, word: str) -> dict[str, Any] | None:
        """Translate Zolai → Myanmar via dictionary."""
        table = Table(
            "dictionary", self.metadata, autoload_with=self.engine
        )
        with self.engine.connect() as conn:
            # Exact match first
            row = conn.execute(
                table.select().where(
                    func.lower(table.c.zolai) == word.lower()
                )
            ).first()
            if row and getattr(row, "myanmar", None):
                return {
                    "zolai": getattr(row, "zolai", None),
                    "myanmar": getattr(row, "myanmar", None),
                    "english": getattr(row, "english", None),
                }
            # LIKE fallback
            row = conn.execute(
                table.select().where(
                    table.c.zolai.ilike(f"%{word}%")
                ).limit(1)
            ).first()
            if row and getattr(row, "myanmar", None):
                return {
                    "zolai": getattr(row, "zolai", None),
                    "myanmar": getattr(row, "myanmar", None),
                    "english": getattr(row, "english", None),
                }
        return None

    def translate_my_zo(self, word: str) -> dict[str, Any] | None:
        """Translate Myanmar → Zolai via dictionary."""
        table = Table(
            "dictionary", self.metadata, autoload_with=self.engine
        )
        with self.engine.connect() as conn:
            # Exact match first
            row = conn.execute(
                table.select().where(
                    func.lower(table.c.myanmar) == word.lower()
                )
            ).first()
            if row:
                return {
                    "myanmar": getattr(row, "myanmar", None),
                    "zolai": getattr(row, "zolai", None),
                    "english": getattr(row, "english", None),
                }
            # LIKE fallback
            row = conn.execute(
                table.select().where(
                    table.c.myanmar.ilike(f"%{word}%")
                ).limit(1)
            ).first()
            if row:
                return {
                    "myanmar": getattr(row, "myanmar", None),
                    "zolai": getattr(row, "zolai", None),
                    "english": getattr(row, "english", None),
                }
        return None

    def search_judson(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search Judson Bible by Myanmar text."""
        table = Table(
            "bible_verses", self.metadata, autoload_with=self.engine
        )
        pattern = f"%{query}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    table.c.myanmar.ilike(pattern)
                ).limit(limit)
            ).fetchall()
        return [
            {
                "ref": getattr(r, "ref", None),
                "myanmar": getattr(r, "myanmar", None),
                "zo_tdb77": getattr(r, "zo_tdb77", None),
                "en_kjv": getattr(r, "en_kjv", None),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Count / aggregate
    # ------------------------------------------------------------------
    def count(self, table_name: str) -> int:
        """Return row count for a table."""
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
    # Audit log
    # ------------------------------------------------------------------
    def _log_change(
        self,
        table_name: str,
        row_id: int,
        field: str,
        old_value: str | None,
        new_value: str | None,
        reason: str = "",
    ) -> None:
        """Log a single data change to the audit_log table."""
        from datetime import datetime, timezone

        table = Table("data_audit_log", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            conn.execute(
                table.insert(),
                {
                    "table_name": table_name,
                    "row_id": row_id,
                    "field": field,
                    "old_value": old_value,
                    "new_value": new_value,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "reason": reason,
                },
            )
            conn.commit()

    def get_audit_log(
        self,
        table_name: str | None = None,
        row_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve audit log entries, optionally filtered by table/row."""
        table = Table("data_audit_log", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            q = table.select()
            if table_name:
                q = q.where(table.c.table_name == table_name)
            if row_id is not None:
                q = q.where(table.c.row_id == row_id)
            q = q.order_by(table.c.changed_at.desc()).limit(limit)
            rows = conn.execute(q).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def quality_report(self) -> dict[str, dict[str, Any]]:
        """Generate a quality report for all tables.

        Returns per-table: row_count, columns with null counts.
        """
        from sqlalchemy import inspect as sa_inspect

        report: dict[str, dict[str, Any]] = {}
        inspector = sa_inspect(self.engine)
        for table_name in inspector.get_table_names():
            with self.engine.connect() as conn:
                row_count = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar()
                cols = inspector.get_columns(table_name)
                null_counts: dict[str, int] = {}
                for col in cols:
                    nc = conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM {table_name} "
                            f"WHERE {col['name']} IS NULL "
                            f"OR TRIM(CAST({col['name']} AS TEXT)) = ''"
                        )
                    ).scalar()
                    null_counts[col["name"]] = nc
            report[table_name] = {
                "row_count": row_count,
                "null_counts": null_counts,
            }
        return report

    # ------------------------------------------------------------------
    # Phase 2: Fix noise data
    # ------------------------------------------------------------------
    def fix_word_usage_books(self, jsonl_path: str | Path) -> int:
        """Re-read word_usage JSONL and populate the empty book field.

        Extracts the most frequent book from per_book_distribution[0].book.
        Logs each change to audit_log.
        Returns number of rows fixed.
        """
        from pathlib import Path

        path = Path(jsonl_path)
        if not path.exists():
            return 0

        # Build word->book map from JSONL
        word_book: dict[str, str] = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                word = d.get("word", "")
                per_book = d.get("per_book_distribution", [])
                if (
                    per_book
                    and isinstance(per_book, list)
                    and len(per_book) > 0
                ):
                    book = per_book[0].get("book", "")
                    if book:
                        word_book[word] = book

        # Collect fixes first, then apply in one connection
        fixes: list[tuple[int, str, str]] = []  # (row_id, old, new)
        table = Table("word_usage", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.book == None) | (table.c.book == "")  # noqa: E711
                )
            ).fetchall()
            for row in rows:
                row_dict = self._row_to_dict(row, table)
                word = row_dict.get("word", "")
                if word in word_book:
                    row_id = row.id if hasattr(row, "id") else None
                    if row_id is not None:
                        fixes.append(
                            (row_id, row_dict.get("book", ""), word_book[word])
                        )
            # Apply all updates
            for row_id, old_book, new_book in fixes:
                conn.execute(
                    table.update()
                    .where(table.c.id == row_id)
                    .values(book=new_book)
                )
            conn.commit()

        # Log changes (separate connection)
        for row_id, old_book, new_book in fixes:
            self._log_change(
                "word_usage", row_id, "book",
                old_book, new_book,
                "Phase 2: backfill book from JSONL per_book_distribution",
            )
        return len(fixes)

    def fix_phrases_english(self, jsonl_path: str | Path) -> int:
        """Re-read phrases JSONL and populate empty english from examples[0].en.

        Logs each change to audit_log.
        Returns number of rows fixed.
        """
        from pathlib import Path

        path = Path(jsonl_path)
        if not path.exists():
            return 0

        # Build zo->english map from JSONL
        zo_english: dict[str, str] = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                zo = d.get("zo", "")
                examples = d.get("examples", [])
                if (
                    examples
                    and isinstance(examples, list)
                    and len(examples) > 0
                ):
                    en = examples[0].get("en", "")
                    if en and en.strip():
                        zo_english[zo] = en.strip()

        # Collect fixes first
        fixes: list[tuple[int, str]] = []  # (row_id, new_en)
        table = Table("phrases", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.english == None) | (table.c.english == "")  # noqa: E711
                )
            ).fetchall()
            for row in rows:
                row_dict = self._row_to_dict(row, table)
                zo = row_dict.get("zo", "")
                if zo in zo_english:
                    row_id = row.id if hasattr(row, "id") else None
                    if row_id is not None:
                        fixes.append((row_id, zo_english[zo]))
            # Apply all updates
            for row_id, new_en in fixes:
                conn.execute(
                    table.update()
                    .where(table.c.id == row_id)
                    .values(english=new_en)
                )
            conn.commit()

        # Log changes
        for row_id, new_en in fixes:
            self._log_change(
                "phrases", row_id, "english",
                "", new_en,
                "Phase 2: backfill english from "
                "phrases_v1.jsonl examples[0].en",
            )
        return len(fixes)

    def dedup_bible_verses(self) -> int:
        """Remove duplicate bible verses, keeping the most complete row per ref.

        Logs each deletion to audit_log.
        Returns number of rows deleted.
        """
        # Collect deletions first
        deletions: list[tuple[int, str, int]] = []  # (del_id, ref, keep_id)
        with self.engine.connect() as conn:
            dups = conn.execute(text("""
                SELECT ref, COUNT(*) as cnt FROM bible_verses
                GROUP BY ref HAVING cnt > 1
            """)).fetchall()

            for ref, cnt in dups:
                rows = conn.execute(text("""
                    SELECT id, ref,
                        LENGTH(COALESCE(zo_tdb77, '')) +
                        LENGTH(COALESCE(en_kJV, '')) as content_len
                    FROM bible_verses WHERE ref = :ref
                    ORDER BY content_len DESC
                """), {"ref": ref}).fetchall()

                keep_id = rows[0][0]
                for row in rows[1:]:
                    del_id = row[0]
                    conn.execute(
                        text("DELETE FROM bible_verses WHERE id = :id"),
                        {"id": del_id},
                    )
                    deletions.append((del_id, ref, keep_id))
            conn.commit()

        # Log changes
        for del_id, ref, keep_id in deletions:
            self._log_change(
                "bible_verses", del_id, "ref",
                ref, None,
                f"Phase 2: dedup — removed duplicate of "
                f"ref {ref} (kept id={keep_id})",
            )
        return len(deletions)

    def backfill_en_zo_translations_clean(self) -> int:
        """Backfill empty translations_clean from translations JSON field.

        Logs each change to audit_log.
        Returns number of rows fixed.
        """
        # Collect fixes first
        fixes: list[tuple[int, str]] = []  # (row_id, clean_value)
        table = Table(
            "dictionary_en_zo", self.metadata, autoload_with=self.engine
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.translations_clean == None)  # noqa: E711
                    | (table.c.translations_clean == "")
                )
            ).fetchall()

            for row in rows:
                row_dict = self._row_to_dict(row, table)
                translations = row_dict.get("translations", "")
                try:
                    if isinstance(translations, str):
                        trans_list = json.loads(translations)
                    else:
                        trans_list = translations
                    if (
                        trans_list
                        and isinstance(trans_list, list)
                        and len(trans_list) > 0
                    ):
                        clean = trans_list[0]
                        if clean and str(clean).strip():
                            row_id = row.id if hasattr(row, "id") else None
                            if row_id is not None:
                                fixes.append(
                                    (row_id, str(clean).strip())
                                )
                except (json.JSONDecodeError, TypeError):
                    pass

            # Apply all updates
            for row_id, clean_val in fixes:
                conn.execute(
                    table.update()
                    .where(table.c.id == row_id)
                    .values(translations_clean=clean_val)
                )
            conn.commit()

        # Log changes
        for row_id, clean_val in fixes:
            self._log_change(
                "dictionary_en_zo", row_id,
                "translations_clean",
                "", clean_val,
                "Phase 2: backfill from translations JSON field[0]",
            )
        return len(fixes)

    # ------------------------------------------------------------------
    # Phase 3: Per-word enrichment
    # ------------------------------------------------------------------
    def enrich_word(self, word: str, **kwargs: Any) -> bool:
        """Enrich a dictionary entry with additional fields.

        Supported fields: myanmar, english, english_clean, pos,
        source, entry_version, update_remarks, update_description,
        zvs_compliance_status, updated_at.

        Returns True if the entry was found and updated, False otherwise.
        """
        from .models import DictionaryEntry

        with self._session() as session:
            row = session.query(DictionaryEntry).filter(
                DictionaryEntry.zolai.ilike(word)
            ).first()
            if not row:
                return False

            changes: list[str] = []
            for field, value in kwargs.items():
                if hasattr(row, field) and value is not None:
                    old = getattr(row, field)
                    setattr(row, field, value)
                    changes.append(f"{field}: {old!r} -> {value!r}")

            if changes:
                log = DataAuditLog(
                    table_name="dictionary",
                    row_id=row.id,
                    field="enrichment",
                    old_value=None,
                    new_value=json.dumps(changes, ensure_ascii=False),
                    changed_at=datetime.now(timezone.utc).isoformat(),
                    reason="word_enrichment",
                )
                session.add(log)
                session.commit()
                return True
        return False

    # ------------------------------------------------------------------
    # CRUD: Add / Delete
    # ------------------------------------------------------------------
    def add_word(
        self,
        zolai: str,
        english: str = "",
        myanmar: str = "",
        pos: str = "",
        source: str = "manual_add",
    ) -> bool:
        """Add a new dictionary entry.

        Returns True if added, False if the word already exists.
        """
        from .models import DictionaryEntry

        with self._session() as session:
            existing = session.query(DictionaryEntry).filter(
                DictionaryEntry.zolai.ilike(zolai)
            ).first()
            if existing:
                return False

            entry = DictionaryEntry(
                zolai=zolai,
                english=english,
                english_clean=english,
                myanmar=myanmar,
                pos=pos,
                source=source,
            )
            session.add(entry)

            log = DataAuditLog(
                table_name="dictionary",
                row_id=0,
                field="zolai",
                old_value=None,
                new_value=zolai,
                changed_at=datetime.now(timezone.utc).isoformat(),
                reason="manual_add",
            )
            session.add(log)
            session.commit()
            return True

    def delete_word(self, zolai: str) -> bool:
        """Delete a dictionary entry by Zolai word.

        Returns True if deleted, False if not found.
        """
        from .models import DictionaryEntry

        with self._session() as session:
            row = session.query(DictionaryEntry).filter(
                DictionaryEntry.zolai.ilike(zolai)
            ).first()
            if not row:
                return False

            log = DataAuditLog(
                table_name="dictionary",
                row_id=row.id,
                field="zolai",
                old_value=zolai,
                new_value="DELETED",
                changed_at=datetime.now(timezone.utc).isoformat(),
                reason="manual_delete",
            )
            session.add(log)
            session.delete(row)
            session.commit()
            return True

    def _session(self):  # noqa: ANN202
        """Context-managed SQLAlchemy session."""
        from sqlalchemy.orm import Session

        return Session(self.engine)

    # ------------------------------------------------------------------
    # Phase 4: Export to JSONL
    # ------------------------------------------------------------------
    def export_table_to_jsonl(
        self,
        table_name: str,
        output_path: str | Path,
        fields: list[str] | None = None,
    ) -> int:
        """Export a table to JSONL format.

        Args:
            table_name: Name of the table to export
            output_path: Path to write the JSONL file
            fields: Optional list of fields to include (all if None)

        Returns:
            Number of rows exported
        """
        from pathlib import Path

        table = Table(table_name, self.metadata, autoload_with=self.engine)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        with self.engine.connect() as conn:
            rows = conn.execute(table.select()).fetchall()
            columns = [c.name for c in table.columns]
            use_fields = fields if fields else columns

            with open(out, "w", encoding="utf-8") as f:
                for row in rows:
                    row_dict = self._row_to_dict(row, table)
                    export = {
                        k: row_dict[k]
                        for k in use_fields
                        if k in row_dict
                    }
                    f.write(json.dumps(export, ensure_ascii=False) + "\n")
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Phase 5: Version history
    # ------------------------------------------------------------------
    def version_history(
        self, table_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Get version history from audit log, grouped by change session."""
        return self.get_audit_log(table_name=table_name, limit=1000)

    # ------------------------------------------------------------------
    # Phase 6: Seed training exercises
    # ------------------------------------------------------------------
    def seed_training_exercises(
        self, exercise_type: str, jsonl_path: str | Path
    ) -> int:
        """Import training exercises from JSONL into the training_exercises
        table."""
        from pathlib import Path

        path = Path(jsonl_path)
        if not path.exists():
            return 0

        count = 0
        table = Table(
            "training_exercises", self.metadata, autoload_with=self.engine
        )
        with self.engine.connect() as conn:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    d = json.loads(line)
                    # Extract zolai/english from common field patterns
                    zolai = d.get(
                        "zolai", d.get("zo", d.get("input", ""))
                    )
                    english = d.get(
                        "english", d.get("en", d.get("output", ""))
                    )
                    if zolai and english:
                        conn.execute(
                            table.insert(),
                            {
                                "exercise_type": exercise_type,
                                "zolai": str(zolai),
                                "english": str(english),
                                "source": str(path.name),
                            },
                        )
                        count += 1
            conn.commit()
        return count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: Any, table: Table) -> dict[str, Any]:
        """Convert a Row to a plain dict, skipping internal id."""
        cols = [c.name for c in table.columns if c.name != "id"]
        return {col: getattr(row, col, None) for col in cols}

    # ------------------------------------------------------------------
    # Provenance tracking
    # ------------------------------------------------------------------
    def track_ingestion(
        self,
        source_file: str,
        table_name: str,
        rows_added: int,
        rows_updated: int,
        method: str,
        notes: str = "",
    ) -> int:
        """Record data ingestion in the audit log.

        Returns the audit log row id.
        """
        from datetime import datetime, timezone

        table = Table("data_audit_log", self.metadata, autoload_with=self.engine)
        payload = json.dumps({
            "source": source_file,
            "rows_added": rows_added,
            "rows_updated": rows_updated,
            "method": method,
            "notes": notes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        with self.engine.connect() as conn:
            result = conn.execute(
                table.insert(),
                {
                    "table_name": table_name,
                    "row_id": 0,
                    "field": "ingestion",
                    "old_value": None,
                    "new_value": payload,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "reason": f"bulk_ingestion:{method}",
                },
            )
            conn.commit()
            return result.inserted_primary_key[0]

    def ensure_myanmar_columns(self) -> list[str]:
        """Add myanmar column to tables that don't have it yet.

        Returns list of tables that were altered.
        """
        tables_needing_my = [
            "dictionary_en_zo", "grammar_patterns", "translations",
            "word_alignments", "word_collocations", "word_usage",
        ]
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(self.engine)
        altered: list[str] = []
        with self.engine.connect() as conn:
            for table in tables_needing_my:
                existing = {c["name"] for c in inspector.get_columns(table)}
                if "myanmar" not in existing:
                    conn.execute(
                        text(f"ALTER TABLE [{table}] ADD COLUMN myanmar TEXT")
                    )
                    altered.append(table)
            conn.commit()
        return altered

    # ------------------------------------------------------------------
    # FTS5 full-text search
    # ------------------------------------------------------------------
    def _fts5_available(self) -> bool:
        """Check if FTS5 extension is available."""
        if not self._db_url.startswith("sqlite"):
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM dictionary_fts LIMIT 0"))
            return True
        except Exception:
            return False

    def create_fts5(self) -> None:
        """Create FTS5 virtual tables for dictionary and Bible search.

        SQLite-only. Creates FTS5 if not already present.
        """
        if not self._db_url.startswith("sqlite"):
            logger.warning("FTS5 only supported on SQLite")
            return
        with self.engine.connect() as conn:
            # Dictionary FTS5
            try:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS dictionary_fts "
                    "USING fts5(zolai, english, content='dictionary', "
                    "content_rowid='id')"
                ))
            except Exception as exc:
                logger.warning("Could not create dictionary_fts: %s", exc)
            # Bible FTS5
            try:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS bible_fts "
                    "USING fts5(zo_tdb77, zo_tedim2010, en_kJV, "
                    "content='bible_verses', content_rowid='id')"
                ))
            except Exception as exc:
                logger.warning("Could not create bible_fts: %s", exc)
            conn.commit()
            logger.info("FTS5 virtual tables created")

    def _populate_fts5(self) -> None:
        """Rebuild FTS5 indexes from source tables."""
        if not self._db_url.startswith("sqlite"):
            return
        with self.engine.connect() as conn:
            try:
                conn.execute(text(
                    "INSERT INTO dictionary_fts(dictionary_fts) "
                    "VALUES('rebuild')"
                ))
            except Exception:
                pass
            try:
                conn.execute(text(
                    "INSERT INTO bible_fts(bible_fts) "
                    "VALUES('rebuild')"
                ))
            except Exception:
                pass
            conn.commit()

    def search_text(self, query: str) -> list[dict[str, Any]]:
        """Full-text search across dictionary and Bible using FTS5.

        Falls back to LIKE search if FTS5 is not available.
        Returns combined results from both tables.
        """
        results: list[dict[str, Any]] = []
        if self._fts5_available():
            # FTS5 dictionary search
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT rowid, * FROM dictionary_fts "
                            "WHERE dictionary_fts MATCH :q LIMIT 50"
                        ),
                        {"q": query},
                    ).fetchall()
                for row in rows:
                    results.append({
                        "table": "dictionary",
                        "zolai": getattr(row, "zolai", None),
                        "english": getattr(row, "english", None),
                    })
            except Exception:
                pass

            # FTS5 Bible search
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT rowid, * FROM bible_fts "
                            "WHERE bible_fts MATCH :q LIMIT 50"
                        ),
                        {"q": query},
                    ).fetchall()
                for row in rows:
                    results.append({
                        "table": "bible_verses",
                        "zo_tdb77": getattr(row, "zo_tdb77", None),
                        "en_kJV": getattr(row, "en_kJV", None),
                    })
            except Exception:
                pass
        else:
            # Fallback: LIKE search
            results.extend(
                self._like_search("dictionary", ["zolai", "english"], query)
            )
            results.extend(
                self._like_search(
                    "bible_verses",
                    ["zo_tdb77", "zo_tedim2010", "en_kJV"],
                    query,
                )
            )
        return results

    def _like_search(
        self,
        table_name: str,
        columns: list[str],
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fallback LIKE search for a single table."""
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        pattern = f"%{query}%"
        col_conditions = [
            getattr(table.c, col).ilike(pattern) for col in columns
        ]
        combined = col_conditions[0]
        for cond in col_conditions[1:]:
            combined = combined | cond
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(combined).limit(limit)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    # ------------------------------------------------------------------
    # Backup / restore
    # ------------------------------------------------------------------
    def backup(self, backup_path: str | Path | None = None) -> Path:
        """Copy current database to a backup file using SQLite backup API.

        Args:
            backup_path: Where to write backup.
                Defaults to <db_name>.bak next to the DB file.

        Returns:
            Path to the backup file.
        """
        db_file = self._get_db_path()
        if db_file is None:
            raise RuntimeError(
                "Backup only supported for file-based SQLite"
            )
        if backup_path is None:
            backup_path = db_file.with_suffix(".db.bak")
        else:
            backup_path = Path(backup_path)

        import sqlite3

        src = sqlite3.connect(str(db_file))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        logger.info("Backed up %s → %s", db_file, backup_path)
        return backup_path

    def restore(self, backup_path: str | Path | None = None) -> Path:
        """Restore database from a backup file.

        Args:
            backup_path: Source backup. Defaults to <db_name>.bak.

        Returns:
            Path restored from.
        """
        db_file = self._get_db_path()
        if db_file is None:
            raise RuntimeError(
                "Restore only supported for file-based SQLite"
            )
        if backup_path is None:
            backup_path = db_file.with_suffix(".db.bak")
        else:
            backup_path = Path(backup_path)
        if not Path(backup_path).exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        import sqlite3

        # Fully dispose to release pooled connections
        self.dispose()

        # Use SQLite backup API for clean restore
        src = sqlite3.connect(str(backup_path))
        dst = sqlite3.connect(str(db_file))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        # Remove WAL/SHM files left from old connections
        for suffix in ("-wal", "-shm"):
            stale = db_file.parent / (db_file.name + suffix)
            if stale.exists():
                stale.unlink()

        logger.info("Restored %s → %s", backup_path, db_file)
        return Path(backup_path)

    def _get_db_path(self) -> Path | None:
        """Extract file path from SQLite URL, or None for non-SQLite."""
        if not self._db_url.startswith("sqlite"):
            return None
        # sqlite:///path/to/file.db → /path/to/file.db
        path_str = self._db_url.replace("sqlite:///", "")
        return Path(path_str)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    def health_check(self) -> dict[str, Any]:
        """Return database health metrics.

        Returns dict with:
            db_url: Database URL (masked)
            backend: "sqlite" or "postgresql"
            table_counts: {table_name: row_count}
            total_rows: sum of all counts
            db_size_bytes: file size (SQLite) or 0
            db_size_human: human-readable size
            fts5_available: bool
            query_latency_ms: time for a COUNT query
            tables: list of table names
        """
        backend = "sqlite" if self._db_url.startswith("sqlite") else "postgresql"
        counts = self.counts()
        total = sum(counts.values())

        # DB size
        db_path = self._get_db_path()
        db_size = 0
        if db_path and db_path.exists():
            db_size = db_path.stat().st_size

        # Query latency
        t0 = time.monotonic()
        try:
            self.count("dictionary")
        except Exception:
            pass
        latency_ms = (time.monotonic() - t0) * 1000

        # Mask URL
        masked_url = self._db_url
        if "@" in masked_url:
            masked_url = masked_url.split("@")[-1]

        return {
            "db_url": masked_url,
            "backend": backend,
            "table_counts": counts,
            "total_rows": total,
            "db_size_bytes": db_size,
            "db_size_human": _human_size(db_size),
            "fts5_available": self._fts5_available(),
            "query_latency_ms": round(latency_ms, 2),
            "tables": self.table_names(),
        }

    def status(self) -> str:
        """Return a human-readable status string."""
        info = self.health_check()
        lines = [
            f"Backend: {info['backend']}",
            f"URL: {info['db_url']}",
            f"Size: {info['db_size_human']}",
            f"Tables: {len(info['tables'])}",
            f"Total rows: {info['total_rows']:,}",
            f"FTS5: {'available' if info['fts5_available'] else 'not available'}",
            f"Query latency: {info['query_latency_ms']}ms",
            "",
            "Table counts:",
        ]
        for name, count in info["table_counts"].items():
            lines.append(f"  {name}: {count:,}")
        return "\n".join(lines)

    def dispose(self) -> None:
        """Release connection pool resources."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._metadata = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _human_size(n: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------
_manager: DatabaseManager | None = None


def get_manager(db_url: str | None = None) -> DatabaseManager:
    """Get or create a DatabaseManager singleton.

    If db_url is provided, creates a new manager (replaces any existing).
    If not provided and no singleton exists, checks ZOLAI_PG_URL env var.
    If ZOLAI_PG_URL is set, uses PostgreSQL; otherwise uses SQLite
    at config.paths.data / "zolai.db".
    """
    global _manager
    if db_url is not None or _manager is None:
        if _manager is not None:
            _manager.dispose()
        if db_url is None:
            db_url = os.environ.get("ZOLAI_PG_URL") or (
                f"sqlite:///{config.paths.data / 'zolai.db'}"
            )
        _manager = DatabaseManager(db_url)
    return _manager


def init_db(db_url: str | None = None) -> DatabaseManager:
    """Create tables and return the manager."""
    mgr = get_manager(db_url)
    mgr.init_db()
    return mgr


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point for database management.

    Commands:
        status   — show table counts, DB size, last migration
        backup   — copy DB to <name>.db.bak
        restore  — restore from backup
        query    — quick word lookup (dictionary + bible)
        stats    — show index sizes, query performance, FTS5 status
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Zolai database management CLI",
    )
    parser.add_argument(
        "--db",
        default=f"sqlite:///{config.paths.data / 'zolai.db'}",
        help="Database URL",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show database status")
    sub.add_parser("backup", help="Backup database to .bak file")
    sub.add_parser("restore", help="Restore database from .bak file")
    sub.add_parser("stats", help="Show detailed statistics")

    query_p = sub.add_parser("query", help="Quick word lookup")
    query_p.add_argument("text", help="Word or phrase to search")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    mgr = get_manager(args.db)
    mgr.init_db()

    if args.command == "status":
        print(mgr.status())

    elif args.command == "backup":
        path = mgr.backup()
        print(f"Backed up to: {path}")

    elif args.command == "restore":
        path = mgr.restore()
        print(f"Restored from: {path}")

    elif args.command == "query":
        text_val = args.text
        print(f"Looking up: {text_val}\n")

        # Dictionary
        dict_results = mgr.lookup_word(text_val)
        if dict_results:
            print(f"Dictionary ({len(dict_results)} results):")
            for r in dict_results[:10]:
                print(f"  {r['zolai']}: {r.get('english_clean', r.get('english', ''))}")
        else:
            print("Dictionary: no results")

        # Bible
        bible_results = mgr.search_bible(text_val)
        if bible_results:
            print(f"\nBible ({len(bible_results)} results):")
            for r in bible_results[:5]:
                print(f"  [{r['ref']}] {r.get('zo_tdb77', '')[:80]}")

        # FTS5 combined search
        if mgr._fts5_available():
            fts_results = mgr.search_text(text_val)
            if fts_results:
                print(f"\nFTS5 ({len(fts_results)} results):")
                for r in fts_results[:5]:
                    table = r.get("table", "")
                    if table == "dictionary":
                        print(f"  [dict] {r.get('zolai', '')}: {r.get('english', '')}")
                    elif table == "bible_verses":
                        print(f"  [bible] {r.get('zo_tdb77', '')[:80]}")

    elif args.command == "stats":
        info = mgr.health_check()
        print(f"Backend: {info['backend']}")
        print(f"Size: {info['db_size_human']}")
        print(f"FTS5: {'available' if info['fts5_available'] else 'not available'}")
        print(f"Query latency: {info['query_latency_ms']}ms")
        print("\nTable counts:")
        for name, count in info["table_counts"].items():
            print(f"  {name}: {count:,}")
        print(f"\nTotal rows: {info['total_rows']:,}")

    mgr.dispose()


if __name__ == "__main__":
    main()
