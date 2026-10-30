"""Role-request queue — an account asks for a role, a superadmin decides.

Mounted under ``/api/v1/role-requests`` (registered **before** the catch-all in
:mod:`zolai.api.server`).  Four routes, two very different callers:

===========================================  ===============================
Route                                        Guard
===========================================  ===============================
``POST /api/v1/role-requests``               member role, strict, **session**
``GET  /api/v1/role-requests/mine``          member role, strict, **session**
``GET  /api/v1/role-requests``               ``role:manage`` + superadmin
``POST /api/v1/role-requests/{id}/decision`` ``role:manage`` + superadmin
===========================================  ===============================

**Why a queue and not a self-service endpoint.**  Promoting yourself is exactly
the thing :func:`zolai.api.session_auth.set_user_role` refuses
(``self_role_change``), so a request is *stored* and applied later by somebody
else.  Approval runs through that one writer and adds no new privilege path: if
the writer says no — the last enabled superadmin, a self-decision — the request
stays ``pending`` and the caller reads the reason.  This router contains no
``UPDATE users`` statement, and a test asserts that.

**Why a key cannot ask.**  An API key is a machine credential with no account
behind it (``user_id`` is ``None``); there is no role to change and no person to
audit.  The two member routes therefore require a resolved credential whose
``auth_source`` is ``session``, and answer **403 ``session_required``** with the
sentence that names the fix.  A *delegated* OAuth access token is also refused:
the process holding it is a client, not the human who consented.

Rate limiting: a route-level bucket of :data:`ASKS_PER_MINUTE` per ``user_id``
stops a loop from hammering INSERTs, but the durable anti-flood guard is
``ux_role_requests_pending`` in :mod:`zolai.data.migrations` — **one pending ask
per (account, role)**, enforced by SQLite itself and independent of any
misconfiguration above it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import auth, rbac, session_auth
from .errors import api_error, internal_http_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/role-requests", tags=["roles"])

#: Asks per account per minute.  Cheap to state, and the unique index means a
#: second ask was never going to land anyway.
ASKS_PER_MINUTE = 5
ASK_WINDOW_S = 60.0
#: Same shape as ``agent_router._RUN_BUCKET`` — the house idiom for a per-route
#: budget: in-process, keyed by the *authenticated* identity, and reset by tests
#: rather than shared with the middleware's credential buckets (whose key is a
#: key prefix, and a session has none).
_ASK_BUCKET: dict[str, list[float]] = {}


def _reset_ask_bucket() -> None:
    """Clear the in-process ask bucket (tests + ops)."""
    _ASK_BUCKET.clear()


def _check_ask_budget(user_id: int) -> None:
    """**429** when this account has asked :data:`ASKS_PER_MINUTE` times a minute."""
    key = f"user:{user_id}"
    now = time.monotonic()
    hits = [t for t in _ASK_BUCKET.get(key, []) if now - t < ASK_WINDOW_S]
    if len(hits) >= ASKS_PER_MINUTE:
        retry_after = max(1, int(ASK_WINDOW_S - (now - hits[0])))
        raise HTTPException(
            status_code=429,
            detail=api_error(
                "rate_limited",
                f"Too many role requests from this account. Try again in {retry_after}s.",
                limit=ASKS_PER_MINUTE,
                retry_after_seconds=retry_after,
            ),
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _ASK_BUCKET[key] = hits

#: The two member-tier routes: a credential must be present (strict) and at
#: least a member.  Members are the only people who need to ask.
_MEMBER_GUARD: tuple[Any, ...] = (Depends(rbac.require_role(rbac.ROLE_MEMBER, strict=True)),)

#: The two decision routes, scope **and** role.  ``role:manage`` is what makes a
#: credential a superadmin (:func:`zolai.api.rbac.role_for`), so the pair reads
#: as belt-and-braces — but if a future grant ever put ``role:manage`` on an
#: admin, the role half still closes the queue.
_ADMIN_GUARD: tuple[Any, ...] = (
    Depends(auth.require_scope("role:manage", strict=True)),
    Depends(rbac.require_role(rbac.ROLE_SUPERADMIN, strict=True)),
)


#: Every ``RoleGuardRejected.reason`` the writer can raise, in the words an
#: operator reads, lives in :func:`zolai.api.session_auth.guard_message` —
#: authored there rather than echoed from the exception text (these are
#: invariants this repository wrote, and ``tests/test_api_error_envelope.py``
#: forbids a handler's ``str(exc)`` in a response body regardless of how benign
#: it looks), and shared with ``admin_users_router`` so the two surfaces cannot
#: describe the same violation differently.


class RoleRequestIn(BaseModel):
    """POST /api/v1/role-requests body — the ask, with a reason a human reads."""

    requested_role: str = Field(..., min_length=1, max_length=32, examples=["admin"])
    reason: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Why this role, in the applicant's own words (10–1000 characters)",
        examples=["I moderate the annotation queue and need review access."],
    )


class RoleDecisionIn(BaseModel):
    """POST /api/v1/role-requests/{id}/decision body."""

    decision: str = Field(..., min_length=1, max_length=16, examples=["approve"])
    note: str = Field(default="", max_length=1000, examples=["Reviewed the annotation history."])


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def _record(request: Request) -> dict[str, Any] | None:
    """The resolved credential the middleware published (of any kind)."""
    record = getattr(request.state, "api_key", None)
    return record if isinstance(record, dict) and record else None


def _person(request: Request) -> tuple[int, str, str]:
    """``(user_id, username, role)`` for a **signed-in account**.

    ``require_role`` has already rejected an anonymous caller on these routes;
    this is the narrower question — is the credential a *person*.  Personhood is
    :func:`zolai.api.rbac.person_identity` (shared with the contribution
    read-back); what lives here is only the answer, and it is 403 with an
    instruction rather than a 401 that would read as "your credential is
    broken".
    """
    record = _record(request)
    identity = rbac.person_identity(record)
    if identity is not None:
        return identity
    if record is None:
        raise HTTPException(
            status_code=401,
            detail=api_error(
                "unauthorized",
                "Sign in with an account before asking for a role — no credential was resolved.",
            ),
        )
    raise HTTPException(
        status_code=403,
        detail=api_error(
            "session_required",
            "Role requests come from a signed-in account, not an API key. Sign in and ask "
            "there — a key has no account behind it, so there is no role for it to hold.",
            auth_source=str(record.get("auth_source") or "unknown"),
        ),
    )


def _decider(request: Request) -> tuple[str, str, int | None]:
    """``(actor_label, actor_role, actor_user_id)`` for a queue decision.

    The label is the resolved identity — session username, else key name — never
    a caller-supplied field, so the audit row says who actually clicked.
    """
    record = _record(request) or {}
    label = str(
        record.get("username") or record.get("name") or record.get("key_prefix") or "role-admin"
    )
    raw_user_id = record.get("user_id")
    return label, rbac.role_for(record), int(raw_user_id) if raw_user_id is not None else None


def _ensure_store() -> None:
    """Create ``role_requests`` on first use (idempotent; startup runs it too)."""
    try:
        session_auth.ensure_role_request_tables()
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="role_requests.ensure_tables",
            code="role_requests_unavailable",
            message="Role requests could not be prepared. Retry in a few seconds.",
        ) from exc


def _parse_status(raw: str | None) -> str | None:
    """``None`` / ``all`` → no filter; anything else must be a real status."""
    if raw is None or raw.strip().lower() in ("", "all"):
        return None
    value = raw.strip().lower()
    if value not in session_auth.ROLE_REQUEST_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=api_error(
                "invalid_status",
                f"'{raw}' is not a request status. Use pending, approved, rejected or all.",
                allowed=sorted(session_auth.ROLE_REQUEST_STATUSES) + ["all"],
            ),
        )
    return value


def _page(
    *, user_id: int | None = None, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Read a page, converting a store failure into the house envelope."""
    try:
        return session_auth.list_role_requests(user_id=user_id, status=status, limit=limit)
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="role_requests.list",
            code="role_requests_unavailable",
            message="The role-request queue could not be read. Retry in a few seconds.",
        ) from exc


# ---------------------------------------------------------------------------
# The ask — member session
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    summary="Ask for a role (account holders only)",
    dependencies=_MEMBER_GUARD,
)
def request_role(payload: RoleRequestIn, request: Request) -> dict[str, Any]:
    """Store a pending ask for ``admin`` or ``superadmin``."""
    _ensure_store()
    user_id, username, current_role = _person(request)

    asked = str(payload.requested_role or "").strip().lower()
    if asked not in session_auth.REQUESTABLE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=api_error(
                "role_not_requestable",
                "Every account is created as a member, so that role is never something you ask "
                "for. Request 'admin' or 'superadmin'.",
                requested_role=asked,
                requestable=list(session_auth.REQUESTABLE_ROLES),
            ),
        )
    if rbac.rank_of({"role": current_role}) >= rbac.rank_of({"role": asked}):
        raise HTTPException(
            status_code=409,
            detail=api_error(
                "role_already_held",
                f"Your account already holds '{current_role}', which covers '{asked}'. "
                "There is nothing to approve.",
                role=current_role,
            ),
        )

    _check_ask_budget(user_id)

    try:
        created = session_auth.create_role_request(
            user_id=user_id,
            username=username,
            requested_role=asked,
            reason=payload.reason,
        )
    except session_auth.DuplicateRoleRequest as exc:
        raise HTTPException(
            status_code=409,
            detail=api_error(
                "duplicate_pending",
                f"A request for '{asked}' is already waiting for a decision. Check "
                "GET /api/v1/role-requests/mine before asking again.",
                requested_role=exc.requested_role,
            ),
        ) from exc
    except ValueError as exc:
        # Role validity is checked above, so this is a constraint this code did
        # not expect. The driver's words go to the log, never into the body.
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="role_requests.create",
            code="role_request_unavailable",
            message="The role request could not be recorded. Retry in a few seconds.",
            log_context={"username": username, "requested_role": asked},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="role_requests.create",
            code="role_request_unavailable",
            message="The role request could not be recorded. Retry in a few seconds.",
            log_context={"username": username},
        ) from exc

    logger.info("Role request created: id=%s user=%s role=%s", created["id"], username, asked)
    return {
        "request": created,
        "message": (
            f"Request #{created['id']} for '{asked}' is queued for a superadmin. Watch "
            "GET /api/v1/role-requests/mine for the decision."
        ),
    }


@router.get(
    "/mine",
    summary="This account's own role requests (newest first)",
    dependencies=_MEMBER_GUARD,
)
def list_my_role_requests(
    request: Request,
    status: str | None = Query(None, description="pending | approved | rejected (all when unset)"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """The caller's own requests, filtered by the ``user_id`` the session proves.

    There is no ``user_id`` parameter on purpose: whose rows you may read is not
    a caller-supplied choice.
    """
    _ensure_store()
    user_id, _username, _role = _person(request)
    items = _page(user_id=user_id, status=_parse_status(status), limit=limit)
    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# The queue — superadmin
# ---------------------------------------------------------------------------


@router.get("", summary="The queue (superadmin)", dependencies=_ADMIN_GUARD)
def list_role_request_queue(
    status: str = Query("pending", description="pending | approved | rejected | all"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Everyone's asks, newest first, defaulting to the pending queue."""
    _ensure_store()
    resolved = _parse_status(status)
    items = _page(status=resolved, limit=limit)
    return {"status": resolved or "all", "items": items, "count": len(items)}


@router.post(
    "/{request_id}/decision",
    summary="Approve or reject a request (superadmin)",
    dependencies=_ADMIN_GUARD,
)
def decide_role_request(request_id: int, payload: RoleDecisionIn, request: Request) -> dict[str, Any]:
    """Apply the decision — approval through the one guarded writer, nothing else.

    ``approve`` calls :func:`zolai.api.session_auth.set_user_role` and **only**
    that: whatever it refuses (a self-decision, the last enabled superadmin) is
    answered here with the same code the admin-users surface uses
    (:data:`zolai.api.session_auth.GUARD_STATUS`), and the request stays
    ``pending`` because the status is flipped **after** the role is written.
    """
    _ensure_store()
    decision = str(payload.decision or "").strip().lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail=api_error(
                "invalid_decision",
                f"'{payload.decision}' is not a decision. Send 'approve' or 'reject'.",
            ),
        )
    note = str(payload.note or "").strip()
    if decision == "reject" and not note:
        raise HTTPException(
            status_code=400,
            detail=api_error(
                "note_required",
                "A rejection needs a note — the applicant reads it and decides whether to ask "
                "again, so 'rejected' on its own is not an answer.",
            ),
        )

    label, actor_role, actor_user_id = _decider(request)

    try:
        target = session_auth.get_role_request(request_id)
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="role_requests.get",
            code="role_requests_unavailable",
            message="That request could not be read. Retry in a few seconds.",
            log_context={"request_id": request_id},
        ) from exc
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=api_error(
                "request_not_found",
                f"No role request #{request_id}. List the queue and retry with a real id.",
                request_id=request_id,
            ),
        )
    if target.get("status") != "pending":
        raise HTTPException(
            status_code=409,
            detail=api_error(
                "already_decided",
                f"Request #{request_id} was already {target.get('status')}. Decisions are "
                "single-use, so nothing changed.",
                request_id=request_id,
                status=str(target.get("status")),
            ),
        )

    username = str(target.get("username") or "")
    requested_role = str(target.get("requested_role") or "")
    approved: dict[str, Any] | None = None

    if decision == "approve":
        try:
            approved = session_auth.set_user_role(
                username,
                role=requested_role,
                actor=label,
                actor_role=actor_role,
                actor_user_id=actor_user_id,
                reason=f"role_request #{request_id} approved",
            )
        except session_auth.RoleGuardRejected as exc:
            # The writer refused, so no role changed **and** the request stays
            # pending — the queue row is only flipped once the role is applied.
            # Same reason code, same status and the same sentence the admin-users
            # surface answers, because both are the one writer refusing.
            raise HTTPException(
                status_code=session_auth.GUARD_STATUS.get(exc.reason, 403),
                detail=api_error(
                    exc.reason,
                    (
                        session_auth.guard_message(exc.reason)
                        + f" Request #{request_id} stays pending."
                    ),
                    request_id=request_id,
                ),
            ) from exc
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=api_error(
                    "user_not_found",
                    f"The account '{username}' that asked no longer exists, so request "
                    f"#{request_id} cannot be approved. Reject it instead.",
                    username=username,
                ),
            ) from exc

    try:
        decided = session_auth.decide_role_request(
            request_id, approve=decision == "approve", decided_by=label, note=note or None
        )
    except session_auth.RoleRequestDecided as exc:
        raise HTTPException(
            status_code=409,
            detail=api_error(
                "already_decided",
                f"Request #{request_id} was decided by another superadmin first; the record "
                "shows that decision.",
                request_id=request_id,
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - converted, logged with traceback
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="role_requests.decide",
            code="role_request_decision_unavailable",
            message="That decision could not be recorded. Retry in a few seconds.",
            log_context={"request_id": request_id, "decision": decision},
        ) from exc

    logger.info(
        "Role request decided: id=%s decision=%s user=%s role=%s by=%s",
        request_id,
        decision,
        username,
        requested_role,
        label,
    )
    return {
        "request": decided,
        "user": session_auth.sanitize_user(approved) if approved else None,
        "message": (
            f"Request #{request_id} {decided.get('status')}"
            + (f"; '{username}' is now '{requested_role}'." if decision == "approve" else ".")
        ),
    }
