"""MCP dual-era protocol — ``server/discover``, ``-32022`` and legacy ``initialize``.

The published MCP specification defines two eras (spec: *basic/versioning*):

* **modern** — revision ``2026-07-28`` and later. The handshake was removed by
  SEP-2575; the version travels as per-request metadata
  (``params._meta["io.modelcontextprotocol/protocolVersion"]`` and/or the
  ``MCP-Protocol-Version`` header) and ``server/discover`` is the mandatory
  discovery call.
* **legacy** — ``2025-11-25`` and earlier. A session opens with ``initialize``,
  which negotiates a version.

A *dual-era* server serves both. This module proves it does, and proves the one
refusal the spec mandates: a request that **declares** a version the server does
not implement is answered with ``-32022`` carrying
``data: {"supported": [...], "requested": ...}`` — surfaced as an HTTP **400**
for a lone request, so a modern client's era probe recognises this server.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.agent.tools.registry import allow_list
from zolai.agent.tools.types import ToolResult
from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.mcp import protocol as proto


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _dispatch(message, *, allow=None, version=None):
    def call_tool(name: str, arguments: dict) -> ToolResult:
        return ToolResult(tool_name=name, ok=True, data={"echo": arguments})

    return proto.handle_message(
        message,
        allow=allow if allow is not None else allow_list("public"),
        call_tool=call_tool,
        protocol_version=version,
    )


def _meta(version: str) -> dict:
    return {"_meta": {proto.META_PROTOCOL_VERSION: version}}


class TestRequestedVersion:
    def test_meta_declaration_wins(self) -> None:
        assert proto.requested_version({"x": 1, **(_meta("2026-07-28"))}) == "2026-07-28"

    def test_falls_back_to_the_header(self) -> None:
        assert proto.requested_version({}, "2025-06-18") == "2025-06-18"

    def test_meta_beats_the_header(self) -> None:
        assert (
            proto.requested_version({**_meta("2026-07-28")}, "1999-01-01") == "2026-07-28"
        )

    def test_no_declaration_is_none_the_server_serves_it_anyway(self) -> None:
        assert proto.requested_version(None) is None
        assert proto.requested_version({}) is None
        assert proto.requested_version({"_meta": "not-a-dict"}) is None
        assert proto.requested_version({"_meta": {proto.META_PROTOCOL_VERSION: "  "}}) is None


class TestDiscover:
    def test_discover_result_is_generated_from_the_constants(self) -> None:
        result = proto.discover_result()
        assert result["resultType"] == "complete"
        assert result["supportedVersions"] == list(proto.SUPPORTED_PROTOCOL_VERSIONS)
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["instructions"] == proto.SERVER_INSTRUCTIONS
        assert result["ttlMs"] == proto.DISCOVER_TTL_MS
        assert result["cacheScope"] == proto.DISCOVER_CACHE_SCOPE
        info = result["_meta"][proto.META_SERVER_INFO]
        assert info == {"name": proto.SERVER_NAME, "version": proto.SERVER_VERSION}

    def test_instructions_fit_the_client_guidance_budget(self) -> None:
        assert 0 < len(proto.SERVER_INSTRUCTIONS) <= 512

    def test_server_discover_is_answered(self) -> None:
        response = _dispatch({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        assert response["result"]["supportedVersions"] == list(proto.SUPPORTED_PROTOCOL_VERSIONS)

    def test_server_discover_is_answered_for_a_declared_supported_version(self) -> None:
        response = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": _meta("2026-07-28")}
        )
        assert response["result"]["instructions"] == proto.SERVER_INSTRUCTIONS

    def test_legacy_initialize_is_retained(self) -> None:
        """A handshake-era client still opens a session (the legacy branch)."""
        response = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
        )
        assert response["result"]["protocolVersion"] == "2025-11-25"

    def test_ping_is_still_answered(self) -> None:
        # Removed by 2026-07-28 but kept as a harmless superset for handshake clients.
        assert _dispatch({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {}


class TestUnsupportedVersion:
    def test_a_declared_unsupported_meta_version_is_refused(self) -> None:
        response = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": _meta("1999-01-01")}
        )
        error = response["error"]
        assert error["code"] == proto.UNSUPPORTED_PROTOCOL_VERSION == -32022
        assert error["data"] == {
            "supported": list(proto.SUPPORTED_PROTOCOL_VERSIONS),
            "requested": "1999-01-01",
        }

    def test_the_refusal_is_returned_for_every_method(self) -> None:
        for method in ("server/discover", "initialize", "tools/list", "ping"):
            response = _dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": _meta("1999-01-01")}
            )
            assert response["error"]["code"] == -32022, method

    def test_a_header_only_declaration_is_refused(self) -> None:
        response = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, version="1999-01-01"
        )
        assert response["error"]["code"] == -32022
        assert response["error"]["data"]["requested"] == "1999-01-01"

    def test_a_supported_meta_version_serves_normally(self) -> None:
        for version in proto.SUPPORTED_PROTOCOL_VERSIONS:
            response = _dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": _meta(version)}
            )
            assert "error" not in response, version

    def test_a_supported_meta_version_with_a_bad_header_is_served(self) -> None:
        """``_meta`` wins, so a stale header cannot reject a modern request."""
        response = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": _meta("2026-07-28")},
            version="1999-01-01",
        )
        assert "error" not in response

    def test_a_lone_refusal_is_http_400(self) -> None:
        status, body = proto.handle_payload(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": _meta("1999-01-01")},
            allow=allow_list("public"),
            call_tool=lambda n, a: ToolResult(tool_name=n, ok=True),
        )
        assert status == proto.UNSUPPORTED_VERSION_HTTP_STATUS == 400
        assert body["error"]["code"] == -32022

    def test_a_notification_with_a_bad_version_gets_no_response(self) -> None:
        status, body = proto.handle_payload(
            {"jsonrpc": "2.0", "method": "tools/list", "params": _meta("1999-01-01")},
            allow=allow_list("public"),
            call_tool=lambda n, a: ToolResult(tool_name=n, ok=True),
        )
        assert (status, body) == (202, None)

    def test_a_batch_keeps_its_errors_inside_a_200(self) -> None:
        status, body = proto.handle_payload(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": _meta("1999-01-01")},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ],
            allow=allow_list("public"),
            call_tool=lambda n, a: ToolResult(tool_name=n, ok=True),
        )
        assert status == 200
        assert body[0]["error"]["code"] == -32022
        assert body[1]["result"] == {}

    def test_a_legacy_initialize_with_a_bogus_protocol_version_still_handshakes(self) -> None:
        """``protocolVersion`` is the legacy negotiation field, not ``_meta``.

        A handshake client cannot fall forward, so the legacy rule answers a
        valid version (the server's latest) rather than refusing.
        """
        response = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
        )
        assert response["result"]["protocolVersion"] == proto.PROTOCOL_VERSION


class TestDualEraOverHttp:
    """The same behaviour through the real transport (both paths)."""

    @pytest.mark.parametrize("path", ["/api/v1/mcp", "/mcp"])
    def test_server_discover_over_http(self, client, path) -> None:
        resp = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"]["supportedVersions"] == list(proto.SUPPORTED_PROTOCOL_VERSIONS)

    @pytest.mark.parametrize("path", ["/api/v1/mcp", "/mcp"])
    def test_an_unsupported_header_version_is_a_400(self, client, path) -> None:
        resp = client.post(
            path,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={proto.PROTOCOL_VERSION_HEADER: "1999-01-01"},
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == -32022
        assert body["error"]["data"]["requested"] == "1999-01-01"
        assert body["error"]["data"]["supported"] == list(proto.SUPPORTED_PROTOCOL_VERSIONS)

    def test_a_modern_request_over_http_is_served(self, client) -> None:
        resp = client.post(
            "/api/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": _meta("2026-07-28")},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["result"]["tools"]) == 8

    def test_a_legacy_initialize_over_http_is_served(self, client) -> None:
        resp = client.post(
            "/api/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["protocolVersion"] == "2025-11-25"
