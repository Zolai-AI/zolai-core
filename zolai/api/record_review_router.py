"""Record-correction review API — PATCH a whitelisted row, audit the change.

Mounted under ``/api/v1/review/records`` (registered **before** the
``server.py`` catch-all):

``PATCH /api/v1/review/records/{table}/{id}``
    Body ``{corrected_fields: {column: new_value, ...}, reason: "..."}``.
    Scope: ``dataset:edit`` (frozen action vocabulary — ``zolai.api.auth``).

Behaviour (api-design §11 — every mutation emits ``data_audit_log``):

- the table must be in :data:`RECORDS_WHITELIST` (**400** otherwise — same
  whitelist as ``GET /api/v1/records``, so ``*_import`` staging, ``api_keys``
  and ``data_audit_log`` itself can never be corrected here);
- every corrected field must be a real, non-``id`` column of that table
  (**400** otherwise — column names are validated against ``PRAGMA
  table_info`` and values are always bound parameters);
- one ``data_audit_log`` row per written field (old → new, actor + reason);
- ``review_status`` is set to ``reviewed`` **only where the column exists**
  (the L1.3 lexicon tables) — no columns are ever added: tables without it
  are audit-logged only.

Reads and the audit tail live in :mod:`zolai.api.records_router`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import config
from . import auth
from .records_router import RECORDS_WHITELIST

router = APIRouter(
    prefix="/api/v1/review/records",
    tags=["review"],
    # Mutating route → frozen dataset:edit action.  Non-strict: an absent key
    # keeps the warn-mode dual-accept; a *presented* key must hold the scope.
    dependencies=[Depends(auth.require_scope("dataset:edit"))],
)

#: Value stamped into ``review_status`` where the column exists.
REVIEWED_STATUS = "reviewed"


class RecordCorrectionIn(BaseModel):
    """PATCH /api/v1/review/records/{table}/{id} body."""

    corrected_fields: dict[str, str | int | float | None] = Field(
        ...,
        min_length=1,
        examples=[{"english": "God"}],
        description="column → new value (columns validated against the live schema)",
    )
    reason: str = Field(..., min_length=1, max_length=500, examples=["typo fix"])


def _connect() -> sqlite3.Connection:
    """Open the canonical SQLite store (one short-lived connection per call)."""
    conn = sqlite3.connect(str(config.paths.db), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _audit_value(value: Any) -> str | None:
    """data_audit_log old/new values are TEXT — None stays NULL."""
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _actor(request: Request) -> str:
    """Audit actor = presented key prefix (never a client-supplied field)."""
    record = getattr(request.state, "api_key", None)
    if record:
        return str(record.get("key_prefix") or record.get("name") or "api-key")
    return "api"


@router.patch("/{table}/{row_id}", summary="Apply a reviewed field correction (writes data_audit_log)")
async def correct_record(
    table: str,
    row_id: int,
    body: RecordCorrectionIn,
    request: Request,
) -> dict[str, Any]:
    """Correct fields on one whitelisted row and audit every write."""
    if table not in RECORDS_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "table_not_allowed",
                "table": table,
                "allowed": sorted(RECORDS_WHITELIST),
            },
        )

    conn = _connect()
    try:
        cols = _columns(conn, table)
        if not cols:
            raise HTTPException(
                status_code=500,
                detail={"error": "table_unavailable", "table": table},
            )

        row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "row_not_found", "table": table, "id": row_id},
            )

        # Validate every requested column against the live schema — `id` is
        # immutable and unknown names are rejected before any write.
        updates: dict[str, Any] = {}
        for field, value in body.corrected_fields.items():
            if field == "id" or field not in cols:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "column_not_correctable", "table": table, "field": field},
                )
            updates[field] = value

        # review_status only where the column already exists (L1.3 lexicon
        # tables) — never ALTER a table from this route.
        if "review_status" in cols and "review_status" not in updates:
            if row["review_status"] != REVIEWED_STATUS:
                updates["review_status"] = REVIEWED_STATUS

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        reason = f"record_review by {_actor(request)}: {body.reason}"

        try:
            for field, value in updates.items():
                conn.execute(
                    f'UPDATE "{table}" SET "{field}" = ? WHERE id = ?',
                    (value, row_id),
                )
                conn.execute(
                    "INSERT INTO data_audit_log "
                    "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        table,
                        row_id,
                        field,
                        _audit_value(row[field]),
                        _audit_value(value),
                        stamp,
                        reason,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        final_status = (
            updates.get("review_status")
            if "review_status" in updates
            else (row["review_status"] if "review_status" in cols else None)
        )
        payload = {
            "table": table,
            "id": row_id,
            "updated_fields": sorted(updates),
            "review_status": final_status,
            "audit_rows": len(updates),
        }
    finally:
        conn.close()

    return payload
