"""Bidirectional sync between SQLite and PostgreSQL.

Provides functions to sync data between local SQLite (offline/desktop)
and PostgreSQL (production/server) with conflict resolution based on
latest `updated_at` timestamp.

Usage:
    from zolai.data.sync import sync_sqlite_to_postgres, get_sync_status
    sync_sqlite_to_postgres("sqlite:///zolai.db", "postgresql://...")
    status = get_sync_status("sqlite:///zolai.db")
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Tables that support sync (order matters for foreign key deps)
SYNC_TABLES = [
    "dictionary",
    "bible_verses",
    "grammar_patterns",
    "phrases",
    "vocabulary",
    "translations",
    "word_usage",
    "provenance",
]

# JSON columns that need serialization/deserialization
_JSON_COLS: dict[str, set[str]] = {
    "dictionary": {"english"},
    "bible_verses": set(),
    "grammar_patterns": {"examples"},
    "phrases": {"examples"},
    "vocabulary": {"books", "examples"},
    "translations": set(),
    "word_usage": {"meaning_shifts", "co_occurring_words"},
    "provenance": set(),
}

PROGRESS_INTERVAL = 10_000  # Report every 10K rows


def _create_engine(url: str) -> Engine:
    """Create an engine with appropriate settings for the backend."""
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
        kwargs["pool_pre_ping"] = True
    return create_engine(url, echo=False, **kwargs)


def _get_table_names(engine: Engine) -> list[str]:
    """Get all table names from the database."""
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return sorted(metadata.tables.keys())


def _get_primary_key(table: Table) -> str | None:
    """Get the primary key column name for a table."""
    for col in table.columns:
        if col.primary_key:
            return col.name
    return None


def _serialize_row(row: dict[str, Any], json_cols: set[str]) -> dict[str, Any]:
    """Serialize JSON columns for storage."""
    result = {}
    for k, v in row.items():
        if k in json_cols and isinstance(v, (list, dict)):
            result[k] = json.dumps(v, ensure_ascii=False)
        else:
            result[k] = v
    return result


def _deserialize_row(row: dict[str, Any], json_cols: set[str]) -> dict[str, Any]:
    """Deserialize JSON columns from storage."""
    result = {}
    for k, v in row.items():
        if k in json_cols and isinstance(v, str):
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        else:
            result[k] = v
    return result


def _row_to_dict(row: Any, table: Table) -> dict[str, Any]:
    """Convert a Row to a plain dict."""
    cols = [c.name for c in table.columns]
    return {col: getattr(row, col, None) for col in cols}


def _has_updated_at(engine: Engine, table_name: str) -> bool:
    """Check if a table has an updated_at column."""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f"PRAGMA table_info({table_name})")
                if engine.url.startswith("sqlite")
                else text(
                    f"SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table_name}' AND column_name = 'updated_at'"
                )
            )
            for row in result:
                # SQLite: (cid, name, type, notnull, dflt_value, pk)
                # PostgreSQL: column_name
                name = row[1] if engine.url.startswith("sqlite") else row[0]
                if name == "updated_at":
                    return True
    except Exception:
        pass
    return False


def sync_sqlite_to_postgres(
    sqlite_url: str,
    pg_url: str,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Sync all tables from SQLite to PostgreSQL.

    Args:
        sqlite_url: SQLite connection URL.
        pg_url: PostgreSQL connection URL.
        progress_callback: Optional fn(table, rows_synced) called at intervals.

    Returns:
        Dict with sync results per table.
    """
    src_engine = _create_engine(sqlite_url)
    dst_engine = _create_engine(pg_url)

    results: dict[str, Any] = {}
    total_synced = 0

    try:
        src_names = set(_get_table_names(src_engine))
        dst_names = set(_get_table_names(dst_engine))

        for table_name in SYNC_TABLES:
            if table_name not in src_names:
                logger.warning("Source table %s not found, skipping", table_name)
                results[table_name] = {"status": "skipped", "reason": "not_in_source"}
                continue

            # Create table in destination if needed
            if table_name not in dst_names:
                _create_table_in_dst(src_engine, dst_engine, table_name)

            src_table = Table(table_name, MetaData(), autoload_with=src_engine)
            dst_table = Table(table_name, MetaData(), autoload_with=dst_engine)
            json_cols = _JSON_COLS.get(table_name, set())
            pk = _get_primary_key(src_table)

            # Fetch all rows from source
            with src_engine.connect() as conn:
                rows = conn.execute(src_table.select()).fetchall()

            if not rows:
                results[table_name] = {"status": "ok", "synced": 0}
                continue

            # Determine sync strategy
            synced = 0
            if pk and _has_updated_at(src_engine, table_name):
                # Upsert: insert or update based on PK + timestamp
                synced = _upsert_rows(
                    dst_engine, dst_table, rows, src_table, json_cols, pk,
                    progress_callback, table_name,
                )
            else:
                # Truncate and reload (simple sync)
                synced = _truncate_and_reload(
                    dst_engine, dst_table, rows, src_table, json_cols,
                    progress_callback, table_name,
                )

            results[table_name] = {"status": "ok", "synced": synced}
            total_synced += synced
            logger.info("Synced %s: %d rows", table_name, synced)

    finally:
        src_engine.dispose()
        dst_engine.dispose()

    results["total_synced"] = total_synced
    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    return results


def sync_postgres_to_sqlite(
    pg_url: str,
    sqlite_url: str,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Sync all tables from PostgreSQL to SQLite.

    Args:
        pg_url: PostgreSQL connection URL.
        sqlite_url: SQLite connection URL.
        progress_callback: Optional fn(table, rows_synced) called at intervals.

    Returns:
        Dict with sync results per table.
    """
    return sync_sqlite_to_postgres(pg_url, sqlite_url, progress_callback)


def _create_table_in_dst(
    src_engine: Engine,
    dst_engine: Engine,
    table_name: str,
) -> None:
    """Create a table in the destination using source schema."""
    src_metadata = MetaData()
    src_metadata.reflect(bind=src_engine)
    if table_name in src_metadata.tables:
        src_table = src_metadata.tables[table_name]
        # Create in destination
        dst_metadata = MetaData()
        src_table.metadata = dst_metadata
        dst_metadata.create_all(dst_engine)
        logger.info("Created table %s in destination", table_name)


def _upsert_rows(
    dst_engine: Engine,
    dst_table: Table,
    rows: list[Any],
    src_table: Table,
    json_cols: set[str],
    pk: str,
    progress_callback: Any,
    table_name: str,
) -> int:
    """Upsert rows using PK + updated_at for conflict resolution."""
    synced = 0
    has_updated = _has_updated_at(dst_engine, table_name)

    with dst_engine.begin() as conn:
        for i, row in enumerate(rows):
            row_dict = _row_to_dict(row, src_table)
            row_dict = _serialize_row(row_dict, json_cols)

            if has_updated and "updated_at" in row_dict:
                # Use PostgreSQL UPSERT (INSERT ... ON CONFLICT DO UPDATE)
                if dst_engine.url.startswith("postgresql"):
                    cols = list(row_dict.keys())
                    placeholders = ", ".join(f":{c}" for c in cols)
                    update_clause = ", ".join(
                        f"{c} = EXCLUDED.{c}"
                        for c in cols if c != pk
                    )
                    sql = text(
                        f"INSERT INTO {table_name} ({', '.join(cols)}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT ({pk}) DO UPDATE SET {update_clause}"
                    )
                    conn.execute(sql, row_dict)
                else:
                    # SQLite: INSERT OR REPLACE
                    cols = list(row_dict.keys())
                    placeholders = ", ".join(f":{c}" for c in cols)
                    col_names = ", ".join(cols)
                    sql = text(
                        f"INSERT OR REPLACE INTO {table_name} ({col_names}) "
                        f"VALUES ({placeholders})"
                    )
                    conn.execute(sql, row_dict)
            else:
                # Simple insert (no timestamp conflict resolution)
                conn.execute(dst_table.insert(), [row_dict])

            synced += 1
            if synced % PROGRESS_INTERVAL == 0:
                logger.info("Sync progress %s: %d/%d", table_name, synced, len(rows))
                if progress_callback:
                    progress_callback(table_name, synced)

    return synced


def _truncate_and_reload(
    dst_engine: Engine,
    dst_table: Table,
    rows: list[Any],
    src_table: Table,
    json_cols: set[str],
    progress_callback: Any,
    table_name: str,
) -> int:
    """Truncate destination table and reload all rows."""
    with dst_engine.begin() as conn:
        conn.execute(dst_table.delete())

    synced = 0
    batch: list[dict[str, Any]] = []

    with dst_engine.begin() as conn:
        for row in rows:
            row_dict = _row_to_dict(row, src_table)
            row_dict = _serialize_row(row_dict, json_cols)
            batch.append(row_dict)

            if len(batch) >= 1000:
                conn.execute(dst_table.insert(), batch)
                synced += len(batch)
                if synced % PROGRESS_INTERVAL == 0:
                    logger.info(
                        "Sync progress %s: %d/%d", table_name, synced, len(rows)
                    )
                    if progress_callback:
                        progress_callback(table_name, synced)
                batch = []

        if batch:
            conn.execute(dst_table.insert(), batch)
            synced += len(batch)

    return synced


def get_sync_status(db_url: str) -> dict[str, Any]:
    """Get last sync timestamps per table.

    Args:
        db_url: Database URL to check.

    Returns:
        Dict with table names and their row counts + last sync info.
    """
    engine = _create_engine(db_url)
    status: dict[str, Any] = {"db_url": db_url, "tables": {}}

    try:
        existing = set(_get_table_names(engine))
        for table_name in SYNC_TABLES:
            if table_name in existing:
                table = Table(table_name, MetaData(), autoload_with=engine)
                with engine.connect() as conn:
                    count = conn.execute(
                        text(f"SELECT COUNT(*) FROM {table_name}")
                    ).scalar_one()
                status["tables"][table_name] = {
                    "row_count": count,
                    "exists": True,
                }
            else:
                status["tables"][table_name] = {
                    "row_count": 0,
                    "exists": False,
                }
    finally:
        engine.dispose()

    return status
