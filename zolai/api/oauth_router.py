"""OAuth 2.1 endpoints for the MCP endpoint (W6) — routes only.

The authorization-server logic lives in :mod:`zolai.auth.oauth`; this module
owns transport concerns: JSON bodies, absolute-URL derivation from the
request, form rendering, and RFC error envelopes.

Discovery documents are served **unconditionally** (independently of
``ZOLAI_MCP_OAUTH_REQUIRED``) because they are what ChatGPT/Gemini's
connectors look for *before* any credential exists — no challenge required:

===============================  ======================================
Path                             Document
===============================  ======================================
``GET /.well-known/             RFC 8414 authorization-server metadata
  oauth-authorization-server``
``GET /.well-known/             the same document — OIDC-discovery
  openid-configuration``        clients fall back to this name
``GET /mcp/.well-known/         the same document for clients that
  oauth-authorization-server``  resolve well-known against the
                                resource path they connected to
``GET /.well-known/             RFC 9728 protected-resource metadata
  oauth-protected-resource``
``GET /.well-known/             the RFC 9728 §2 inserted-path form
  oauth-protected-resource/mcp``
``GET /mcp/.well-known/         the path-relative form MCP clients probe
  oauth-protected-resource``
===============================  ======================================

Every URL inside a document is absolute (``ZOLAI_PUBLIC_ORIGIN`` override,
else derived from the forwarded/host headers), so the document a client
fetches on ``mcp.zolai.space`` points back at ``mcp.zolai.space`` and the
one on ``api.zolai.space`` points at ``api.zolai.space``.

Routes below: ``POST /oauth/register`` (RFC 7591), ``GET|POST
/oauth/authorize`` (authorization code flow, PKCE S256 mandatory, reusing
the existing username/password check + an explicit consent step);
``POST /oauth/token`` follows in the next commit.

Protocol errors are **RFC 6749 §5.2 envelopes** (top-level ``error`` /
``error_description``), not the project's ``{"detail": ...}`` REST envelope —
OAuth clients parse the RFC shape. Literal descriptions only: no exception
text other than this repository's own validation messages ever reaches a
response.
"""

from __future__ import annotations

import html
import logging
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..api import auth as api_auth
from ..api import session_auth
from ..auth import oauth
from .auth_middleware import client_ip, limiter

router = APIRouter(tags=["oauth"])

logger = logging.getLogger("zolai.oauth")

#: Cache policy for discovery documents: dynamic (origin-derived), so never
#: shared by a proxy for long. RFC 8414 allows caching; 60s is a compromise.
_DISCOVERY_CACHE = "public, max-age=60"


def _json(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": _DISCOVERY_CACHE},
    )


# ── RFC 8414 — authorization-server metadata ────────────────────────────────


def _as_metadata(request: Request) -> JSONResponse:
    return _json(oauth.authorization_server_metadata(oauth.public_origin(request.scope)))


@router.get("/.well-known/oauth-authorization-server")
async def well_known_oauth_authorization_server(request: Request) -> JSONResponse:
    """RFC 8414 metadata — the document ChatGPT reports as missing."""
    return _as_metadata(request)


@router.get("/.well-known/openid-configuration")
async def well_known_openid_configuration(request: Request) -> JSONResponse:
    """OIDC-discovery name for the same metadata (documented fallback).

    Not an OpenID Provider — the alias exists because several clients try
    this path before ``/.well-known/oauth-authorization-server``; serving it
    keeps one more discovery probe from 404-ing. The body is the RFC 8414
    document (which carries no ``id_token_signing_alg_values_supported``,
    so an OIDC-only client can tell we are pure OAuth 2.1).
    """
    return _as_metadata(request)


@router.get("/mcp/.well-known/oauth-authorization-server")
async def mcp_relative_oauth_authorization_server(request: Request) -> JSONResponse:
    """RFC 8414 document for clients resolving well-known against ``/mcp``."""
    return _as_metadata(request)


# ── RFC 9728 — protected-resource metadata ──────────────────────────────────


def _pr_metadata(request: Request, resource_path: str = "/mcp") -> JSONResponse:
    return _json(
        oauth.protected_resource_metadata(
            oauth.public_origin(request.scope), resource_path
        )
    )


@router.get("/.well-known/oauth-protected-resource")
async def well_known_oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 metadata — what ``WWW-Authenticate: resource_metadata`` points at."""
    return _pr_metadata(request)


@router.get("/.well-known/oauth-protected-resource/mcp")
async def well_known_oauth_protected_resource_mcp(request: Request) -> JSONResponse:
    """RFC 9728 §2 inserted-path form (``resource`` is ``<origin>/mcp``)."""
    return _pr_metadata(request)


@router.get("/.well-known/oauth-protected-resource/api/v1/mcp")
async def well_known_oauth_protected_resource_api_mcp(request: Request) -> JSONResponse:
    """Inserted-path form for the versioned alias (``<origin>/api/v1/mcp``)."""
    return _pr_metadata(request, "/api/v1/mcp")


@router.get("/mcp/.well-known/oauth-protected-resource")
async def mcp_relative_oauth_protected_resource(request: Request) -> JSONResponse:
    """Path-relative form — the variant MCP clients probe next to the endpoint."""
    return _pr_metadata(request)


@router.get("/api/v1/mcp/.well-known/oauth-protected-resource")
async def api_mcp_relative_oauth_protected_resource(request: Request) -> JSONResponse:
    """Path-relative form next to the versioned alias."""
    return _pr_metadata(request, "/api/v1/mcp")


# ---------------------------------------------------------------------------
# RFC 7591 — dynamic client registration
# ---------------------------------------------------------------------------

#: Anonymous registration bucket (registrations/minute per IP). Open
#: registration is required by the MCP ecosystem (ChatGPT/Gemini register on
#: first connect), so the control is a rate limit, not a credential.
DEFAULT_REGISTER_RPM = 10


class RegistrationIn(BaseModel):
    """RFC 7591 ``client_metadata``.

    Every field is optional at the type level so validation errors can be
    reported as RFC 6749 ``invalid_client_metadata`` (400) instead of
    framework 422 envelopes — OAuth clients parse those. Unknown extension
    fields are ignored, as RFC 7591 §2 requires.
    """

    model_config = ConfigDict(extra="ignore")

    client_name: str | None = Field(default=None, max_length=500)
    redirect_uris: list[str] | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None
    scope: str | None = None
    software_id: str | None = Field(default=None, max_length=500)
    software_version: str | None = Field(default=None, max_length=100)


def _oauth_error(
    status: int,
    error: str,
    description: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """RFC 6749 §5.2 error envelope: top-level ``error`` fields, never cached."""
    merged = {"Cache-Control": "no-store", **(headers or {})}
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers=merged,
    )


def _invalid_client(description: str, *, basic: bool) -> JSONResponse:
    """``invalid_client`` 401.

    RFC 6749 §5.2: when the client authenticated with the ``Authorization``
    header, the 401 MUST carry a ``WWW-Authenticate`` challenge for the scheme
    it used.  A body credential (``client_secret_post``) gets no challenge.
    """
    headers = {"WWW-Authenticate": 'Basic realm="zolai"'} if basic else None
    return _oauth_error(401, "invalid_client", description, headers)


def _invalid_metadata(description: str) -> JSONResponse:
    return _oauth_error(400, "invalid_client_metadata", description)


async def _register_rate_limit(scope: Any) -> None:
    capacity = DEFAULT_REGISTER_RPM
    allowed, _remaining, _reset, retry_after = await run_in_threadpool(
        limiter.hit, f"oauth:register:{client_ip(scope)}", capacity
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "scope": "oauth_register",
                "limit_rpm": capacity,
                "retry_after_s": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/oauth/register", status_code=201)
async def oauth_register(body: RegistrationIn, request: Request) -> JSONResponse:
    """Open dynamic client registration (RFC 7591).

    Issues ``client_id`` (+ ``client_secret`` for a confidential client —
    shown **once**; only its SHA-256 reaches ``oauth_clients``). Security
    rests on two things: redirect URIs are pinned here (https, or http only
    on loopback) and every authorization code later checks against them, and
    the token endpoint checks the secret in constant time. A client asking
    for ``token_endpoint_auth_method='none'`` registers as a *public* client
    — no secret exists then, and PKCE is what protects its codes (mandatory
    for everyone anyway).

    ``scope`` (if sent) is validated against the frozen vocabulary and
    otherwise advisory: what a grant may actually carry is decided at
    ``/oauth/authorize`` by intersecting the request with the *user's* role —
    a registration can never widen a role.
    """
    await _register_rate_limit(request.scope)

    if body.scope:
        for raw_scope in body.scope.split():
            if raw_scope not in api_auth.VALID_ACTIONS:
                return _invalid_metadata(
                    f"scope {raw_scope!r} is not in the frozen action vocabulary"
                )

    try:
        response = await run_in_threadpool(
            oauth.register_client,
            client_name=body.client_name or "",
            redirect_uris=body.redirect_uris,
            token_endpoint_auth_method=(
                body.token_endpoint_auth_method or "client_secret_post"
            ),
            grant_types=body.grant_types,
            response_types=body.response_types,
        )
    except oauth.ClientMetadataError as exc:
        # The specific (repo-authored) reason stays server-side: the
        # error-envelope gate forbids *any* str(exception) in a response
        # body, and RFC 7591 only requires the error code to be nameable.
        logger.debug("oauth register rejected: %s", exc)
        return _invalid_metadata("client metadata failed validation")

    # RFC 7591 §3.2.1: scope/software_id/software_version must be echoed when
    # the client sent them — some hosts (ChatGPT included) check for the echo.
    if body.scope:
        response["scope"] = body.scope
    if body.software_id:
        response["software_id"] = body.software_id
    if body.software_version:
        response["software_version"] = body.software_version

    # The response carries a plaintext secret — never cacheable.
    return JSONResponse(content=response, status_code=201, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# RFC 6749 §4.1 — authorization code flow (PKCE S256 mandatory)
# ---------------------------------------------------------------------------


async def _validate_authorize_params(
    form: dict[str, str],
    origin: str,
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Shared validation for GET (query) and POST (form).

    Returns ``(params, None)`` on success or ``(None, error)`` — an RFC error
    envelope. Client/redirect problems are answered **without** redirecting
    (RFC 6749 §4.1.2.1: never redirect to an unvalidated URI), and PKCE
    violations are refused before any credential is asked for, so a lazy
    client sees the error instead of a login form it cannot complete.

    ``get_client`` is awaited in a worker thread: a CIMD ``client_id``
    (draft-ietf-oauth-client-id-metadata-document) is an https URL, and
    resolving it performs a bounded network fetch — never on the event loop.
    """
    client = await run_in_threadpool(oauth.get_client, form.get("client_id"))
    if client is None:
        return None, _oauth_error(400, "invalid_client", "unknown client_id")
    redirect_uri = str(form.get("redirect_uri") or "")
    if not redirect_uri or redirect_uri not in client["redirect_uris"]:
        return None, _oauth_error(
            400,
            "invalid_request",
            "redirect_uri is not one of the URIs registered for this client",
        )
    if str(form.get("response_type") or "") != "code":
        return None, _oauth_error(
            400, "unsupported_response_type", "only response_type=code is supported"
        )
    # RFC 8707 resource indicator: a client may name the audience it wants a
    # token for. This AS serves exactly one (the MCP endpoint), so a foreign
    # value is refused instead of minting a token for the wrong audience.
    if not oauth.resource_is_served(form.get("resource"), origin):
        return None, _oauth_error(
            400, "invalid_target", "resource must be this server's MCP endpoint"
        )
    try:
        challenge, method = oauth.validate_code_challenge(
            form.get("code_challenge"), form.get("code_challenge_method")
        )
    except oauth.PkceError as exc:
        # Same rule: one fixed, accurate description outward; the authored
        # detail (which parameter, which bound) goes to the server log.
        logger.debug("oauth authorize PKCE rejected: %s", exc)
        return None, _oauth_error(
            400,
            "invalid_request",
            "code_challenge must be present, use S256 and be 43-128 unreserved characters",
        )
    requested = [s for s in str(form.get("scope") or "").split() if s]
    grantable = set(oauth.grantable_scopes())
    for raw_scope in requested:
        if raw_scope not in grantable:
            return None, _oauth_error(
                400, "invalid_scope", f"unsupported scope: {raw_scope}"
            )
    return {
        "client": client,
        "client_id": str(client["client_id"]),
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": method,
        "requested_scopes": requested,
        "state": str(form.get("state") or ""),
    }, None


_HIDDEN_FIELDS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "scope",
    "state",
    # RFC 8707 — carried through consent so the token request sees the audience.
    "resource",
)


def _authorize_page(
    *,
    params: dict[str, str],
    client_name: str,
    error: str | None = None,
    username: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """The consent page: who is asking, for what, with a login form.

    Server-rendered, **no JavaScript**; styling lives in the same-origin
    ``/oauth/authorize.css`` because the deployment CSP is
    ``default-src 'self'`` and would block a ``<style>`` block. Every
    interpolated value is HTML-escaped — ``client_name`` and ``state`` are
    attacker-controlled inputs. Submitting the form *is* the consent step:
    the POST carries the credentials and the unchanged OAuth parameters
    back to this endpoint; Cancel returns the RFC 6749 ``access_denied``
    redirect without any credential check.
    """
    esc = html.escape
    shown_scopes = params.get("scope", "").strip() or "all scopes your role permits"
    hidden = "\n".join(
        f'    <input type="hidden" name="{esc(key)}" value="{esc(params.get(key, ""))}">'
        for key in _HIDDEN_FIELDS
        if params.get(key) is not None
    )
    error_html = (
        f'    <p class="error" role="alert" id="oauth-error">{esc(error)}</p>'
        if error
        else ""
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Authorize — Zolai MCP</title>
  <link rel="stylesheet" href="/oauth/authorize.css">
</head>
<body>
  <main class="card">
    <p class="brand">Zolai · MCP</p>
    <h1>Authorize access</h1>
{error_html}
    <p class="ask"><strong>{esc(client_name)}</strong> wants to connect to your
       Zolai account and use the MCP endpoint on your behalf.</p>
    <p class="scopes">Requested scopes: <code>{esc(shown_scopes)}</code>. A grant
       can only ever <em>narrow</em> what your role allows — never widen it.</p>
    <form method="post" action="/oauth/authorize">
{hidden}
      <label class="field">Username
        <input name="username" autocomplete="username" autocapitalize="none"
               spellcheck="false" value="{esc(username)}" required>
      </label>
      <label class="field">Password
        <input name="password" type="password" autocomplete="current-password"
               required>
      </label>
      <div class="actions">
        <button type="submit" name="consent" value="deny" class="secondary"
                formnovalidate>Cancel</button>
        <button type="submit" name="consent" value="allow" class="primary">Allow</button>
      </div>
    </form>
    <p class="footnote">Signing in grants this one connector access to the tools
       your role allows. You can revoke it any time from your Zolai admin.</p>
  </main>
</body>
</html>
"""
    return HTMLResponse(document, status_code=status_code)


_AUTHORIZE_CSS = """\
:root {
  color-scheme: light dark;
  --bg: #f4f5f7; --card: #ffffff; --ink: #16181d; --muted: #5b6270;
  --line: #dfe2e8; --accent: #4f46e5; --accent-ink: #ffffff;
  --error-bg: #fdecec; --error-ink: #b3261e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111318; --card: #1a1d24; --ink: #e8eaf0; --muted: #9aa1b0;
    --line: #2b2f3a; --accent: #6d5df7; --accent-ink: #ffffff;
    --error-bg: #3a1f21; --error-ink: #f2b8b5;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: flex; align-items: center;
  justify-content: center; background: var(--bg); color: var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  padding: 16px;
}
.card {
  width: 100%; max-width: 420px; background: var(--card);
  border: 1px solid var(--line); border-radius: 14px;
  padding: 28px 26px; box-shadow: 0 10px 30px rgb(0 0 0 / 8%);
}
.brand {
  margin: 0 0 14px; font-size: 13px; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--accent);
}
h1 { margin: 0 0 12px; font-size: 22px; }
.ask, .scopes, .footnote { margin: 0 0 12px; }
.scopes code {
  background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
  padding: 1px 6px; font-size: 14px; overflow-wrap: anywhere;
}
.footnote { color: var(--muted); font-size: 13px; margin-top: 18px; }
.error {
  background: var(--error-bg); color: var(--error-ink);
  border: 1px solid currentColor; border-radius: 8px;
  padding: 10px 12px; margin: 0 0 12px; font-weight: 500;
}
form { display: grid; gap: 14px; margin-top: 6px; }
.field { display: grid; gap: 6px; font-size: 14px; font-weight: 600; }
.field input {
  font: inherit; font-weight: 400; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  min-height: 44px;
}
.field input:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
  border-color: var(--accent);
}
.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }
button {
  font: inherit; font-weight: 600; border-radius: 8px; min-height: 44px;
  padding: 10px 18px; cursor: pointer; border: 1px solid transparent;
}
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.primary { background: var(--accent); color: var(--accent-ink); }
.primary:hover { filter: brightness(1.08); }
.secondary {
  background: transparent; color: var(--muted); border-color: var(--line);
}
.secondary:hover { color: var(--ink); }
"""


@router.get("/oauth/authorize.css")
async def oauth_authorize_css() -> Response:
    """Same-origin stylesheet for the consent page (CSP-safe, no JS)."""
    return Response(
        content=_AUTHORIZE_CSS,
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/oauth/authorize")
def oauth_authorize_get(
    request: Request,
    response_type: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    scope: str | None = None,
    state: str | None = None,
    resource: str | None = None,
) -> Response:
    """Authorization-code request → the login + consent form (PKCE checked first)."""
    form = {
        "response_type": response_type or "",
        "client_id": client_id or "",
        "redirect_uri": redirect_uri or "",
        "code_challenge": code_challenge or "",
        "code_challenge_method": code_challenge_method or "",
        "scope": scope or "",
        "state": state or "",
        # RFC 8707 — echoed through the consent form so the POST carries it too.
        "resource": resource or "",
    }
    params, error = await _validate_authorize_params(
        form, oauth.public_origin(request.scope)
    )
    if error is not None:
        return error
    assert params is not None  # narrowed by the tuple contract above
    return _authorize_page(
        params={**form, "code_challenge": params["code_challenge"]},
        client_name=str(params["client"].get("client_name") or "An OAuth client"),
    )


@router.post("/oauth/authorize")
async def oauth_authorize_post(request: Request) -> Response:
    """Consent submission: verify credentials (the existing identity check),
    narrow the grant to the user's role, mint a single-use code, redirect."""
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return _oauth_error(
            415,
            "unsupported_media_type",
            "POST /oauth/authorize expects application/x-www-form-urlencoded",
        )
    raw = await request.body()
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    form = {key: values[0] for key, values in parsed.items() if values}

    params, error = await _validate_authorize_params(
        form, oauth.public_origin(request.scope)
    )
    if error is not None:
        return error
    assert params is not None

    # RFC 6749 §4.1.2.1 — refusal is a redirect, not an AS error. Only an
    # explicit Cancel counts as refusal; a submission that carries credentials
    # (the consent button, or a scripted client posting the form fields) is
    # the intent to allow, matching the pre-existing endpoint contract.
    if str(form.get("consent") or "").strip().lower() == "deny":
        separator = "&" if "?" in params["redirect_uri"] else "?"
        denial = (
            f"{params['redirect_uri']}{separator}"
            "error=access_denied&error_description="
            + quote("The user denied the authorization request", safe="")
        )
        if params["state"]:
            denial += f"&state={quote(params['state'], safe='')}"
        return RedirectResponse(denial, status_code=302, headers={"Cache-Control": "no-store"})

    username = str(form.get("username") or "")
    password = str(form.get("password") or "")

    # Rate limit **before** the argon2 work — the same two buckets the
    # /api/v1/auth/login route uses, so one spray quota covers both paths.
    ip = client_ip(request.scope)
    try:
        normalized = session_auth.normalize_username(username)
    except ValueError:
        normalized = username[:64]
    for bucket, capacity in (
        (f"login:ip:{ip}", session_auth.login_rate_limit_rpm()),
        (f"login:user:{normalized}", session_auth.login_rate_limit_user_rpm()),
    ):
        allowed, remaining, reset, retry_after = await run_in_threadpool(
            limiter.hit, bucket, capacity
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limited",
                    "scope": "oauth_authorize",
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
        # argon2id (~200ms) — never on the event loop. This is *the* existing
        # username/password check; there is no second verifier.
        user = await run_in_threadpool(session_auth.verify_login, username, password)
    except (session_auth.LoginRejected, ValueError):
        # One shape for unknown/wrong/disabled (no enumeration); the form
        # comes back with a plain message instead of a JSON body, because the
        # submitter is a browser.
        session_auth.record_audit(
            table="oauth_authorization_codes",
            row_id=0,
            field="authorize_rejected",
            old_value=None,
            new_value=None,
            reason=f"oauth authorize login rejected (invalid_credentials) from {ip}",
        )
        return _authorize_page(
            params=form,
            client_name=str(params["client"].get("client_name") or "An OAuth client"),
            error="Invalid username or password.",
            username=username,
            status_code=401,
        )

    role = str(user.get("role") or "member").strip().lower()
    granted = oauth.granted_scopes(role, params["requested_scopes"])
    issued = await run_in_threadpool(
        oauth.store_authorization_code,
        client_id=params["client_id"],
        user_id=int(user["id"]),
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
        code_challenge_method=params["code_challenge_method"],
        scopes=granted,
    )
    session_auth.record_audit(
        table="oauth_authorization_codes",
        row_id=int(issued["row_id"]),
        field="authorize",
        old_value=None,
        new_value=None,  # never the code
        reason=(
            f"authorization code issued to client {params['client_id']} "
            f"for user {user.get('username')} ({len(granted)} scope(s))"
        ),
    )

    separator = "&" if "?" in params["redirect_uri"] else "?"
    target = (
        f"{params['redirect_uri']}{separator}code={quote(issued['code'], safe='')}"
    )
    if params["state"]:
        target += f"&state={quote(params['state'], safe='')}"
    # RFC 9207: ``iss`` must equal the advertised issuer exactly — ChatGPT and
    # Codex check it before redeeming the code, and only use their stable
    # connector redirect URI when the AS does this.
    target += f"&iss={quote(oauth.public_origin(request.scope).rstrip('/'), safe='')}"
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})


# ── RFC 6749 §3.2 — token endpoint ──────────────────────────────────────────

#: Per-IP cap on ``POST /oauth/token``. Refresh rotation makes this the one
#: OAuth endpoint a healthy client calls repeatedly, so it is looser than
#: registration (10/min) but still far below any legitimate interactive rate.
DEFAULT_TOKEN_RPM = 30


def _parse_basic(header: str) -> tuple[str | None, str | None]:
    """Decode ``Authorization: Basic base64(urlencode(id):urlencode(secret))``.

    RFC 6749 §2.3.1: both halves are form-urlencoded *before* base64 (many
    clients send them raw; ``unquote`` on a raw value is a no-op unless it
    contains a literal ``%``).  Malformed input yields ``(None, None)`` — the
    caller answers ``invalid_client``, never an exception.
    """
    import base64

    try:
        decoded = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
    except Exception:  # noqa: BLE001 - any decode failure is just "no credentials"
        return None, None
    client_id, sep, secret = decoded.partition(":")
    if not sep:
        return None, None
    from urllib.parse import unquote

    return unquote(client_id) or None, unquote(secret) or None


async def _token_rate_limit(scope: Any) -> None:
    capacity = DEFAULT_TOKEN_RPM
    allowed, _remaining, _reset, retry_after = await run_in_threadpool(
        limiter.hit, f"oauth:token:{client_ip(scope)}", capacity
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "scope": "oauth_token",
                "limit_rpm": capacity,
                "retry_after_s": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


async def _token_client_auth(
    request: Request, form: dict[str, str]
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Authenticate the caller against its registered method (§2.3).

    One method per request: ``client_secret_basic`` wins if the header is
    present, else ``client_secret_post`` (body secret), else ``none`` (public
    client).  A confidential client may use either secret-bearing method
    (RFC 6749 §2.3.1 does not require the registered spelling), but the
    secret itself must match and a public client can never present one.

    ``get_client`` runs in a worker thread for the same reason as the
    authorize endpoint: a CIMD client_id is an https URL.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        cid, secret = _parse_basic(header)
        method = "client_secret_basic"
    elif form.get("client_secret"):
        cid, secret = form.get("client_id"), form.get("client_secret")
        method = "client_secret_post"
    else:
        cid, secret = form.get("client_id"), None
        method = "none"
    client = await run_in_threadpool(oauth.get_client, cid)
    if client is None:
        return None, _invalid_client("unknown client_id", basic=method == "client_secret_basic")
    registered = str(client.get("token_endpoint_auth_method") or "none")
    if method == "none":
        # A confidential client cannot downgrade to anonymous.
        if client.get("client_secret_hash"):
            return None, _invalid_client(
                f"client must authenticate with {registered}", basic=False
            )
    elif not client.get("client_secret_hash"):
        # A public client cannot present (or fabricate) a secret.
        return None, _invalid_client(
            "a public client cannot present a client secret",
            basic=method == "client_secret_basic",
        )
    elif not oauth.verify_client_secret(client, secret):
        return None, _invalid_client(
            "client authentication failed", basic=method == "client_secret_basic"
        )
    return client, None


@router.post("/oauth/token")
async def oauth_token(request: Request) -> Response:
    """``POST /oauth/token`` — authorization_code + refresh_token grants.

    RFC 6749 §5.2 envelopes, ``Cache-Control: no-store`` on every response,
    and a single ``invalid_grant`` for every code/refresh failure so a prober
    cannot tell "consumed" from "expired" from "wrong client".
    """
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return _oauth_error(
            415,
            "unsupported_media_type",
            "POST /oauth/token expects application/x-www-form-urlencoded",
        )
    await _token_rate_limit(request.scope)
    raw = await request.body()
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    form = {key: values[0] for key, values in parsed.items() if values}

    client, error = await _token_client_auth(request, form)
    if error is not None:
        return error
    assert client is not None

    # RFC 8707 §2.2 — a foreign audience is refused *before* any code is
    # consumed, so a misdirected request cannot burn a single-use grant.
    if not oauth.resource_is_served(form.get("resource"), oauth.public_origin(request.scope)):
        return _oauth_error(
            400, "invalid_target", "resource must be this server's MCP endpoint"
        )

    grant_type = str(form.get("grant_type") or "")
    if grant_type == "authorization_code":
        try:
            grant = await run_in_threadpool(
                oauth.consume_authorization_code,
                code=form.get("code"),
                client_id=str(client["client_id"]),
                redirect_uri=str(form.get("redirect_uri") or ""),
                code_verifier=form.get("code_verifier"),
            )
        except oauth.AuthorizationCodeError:
            return _oauth_error(400, "invalid_grant", "authorization code rejected")
        tokens = await run_in_threadpool(
            oauth.issue_tokens,
            client_id=str(client["client_id"]),
            user_id=int(grant["user_id"]),
            scopes=list(grant["scopes"]),
        )
    elif grant_type == "refresh_token":
        try:
            tokens = await run_in_threadpool(
                oauth.refresh_tokens,
                refresh_token=form.get("refresh_token"),
                client_id=str(client["client_id"]),
            )
        except oauth.TokenError:
            return _oauth_error(400, "invalid_grant", "refresh token rejected")
    else:
        return _oauth_error(
            400,
            "unsupported_grant_type",
            "only authorization_code and refresh_token are implemented",
        )
    return JSONResponse(content=tokens, headers={"Cache-Control": "no-store"})
