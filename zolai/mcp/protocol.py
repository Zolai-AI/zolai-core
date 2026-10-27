"""Pure, stateless JSON-RPC 2.0 dispatch for the MCP tool surface.

**Dual-era.** The MCP specification defines two eras, and this server serves
both from the one endpoint (spec: *"A dual-era server MAY serve both eras
concurrently on the same endpoint or process"*):

- **legacy** — a session opened with the ``initialize`` handshake (revisions
  ``2025-11-25`` and earlier). Retained here (:func:`initialize_result`) so
  every handshake-era client keeps working.
- **modern** — revision ``2026-07-28`` and later, which **removed** the
  handshake: the version travels as per-request metadata and ``server/discover``
  is the mandatory discovery call (:func:`discover_result`). A version the
  server does not implement is refused with ``-32022``
  (:data:`UNSUPPORTED_PROTOCOL_VERSION`) carrying
  ``data: {"supported": [...], "requested": ...}``.

Design constraints (all enforced here):

- **Stateless.** There is no session store and no per-connection state: a
  client may send ``initialize`` and a ``tools/call`` in one batch, or skip
  ``initialize`` entirely (the capabilities are fixed and documented, so
  nothing is negotiated). A request that declares no version is served as-is —
  it is era-ambiguous, and this server's tool surface is identical in both eras.
- **Pure.** No FastAPI, no database, no network, no clock. Tool execution is
  injected as ``call_tool`` so this module is directly unit-testable; the
  transport decides *which* tools the caller may use.
- **Server-side authority.** The tool set is whatever ``allow`` says. Nothing
  in a client message (no ``scope``/``role``/``tools`` field) can widen it.
- **No disclosure.** An unknown tool and a tool outside ``allow`` produce the
  *same* ``-32602`` error, so the surface never reveals that an admin-only tool
  exists.

Transports return :func:`handle_payload`'s ``(http_status, body)``:
``(200, response)`` for a request, ``(202, None)`` for a notification, and
``(400, response)`` for a lone unsupported-version error (the streamable-HTTP
*binding*'s server-validation status, so a modern client's era probe recognises
it — see :data:`UNSUPPORTED_VERSION_HTTP_STATUS`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from .. import __version__
from ..agent.tools.errors import safe_message
from ..agent.tools.registry import get_tool, mcp_tools, ordered_names
from ..agent.tools.types import ToolErrorCode, ToolResult

# ── Version + identity ───────────────────────────────────────────────────────
#
# Verified 2026-10-18 against the official specification:
#   * https://modelcontextprotocol.io/specification/latest — the published
#     revision is ``2026-07-28`` (its authoritative schema is
#     ``schema/2026-07-28/schema.ts``); the ``llms.txt`` index lists
#     ``2026-07-28``, ``2025-11-25``, ``2025-06-18`` and ``2025-03-26``.
#   * https://modelcontextprotocol.io/specification/draft/basic/versioning —
#     "Modern: protocol versions that convey version, identity, and
#     capabilities as per-request metadata (revision 2026-07-28 and later);
#     Legacy: protocol versions that establish a session with an initialize
#     handshake (2025-11-25 and earlier)"; an unimplemented version MUST be
#     answered with ``-32022`` + ``data: {supported, requested}``; and a
#     "dual-era" server may serve both concurrently.
#   * https://modelcontextprotocol.io/specification/draft/server/discover —
#     ``server/discover`` (introduced by SEP-2575) MUST be implemented by a
#     2026-07-28 server; SEP-2575 is also what removed ``initialize``.

#: The current revision — the first *modern* era, which has no handshake.
PROTOCOL_VERSION = "2026-07-28"

#: Accepted versions, newest first: the modern revision plus every legacy
#: revision upstream still publishes. ``initialize`` echoes a version from this
#: list; a *declared* version outside it is refused with
#: :data:`UNSUPPORTED_PROTOCOL_VERSION`.
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
)

SERVER_NAME = "zolai-core"
#: Implementation version — one value for the whole product (core#13): the
#: package ``zolai.__version__``, never a second literal.
SERVER_VERSION = __version__

#: A self-contained ``server/discover`` blurb an LLM client can act on (kept
#: under ChatGPT's 512-character guidance). Names the corpus, the grammar
#: ground truth, the recommended call order and the honest-degradation contract.
SERVER_INSTRUCTIONS = (
    "Zolai (Tedim; ZVS 2018 orthography) language toolkit over the project corpus. "
    "Start with rag_search / dictionary_lookup / verse_lookup, then analyze_text or "
    "grammar_check. Word order is SOV; 'in' marks the transitive agent; "
    "'kei' negates; 'hiam' asks a yes/no question. Burmese: search_myanmar_dictionary, "
    "translate_zo_to_myanmar, translate_myanmar_to_zo. AI-backed answers come from the "
    "corpus only and report retrieval_only when no provider key is set — never invent "
    "a word, a meaning or a translation."
)

JSONRPC_VERSION = "2.0"

# ── JSON-RPC 2.0 error codes ─────────────────────────────────────────────────

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
#: ``UnsupportedProtocolVersionError`` — the version a request declared is not
#: one this server implements (spec: versioning page, ``-32022``).
UNSUPPORTED_PROTOCOL_VERSION = -32022

#: HTTP status the *binding* uses for a lone unsupported-version error, so the
#: modern-era probe ("attempt a modern request and inspect the body of a
#: ``400 Bad Request``") recognises this server as modern rather than legacy.
UNSUPPORTED_VERSION_HTTP_STATUS = 400

# ── ``_meta`` keys + the HTTP version header ─────────────────────────────────

#: ``_meta`` key carrying the per-request version (modern era).
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
#: ``_meta`` key carrying the server identity in a discovery result.
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
#: Header the same version also travels in on HTTP (SEP-2243 / streamable HTTP).
#: Starlette lower-cases header names, hence the lower-case literal.
PROTOCOL_VERSION_HEADER = "mcp-protocol-version"
#: ``server/discover`` caching hints — the registry is static, so a client may
#: cache the result (spec: discover supports caching).
DISCOVER_TTL_MS = 3_600_000
DISCOVER_CACHE_SCOPE = "public"

#: ``call_tool(name, arguments) -> ToolResult`` — injected by the transport.
ToolExecutor = Callable[[str, dict[str, Any]], ToolResult]

#: Client → server notifications we accept (no response is ever sent).
NOTIFICATION_METHODS: frozenset[str] = frozenset(
    {"notifications/initialized", "notifications/cancelled"}
)

#: List methods answered with an empty list for **compatibility only**.
#: The ``capabilities`` block advertises ``tools`` and nothing else, so a
#: spec-correct client never calls these; several real clients list anyway and
#: some surface ``-32601`` as a hard error.  This server genuinely has no
#: resources and no prompts, so an empty list is the truthful answer — and it is
#: mapped to the exact result key the spec defines for each method.
EMPTY_LIST_METHODS: dict[str, str] = {
    "resources/list": "resources",
    "resources/templates/list": "resourceTemplates",
    "prompts/list": "prompts",
}

#: Requests answered with ``{}`` for compatibility only (handshake-era
#: housekeeping calls a client may send without reading ``capabilities``).
NOOP_METHODS: frozenset[str] = frozenset({"logging/setLevel"})


def jsonrpc_result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """A JSON-RPC success response."""
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}


def jsonrpc_error(
    message_id: Any, code: int, message: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A JSON-RPC error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "error": error}


def parse_error_response(message: str = "Parse error") -> dict[str, Any]:
    """The body for a transport-level parse error (no usable ``id``)."""
    return jsonrpc_error(None, PARSE_ERROR, message)


# ── Methods ──────────────────────────────────────────────────────────────────


def requested_version(
    params: dict[str, Any] | None, header_version: str | None = None
) -> str | None:
    """The protocol version a request declares, or ``None`` when it declares none.

    Modern clients declare it in ``params._meta``
    (:data:`META_PROTOCOL_VERSION`); on HTTP it also travels in the
    :data:`PROTOCOL_VERSION_HEADER` header, which the transport passes through.
    ``None`` means *era-ambiguous* — this server serves such a request as-is,
    because its tool surface and capabilities are identical in both eras.
    """
    meta = (params or {}).get("_meta")
    if isinstance(meta, dict):
        declared = meta.get(META_PROTOCOL_VERSION)
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
    if isinstance(header_version, str) and header_version.strip():
        return header_version.strip()
    return None


def unsupported_version_error(message_id: Any, requested: str) -> dict[str, Any]:
    """``-32022`` naming every version this server does implement.

    The ``supported`` list is what lets a modern client retry with a mutually
    supported version, and what a legacy client (which has no fall-forward
    mechanism) can at least surface to its user.
    """
    return jsonrpc_error(
        message_id,
        UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": requested},
    )


def initialize_result(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """The legacy ``initialize`` result (the handshake era's version of discover).

    ``protocolVersion`` echoes the client's version when it is one we accept
    (:data:`SUPPORTED_PROTOCOL_VERSIONS`), otherwise the server's latest — the
    legacy lifecycle's rule, kept because a legacy client has no fall-forward:
    it must be answered with a version the server does support.  The only
    capability advertised is ``tools`` (with ``listChanged: false``: the registry
    is static, so no ``notifications/tools/list_changed`` will ever be sent).
    """
    requested = str((params or {}).get("protocolVersion") or "").strip()
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        # Legacy clients (Claude Desktop, Cursor, OpenCode, the Inspector) read
        # the model-facing guidance from ``result.instructions``; the modern era
        # carries the identical text on ``server/discover``.  One constant, so
        # the two eras cannot drift apart.
        "instructions": SERVER_INSTRUCTIONS,
    }


def discover_result() -> dict[str, Any]:
    """The modern ``server/discover`` result.

    Generated from this module's own constants, so what a client discovers can
    never drift from what ``initialize`` answers or from the versions this
    server actually accepts.
    """
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": {"tools": {"listChanged": False}},
        "instructions": SERVER_INSTRUCTIONS,
        "_meta": {META_SERVER_INFO: {"name": SERVER_NAME, "version": SERVER_VERSION}},
        "ttlMs": DISCOVER_TTL_MS,
        "cacheScope": DISCOVER_CACHE_SCOPE,
    }


def tools_list_result(allow: Iterable[str]) -> dict[str, Any]:
    """The ``tools/list`` result for a caller's allow-list."""
    return {"tools": mcp_tools(ordered_names(allow))}


def tool_result_payload(result: ToolResult) -> dict[str, Any]:
    """An MCP ``tools/call`` result: content + the honest ``isError`` flag.

    A success carries the tool's own data.  A failure carries a **typed** code
    and a human sentence — never a raw exception string, which would publish
    internal type names and could echo SQL, file paths or provider payloads into
    text the client renders to a person (see
    :mod:`zolai.agent.tools.errors`; the raw detail stays in the server log).
    """
    if result.ok:
        payload: dict[str, Any] = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result.data, ensure_ascii=False, default=str),
                }
            ],
            "isError": False,
        }
        # Structured tool output (spec 2025-06-18 and later): a modern client
        # may read the result programmatically instead of re-parsing the text
        # block.  ``structuredContent`` is defined as a JSON *object*, so only
        # an object result qualifies, and it is only ever the same value the
        # text already carries — never additional data the text withholds.
        if isinstance(result.data, dict):
            payload["structuredContent"] = result.data
        return payload

    code = result.error_code or ToolErrorCode.INTERNAL
    message = safe_message(code, result.error)
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        # The machine-readable half: a client can branch on ``code`` without
        # parsing prose, and ``message`` is the exact text it already rendered.
        "structuredContent": {
            "error": {
                "code": code.value,
                "message": message,
                "tool": result.tool_name,
            }
        },
    }


def _call_tool_result(
    message_id: Any,
    params: dict[str, Any],
    *,
    allow: frozenset[str],
    call_tool: ToolExecutor,
) -> dict[str, Any]:
    """Dispatch ``tools/call`` — unknown and not-allowed are the same error."""
    name = params.get("name")
    if not isinstance(name, str) or not name.strip():
        return jsonrpc_error(
            message_id, INVALID_PARAMS, "Invalid params: 'name' must be a tool name"
        )
    name = name.strip()
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return jsonrpc_error(
            message_id, INVALID_PARAMS, "Invalid params: 'arguments' must be an object"
        )
    # One refusal for both cases — a client must not learn that a name exists
    # but is privileged.
    if get_tool(name) is None or name not in allow:
        return jsonrpc_error(message_id, INVALID_PARAMS, f"Unknown tool: {name}")
    return jsonrpc_result(message_id, tool_result_payload(call_tool(name, arguments)))


# ── Dispatch ─────────────────────────────────────────────────────────────────


def handle_message(
    message: Any,
    *,
    allow: frozenset[str],
    call_tool: ToolExecutor,
    protocol_version: str | None = None,
) -> dict[str, Any] | None:
    """One JSON-RPC message → a response, or ``None`` for a notification.

    Args:
        message: the decoded JSON-RPC message.
        allow: the caller's tool allow-list (server-derived — never a client field).
        call_tool: executor for an allowed tool.
        protocol_version: the version the transport read off the wire (the
            ``MCP-Protocol-Version`` header), used only when the message itself
            declares none in ``_meta``.

    Returns:
        A JSON-RPC response dict, or ``None`` when the message is a
        notification (a *notification never gets a response*, not even for an
        error).
    """
    if not isinstance(message, dict):
        return jsonrpc_error(None, INVALID_REQUEST, "Invalid Request")

    notification = "id" not in message
    message_id = message.get("id")
    method = message.get("method")

    if message.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str) or not method:
        if notification:
            return None
        return jsonrpc_error(message_id, INVALID_REQUEST, "Invalid Request")

    params = message.get("params")
    if params is not None and not isinstance(params, dict):
        if notification:
            return None
        return jsonrpc_error(message_id, INVALID_PARAMS, "Invalid params: must be an object")

    if method in NOTIFICATION_METHODS:
        return None

    # A *declared* version we do not implement is refused before any method runs
    # (spec: versioning — this is the modern era's only negotiation mechanism,
    # and it is per-request, so a rejected request costs the client nothing).
    declared = requested_version(params, protocol_version)
    if declared is not None and declared not in SUPPORTED_PROTOCOL_VERSIONS:
        return None if notification else unsupported_version_error(message_id, declared)

    # ``server/discover`` is the modern entry point; ``initialize`` is the
    # legacy one. Both are answered, which is what makes this a dual-era server.
    if method == "server/discover":
        response = jsonrpc_result(message_id, discover_result())
    elif method == "initialize":
        response = jsonrpc_result(message_id, initialize_result(params))
    elif method == "ping":
        # Removed from the protocol by 2026-07-28, but answering a liveness
        # probe is harmless and handshake-era clients still send it — dropping
        # it could only break a caller, so this server keeps the superset.
        response = jsonrpc_result(message_id, {})
    elif method == "tools/list":
        response = jsonrpc_result(message_id, tools_list_result(allow))
    elif method == "tools/call":
        response = _call_tool_result(message_id, params or {}, allow=allow, call_tool=call_tool)
    elif method in EMPTY_LIST_METHODS:
        response = jsonrpc_result(message_id, {EMPTY_LIST_METHODS[method]: []})
    elif method in NOOP_METHODS:
        response = jsonrpc_result(message_id, {})
    else:
        response = jsonrpc_error(message_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    # A notification never gets a response — not even for an error (JSON-RPC 2.0).
    return None if notification else response


def handle_payload(
    payload: Any,
    *,
    allow: frozenset[str],
    call_tool: ToolExecutor,
    protocol_version: str | None = None,
) -> tuple[int, Any]:
    """Dispatch one message **or** a batch.

    Returns:
        ``(http_status, body)`` — ``(202, None)`` when every message was a
        notification, ``(400, error)`` for a lone unsupported-version error,
        ``(200, response)`` otherwise.
    """
    if isinstance(payload, list):
        if not payload:
            return 200, jsonrpc_error(None, INVALID_REQUEST, "Invalid Request: empty batch")
        responses = [
            response
            for response in (
                handle_message(
                    item, allow=allow, call_tool=call_tool, protocol_version=protocol_version
                )
                for item in payload
            )
            if response is not None
        ]
        if not responses:
            return 202, None
        # A batch is a JSON-RPC-level construct: its per-message errors stay
        # inside a 200, exactly as before.
        return 200, responses

    response = handle_message(
        payload, allow=allow, call_tool=call_tool, protocol_version=protocol_version
    )
    if response is None:
        return 202, None
    if _is_unsupported_version(response):
        return UNSUPPORTED_VERSION_HTTP_STATUS, response
    return 200, response


def _is_unsupported_version(response: Any) -> bool:
    """Whether a response is the lone ``-32022`` version refusal."""
    return (
        isinstance(response, dict)
        and isinstance(response.get("error"), dict)
        and response["error"].get("code") == UNSUPPORTED_PROTOCOL_VERSION
    )


__all__ = [
    "DISCOVER_CACHE_SCOPE",
    "EMPTY_LIST_METHODS",
    "DISCOVER_TTL_MS",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "INTERNAL_ERROR",
    "JSONRPC_VERSION",
    "META_PROTOCOL_VERSION",
    "META_SERVER_INFO",
    "METHOD_NOT_FOUND",
    "NOTIFICATION_METHODS",
    "NOOP_METHODS",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "PROTOCOL_VERSION_HEADER",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "UNSUPPORTED_VERSION_HTTP_STATUS",
    "discover_result",
    "handle_message",
    "handle_payload",
    "initialize_result",
    "jsonrpc_error",
    "jsonrpc_result",
    "parse_error_response",
    "requested_version",
    "tool_result_payload",
    "tools_list_result",
    "unsupported_version_error",
]
