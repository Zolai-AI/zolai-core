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

The ``/oauth/register`` (RFC 7591), ``/oauth/authorize`` (authorization code
+ PKCE, S256 only) and ``/oauth/token`` (RFC 6749 §4.1.3 / §6) routes are
appended below as their components land.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..auth import oauth

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
