"""Idempotent DDL for DB-first tables added by the migration.

These tables fill gaps that the first-generation import pipeline did NOT create
(e.g. ``knowledge_vectors``, ``ngram``, particle/verb databases) and that the
serving layer needs to read from SQLite instead of raw JSONL.

Every table carries the unified build metadata columns
(``import_batch_id``, ``source_file``, ``version``, ``imported_at``) so they
participate in the same versioning scheme as the pre-existing build tables
(see ``core/jsonl_pipeline_v3.py``).

Safe to call repeatedly — ``CREATE TABLE IF NOT EXISTS`` + ``ALTER ... ADD
COLUMN`` guarded by column presence checks.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Unified build-metadata columns appended to every new table.
_META_COLS = (
    "import_batch_id TEXT",
    "source_file TEXT",
    "version INTEGER DEFAULT 1",
    "imported_at TEXT",
)

# DDL for the tables introduced by this migration. Order matters only for FK
# references (none here). Expressed as (table, [column DDL, ...]).
_EXTRA_TABLES: dict[str, list[str]] = {
    "knowledge_vectors": [
        "id TEXT PRIMARY KEY",
        "text TEXT NOT NULL",
        "metadata TEXT",  # JSON blob (dict)
        "embedding TEXT",  # JSON list of floats (or empty)
        "source_type TEXT",
        "source TEXT",
    ],
    "ngram": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "ngram_type TEXT NOT NULL",  # 'unigram' | 'bigram'
        "word TEXT",  # unigram token
        "a TEXT",  # bigram first token
        "b TEXT",  # bigram second token
        "count INTEGER NOT NULL DEFAULT 0",
    ],
    "particle_database": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "particle TEXT NOT NULL",
        "function TEXT",
        "function_type TEXT",
        "meaning TEXT",
        "examples TEXT",  # JSON list
        "notes TEXT",
    ],
    "verb_database": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "verb TEXT NOT NULL",
        "stem TEXT",
        "verb_class TEXT",
        "tense TEXT",
        "meaning TEXT",
        "examples TEXT",  # JSON list
        "transitivity TEXT",
    ],
    "training_validation": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "exercise_id INTEGER",
        "level TEXT",
        "prompt TEXT",
        "expected TEXT",
        "predicted TEXT",
        "is_valid INTEGER DEFAULT 0",
        "error_codes TEXT",  # JSON list
        "checked_at TEXT",
    ],
    "simbu": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "text TEXT NOT NULL",
        "source TEXT",
        "type TEXT",
    ],
    "grammar_instructions": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "instruction TEXT NOT NULL",
        "input TEXT",
        "output TEXT",
    ],
    "corrections": [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "table_name TEXT NOT NULL",
        "row_id INTEGER",
        "field TEXT",
        "current_value TEXT",
        "proposed_value TEXT",
        "status TEXT DEFAULT 'pending'",
        "proposed_by TEXT",
        "proposed_at TEXT",
        "reviewed_by TEXT",
        "reviewed_at TEXT",
        "notes TEXT",
    ],
}


def ensure_schema_v2(engine: Engine) -> list[str]:
    """Create the migration tables (+ metadata columns), idempotently.

    Returns the names of tables that were created (empty if none were new).
    """
    inspector = sa_inspect(engine)
    existing = set(inspector.get_table_names())
    created: list[str] = []

    with engine.begin() as conn:
        for table, col_ddls in _EXTRA_TABLES.items():
            if table in existing:
                continue
            ddl = ",\n    ".join([*col_ddls, *_META_COLS])
            conn.execute(text(f"CREATE TABLE {table} (\n    {ddl}\n)"))
            logger.info("  created table: %s", table)
            created.append(table)

        # Ensure unified metadata columns on all non-new tables too.
        for table in existing:
            if table in _EXTRA_TABLES:
                _ensure_meta_cols(conn, table)

    return created


def _ensure_meta_cols(conn, table: str) -> None:
    """Add unified build-metadata columns if missing (idempotent)."""
    cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
    defaults = {
        "import_batch_id": '"import_batch_id" TEXT',
        "source_file": '"source_file" TEXT',
        "version": '"version" INTEGER DEFAULT 1',
        "imported_at": '"imported_at" TEXT',
    }
    for col, ddl in defaults.items():
        if col not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            logger.info("  added column: %s.%s", table, col)


def get_engine(db_path=None):
    """Common engine factory for the repository layer.

    Uses the canonical DB path from ``config.paths.zolai_db`` unless overridden,
    with the same busy_timeout/WAL settings used across the stack.
    """
    from sqlalchemy import create_engine

    if db_path is None:
        from ..config import config

        db_path = config.paths.zolai_db

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
    ensure_schema_v2(engine)
    return engine


def migrate(db_path=None):
    """Apply the v2 schema to the canonical database (or a given path)."""
    engine = get_engine(db_path)
    created = ensure_schema_v2(engine)
    print(f"[schema_v2] tables created: {created or 'none'}")
    return created
