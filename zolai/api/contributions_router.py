"""Contribution endpoint — submit new words, meanings, usage, corrections.

Anyone may POST a contribution; no auth required. That openness is a
deliberate, standing decision about the community corpus, not an oversight: the
dictionary improves fastest when a speaker with no account can correct a gloss
on the way past it. Submissions land in ``contributions`` for editorial review
via the admin Review queue (``PATCH /api/v1/review/records/contributions/{id}``).

**What changed with the 4-role model** — attribution and a read-back:

- every row now records *who* submitted it, derived **only** from the credential
  the middleware resolved (``request.state.api_key``). The request body carries
  no identity field, and never will: a client that names its own identity is a
  client that can pin a bad suggestion on somebody else. A row submitted while
  signed in gets ``user_id`` + ``submitted_by`` + ``auth_source='session'``; a
  key gets ``submitted_by`` + ``auth_source='api_key'`` and ``user_id`` NULL,
  because a key is not a person; an anonymous post keeps all three NULL —
  exactly as open as it was before.
- ``GET /api/v1/contributions/mine`` lets a signed-in account see what it asked
  for, newest first, and only its own rows (the filter is the session's
  ``user_id``, never a caller-supplied one).

The table definition lives in :mod:`zolai.data.migrations` — this module creates
it through the migration, so the second writer
(:mod:`zolai.agent.tools.learning`) and the API cannot drift apart.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..data.database import get_manager
from ..shared.clock import stamp
from . import rbac
from .errors import api_error, internal_http_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["contributions"])

#: The columns ``/contributions/mine`` may return.  Named explicitly — a
#: ``SELECT *`` here would hand ``email`` to the caller the day someone adds a
#: column, and an address a contributor offered for editorial contact is not
#: this endpoint's to give away.
MINE_COLUMNS = (
    "id, type, word, field, status, created_at, reviewed_at, reviewed_by"
)


class ContributionIn(BaseModel):
    """Incoming contribution payload — content only, never an identity."""

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
    attributed_to: str | None = None


def ensure_contributions_table() -> None:
    """Create ``contributions`` if it doesn't exist.

    A thin caller of the migrated DDL (:mod:`zolai.data.migrations`), not a
    definition: the table is written from two places — this router and
    :mod:`zolai.agent.tools.learning` — and both must see the same columns,
    including the three attribution ones.  Keeping the CREATE here is how the
    table grew a column in one path and not the other.
    """
    from ..data.migrations import (
        add_contribution_attribution_columns,
    )
    from ..data.migrations import (
        ensure_contributions_table as _migrate,
    )

    mgr = get_manager()
    _migrate(mgr)
    # A store created before attribution exists gains the columns on first use;
    # a fresh one reports every column as skipped.
    add_contribution_attribution_columns(mgr)


def _attribution(request: Request) -> dict[str, Any]:
    """``user_id`` / ``submitted_by`` / ``auth_source`` for the row.

    Read off the credential the middleware resolved — session, OAuth or key —
    and never off the payload.  Personhood is
    :func:`zolai.api.rbac.person_identity`, the same predicate the role-request
    queue uses, so "is this an account?" cannot mean two different things.
    An unresolved credential stays the anonymous shape it always had: three
    NULLs, no ``auth_source`` to mislead an editor.
    """
    record = getattr(request.state, "api_key", None)
    record = record if isinstance(record, dict) and record else None
    if record is None:
        return {"user_id": None, "submitted_by": None, "auth_source": None}
    source = str(record.get("auth_source") or "api_key")
    identity = rbac.person_identity(record)
    if identity is not None:
        user_id, username, _role = identity
        return {"user_id": user_id, "submitted_by": username or None, "auth_source": source}
    # A machine credential: attributed so an editor can trace a bad row back to
    # the key that sent it, but with no account to attach it to.
    label = str(record.get("username") or record.get("name") or record.get("key_prefix") or "")
    return {"user_id": None, "submitted_by": label or None, "auth_source": source}


@router.post("/contributions", response_model=ContributionOut)
def submit_contribution(payload: ContributionIn, request: Request) -> ContributionOut:
    """Accept a public dictionary contribution.

    No authentication required. The contribution is stored for editorial review.
    When the caller *does* hold a credential, the row carries who it came from —
    attribution only, never a gate: an anonymous submission is still accepted.
    """
    ensure_contributions_table()
    who = _attribution(request)

    mgr = get_manager()
    created_at = stamp()

    try:
        with mgr.engine.begin() as conn:
            cursor = conn.exec_driver_sql(
                """
                INSERT INTO contributions
                (type, word, field, current_value, suggested_value, context, source, email,
                 status, created_at, user_id, submitted_by, auth_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
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
                    who["user_id"],
                    who["submitted_by"],
                    who["auth_source"],
                ),
            )
            contribution_id = cursor.lastrowid
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="contributions.submit",
            code="contribution_unavailable",
            message="Your contribution could not be recorded. Retry in a few seconds.",
            log_context={"type": payload.type, "word": payload.word[:60]},
        ) from exc

    logger.info(
        "Contribution received: id=%s type=%s word=%s by=%s",
        contribution_id,
        payload.type,
        payload.word,
        who["auth_source"] or "anonymous",
    )

    return ContributionOut(
        id=contribution_id,
        status="pending",
        message="Contribution received and queued for editorial review.",
        attributed_to=who["submitted_by"],
    )


@router.get(
    "/contributions/mine",
    summary="This account's own submissions (newest first)",
    # A credential must be present (strict: 401 anonymous in warn *and*
    # enforce), and it must belong to at least a member.  An API key passes the
    # role test and then fails the person test below — reading "my" rows is
    # meaningless for a credential with no account behind it.
    dependencies=[Depends(rbac.require_role(rbac.ROLE_MEMBER, strict=True))],
)
def list_my_contributions(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Page size (newest first)"),
) -> dict[str, Any]:
    """Rows the signed-in account submitted, and their review status.

    The ``WHERE`` clause uses the ``user_id`` the **session proves** — there is
    no ``user_id`` parameter to pass, so this route cannot be pointed at someone
    else's submissions.  ``email`` is not in the projection at all.
    """
    identity = rbac.person_identity(getattr(request.state, "api_key", None))
    if identity is None:
        raise HTTPException(
            status_code=403,
            detail=api_error(
                "session_required",
                "This lists the contributions your **account** submitted. A key has no account "
                "behind it, so it has no submissions to show — sign in and ask again.",
            ),
        )
    user_id, _username, _role = identity

    ensure_contributions_table()
    mgr = get_manager()
    try:
        with mgr.engine.connect() as conn:
            rows = [
                dict(row._mapping)
                for row in conn.exec_driver_sql(
                    f"SELECT {MINE_COLUMNS} FROM contributions "
                    "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, int(limit)),
                )
            ]
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="contributions.mine",
            code="contributions_unavailable",
            message="Your contributions could not be read. Retry in a few seconds.",
        ) from exc

    return {"items": rows, "count": len(rows)}
