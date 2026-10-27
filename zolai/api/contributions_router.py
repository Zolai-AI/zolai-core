"""Public contribution endpoint — submit new words, meanings, usage, corrections.

Anyone may POST a contribution; no auth required. Submissions are stored in a
``contributions`` table for editorial review via the admin Review queue.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..data.database import get_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["contributions"])


class ContributionIn(BaseModel):
    """Incoming contribution payload."""

    type: str = Field(..., min_length=1, max_length=32)
    word: str = Field(..., min_length=1, max_length=200)
    field: str = Field(default="", max_length=64)
    current_value: str = Field(default="", max_length=2000)
    suggested_value: str = Field(..., min_length=1, max_length=2000)
    context: str = Field(default="", max_length=4000)
    source: str = Field(default="", max_length=500)
    email: str = Field(default="", max_length=200)


class ContributionOut(BaseModel):
    """Response after successful submission."""

    id: int
    status: str = "pending"
    message: str = "Contribution received and queued for editorial review."


def ensure_contributions_table() -> None:
    """Create contributions table if it doesn't exist."""
    mgr = get_manager()
    with mgr.engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                word TEXT NOT NULL,
                field TEXT NOT NULL DEFAULT '',
                current_value TEXT NOT NULL DEFAULT '',
                suggested_value TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT
            )
        """)


@router.post("/contributions", response_model=ContributionOut)
async def submit_contribution(payload: ContributionIn) -> ContributionOut:
    """Accept a public dictionary contribution.

    No authentication required. The contribution is stored for editorial review.
    """
    ensure_contributions_table()

    mgr = get_manager()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with mgr.engine.begin() as conn:
        cursor = conn.exec_driver_sql(
            """
            INSERT INTO contributions
            (type, word, field, current_value, suggested_value, context, source, email, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                payload.type[:32],
                payload.word[:200],
                payload.field[:64],
                payload.current_value[:2000],
                payload.suggested_value[:2000],
                payload.context[:4000],
                payload.source[:500],
                payload.email[:200],
                created_at,
            ),
        )
        contribution_id = cursor.lastrowid

    logger.info("Contribution received: id=%s type=%s word=%s", contribution_id, payload.type, payload.word)

    return ContributionOut(
    id=contribution_id,
    status="pending",
    message="Contribution received and queued for editorial review.",
)
