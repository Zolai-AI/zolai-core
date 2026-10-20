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

``POST /oauth/register`` (RFC 7591) appends below; ``/oauth/authorize`` and
``/oauth/token`` follow in the next commits of this series.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..api import auth as api_auth
from ..auth import oauth
from .auth_middleware import client_ip, limiter

router = APIRouter(tags=["oauth"])

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


def _pr_metadata(request: Request) -> JSONResponse:
    return _json(oauth.protected_resource_metadata(oauth.public_origin(request.scope)))


@router.get("/.well-known/oauth-protected-resource")
async def well_known_oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 metadata — what ``WWW-Authenticate: resource_metadata`` points at."""
    return _pr_metadata(request)


@router.get("/.well-known/oauth-protected-resource/mcp")
async def well_known_oauth_protected_resource_mcp(request: Request) -> JSONResponse:
    """RFC 9728 §2 inserted-path form (``resource`` is ``<origin>/mcp``)."""
    return _pr_metadata(request)


@router.get("/mcp/.well-known/oauth-protected-resource")
async def mcp_relative_oauth_protected_resource(request: Request) -> JSONResponse:
    """Path-relative form — the variant MCP clients probe next to the endpoint."""
    return _pr_metadata(request)


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


def _invalid_metadata(description: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "invalid_client_metadata",
            "error_description": description,
        },
    )


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
                raise _invalid_metadata(
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
        raise _invalid_metadata(str(exc)) from None

    # The response carries a plaintext secret — never cacheable.
    return JSONResponse(content=response, status_code=201, headers={"Cache-Control": "no-store"})
