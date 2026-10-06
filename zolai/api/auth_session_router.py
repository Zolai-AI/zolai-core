"""Username + password login / logout — ``POST /api/v1/auth/{login,logout}``.

Session auth sits **beside** API-key auth, never in front of it: the middleware
still resolves an ``X-API-Key`` / ``Authorization: Bearer zolai_sk_*`` first and
only falls through to :mod:`zolai.api.session_auth` for a ``zolai_ss_*`` token
that a key lookup did not already satisfy.  Nothing here changes the key path.

Routes
- ``POST /api/v1/auth/login`` — username + password in, a session token out
  (returned once). Rate limited per IP **and** per username before any argon2
  work; the verify itself runs in a thread pool because argon2id is CPU-bound
  (~200ms per verify here).
- ``POST /api/v1/auth/logout`` — revokes **the presented session only** and
  always answers 200 ``{revoked: bool}``, so it is idempotent and cannot be used
  to probe whether a token exists.

Both routes are in :data:`zolai.api.rbac.PUBLIC_ROUTES`: a login cannot require
a credential.  Under ``ZOLAI_API_AUTH=enforce`` that is the difference between
"sign in" and "impossible".

Failure shape
-------------
An unknown user, a wrong password and a disabled account all answer **401
``{"error": "invalid_credentials"}``** with no reason and no username echo — no
enumeration.  The specific reason reaches the ``data_audit_log`` row and nothing
else.  Unknown-user attempts still pay a dummy argon2 verify (see
:func:`zolai.api.session_auth.verify_login`) so the timing does not split.

Kill switch
-----------
``ZOLAI_AUTH_SESSIONS=off`` makes both routes **404** with
``{error: "session_auth_disabled"}`` and the middleware ignore ``zolai_ss_*``
tokens entirely — the full rollback posture, leaving the tables inert in place.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from . import auth, session_auth
from .auth_middleware import client_ip, extract_bearer, limiter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    """POST /api/v1/auth/login body."""

    username: str = Field(min_length=1, max_length=64, examples=["founder"])
    password: str = Field(min_length=1, max_length=session_auth.MAX_PASSWORD_LENGTH)

    @field_validator("username")
    @classmethod
    def _username_is_valid(cls, value: str) -> str:
        try:
            return session_auth.normalize_username(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("password")
    @classmethod
    def _password_length(cls, value: str) -> str:
        # Length floor only at the HTTP boundary is **not** enforced here: a
        # too-short password can only match an account that was created with
        # one, which the CLI already refuses.  Keeping it out of the validator
        # means a short password is rejected as invalid_credentials (401, one
        # shape) rather than as a distinguishable 422.
        return value


def _disabled_error() -> HTTPException:
    """The stable 404 for ``ZOLAI_AUTH_SESSIONS=off``."""
    return HTTPException(
        status_code=404,
        detail={"error": "session_auth_disabled"},
    )


def _require_sessions() -> None:
    if not session_auth.sessions_enabled():
        raise _disabled_error()


@router.post("/login")
async def login(body: LoginIn, request: Request) -> dict[str, Any]:
    """Exchange a username + password for a session token (shown once)."""
    _require_sessions()

    ip = client_ip(request.scope)
    ip_limit = session_auth.login_rate_limit_rpm()
    user_limit = session_auth.login_rate_limit_user_rpm()

    # Rate limit **before** any argon2 work: two buckets, so a password spray
    # from one host stops at ip_limit/min and a distributed one spraying a
    # single account stops at user_limit/min.
    for bucket, capacity in (
        (f"login:ip:{ip}", ip_limit),
        (f"login:user:{body.username}", user_limit),
    ):
        allowed, remaining, reset, retry_after = await run_in_threadpool(
            limiter.hit, bucket, capacity
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limited",
                    "scope": "login",
                    "limit_rpm": capacity,
                    "retry_after_s": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(capacity),
                    "X-RateLimit-Remaining": str(max(0, remaining)),
                    "X-RateLimit-Reset": str(int(reset) if reset > 0 else 0),
                },
            )

    try:
        # argon2id verify (~200ms) is CPU-bound — never on the event loop.
        return await run_in_threadpool(session_auth.login, body.username, body.password, ip=ip)
    except session_auth.LoginRejected as rejected:
        # One shape for all three rejections; the reason is audited, never sent.
        session_auth.record_audit(
            table="sessions",
            row_id=0,
            field="login_rejected",
            old_value=None,
            new_value=None,
            reason=f"login rejected ({rejected.reason}) for username from {ip}",
        )
        auth.log_auth_failure(f"login_{rejected.reason}", "/api/v1/auth/login", None)
        raise HTTPException(
            status_code=401, detail={"error": "invalid_credentials"}
        ) from None


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    """Revoke the presented session. Always 200 — idempotent by design."""
    _require_sessions()

    # Only the Bearer token, and only when it is a session token: an API key
    # presented to logout has no session to revoke, and must not revoke one.
    token = extract_bearer(request.scope)
    if not session_auth.is_session_token(token):
        return {"revoked": False}

    revoked = await run_in_threadpool(session_auth.revoke_session, token, actor="api")
    return {"revoked": bool(revoked)}
