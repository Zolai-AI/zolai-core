"""OAuth 2.1 authorization server for the MCP endpoint (W6).

An **authorization layer in front of the existing identity** — there is no
second user system here:

- credentials are checked by :func:`zolai.api.session_auth.verify_login`
  (argon2id, one failure shape, timing-equalized);
- an issued access token is resolved to the *same* auth-record shape the
  middleware already publishes for API keys and ``zolai_ss_`` sessions, so
  :func:`zolai.api.rbac.role_for` and
  :func:`zolai.agent.tools.registry.allow_list_for_record` work unchanged;
- OAuth **scopes are a subset of the frozen 33-action vocabulary** and can
  only ever *narrow* what the user's role allows
  (:func:`granted_scopes` intersects a requested grant with
  :func:`zolai.api.session_auth.role_scopes`).

Storage is three additive tables (``oauth_clients``,
``oauth_authorization_codes``, ``oauth_tokens`` — see
:func:`zolai.data.migrations.create_oauth_tables`), created ``IF NOT EXISTS``
with 0 DROP/RENAME/ALTER-of-existing.

Token handling mirrors the existing ``zolai_ss_`` convention exactly:
plaintext is minted with ``secrets.token_urlsafe``, stored **only** as its
SHA-256 hex digest, and returned once in the single issuance response.
There is no signing key and no JWT: tokens are opaque random strings, so
nothing secret lives in the repository — and PKCE (RFC 7636) is what binds
the authorization code to the client, ``S256`` only.

Discovery documents (RFC 8414 authorization-server metadata, RFC 9728
protected-resource metadata) are published **unconditionally** — they are the
thing ChatGPT/Gemini fail to find today, and they need no challenge:

- ``GET /.well-known/oauth-authorization-server`` (+ ``/mcp``-relative and
  ``openid-configuration`` aliases)
- ``GET /.well-known/oauth-protected-resource`` (+ path variants)

The 401 challenge is **opt-in** via ``ZOLAI_MCP_OAUTH_REQUIRED`` (default
off), so the five clients that connect keyless today (Claude, Cursor,
OpenCode, VS Code, Gemini CLI) keep working byte-for-byte:

- **default**: no credential → served anonymously as before (200, public
  tool set); a valid ``Authorization: Bearer <oauth access token>`` is
  accepted and resolved through ``rbac.role_for``;
- **strict** (``ZOLAI_MCP_OAUTH_REQUIRED=1|true|on|yes``): no valid
  credential on the MCP surface → **401** with
  ``WWW-Authenticate: Bearer resource_metadata="<absolute URL>"``.

OAuth covers **the MCP surface only** (``/mcp`` and ``/api/v1/mcp``), never
REST ``/api/v1/*`` — REST keeps its API-key/session auth untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

# ── Token / identifier formats ───────────────────────────────────────────────
#
# Every prefix doubles as a discriminator (the middleware must never send an
# ``zolai_at_*`` token down the API-key or session path) and, like
# ``zolai_sk_`` / ``zolai_ss_``, doubles as the display prefix in logs —
# never enough of the secret to reconstruct it.

ACCESS_TOKEN_PREFIX = "zolai_at_"
REFRESH_TOKEN_PREFIX = "zolai_rt_"
AUTH_CODE_PREFIX = "zolai_ac_"
CLIENT_ID_PREFIX = "zolai_cid_"
CLIENT_SECRET_PREFIX = "zolai_cs_"

#: ``ZOLAI_MCP_OAUTH_REQUIRED`` values that turn the 401 challenge on.
Truthy = frozenset({"1", "true", "on", "yes"})

#: Token / code lifetimes (seconds) when the env override is unset/invalid.
DEFAULT_ACCESS_TTL_S = 3600
DEFAULT_REFRESH_TTL_S = 30 * 24 * 3600
DEFAULT_CODE_TTL_S = 120

#: RFC 7636 §4.1 — ``code_verifier`` length bounds and unreserved charset.
PKCE_VERIFIER_MIN = 43
PKCE_VERIFIER_MAX = 128
PKCE_VERIFIER_CHARSET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)

#: MCP surface paths — the **only** paths the OAuth challenge covers.
MCP_PATHS: frozenset[str] = frozenset({"/mcp", "/api/v1/mcp"})


# ── Small helpers ────────────────────────────────────────────────────────────


def hash_token(token: str) -> str:
    """SHA-256 hex digest — the only form of a token/code stored at rest.

    Byte-identical to :func:`zolai.api.session_auth.hash_token` (same
    convention, deliberately re-declared so this module stays importable
    without pulling the identity layer).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stamp(moment: Any = None) -> str:
    """UTC ``YYYY-MM-DD HH:MM:SS`` — the format every table here uses."""
    from datetime import datetime, timezone

    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def _env_seconds(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(30, value)


def access_ttl_seconds() -> int:
    """Access-token lifetime (``ZOLAI_OAUTH_ACCESS_TTL_S``, default 3600)."""
    return _env_seconds("ZOLAI_OAUTH_ACCESS_TTL_S", DEFAULT_ACCESS_TTL_S)


def refresh_ttl_seconds() -> int:
    """Refresh-token lifetime (``ZOLAI_OAUTH_REFRESH_TTL_S``, default 30 d)."""
    return _env_seconds("ZOLAI_OAUTH_REFRESH_TTL_S", DEFAULT_REFRESH_TTL_S)


def code_ttl_seconds() -> int:
    """Authorization-code lifetime (``ZOLAI_OAUTH_CODE_TTL_S``, default 120 s)."""
    return _env_seconds("ZOLAI_OAUTH_CODE_TTL_S", DEFAULT_CODE_TTL_S)


# ── The flag rule ────────────────────────────────────────────────────────────


def mcp_oauth_required() -> bool:
    """Strict mode: challenge credential-less MCP calls (``ZOLAI_MCP_OAUTH_REQUIRED``).

    **Default off** — unset/blank/anything-but-truthy keeps today's behaviour
    (keyless MCP callers are served anonymously), so the five clients that
    work today cannot be broken by upgrading to this code. Read live at call
    time, same pattern as :func:`zolai.api.auth.api_auth_mode`, so an operator
    can flip it per-shell without a restart.
    """
    raw = os.environ.get("ZOLAI_MCP_OAUTH_REQUIRED", "")
    return raw.strip().lower() in Truthy


def is_mcp_path(path: str | None) -> bool:
    """True for the two MCP transport paths (bare alias + versioned)."""
    return bool(path) and path in MCP_PATHS


# ── Absolute-URL derivation (metadata + challenge must be absolute) ──────────


def public_origin(scope: Any) -> str:
    """Absolute origin (``scheme://host``) a client outside can reach us on.

    Resolution order:

    1. ``ZOLAI_PUBLIC_ORIGIN`` — operator override (honoured verbatim);
    2. ``X-Forwarded-Proto`` / ``X-Forwarded-Ssl`` / ``Cf-Visitor`` (the
       scheme actually seen by the edge — behind the Cloudflare tunnel +
       nginx the app itself only ever sees ``http``);
    3. the ASGI ``scheme``.
    Host comes from ``X-Forwarded-Host``, else ``Host``, else the ASGI
    server tuple. No path — origins are what RFC 8414/9728 build on.
    """
    configured = str(os.environ.get("ZOLAI_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    if configured:
        return configured
    headers: dict[bytes, bytes] = {}
    for name, value in scope.get("headers") or []:
        headers.setdefault(name.lower(), value)

    def _h(name: bytes) -> str:
        raw = headers.get(name, b"").decode("latin-1").strip()
        return raw.split(",")[0].strip()

    proto = _h(b"x-forwarded-proto")
    if not proto and _h(b"x-forwarded-ssl").lower() == "on":
        proto = "https"
    if not proto:
        visitor = _h(b"cf-visitor")  # {"scheme":"https"} as sent by Cloudflare
        if visitor.startswith("{") and '"scheme"' in visitor:
            try:
                proto = json.loads(visitor).get("scheme") or ""
            except ValueError:
                proto = ""
    if not proto:
        proto = str(scope.get("scheme") or "http")

    host = _h(b"x-forwarded-host") or _h(b"host")
    if not host:
        server = scope.get("server")
        if server and server[0]:
            host = server[0]
            if server[1] and server[1] not in (80, 443):
                host = f"{host}:{server[1]}"
    return f"{proto}://{host}" if host else f"{proto}://localhost"


def resource_url(origin: str) -> str:
    """The protected resource: the MCP endpoint on ``origin``."""
    return origin.rstrip("/") + "/mcp"


# ── Scope vocabulary (frozen, narrowing only) ────────────────────────────────


def grantable_scopes() -> list[str]:
    """Scopes a grant may carry — a strict subset of the frozen 33 actions.

    The union of the existing role scope sets (session_auth), i.e. exactly
    what :func:`zolai.api.session_auth.role_scopes` can ever hand out. The
    33-action vocabulary in :mod:`zolai.api.auth` is **not** edited by this
    module; an unknown scope is rejected at the endpoint, not silently added.
    """
    from ..api import session_auth

    return sorted({*session_auth.MEMBER_SCOPES, *session_auth.ADMIN_EXTRA_SCOPES})


def granted_scopes(user_role: str, requested: list[str] | None) -> list[str]:
    """Narrow a requested grant to the role's scopes — **can only narrow**.

    ``requested`` ``None``/empty means "everything the role allows"; anything
    asked for outside the role's set is dropped, never widened. The result is
    what lands in the token row and therefore what :func:`rbac.role_for`
    sees later — an admin granted only member scopes resolves to ``member``
    (26 tools, not 31).
    """
    from ..api import session_auth

    role_scopes = set(session_auth.role_scopes(user_role))
    if not requested:
        return sorted(role_scopes)
    wanted = [str(s).strip() for s in requested if str(s).strip()]
    return sorted(scope for scope in wanted if scope in role_scopes)


# ── Discovery documents (RFC 8414 / RFC 9728) ───────────────────────────────


def authorization_server_metadata(origin: str) -> dict[str, Any]:
    """RFC 8414 authorization-server metadata for this deployment.

    Every endpoint URL is **absolute** and points at a route that exists in
    :mod:`zolai.api.oauth_router`. ``code_challenge_methods_supported`` lists
    ``S256`` only — OAuth 2.1 forbids ``plain`` and the endpoints enforce it.
    """
    base = origin.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "none",
        ],
        "scopes_supported": grantable_scopes(),
        "require_pushed_authorization_requests": False,
    }


def protected_resource_metadata(origin: str) -> dict[str, Any]:
    """RFC 9728 protected-resource metadata for the MCP resource.

    ``resource`` is the MCP endpoint itself (``<origin>/mcp``) — the URL a
    ChatGPT/Gemini operator enters — and ``authorization_servers`` points back
    at this same host's RFC 8414 document.
    """
    base = origin.rstrip("/")
    return {
        "resource": resource_url(origin),
        "resource_name": "Zolai MCP endpoint",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": grantable_scopes(),
    }


def www_authenticate(scope: Any, *, error: str | None = None) -> str:
    """A well-formed ``WWW-Authenticate`` challenge for ``scope``'s origin.

    The ``resource_metadata`` attribute (MCP's documented discovery hook) is
    always an **absolute** URL of the RFC 9728 document served at
    ``/.well-known/oauth-protected-resource``. ``error``/``error_description``
    follow RFC 6750 and are fixed literals here — never request-derived — so
    header injection is impossible.
    """
    value = f'Bearer resource_metadata="{public_origin(scope)}/.well-known/oauth-protected-resource"'
    if error:
        value += f', error="{error}"'
        if error == "invalid_token":
            value += ', error_description="The access token is invalid or expired"'
    return value


# ── Storage (additive tables; monkeypatch seam for tests) ────────────────────


def _get_manager() -> Any:
    """Resolve the DatabaseManager singleton (late import avoids cycles)."""
    from ..data.database import get_manager

    return get_manager()


def ensure_oauth_tables() -> dict[str, Any]:
    """Idempotently create ``oauth_clients`` / codes / tokens (first use)."""
    from ..data.migrations import create_oauth_tables

    return create_oauth_tables(_get_manager())


def _now_plus(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    return _stamp(datetime.now(timezone.utc) + timedelta(seconds=seconds))
