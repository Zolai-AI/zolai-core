"""MCP transport — ``POST /api/v1/mcp`` **and** the bare ``POST /mcp`` alias.

This is the surface an MCP client (ChatGPT, Gemini, Claude Desktop, a CLI)
consumes.  It is deliberately thin: :mod:`zolai.mcp.protocol` owns every
protocol decision and this module owns only the transport concerns.

**Two paths, one implementation.**  The public host ``mcp.zolai.space`` proxies
``POST /mcp`` to core ``:8001/mcp``, while the in-cluster/API host speaks
``/api/v1/mcp``; both are the same handler, so a client configuration written
against either keeps working.  Both are declared *before* ``serve_static``'s
``GET /{path:path}`` catch-all in :mod:`zolai.api.server` (the catch-all is
GET-only, but route order is the project's standing invariant).

**Auth (founder decision): key → role → tool set, never a client field.**
The tool set is derived *server-side* from the authenticated caller:
:func:`zolai.agent.tools.registry.tool_tier_for_record` (which is
:func:`zolai.api.rbac.role_for` plus the role → tier table), then
:func:`zolai.agent.tools.registry.allow_list`:

| caller | scope | tools |
|---|---|---|
| anonymous | ``public`` | 8 |
| valid key (member) | ``member`` | 31 |
| admin key / admin account | ``admin`` | 39 |
| superadmin | ``admin`` | 39 — the tier is an *inventory*, not a rank; role grants are a route guard |

Both paths are declared in :mod:`zolai.api.rbac` as **public** routes so
``ZOLAI_API_AUTH=enforce`` never 401s an anonymous MCP client, and both ride
the stricter anonymous bucket (10/min) because a ``tools/call`` runs a real DB
query; ``ApiKeyMiddleware`` gates ``/mcp`` exactly as it gates ``/api/v1/mcp``,
so a *presented* key still widens the tool set.  A message may not widen its own
tool set, and a body may not name a scope: any ``scope``/``role``/``tools``
field is ignored.

**Dual-era.**  The request's protocol version is read from ``params._meta``
(modern era) or, failing that, from the ``MCP-Protocol-Version`` header, and
handed to the protocol layer.  An unimplemented version is refused with
``-32022`` inside an HTTP **400** (see :mod:`zolai.mcp.protocol`).

Responses: ``200`` for a request, ``202`` (no body) for a notification, ``400``
for a body that is not JSON at all, a lone unsupported-version error or a
header/body mismatch (``-32020``), and ``403`` for a browser ``Origin`` that is
not on the CORS allowlist (DNS-rebinding protection).  A request that declared
``2026-07-28`` and named an unknown method gets the modern binding's ``404``
(``-32601``); era-ambiguous requests keep ``200`` so a legacy client never
mistakes the endpoint for a missing one and falls back to HTTP+SSE.
Every other failure is a JSON-RPC error object inside a ``200``/``202``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..agent.tools.executor import execute_tool
from ..agent.tools.registry import allow_list, tool_tier_for_record
from ..agent.tools.types import ToolCall, ToolResult
from ..mcp import protocol
from .security import get_cors_origins

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["mcp"])
#: Bare ``/mcp`` alias for the public MCP host (``mcp.zolai.space`` proxies it
#: here).  Same handler, same auth, same tool scope.
root_router = APIRouter(tags=["mcp"])


def tool_scope_for_request(request: Request) -> str:
    """Tool scope for the caller, derived server-side from the resolved key.

    Delegates to :func:`zolai.agent.tools.registry.tool_tier_for_record` so the
    role → tier table is written **once**.  Inlining a ``public``/``admin``
    mapping here is how a new role lands as a tier that ``allow_list`` raises
    on — which is a 500 on every MCP call, not an honest 403.
    """
    record: dict[str, Any] | None = getattr(request.state, "api_key", None)
    return tool_tier_for_record(record)


def _origin_refused(request: Request) -> Response | None:
    """``403`` for a browser ``Origin`` outside the trusted allowlist.

    The streamable-HTTP binding requires Origin validation to prevent DNS
    rebinding: a page on an attacker-controlled origin must not be able to
    drive a locally reachable MCP endpoint.  A request with **no** ``Origin``
    is a non-browser client (ChatGPT, Claude Desktop, a CLI) and stays open;
    ``Origin: null`` and unknown origins are refused.  The allowlist is the
    same one CORS uses (:func:`zolai.api.security.get_cors_origins`, env-
    overridable), so a browser origin that can read the API can also speak
    MCP to it — and nothing else can.
    """
    origin = request.headers.get("origin")
    if origin is None or origin in get_cors_origins():
        return None
    return JSONResponse(
        status_code=403,
        content=protocol.jsonrpc_error(
            None, protocol.INVALID_REQUEST, f"Origin not allowed: {origin}"
        ),
    )


def _header_mismatch_response(request: Request, payload: Any) -> Response | None:
    """``400`` + ``-32020`` when a mirrored header disagrees with the body.

    The modern binding mirrors ``method`` into ``Mcp-Method`` and
    ``params.name``/``params.uri`` into ``Mcp-Name`` so intermediaries can
    route without parsing the body.  If the two disagree, one component would
    act on the header and another on the body — the confusion the spec's
    *Server Validation* rule exists to prevent.  Absent headers are tolerated:
    handshake-era clients never send them and the body is the source of truth,
    so leniency here cannot change which request runs.  A batch has no single
    method, so it is not validated against these headers.
    """
    if not isinstance(payload, dict):
        return None
    body_method = payload.get("method")
    header_method = request.headers.get(protocol.METHOD_HEADER)
    if header_method is not None and header_method != body_method:
        return JSONResponse(
            status_code=400,
            content=protocol.header_mismatch_error(
                payload.get("id"),
                f"Mcp-Method header value {header_method!r} does not match body value {body_method!r}",
            ),
        )
    header_name = request.headers.get(protocol.NAME_HEADER)
    params = payload.get("params")
    if header_name is not None and isinstance(params, dict):
        body_name = params.get("name")
        if body_name is None:
            body_name = params.get("uri")
        if body_name is not None and protocol.decode_header_value(header_name) != body_name:
            return JSONResponse(
                status_code=400,
                content=protocol.header_mismatch_error(
                    payload.get("id"),
                    f"Mcp-Name header value {header_name!r} does not match body value {body_name!r}",
                ),
            )
    return None


async def handle_mcp_request(request: Request) -> Response:
    """Dispatch one JSON-RPC message or a batch.

    The caller's allow-list is resolved **before** the body is read, so nothing
    a client writes can change which tools it may use.
    """
    refused = _origin_refused(request)
    if refused is not None:
        return refused

    scope = tool_scope_for_request(request)
    allow = allow_list(scope)
    declared_version = request.headers.get(protocol.PROTOCOL_VERSION_HEADER)

    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return JSONResponse(
            status_code=400, content=protocol.parse_error_response("Parse error: invalid JSON")
        )

    mismatch = _header_mismatch_response(request, payload)
    if mismatch is not None:
        return mismatch

    def call_tool(name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute one allow-listed tool (blocking; runs in the threadpool)."""
        return execute_tool(ToolCall(name=name, input=arguments, source="mcp"), allow)

    status, body = await run_in_threadpool(
        protocol.handle_payload,
        payload,
        allow=allow,
        call_tool=call_tool,
        protocol_version=declared_version,
    )
    if body is None:  # 202: a notification was accepted and nothing is returned
        return Response(status_code=status)
    # Streamable-HTTP lets the server answer either application/json or
    # text/event-stream. A client that explicitly asked for the SSE media type
    # (Claude's stack, the MCP Inspector) breaks on bare JSON, so when its
    # Accept names event-stream we wrap the *single* response as one SSE
    # message event — still stateless: no stream stays open afterwards.
    # Media types are case-insensitive (RFC 9110 §8.3.1), so lower-case first:
    # ``Text/Event-Stream`` must not fall through to the JSON branch.
    if "text/event-stream" in request.headers.get("accept", "").lower():
        sse = f"event: message\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"
        return Response(
            content=sse,
            media_type="text/event-stream",
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                # Spec: servers SHOULD send this so nginx does not buffer the
                # stream (mcp.zolai.space fronts this endpoint with nginx).
                "X-Accel-Buffering": "no",
            },
        )
    return JSONResponse(status_code=status, content=body)


def _method_not_allowed(method: str) -> Response:
    """``405`` for a method this stateless transport does not implement.

    The streamable-HTTP binding requires an MCP endpoint that offers no SSE
    stream to answer ``GET`` with ``405 Method Not Allowed``, and ``DELETE``
    (session termination) is the same here because there is no session to
    terminate.  Declaring these explicitly matters: without them ``GET /mcp``
    fell through to ``serve_static``'s ``GET /{path:path}`` catch-all and
    answered a confusing ``404`` — not the status a client probes for when it
    works out whether an endpoint is a streamable-HTTP server.

    The body carries a stable machine code *and* a human sentence, in the
    project's documented ``{"detail": {...}}`` envelope, so nothing a client
    sees is a raw server error.
    """
    return JSONResponse(
        status_code=405,
        content={
            "detail": {
                "error": "method_not_allowed",
                "message": f"{method} is not supported on the MCP endpoint. Use POST.",
                "allow": "POST",
            }
        },
        headers={"Allow": "POST"},
    )


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """Versioned path: ``POST /api/v1/mcp``."""
    return await handle_mcp_request(request)


@router.get("/mcp")
async def mcp_get_not_allowed() -> Response:
    """``GET /api/v1/mcp``: no SSE stream is offered, so ``405``."""
    return _method_not_allowed("GET")


@router.delete("/mcp")
async def mcp_delete_not_allowed() -> Response:
    """``DELETE /api/v1/mcp``: stateless, so there is no session to terminate."""
    return _method_not_allowed("DELETE")


@root_router.post("/mcp")
async def mcp_root_endpoint(request: Request) -> Response:
    """Public MCP host path: ``POST /mcp`` (the alias ``mcp.zolai.space`` uses)."""
    return await handle_mcp_request(request)


@root_router.get("/mcp")
async def mcp_root_get_not_allowed() -> Response:
    """``GET /mcp`` on the public MCP host: no SSE stream, so ``405``."""
    return _method_not_allowed("GET")


@root_router.delete("/mcp")
async def mcp_root_delete_not_allowed() -> Response:
    """``DELETE /mcp`` on the public MCP host: stateless, so ``405``."""
    return _method_not_allowed("DELETE")
