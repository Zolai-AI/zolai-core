"""Unified build/metadata versioning across canonical + pipeline tables.

Database-first principle: every row that enters the DB through the JSONL/Kaggle
pipelines carries the same four metadata columns:

    import_batch_id  TEXT        uuid per import run
    source_file      TEXT        canonical source label ("kaggle/data/...", "pipeline/...")
    version          INTEGER     intra-table row version (default 1)
    imported_at      TEXT        ISO-8601 write timestamp

This module
  * discovers tables missing these columns and adds them (idempotent),
  * backfills pre-existing rows with a deterministic ``legacy`` batch marker,
  * exposes an "active" data helper (current version per table).

Use ``apply_versioning(db=None)`` once after any schema change; use
``active_rows(...)`` from serving code to read only the current version.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from .schema_v2 import get_engine as _get_engine

logger = logging.getLogger(__name__)

# System / internal tables that must NOT receive import metadata.
_SKIP_PREFIXES = ("sqlite_",)
_SKIP_NAMES = {"import_log", "provenance", "data_audit_log"}

# FTS shadow tables cannot be altered.
_FTS_SUFFIXES = ("_fts", "_fts_data", "_fts_idx", "_fts_docsize", "_fts_config")

_META = {
    "import_batch_id": '"import_batch_id" TEXT',
    "source_file": '"source_file" TEXT',
    "version": '"version" INTEGER DEFAULT 1',
    "imported_at": '"imported_at" TEXT',
}


def _is_skippable(table: str) -> bool:
    if any(table.startswith(p) for p in _SKIP_PREFIXES):
        return True
    if table in _SKIP_NAMES:
        return True
    if any(table.endswith(fx) for fx in _FTS_SUFFIXES):
        return True
    return False


def _latest_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_versioning(db=None) -> dict[str, list[str]]:
    """Add missing versioning columns to build tables + backfill legacy rows.

    Returns ``{"added": [table,...], "backfilled": [table,...]}``.
    """
    engine = _get_engine(db)
    inspector = sa_inspect(engine)
    tables = inspector.get_table_names()
    added: list[str] = []
    backfilled: list[str] = []
    batch_id = f"legacy-v1-{uuid.uuid4().hex[:8]}"
    ts = _latest_timestamp()

    with engine.begin() as conn:
        for table in tables:
            if _is_skippable(table):
                continue
            try:
                cols = {c["name"] for c in inspector.get_columns(table)}
            except Exception:  # pragma: no cover
                continue
            missing = [c for c in _META if c not in cols]
            if missing:
                for col in missing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {_META[col]}"))
                    logger.info("  added column: %s.%s", table, col)
                added.append(table)

            # Backfill legacy rows so versioning is consistent everywhere.
            conn.execute(
                text(
                    f"UPDATE {table} SET "
                    f"import_batch_id = COALESCE(import_batch_id, :batch), "
                    f"version = COALESCE(version, 1), "
                    f"imported_at = COALESCE(imported_at, :ts) "
                    f"WHERE import_batch_id IS NULL OR version IS NULL"
                ),
                {"batch": batch_id, "ts": ts},
            )
            backfilled.append(table)

    return {"added": added, "backfilled": backfilled}


def latest_version(table: str, db=None) -> int:
    """Return the maximum ``version`` present in ``table`` (0 if empty)."""
    engine = _get_engine(db)
    with engine.connect() as conn:
        try:
            row = conn.execute(
                text(f"SELECT COALESCE(MAX(version), 0) FROM {table}")
            ).scalar_one()
        except Exception:
            return 0
    return int(row or 0)


def active_version(db=None) -> dict[str, int]:
    """Return ``{table: latest_version}`` for every versioned build table."""
    engine = _get_engine(db)
    inspector = sa_inspect(engine)
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for table in inspector.get_table_names():
            if _is_skippable(table) or "version" not in {
                c["name"] for c in inspector.get_columns(table)
            }:
                continue
            try:
                v = conn.execute(
                    text(f"SELECT COALESCE(MAX(version), 0) FROM {table}")
                ).scalar_one()
                out[table] = int(v or 0)
            except Exception:  # pragma: no cover
                continue
    return out


def active_rows(table: str, filters: dict[str, Any] | None = None, db=None,
                limit: int | None = None) -> list[dict[str, Any]]:
    """Return rows of the current (latest) version for ``table``."""
    engine = _get_engine(db)
    sel = ["*"]
    where = [f"{table}.version = (SELECT COALESCE(MAX(version), 1) FROM {table})"]
    for col, val in (filters or {}).items():
        where.append(f"{table}.{col} = :{col}")
    sql = "SELECT " + ", ".join(sel) + f" FROM {table} WHERE " + " AND ".join(where)
    if limit:
        sql += f" LIMIT {int(limit)}"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), filters or {}).fetchall()
    return [dict(r._mapping) for r in rows]


if __name__ == "__main__":  # pragma: no cover
    import sys

    res = apply_versioning()
    print("[versioning] added:", res["added"])
    print("[versioning] backfilled:", res["backfilled"])
    sys.exit(0)
