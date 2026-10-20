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

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any

from sqlalchemy import text

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


# ---------------------------------------------------------------------------
# Dynamic client registration (RFC 7591) — ``oauth_clients`` rows
# ---------------------------------------------------------------------------

#: Redirect-URI policy: ``https`` anywhere, ``http`` **only** on loopback
#: (RFC 8252 §7.3 — native apps and local dev servers). An authorization code
#: is worth exactly the URI it is delivered to, so an arbitrary ``http://``
#: receiver (cleartext, any host) is refused at registration time.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

#: Bounds on registration metadata (DoS hygiene for an open endpoint).
MAX_REDIRECT_URIS = 10
MAX_URI_LENGTH = 2000
MAX_CLIENT_NAME_LENGTH = 200

#: Grant/response types this server implements.
SUPPORTED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})
SUPPORTED_RESPONSE_TYPES = frozenset({"code"})
SUPPORTED_AUTH_METHODS = frozenset(
    {"client_secret_post", "client_secret_basic", "none"}
)


class ClientMetadataError(ValueError):
    """Invalid RFC 7591 ``client_metadata`` (maps to ``invalid_client_metadata``)."""


def validate_redirect_uris(uris: Any) -> list[str]:
    """Validate + normalize ``redirect_uris`` (https, or http on loopback).

    Raises:
        ClientMetadataError: not a list, empty, too many, unparseable, a
        fragment/userinfo-carrying URI, plain ``http`` off-loopback, or a
        non-http(s) scheme.
    """
    from urllib.parse import urlsplit

    if not isinstance(uris, (list, tuple)) or not uris:
        raise ClientMetadataError("redirect_uris must be a non-empty array")
    if len(uris) > MAX_REDIRECT_URIS:
        raise ClientMetadataError(f"at most {MAX_REDIRECT_URIS} redirect_uris")
    cleaned: list[str] = []
    for raw in uris:
        uri = str(raw or "").strip()
        if not uri or len(uri) > MAX_URI_LENGTH:
            raise ClientMetadataError("each redirect_uri must be a bounded non-empty URI")
        try:
            parts = urlsplit(uri)
        except ValueError as exc:
            raise ClientMetadataError(f"unparseable redirect_uri: {uri!r}") from exc
        if parts.scheme not in {"http", "https"}:
            raise ClientMetadataError(
                f"unsupported redirect_uri scheme {parts.scheme!r} (https required; "
                "http only on loopback)"
            )
        if parts.fragment:
            raise ClientMetadataError("redirect_uri must not carry a fragment")
        if parts.username or parts.password:
            raise ClientMetadataError("redirect_uri must not carry userinfo")
        if parts.scheme == "http" and (parts.hostname or "").lower() not in _LOOPBACK_HOSTS:
            raise ClientMetadataError(
                "http redirect_uri is only allowed on loopback (localhost/127.0.0.1)"
            )
        if not parts.hostname:
            raise ClientMetadataError(f"redirect_uri without a host: {uri!r}")
        cleaned.append(uri)
    return cleaned


def _decode_json_list(raw: Any) -> list[str]:
    """Stored JSON array column -> list (a hand-edited row never 500s)."""
    if isinstance(raw, (list, tuple)):
        return [str(v) for v in raw]
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def register_client(
    *,
    client_name: str,
    redirect_uris: Any,
    token_endpoint_auth_method: str = "client_secret_post",
    grant_types: Any = None,
    response_types: Any = None,
) -> dict[str, Any]:
    """Persist a dynamically registered client (RFC 7591).

    Returns the **registration response record** — for a confidential client
    it carries ``client_secret`` (plaintext, returned **once**; only its
    SHA-256 hash reaches the row). ``redirect_uris`` are validated first, so
    an open registration endpoint can never mint a client that ships codes in
    cleartext to an arbitrary http host.

    Raises:
        ClientMetadataError: on any invalid metadata (before any write).
    """
    name = str(client_name or "").strip()
    if not name or len(name) > MAX_CLIENT_NAME_LENGTH:
        raise ClientMetadataError(
            f"client_name must be 1-{MAX_CLIENT_NAME_LENGTH} characters"
        )
    uris = validate_redirect_uris(redirect_uris)
    method = str(token_endpoint_auth_method or "client_secret_post").strip()
    if method not in SUPPORTED_AUTH_METHODS:
        raise ClientMetadataError(
            f"unsupported token_endpoint_auth_method {method!r}; "
            f"expected one of {sorted(SUPPORTED_AUTH_METHODS)}"
        )
    for field, allowed, supplied in (
        ("grant_types", SUPPORTED_GRANT_TYPES, grant_types),
        ("response_types", SUPPORTED_RESPONSE_TYPES, response_types),
    ):
        if supplied is None:
            continue
        if not isinstance(supplied, (list, tuple)) or not set(map(str, supplied)) <= allowed:
            raise ClientMetadataError(
                f"{field} must be a subset of {sorted(allowed)}"
            )

    client_id = CLIENT_ID_PREFIX + secrets.token_urlsafe(24)
    plaintext_secret: str | None = None
    secret_hash: str | None = None
    if method != "none":
        plaintext_secret = CLIENT_SECRET_PREFIX + secrets.token_urlsafe(32)
        secret_hash = hash_token(plaintext_secret)

    ensure_oauth_tables()
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO oauth_clients "
                "(client_id, client_secret_hash, client_name, redirect_uris, "
                " token_endpoint_auth_method) "
                "VALUES (:client_id, :secret_hash, :name, :uris, :method)"
            ),
            {
                "client_id": client_id,
                "secret_hash": secret_hash,
                "name": name,
                "uris": json.dumps(uris),
                "method": method,
            },
        )
    response: dict[str, Any] = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        # 0 == "never expires" (RFC 7591 §3.2).
        "client_secret_expires_at": 0,
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
    }
    if plaintext_secret is not None:
        response["client_secret"] = plaintext_secret
    return response


def get_client(client_id: str | None) -> dict[str, Any] | None:
    """Fetch a registered client by id, ``redirect_uris`` decoded. Never raises."""
    if not client_id:
        return None
    ensure_oauth_tables()
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT client_id, client_secret_hash, client_name, redirect_uris, "
                "token_endpoint_auth_method FROM oauth_clients WHERE client_id = :cid"
            ),
            {"cid": str(client_id)},
        ).first()
    if row is None:
        return None
    found = dict(row._mapping)
    found["redirect_uris"] = _decode_json_list(found.get("redirect_uris"))
    return found


def verify_client_secret(client: dict[str, Any], presented: str | None) -> bool:
    """Constant-time client credential check.

    A public client (``token_endpoint_auth_method='none'``) stores no secret
    and must present none; a confidential client must present the exact
    plaintext whose SHA-256 matches the row. Timing does not split between
    "no secret registered" and "wrong secret": both are a single
    :func:`hashlib.sha256` + :func:`secrets.compare_digest`.
    """
    stored = client.get("client_secret_hash")
    candidate = str(presented or "")
    if not stored:
        return candidate == ""
    if not candidate:
        return False
    return secrets.compare_digest(hash_token(candidate), str(stored))


# ---------------------------------------------------------------------------
# PKCE (RFC 7636) — S256 mandatory, ``plain`` rejected everywhere
# ---------------------------------------------------------------------------


class PkceError(ValueError):
    """A PKCE parameter violation. Messages are repo-authored literals."""


def validate_code_challenge(
    challenge: str | None, method: str | None
) -> tuple[str, str]:
    """Validate the authorize-time PKCE pair. Returns ``(challenge, "S256")``.

    OAuth 2.1 makes PKCE required and confines it to ``S256``:

    - missing challenge **or** missing method → error (a client must send both);
    - ``plain`` → error (trivially forgeable — the whole point of leaving it
      behind);
    - any other method → error (unsupported);
    - the challenge itself must be 43-128 unreserved characters
      (RFC 7636 §4.2 — for S256 that is the base64url-no-pad SHA-256).

    Raises:
        PkceError: with a literal, human-readable reason.
    """
    clean_method = str(method or "").strip()
    clean_challenge = str(challenge or "").strip()
    if not clean_challenge:
        raise PkceError("PKCE is required: code_challenge is missing")
    if not clean_method:
        raise PkceError("PKCE is required: code_challenge_method is missing")
    if clean_method.lower() == "plain":
        raise PkceError("code_challenge_method 'plain' is not accepted; use S256")
    if clean_method.upper() != "S256":
        raise PkceError(
            f"unsupported code_challenge_method {clean_method!r}; only S256 is supported"
        )
    if not (PKCE_VERIFIER_MIN <= len(clean_challenge) <= PKCE_VERIFIER_MAX):
        raise PkceError("code_challenge must be 43-128 characters")
    if any(ch not in PKCE_VERIFIER_CHARSET for ch in clean_challenge):
        raise PkceError("code_challenge contains characters outside the unreserved set")
    return clean_challenge, "S256"


def s256_challenge(verifier: str) -> str:
    """``BASE64URL-NO-PAD(SHA256(verifier))`` — RFC 7636 §4.2."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_code_verifier(verifier: str | None, method: str, challenge: str) -> bool:
    """Constant-time PKCE check at the token endpoint. Never raises.

    ``method`` is whatever the *code row* stored (``S256`` — anything else is
    rejected outright, so a hand-edited row cannot downgrade to ``plain``),
    and the verifier is length/charset-checked per RFC 7636 §4.1 first: a
    malformed verifier fails instead of hashing to something that could match.
    """
    candidate = str(verifier or "")
    if str(method or "").upper() != "S256":
        return False
    if not (PKCE_VERIFIER_MIN <= len(candidate) <= PKCE_VERIFIER_MAX):
        return False
    if any(ch not in PKCE_VERIFIER_CHARSET for ch in candidate):
        return False
    try:
        computed = s256_challenge(candidate)
    except (UnicodeEncodeError, ValueError):  # pragma: no cover - charset-checked above
        return False
    return secrets.compare_digest(computed, str(challenge or ""))


# ---------------------------------------------------------------------------
# Authorization codes — single-use, short-lived, hashed at rest
# ---------------------------------------------------------------------------


class AuthorizationCodeError(ValueError):
    """Why a code exchange failed. ``reason`` is for logs, never the wire."""


def store_authorization_code(
    *,
    client_id: str,
    user_id: int,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scopes: list[str],
) -> dict[str, Any]:
    """Mint an authorization code (plaintext returned once, hash stored).

    Returns ``{"code", "code_hash", "row_id", "expires_at"}``; callers must
    forward only ``code`` (in the redirect) and keep the rest internal.
    """
    plaintext = AUTH_CODE_PREFIX + secrets.token_urlsafe(32)
    code_hash = hash_token(plaintext)
    expires_at = _now_plus(code_ttl_seconds())
    ensure_oauth_tables()
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO oauth_authorization_codes "
                "(code_hash, client_id, user_id, redirect_uri, code_challenge, "
                " code_challenge_method, scopes, expires_at) "
                "VALUES (:h, :cid, :uid, :uri, :challenge, :method, :scopes, :exp)"
            ),
            {
                "h": code_hash,
                "cid": str(client_id),
                "uid": int(user_id),
                "uri": str(redirect_uri),
                "challenge": str(code_challenge),
                "method": str(code_challenge_method),
                "scopes": json.dumps(list(scopes)),
                "exp": expires_at,
            },
        )
        row = conn.execute(
            text("SELECT id FROM oauth_authorization_codes WHERE code_hash = :h"),
            {"h": code_hash},
        ).first()
    if row is None:  # pragma: no cover - defensive
        raise AuthorizationCodeError("failed to persist the authorization code")
    return {
        "code": plaintext,
        "code_hash": code_hash,
        "row_id": int(row[0]),
        "expires_at": expires_at,
    }


def consume_authorization_code(
    *,
    code: str | None,
    client_id: str,
    redirect_uri: str,
    code_verifier: str | None,
) -> dict[str, Any]:
    """Validate + **consume** a code at the token endpoint (one shot).

    Checks, in order: the code exists, belongs to this client, was issued for
    exactly this ``redirect_uri``, is unexpired, is not already consumed, and
    passes PKCE (S256, constant time). Consumption is a conditional
    ``UPDATE ... WHERE consumed_at IS NULL`` inside one transaction, so two
    racing exchanges of the same code cannot both win.

    Returns the grant: ``{"user_id", "scopes", "client_id"}``.

    Raises:
        AuthorizationCodeError: any failure — the endpoint answers a single
        ``invalid_grant`` for all of them (the ``reason`` never reaches the
        wire, so a prober cannot tell "consumed" from "wrong client").
    """
    if not code:
        raise AuthorizationCodeError("missing code")
    code_hash = hash_token(str(code))
    now = _stamp()
    with _get_manager().engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, client_id, user_id, redirect_uri, code_challenge, "
                "code_challenge_method, scopes, expires_at, consumed_at "
                "FROM oauth_authorization_codes WHERE code_hash = :h"
            ),
            {"h": code_hash},
        ).first()
        if row is None:
            raise AuthorizationCodeError("unknown code")
        found = dict(row._mapping)
        if found.get("consumed_at"):
            raise AuthorizationCodeError("code already consumed")
        if str(found.get("expires_at") or "") <= now:
            raise AuthorizationCodeError("code expired")
        if str(found.get("client_id")) != str(client_id):
            raise AuthorizationCodeError("code was issued to another client")
        if str(found.get("redirect_uri")) != str(redirect_uri):
            raise AuthorizationCodeError("redirect_uri mismatch")
        if not verify_code_verifier(
            code_verifier,
            str(found.get("code_challenge_method") or ""),
            str(found.get("code_challenge") or ""),
        ):
            raise AuthorizationCodeError("PKCE verification failed")
        claimed = conn.execute(
            text(
                "UPDATE oauth_authorization_codes SET consumed_at = :now "
                "WHERE id = :id AND consumed_at IS NULL"
            ),
            {"now": now, "id": int(found["id"])},
        )
        if int(claimed.rowcount or 0) != 1:
            # Lost the race against a concurrent exchange of the same code.
            raise AuthorizationCodeError("code already consumed")
        return {
            "user_id": int(found["user_id"]),
            "scopes": _decode_json_list(found.get("scopes")),
            "client_id": str(found["client_id"]),
        }


# ---------------------------------------------------------------------------
# Access / refresh tokens (RFC 6749 §5) — ``oauth_tokens`` rows
# ---------------------------------------------------------------------------


class TokenError(ValueError):
    """Why a token request failed. ``reason`` is for logs, never the wire."""


def issue_tokens(
    *,
    client_id: str,
    user_id: int,
    scopes: list[str],
) -> dict[str, Any]:
    """Mint an access + refresh token pair (plaintext returned **once**).

    Only SHA-256 hashes are stored (``access_token_hash`` /
    ``refresh_token_hash``); the plaintexts leave this function and never
    touch disk or a log line.  The response contract is RFC 6749 §5.1:
    ``access_token`` / ``refresh_token`` / ``expires_in`` /
    ``token_type='Bearer'`` / ``scope``.
    """
    access = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    access_hash = hash_token(access)
    refresh_hash = hash_token(refresh)
    access_expires_at = _now_plus(access_ttl_seconds())
    refresh_expires_at = _now_plus(refresh_ttl_seconds())
    ensure_oauth_tables()
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO oauth_tokens (access_token_hash, refresh_token_hash, "
                " client_id, user_id, scopes, access_expires_at, refresh_expires_at) "
                "VALUES (:a, :r, :cid, :uid, :scopes, :arexp, :rexp)"
            ),
            {
                "a": access_hash,
                "r": refresh_hash,
                "cid": str(client_id),
                "uid": int(user_id),
                "scopes": json.dumps(list(scopes)),
                "arexp": access_expires_at,
                "rexp": refresh_expires_at,
            },
        )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": access_ttl_seconds(),
        "scope": " ".join(scopes),
    }


def refresh_tokens(*, refresh_token: str | None, client_id: str) -> dict[str, Any]:
    """Rotate a refresh token into a fresh access + refresh pair (one shot).

    Rotation is unconditional: the presented row is revoked in the same
    transaction that issues the replacement, so a stolen refresh token that
    loses the race (or is replayed afterwards) is worth nothing — the honest
    holder's rotation already revoked the hash it shares.  A token issued to
    another client, an expired one and a revoked one all answer the single
    ``invalid_grant`` the endpoint reports.

    Raises:
        TokenError: any failure — the endpoint never leaks which one.
    """
    if not refresh_token:
        raise TokenError("missing refresh_token")
    presented_hash = hash_token(str(refresh_token))
    now = _stamp()
    with _get_manager().engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, client_id, user_id, scopes, refresh_expires_at, revoked_at "
                "FROM oauth_tokens WHERE refresh_token_hash = :h"
            ),
            {"h": presented_hash},
        ).first()
        if row is None:
            raise TokenError("unknown refresh token")
        found = dict(row._mapping)
        if found.get("revoked_at"):
            raise TokenError("refresh token revoked")
        if str(found.get("refresh_expires_at") or "") <= now:
            raise TokenError("refresh token expired")
        if str(found.get("client_id")) != str(client_id):
            raise TokenError("refresh token was issued to another client")
        claimed = conn.execute(
            text(
                "UPDATE oauth_tokens SET revoked_at = :now "
                "WHERE id = :id AND revoked_at IS NULL"
            ),
            {"now": now, "id": int(found["id"])},
        )
        if int(claimed.rowcount or 0) != 1:
            raise TokenError("refresh token already rotated")
        scopes = _decode_json_list(found.get("scopes"))
        user_id = int(found["user_id"])
    # New pair on a fresh row: the revoked row stays as the rotation trail.
    return issue_tokens(
        client_id=str(client_id), user_id=user_id, scopes=scopes
    )


#: ``last_used_at`` write throttle for OAuth rows (≤ 1 write/60s per token,
#: mirroring :func:`zolai.api.session_auth._touch_last_used` — the resolve
#: path runs on every MCP request and must stay read-only in the common case).
_TOUCH_THROTTLE_S = 60.0
_touch_lock = threading.Lock()
_touch_marks: dict[str, float] = {}


def _touch_token_used(row_id: Any, token_hash: str) -> None:
    """Throttled ``last_used_at`` update for an ``oauth_tokens`` row."""
    now = time.monotonic()
    with _touch_lock:
        last = _touch_marks.get(token_hash)
        if last is not None and now - last < _TOUCH_THROTTLE_S:
            return
        if len(_touch_marks) >= 4096:
            _touch_marks.clear()
        _touch_marks[token_hash] = now
    try:
        with _get_manager().engine.begin() as conn:
            conn.execute(
                text("UPDATE oauth_tokens SET last_used_at = :stamp WHERE id = :id"),
                {"stamp": _stamp(), "id": int(row_id)},
            )
    except Exception:  # noqa: BLE001 - observability only, never breaks auth
        pass


def resolve_access_token(token: str | None) -> dict[str, Any] | None:
    """Resolve a ``zolai_at_*`` access token to a principal record, or ``None``.

    The record is deliberately the **same shape** the middleware publishes for
    an API key (``id``/``name``/``key_prefix``/``scopes``) so
    ``require_scope`` / ``role_for`` / ``has_role`` work unchanged, plus the
    identity fields ``auth_source='oauth'``, ``user_id``, ``username`` and
    ``client_id``.  ``scopes`` is the grant minted at the token endpoint —
    already narrowed to the user's role at authorize time, so an OAuth
    principal can never reach a tier its user does not hold.

    Returns ``None`` for missing / foreign-prefix / unknown / expired /
    revoked tokens, and for a grant whose user has since been **disabled or
    deleted** — disabling the account kills its OAuth grants with it (the
    identity layer stays the single source of truth for that).
    """
    if not token or not token.startswith(ACCESS_TOKEN_PREFIX):
        return None
    presented_hash = hash_token(token)
    now = _stamp()
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT t.id, t.client_id, t.user_id, t.scopes, "
                "       t.access_expires_at, t.revoked_at, u.username, u.enabled "
                "FROM oauth_tokens t "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.access_token_hash = :h"
            ),
            {"h": presented_hash},
        ).first()
    if row is None:
        return None
    found = dict(row._mapping)
    if found.get("revoked_at"):
        return None
    if str(found.get("access_expires_at") or "") <= now:
        return None
    if found.get("username") is None or not found.get("enabled"):
        # User disabled or deleted after the grant was minted.
        return None
    _touch_token_used(found["id"], presented_hash)
    return {
        "id": f"oauth:{found['id']}",
        "name": f"oauth:{found['client_id']}",
        "key_prefix": token[:16],
        "scopes": _decode_json_list(found.get("scopes")),
        "auth_source": "oauth",
        "user_id": int(found["user_id"]),
        "username": str(found["username"]),
        "client_id": str(found["client_id"]),
    }
