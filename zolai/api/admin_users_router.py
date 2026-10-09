"""Admin user endpoints — list / create / role / password / revoke sessions.

Mounted under ``/api/v1/admin/users`` (registered **before** the catch-all in
:mod:`zolai.api.server`).  The router carries **both** guards:

- :func:`zolai.api.auth.require_scope` (``user:manage``, ``strict=True``) — a
  presented key without the scope is **403**, an absent key is **401** in
  ``warn`` *and* ``enforce`` (only ``ZOLAI_API_AUTH=off`` bypasses);
- :func:`zolai.api.rbac.require_role` (``admin``, ``strict=True``) — the role
  half of the matrix, so scope alone never substitutes for an admin role.

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


def _detail(error: str, **extra: Any) -> dict[str, Any]:
    return {"error": error, **extra}


def _http_from(exc: Exception, *, username: str | None = None) -> HTTPException:
    """Map service exceptions to the ce04c72 status-code contract."""
    if isinstance(exc, LookupError):
        return HTTPException(
            status_code=404,
            detail=_detail("user_not_found", username=username or str(exc)),
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
    """Create an account. ``role`` defaults to ``member`` when omitted."""
    try:
        user = session_auth.create_user(
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            role=body.role or "member",
            actor=_actor(request),
        )
    except ValueError as exc:
        raise _http_from(exc, username=body.username) from exc
    return _user_out(user)


@router.put("/{username}")
def update_user(username: str, body: UserUpdateIn, request: Request) -> dict[str, Any]:
    """Change ``role`` / ``enabled`` on one account.

    A role change is audit-logged and drops the session cache, so a demotion
    applies on the caller's next request.  Disabling revokes live sessions.
    """
    actor = _actor(request)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(
            status_code=400,
            detail=_detail("empty_update", detail="set at least one of role/enabled"),
        )
    user: dict[str, Any] | None = None
    try:
        if "role" in fields:
            user = session_auth.set_user_role(username, role=str(fields["role"]), actor=actor)
        if "enabled" in fields:
            user = session_auth.set_user_enabled(
                username, enabled=bool(fields["enabled"]), actor=actor
            )
    except (LookupError, ValueError) as exc:
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
