"""Public/member/admin RBAC (P2) — roles, ``PUBLIC_ROUTES``, route classes.

Three roles, derived per request from the resolved API-key record (a future
session token slots into :func:`role_for` without touching call sites):

- ``anonymous`` — no key (or a key that did not resolve)
- ``member``    — a valid key without identity/settings power
- ``admin``     — a key granting ``apikey:manage`` / ``settings:write`` /
  ``user:manage`` / ``role:manage`` / ``*``

``PUBLIC_ROUTES`` is the **single source consulted by both**
:func:`zolai.api.auth_middleware.ApiKeyMiddleware` **and**
:func:`zolai.api.auth.require_scope` — that is what guarantees
``ZOLAI_API_AUTH=enforce`` never 401s the public dictionary/search/word reads
or ``POST /api/v1/assistant/chat``.

Route classes (also the declaration table the completeness guard walks):

- **public**  — anonymous OK, in *any* mode
- **member**  — needs a key (401 anon in enforce; dual-accept in warn)
- **admin**   — strict (401 anon in warn *and* enforce) + scope-gated
- **unclassified** — a bug: a new route joined the surface without declaring
  which class it belongs to (``tests/test_rbac_public_matrix.py`` fails).
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from . import auth

# ── Roles ────────────────────────────────────────────────────────────────────

ROLE_ANONYMOUS = "anonymous"
ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"

#: Ordered least → greatest privilege.
ROLES: tuple[str, ...] = (ROLE_ANONYMOUS, ROLE_MEMBER, ROLE_ADMIN)
_RANK = {name: idx for idx, name in enumerate(ROLES)}

#: Scopes whose holder is treated as an admin (docs/admin/permissions.md §3).
ADMIN_SCOPES: frozenset[str] = frozenset(
    {"*", "apikey:manage", "settings:write", "user:manage", "role:manage"}
)


def role_for(record: dict[str, Any] | None) -> str:
    """Role for a resolved key record (``None`` ⇒ anonymous)."""
    if not record:
        return ROLE_ANONYMOUS
    scopes = list(record.get("scopes") or [])
    if any(s in ADMIN_SCOPES for s in scopes):
        return ROLE_ADMIN
    return ROLE_MEMBER


def has_role(record: dict[str, Any] | None, minimum: str) -> bool:
    """Whether ``record`` meets the ``minimum`` role rank."""
    return _RANK[role_for(record)] >= _RANK[minimum]


# ── Public routes ────────────────────────────────────────────────────────────
#
# Exact entries: ``(method, path)`` — method ``None`` = every method.


def _norm(method: str | None) -> str | None:
    return method.upper() if method else None


PUBLIC_ROUTES: tuple[tuple[str | None, str], ...] = (
    (None, "/health"),
    (None, "/metrics"),
    (None, "/api/v1/health"),
    # Identity probe — never 401s, so Studio can gate its own UI.
    ("GET", "/api/v1/auth/me"),
    # Username + password sign-in/out. Public by necessity: a login cannot
    # require the credential it is asking for, so under ``enforce`` these two
    # must stay open or sign-in becomes impossible (chicken-and-egg). Both are
    # rate limited per IP **and** per username, and 404 when
    # ``ZOLAI_AUTH_SESSIONS=off``.
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    # Public assistant (P4) — anonymous chat stays open under enforce.
    ("POST", "/api/v1/assistant/chat"),
    # Anonymous Studio reads (plan §B table).
    ("GET", "/api/v1/search"),
    ("POST", "/api/v1/search"),
    ("POST", "/api/v1/rag"),
    ("GET", "/api/v1/foundation/stats"),
    ("GET", "/api/v1/knowledge/version"),
    ("GET", "/api/v1/knowledge/statistics"),
)

#: Prefix entries: ``(method, prefix)`` — ``/api/v1/word`` covers every
#: sub-resource (``/forms``, ``/evidence``, ``/related``, …).
PUBLIC_PREFIXES: tuple[tuple[str | None, str], ...] = (
    ("GET", "/api/v1/word"),
    ("GET", "/api/v1/lexicon"),
    ("POST", "/api/v1/analyze"),
)

#: Declared authed — everything below needs a key (member class unless admin).
MEMBER_PREFIXES: tuple[str, ...] = (
    "/api/v1/agent",
    "/api/v1/assistant",
    "/api/v1/records",
    "/api/v1/audit",
    "/api/v1/review",
    "/api/v1/linguistics",
    "/api/v1/predictions",
    "/api/v1/foundation",
    "/api/v1/auth",
)

#: Strict admin surface (401 anon in warn *and* enforce + scope-gated).
ADMIN_PREFIXES: tuple[str, ...] = ("/api/v1/admin",)

#: Paths under the public assistant that get the stricter anonymous chat bucket.
PUBLIC_CHAT_PATHS: tuple[str, ...] = ("/api/v1/assistant/chat",)


def _prefix_match(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def is_public_path(method: str | None, path: str) -> bool:
    """True when ``(method, path)`` may be served without a key, in any mode.

    Both the middleware and ``require_scope`` consult this — it is the single
    definition of "anonymous is allowed here".
    """
    if not path:
        return False
    norm = _norm(method)
    for entry_method, entry in PUBLIC_ROUTES:
        if entry_method is None or entry_method == norm:
            if path == entry:
                return True
    for entry_method, entry in PUBLIC_PREFIXES:
        if entry_method is None or entry_method == norm:
            if _prefix_match(path, entry):
                return True
    return False


def is_public_chat_path(method: str | None, path: str) -> bool:
    """Whether this request gets the stricter anonymous chat bucket."""
    norm = _norm(method)
    for entry in PUBLIC_CHAT_PATHS:
        if norm in (None, "POST") and path == entry:
            return True
    return False


def is_admin_path(path: str) -> bool:
    """True for the strict admin surface (``/api/v1/admin/...``)."""
    return any(path == p or path.startswith(p + "/") for p in ADMIN_PREFIXES)


def classify_route(method: str | None, path: str) -> str:
    """Route class: ``admin`` | ``public`` | ``member`` | ``unclassified``."""
    if is_admin_path(path):
        return "admin"
    if is_public_path(method, path):
        return "public"
    if any(path == p or path.startswith(p + "/") for p in MEMBER_PREFIXES):
        return "member"
    return "unclassified"


# ── require_role ─────────────────────────────────────────────────────────────


def require_role(minimum: str, *, strict: bool = False) -> Callable[..., dict[str, Any]]:
    """Route dependency enforcing a minimum role.

    ``strict=True`` (admin/agent surfaces): an anonymous caller is **401** in
    both ``warn`` and ``enforce`` — only ``ZOLAI_API_AUTH=off`` bypasses.
    ``strict=False`` (member surfaces): 401 in ``enforce``, dual-accept in
    ``warn`` (zero regression on today's posture).
    """
    if minimum not in _RANK:
        raise ValueError(f"unknown role {minimum!r}; expected one of {ROLES}")

    def dependency(request: Request) -> dict[str, Any]:
        # Public routes are public for everyone — role checks never apply.
        if is_public_path(request.method, request.url.path):
            return {}
        record: dict[str, Any] | None = getattr(request.state, "api_key", None)
        role = role_for(record)
        if role == ROLE_ANONYMOUS:
            mode = auth.api_auth_mode()
            if mode == "enforce" or (strict and mode != "off"):
                reason = getattr(request.state, "api_key_error", None) or "missing_api_key"
                raise HTTPException(
                    status_code=401,
                    detail={"error": "unauthorized", "reason": reason},
                )
            return {}
        if not has_role(record, minimum):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden",
                    "required_role": minimum,
                    "role": role,
                    "reason": "insufficient_role",
                },
            )
        return record

    return dependency


# ── GET /api/v1/auth/me ──────────────────────────────────────────────────────

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@auth_router.get("/me")
def auth_me(request: Request) -> dict[str, Any]:
    """Who am I?  Public, never 401 — powers Studio role gating.

    The original four keys (``role``, ``key_prefix``, ``scopes``, ``mode``) are
    unchanged — additive only.  When the caller authenticated with a username
    session (``Authorization: Bearer zolai_ss_*``) the response also carries
    ``auth_source="session"`` with ``username`` / ``display_name`` / ``user_id``
    / ``expires_at``; ``key_prefix`` stays ``None`` because a session has no key
    prefix (D7).  With ``ZOLAI_AUTH_SESSIONS=off`` the response is the original
    four keys plus ``auth_source`` set to ``"api_key"``/``"anonymous"``.
    """
    record: dict[str, Any] | None = getattr(request.state, "api_key", None)
    is_session = bool(record) and record.get("auth_source") == "session"
    body: dict[str, Any] = {
        "role": role_for(record),
        "key_prefix": (record or {}).get("key_prefix"),
        "scopes": list((record or {}).get("scopes") or []),
        "mode": auth.api_auth_mode(),
        "auth_source": "session" if is_session else ("api_key" if record else "anonymous"),
    }
    if is_session:
        body["username"] = record.get("username")
        body["display_name"] = record.get("display_name")
        body["user_id"] = record.get("user_id")
        body["expires_at"] = record.get("expires_at")
    return body
