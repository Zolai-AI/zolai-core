"""Records + audit read API — whitelisted row reads and the audit tail.

Mounted under ``/api/v1`` (registered **before** the ``server.py`` catch-all):

- ``GET /api/v1/records?table=&q=&limit=&cursor=`` — read-only rows over a
  **fixed whitelist** (:data:`RECORDS_WHITELIST`): ``dictionary``,
  ``bible_verses``, ``grammar_patterns``, ``phrases``, ``vocabulary``.
  Anything else (``*_import`` staging, ``api_keys``, ``data_audit_log``, …)
  is **400** before a single row is touched.  Scope: ``dataset:read``.
- ``GET /api/v1/audit?table=&row_id=&limit=&cursor=`` — read-only tail of
  ``data_audit_log`` (newest first, cursor walks downwards).  Scope:
  ``audit:read``.  There is **no** audit write endpoint here — writes are a
  side effect of mutating routes (api-design §11).

Cursor pagination follows api-design §4: ``{items, next_cursor, has_more}``,
default ``limit=50``, hard cap 500.  This module also hosts the whitelist the
record-review router reuses (single source of truth).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import config
from . import auth

router = APIRouter(prefix="/api/v1", tags=["records"])

#: Read whitelist — table → free-text search fields (intersected with the
#: live schema at query time).  Deliberately excludes every ``*_import``
#: staging table, ``api_keys`` and ``data_audit_log`` (audit has its own
#: scoped endpoint below).
RECORDS_WHITELIST: dict[str, tuple[str, ...]] = {
    "dictionary": ("zolai", "english", "myanmar", "pos"),
    "bible_verses": ("ref", "book", "zo_tdb77", "en_kJV", "myanmar"),
    "grammar_patterns": ("pattern_id", "pattern", "description", "function", "examples"),
    "phrases": ("zolai", "english", "examples"),
    "vocabulary": ("headword", "english", "examples"),
}


def connect_db() -> sqlite3.Connection:
    """Open the canonical SQLite store (short-lived, one per request)."""
    conn = sqlite3.connect(str(config.paths.db), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Column names of ``table`` (empty when the table does not exist)."""
    return [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def escape_like(term: str) -> str:
    """Escape LIKE wildcards in user input (paired with ``ESCAPE '\\'``)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require_whitelisted(table: str) -> tuple[str, ...]:
    """Return the table's search fields or raise **400** for anything else."""
    fields = RECORDS_WHITELIST.get(table)
    if fields is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "table_not_allowed",
                "table": table,
                "allowed": sorted(RECORDS_WHITELIST),
            },
        )
    return fields


# ---------------------------------------------------------------------------
# GET /api/v1/records — whitelisted, read-only, cursor paginated
# ---------------------------------------------------------------------------


@router.get(
    "/records",
    dependencies=[Depends(auth.require_scope("dataset:read"))],
    summary="Read rows from a whitelisted table",
)
def list_records(
    table: str = Query(..., description="Whitelisted table name (400 for anything else)"),
    q: str | None = Query(None, min_length=1, description="Free-text filter over the table's search fields"),
    limit: int = Query(50, ge=1, le=500, description="Page size (api-design §4: default 50, cap 500)"),
    cursor: int | None = Query(None, ge=0, description="Opaque cursor = id of the last item seen"),
) -> dict[str, Any]:
    """List rows of one whitelisted table, ascending id, cursor paginated."""
    search_fields = _require_whitelisted(table)

    conn = connect_db()
    try:
        available = table_columns(conn, table)
        if not available:
            raise HTTPException(
                status_code=500,
                detail={"error": "table_unavailable", "table": table},
            )

        where: list[str] = []
        params: list[Any] = []
        if cursor is not None:
            where.append("id > ?")
            params.append(cursor)
        if q is not None:
            fields = [field for field in search_fields if field in available]
            if not fields:
                raise HTTPException(
                    status_code=500,
                    detail={"error": "table_unavailable", "table": table},
                )
            pattern = f"%{escape_like(q.strip())}%"
            clauses = [f"{field} LIKE ? ESCAPE '\\'" for field in fields]
            where.append(f"({' OR '.join(clauses)})")
            params.extend([pattern] * len(fields))

        sql = f'SELECT * FROM "{table}"'
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit + 1)

        rows = [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1]["id"]) if (has_more and items) else None
    return {
        "table": table,
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/audit — read-only tail of data_audit_log (scope: audit:read)
# ---------------------------------------------------------------------------


@router.get("/audit", dependencies=[Depends(auth.require_scope("audit:read"))], summary="Read-only data_audit_log tail")
def list_audit(
    table: str | None = Query(None, min_length=1, description="Filter by audited table name"),
    row_id: int | None = Query(None, ge=0, description="Filter by audited row id"),
    limit: int = Query(50, ge=1, le=500, description="Page size (newest first)"),
    cursor: int | None = Query(None, ge=0, description="Opaque cursor = id of the oldest item seen"),
) -> dict[str, Any]:
    """Tail ``data_audit_log`` newest-first; ``cursor`` walks further back."""
    where: list[str] = []
    params: list[Any] = []
    if table is not None:
        where.append("table_name = ?")
        params.append(table)
    if row_id is not None:
        where.append("row_id = ?")
        params.append(row_id)
    if cursor is not None:
        where.append("id < ?")
        params.append(cursor)

    sql = "SELECT * FROM data_audit_log"
    if where:
        sql += f" WHERE {' AND '.join(where)}"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit + 1)

    conn = connect_db()
    try:
        rows = [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1]["id"]) if (has_more and items) else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}
