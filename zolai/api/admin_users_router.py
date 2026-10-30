"""Admin user endpoints — list / create / role / password / revoke sessions.

Mounted under ``/api/v1/admin/users`` (registered **before** the catch-all in
:mod:`zolai.api.server`).  The router carries **both** guards:

- :func:`zolai.api.auth.require_scope` (``user:manage``, ``strict=True``) — a
  presented key without the scope is **403**, an absent key is **401** in
  ``warn`` *and* ``enforce`` (only ``ZOLAI_API_AUTH=off`` bypasses);
- :func:`zolai.api.rbac.require_role` (``admin``, ``strict=True``) — the role
  half of the matrix, so scope alone never substitutes for an admin role.

**What an admin cannot do here.**  Creating an account, resetting a password and
disabling a *member* are ``user:manage``.  Changing a role is not: it needs
``role:manage`` (superadmin), and so does creating an account that starts above
member or touching an admin account at all.  Those rules are enforced in the
single writer :func:`zolai.api.session_auth.set_user_role`, which this router
only feeds with the caller's own resolved role — see
``docs/admin/permissions.md`` §5.

Every response is :func:`zolai.api.session_auth.sanitize_user`-shaped: the
argon2 ``password_hash`` never leaves the service, and no token is ever
returned.  The acting identity comes from ``request.state.session`` (a
username session) or ``request.state.api_key`` (a key's ``name``) — the
middleware publishes those, never ``state.auth``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, rbac, session_auth

router = APIRouter(
    prefix="/api/v1/admin/users",
    tags=["admin-users"],
    dependencies=[
        Depends(auth.require_scope("user:manage", strict=True)),
        Depends(rbac.require_role("admin", strict=True)),
    ],
)


class UserCreateIn(BaseModel):
    """POST /api/v1/admin/users body — no default password, no sign-up path."""

    username: str = Field(min_length=1, max_length=64, examples=["founder"])
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, examples=["member"])


class UserUpdateIn(BaseModel):
    """PUT /api/v1/admin/users/{username} body — every field optional."""

    role: str | None = Field(default=None, examples=["admin"])
    enabled: bool | None = None


class UserPasswordIn(BaseModel):
    """PUT /api/v1/admin/users/{username}/password body."""

    password: str = Field(min_length=8, max_length=256)


def _actor(request: Request) -> str:
    """Who is calling: session username → API-key name → a stable fallback."""
    session = getattr(request.state, "session", None)
    if isinstance(session, dict) and session.get("username"):
        return str(session["username"])
    record = getattr(request.state, "api_key", None)
    if isinstance(record, dict) and record.get("name"):
        return str(record["name"])
    return "admin-api"


def _credential(request: Request) -> tuple[str, int | None]:
    """``(role, user_id)`` of the **resolved** credential, from one record.

    Both come from ``state["api_key"]``, which the middleware fills for a
    session, an OAuth token *and* a raw key — so a caller never has to know
    which kind it holds.  ``user_id`` is ``None`` for a key, and that is what
    makes the self-guards inert for a key: **a key is not a person**, so it
    cannot be "the same account" as the one being edited.

    The role is :func:`zolai.api.rbac.role_for`, the same resolver every route
    uses — never a re-derivation here, or a guard could disagree with a route.
    """
    record = getattr(request.state, "api_key", None)
    record = record if isinstance(record, dict) else None
    user_id = record.get("user_id") if record else None
    return rbac.role_for(record), int(user_id) if user_id is not None else None


def _detail(error: str, **extra: Any) -> dict[str, Any]:
    return {"error": error, **extra}


#: Guard reason → HTTP status.  Defined once next to the exception itself
#: (:data:`zolai.api.session_auth.GUARD_STATUS`), because the role-request queue
#: refuses the same violations and must answer the same code.
_GUARD_STATUS: dict[str, int] = session_auth.GUARD_STATUS


def _http_from(exc: Exception, *, username: str | None = None) -> HTTPException:
    """Map service exceptions to the ce04c72 status-code contract."""
    if isinstance(exc, LookupError):
        return HTTPException(
            status_code=404,
            detail=_detail("user_not_found", username=username or str(exc)),
        )
    # Checked before ValueError on purpose: RoleGuardRejected *is* a ValueError,
    # and a 400 here would tell the operator the request was malformed when the
    # real answer is "you are not allowed to do that".
    if isinstance(exc, session_auth.RoleGuardRejected):
        status = _GUARD_STATUS.get(exc.reason, 403)
        return HTTPException(
            status_code=status,
            # The body comes from the shared table, never from str(exc): the
            # guard's own sentence names the account, its role and the actor,
            # and an error body is readable by whoever holds the credential.
            detail=_detail(exc.reason, detail=session_auth.guard_message(exc.reason)),
        )
    if isinstance(exc, ValueError):
        message = str(exc)
        if "already exists" in message:
            return HTTPException(status_code=409, detail=_detail("username_taken"))
        if "unknown role" in message:
            return HTTPException(status_code=400, detail=_detail("invalid_role", detail=message))
        return HTTPException(status_code=400, detail=_detail("invalid_input", detail=message))
    raise exc


def _user_out(user: dict[str, Any]) -> dict[str, Any]:
    return {"user": session_auth.sanitize_user(user)}


@router.get("")
def list_users() -> dict[str, Any]:
    """Every account (roles/enabled/timestamps) — never a password hash."""
    items = session_auth.sanitize_users(session_auth.list_users())
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
def create_user(body: UserCreateIn, request: Request) -> dict[str, Any]:
    """Create an account. ``role`` defaults to ``member`` when omitted.

    Creating a member is ``user:manage``.  Creating an account that *starts*
    as admin or superadmin is the same power as granting that role later, so it
    needs the caller to hold superadmin — checked here because ``create_user``
    in the service has no actor role to consult (an INSERT is not a role
    *change*, and the writer guard that governs changes cannot see it).
    """
    actor = _actor(request)
    role = (body.role or "member").strip().lower()
    actor_role, _actor_uid = _credential(request)
    # A nonsense role is a bad request whoever asks, so it is answered before
    # the rank question — 403 for `role="wizard"` would tell an admin they lack
    # permission to do something that is not possible for anybody.
    if role not in session_auth.VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=_detail(
                "invalid_role",
                detail=f"unknown role {role!r}; expected one of {sorted(session_auth.VALID_ROLES)}",
            ),
        )
    if role != "member" and actor_role != rbac.ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail=_detail(
                "role_change_requires_superadmin",
                detail=(
                    f"creating an account as '{role}' needs role:manage (superadmin); "
                    f"this credential is '{actor_role}'. Create it as a member and "
                    "let a superadmin promote it."
                ),
                role=role,
            ),
        )
    try:
        user = session_auth.create_user(
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            role=role,
            actor=actor,
        )
    except ValueError as exc:
        raise _http_from(exc, username=body.username) from exc
    return _user_out(user)


@router.put("/{username}")
def update_user(username: str, body: UserUpdateIn, request: Request) -> dict[str, Any]:
    """Change ``role`` / ``enabled`` on one account.

    The guards live in the **writer** (:func:`zolai.api.session_auth.set_user_role`
    / :func:`~zolai.api.session_auth.set_user_enabled`), not in a decorator, so
    they cannot be skipped by a future route: only a superadmin changes a role,
    nobody changes their own, no actor below superadmin touches an admin account,
    and the last enabled superadmin is neither demoted nor disabled.  Each
    refusal is a 403 (or 409 for the last superadmin) naming its reason — never
    a silently disabled control.

    A role change is audit-logged and drops the session cache, so a demotion
    applies on the caller's next request.  Disabling revokes live sessions.
    """
    actor = _actor(request)
    actor_role, actor_user_id = _credential(request)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(
            status_code=400,
            detail=_detail("empty_update", detail="set at least one of role/enabled"),
        )
    user: dict[str, Any] | None = None
    try:
        if "role" in fields:
            user = session_auth.set_user_role(
                username,
                role=str(fields["role"]),
                actor=actor,
                actor_role=actor_role,
                actor_user_id=actor_user_id,
            )
        if "enabled" in fields:
            user = session_auth.set_user_enabled(
                username,
                enabled=bool(fields["enabled"]),
                actor=actor,
                actor_role=actor_role,
                actor_user_id=actor_user_id,
            )
    except (LookupError, ValueError) as exc:
        # ValueError catches RoleGuardRejected too; _http_from tests for the
        # guard first and answers 403/409 instead of a 400.
        raise _http_from(exc, username=username) from exc
    if user is None:  # pragma: no cover - defensive (fields validated above)
        raise HTTPException(status_code=404, detail=_detail("user_not_found", username=username))
    return _user_out(user)


@router.put("/{username}/password")
def change_password(username: str, body: UserPasswordIn, request: Request) -> dict[str, Any]:
    """Replace a password — every live session for that user is revoked."""
    try:
        user = session_auth.change_password(
            username, body.password, actor=_actor(request)
        )
    except (LookupError, ValueError) as exc:
        raise _http_from(exc, username=username) from exc
    return _user_out(user)


@router.post("/{username}/revoke-sessions")
def revoke_sessions(username: str, request: Request) -> dict[str, Any]:
    """Logout-everywhere for one account (count only — never a token)."""
    try:
        result = session_auth.revoke_user_sessions(username, actor=_actor(request))
    except LookupError as exc:
        raise _http_from(exc, username=username) from exc
    return {"username": result["username"], "revoked": int(result["revoked"])}
