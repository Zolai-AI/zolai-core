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
:func:`zolai.api.rbac.role_for` on the key record the middleware resolved, then
:func:`zolai.agent.tools.registry.allow_list`:

| caller | scope | tools |
|---|---|---|
| anonymous | ``public`` | 8 |
| valid key (member) | ``member`` | 31 |
| admin key | ``admin`` | 39 |

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
for a body that is not JSON at all or for a lone unsupported-version error —
every other failure is a JSON-RPC error object inside a ``200``/``202``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..agent.tools.executor import execute_tool
from ..agent.tools.registry import allow_list
from ..agent.tools.types import ToolCall, ToolResult
from ..mcp import protocol
from . import rbac

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["mcp"])
#: Bare ``/mcp`` alias for the public MCP host (``mcp.zolai.space`` proxies it
#: here).  Same handler, same auth, same tool scope.
root_router = APIRouter(tags=["mcp"])


def tool_scope_for_request(request: Request) -> str:
    """Tool scope for the caller, derived server-side from the resolved key.

    ``anonymous`` → ``public`` (the retrieval-only set); a member/admin key maps
    straight to its :func:`zolai.agent.tools.registry.allow_list` scope.
    """
    record: dict[str, Any] | None = getattr(request.state, "api_key", None)
    role = rbac.role_for(record)
    return "public" if role == rbac.ROLE_ANONYMOUS else role


async def handle_mcp_request(request: Request) -> Response:
    """Dispatch one JSON-RPC message or a batch.

    The caller's allow-list is resolved **before** the body is read, so nothing
    a client writes can change which tools it may use.
    """
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
    # Accept prefers event-stream we wrap the *single* response as one SSE
    # message event — still stateless: no stream stays open afterwards.
    if "text/event-stream" in request.headers.get("accept", ""):
        sse = f"event: message\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"
        return Response(
            content=sse,
            media_type="text/event-stream",
            status_code=status,
            headers={"Cache-Control": "no-store"},
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
