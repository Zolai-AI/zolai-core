"""MCP layer — the tool surface an MCP client (ChatGPT/Gemini/any) consumes.

Two halves:

* **Protocol (pure).** `zolai.mcp.protocol` is dispatched with an explicit
  allow-list and a stub executor, so the JSON-RPC contract is asserted with no
  HTTP, no DB and no network: version negotiation, `tools/list` per scope,
  `tools/call` success/failure, notifications getting **no** response, batches,
  and the one refusal message shared by an unknown tool and a not-allowed one.
* **Transport (HTTP).** `POST /api/v1/mcp` (and its bare `POST /mcp` alias)
  through the real app: anonymous is open in `warn` *and* `enforce`, the tool
  set is derived from the presented key (anon 8 / member 26 / admin 31), a
  client-supplied `scope` is ignored, and both routes are declared public
  routes on the strict anonymous bucket.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_agent_tools import _SEED_SQL

from zolai.agent.tools.registry import allow_list
from zolai.agent.tools.types import ToolCall, ToolErrorCode, ToolResult
from zolai.api import auth, rbac
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_ai_provider_tables, create_api_keys_table
from zolai.mcp import protocol as proto

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def data_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Throwaway store wired through ``config.paths`` (never the canonical DB)."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_ai_provider_tables(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    yield db_path
    mgr.dispose()


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'mcp_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def admin_key() -> dict:
    return auth.create_api_key(name="mcp-admin", scopes=["*"], created_by="test")


@pytest.fixture()
def member_key() -> dict:
    return auth.create_api_key(name="mcp-member", scopes=["dataset:read"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


# ---------------------------------------------------------------------------
# Pure protocol
# ---------------------------------------------------------------------------


def _stub_tool(result: ToolResult | None = None):
    calls: list[tuple[str, dict]] = []

    def call_tool(name: str, arguments: dict) -> ToolResult:
        calls.append((name, arguments))
        return result if result is not None else ToolResult(
            tool_name=name, ok=True, data={"echo": arguments}
        )

    return call_tool, calls


def _dispatch(message, *, allow=None, result=None):
    call_tool, calls = _stub_tool(result)
    response = proto.handle_message(
        message, allow=allow if allow is not None else allow_list("public"), call_tool=call_tool
    )
    return response, calls


class TestProtocolHandshake:
    def test_initialize_advertises_tools_and_the_given_version(self) -> None:
        response, _ = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        )
        result = response["result"]
        # Legacy negotiation: a supported requested version is echoed verbatim.
        assert result["protocolVersion"] == "2025-06-18"
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["serverInfo"]["name"] == proto.SERVER_NAME
        assert response["jsonrpc"] == "2.0" and response["id"] == 1

    def test_initialize_accepts_every_supported_version(self) -> None:
        for version in proto.SUPPORTED_PROTOCOL_VERSIONS:
            response, _ = _dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version}}
            )
            assert response["result"]["protocolVersion"] == version
        assert proto.SUPPORTED_PROTOCOL_VERSIONS == (
            "2026-07-28",
            "2025-11-25",
            "2025-06-18",
            "2025-03-26",
        )

    def test_initialize_falls_back_to_the_latest_known_version(self) -> None:
        response, _ = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
        )
        assert response["result"]["protocolVersion"] == proto.PROTOCOL_VERSION

    def test_ping(self) -> None:
        response, _ = _dispatch({"jsonrpc": "2.0", "id": "p", "method": "ping"})
        assert response["result"] == {}

    def test_initialized_notification_gets_no_response(self) -> None:
        response, _ = _dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert response is None


class TestToolsList:
    @pytest.mark.parametrize(
        ("scope", "count"), [("public", 8), ("member", 26), ("admin", 31)]
    )
    def test_tool_count_per_scope(self, scope: str, count: int) -> None:
        response, _ = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, allow=allow_list(scope)
        )
        tools = response["result"]["tools"]
        assert len(tools) == count
        assert all({"name", "title", "description", "inputSchema"} <= set(t) for t in tools)

    def test_admin_tools_are_absent_from_an_anonymous_list(self) -> None:
        response, _ = _dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in response["result"]["tools"]}
        assert names.isdisjoint({"knowledge_stats", "kb_research", "provider_status", "web_search"})

    def test_public_and_member_are_read_only_sets(self) -> None:
        for scope in ("public", "member"):
            response, _ = _dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, allow=allow_list(scope)
            )
            assert all(t["annotations"]["readOnlyHint"] for t in response["result"]["tools"])


class TestToolsCall:
    def test_allowed_tool_executes_and_returns_text_content(self) -> None:
        response, calls = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "grammar_check", "arguments": {"text": "Pasian in gam"}},
            }
        )
        result = response["result"]
        assert result["isError"] is False
        assert result["content"][0]["type"] == "text"
        assert json.loads(result["content"][0]["text"]) == {"echo": {"text": "Pasian in gam"}}
        assert calls == [("grammar_check", {"text": "Pasian in gam"})]

    def test_missing_arguments_default_to_an_empty_object(self) -> None:
        _, calls = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "grammar_check"}}
        )
        assert calls == [("grammar_check", {})]

    def test_tool_failure_is_a_result_with_is_error(self) -> None:
        """A handler failure is NOT a JSON-RPC error — the client reads isError."""
        response, _ = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "grammar_check", "arguments": {}},
            },
            result=ToolResult(tool_name="grammar_check", ok=False, error="text is required"),
        )
        assert "error" not in response
        assert response["result"]["isError"] is True
        assert response["result"]["content"][0]["text"] == "text is required"

    def test_unknown_tool_is_invalid_params(self) -> None:
        response, calls = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}}
        )
        assert response["error"]["code"] == proto.INVALID_PARAMS == -32602
        assert calls == []

    def test_not_allowed_tool_is_indistinguishable_from_unknown(self) -> None:
        """An anonymous caller must not learn that an admin tool exists."""
        not_allowed, calls = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "provider_status"}}
        )
        unknown, _ = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "definitely_missing"}}
        )
        assert not_allowed["error"]["code"] == -32602
        assert not_allowed["error"]["message"].replace(
            "provider_status", "definitely_missing"
        ) == unknown["error"]["message"]
        assert calls == []  # never executed

    def test_admin_scope_may_call_an_admin_tool(self) -> None:
        response, calls = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "provider_status", "arguments": {}},
            },
            allow=allow_list("admin"),
        )
        assert response["result"]["isError"] is False
        assert calls == [("provider_status", {})]

    def test_bad_params_are_invalid_params(self) -> None:
        for params in ({"arguments": {}}, {"name": 5}, {"name": "  "}, {"name": "grammar_check", "arguments": []}):
            response, calls = _dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
            )
            assert response["error"]["code"] == -32602, params
            assert calls == []

    def test_client_supplied_scope_is_ignored(self) -> None:
        """The allow-list is the only authority — never a message field."""
        response, calls = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "provider_status",
                    "arguments": {},
                    "scope": "admin",
                    "role": "admin",
                },
            }
        )
        assert response["error"]["code"] == -32602
        assert calls == []


class TestProtocolErrors:
    def test_method_not_found(self) -> None:
        # ``resources/list`` is deliberately *not* an error any more (it answers
        # an empty list — see ``TestCrossClientCompatibility``); a method this
        # server genuinely does not know still is.
        response, _ = _dispatch({"jsonrpc": "2.0", "id": 1, "method": "totally/unknown"})
        assert response["error"]["code"] == proto.METHOD_NOT_FOUND == -32601

    def test_invalid_request_shapes(self) -> None:
        for message in (5, "x", None, [], {"id": 1}, {"jsonrpc": "1.0", "id": 1, "method": "ping"}):
            response, _ = _dispatch(message)
            assert response["error"]["code"] == proto.INVALID_REQUEST == -32600, message

    def test_params_must_be_an_object(self) -> None:
        response, _ = _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1, 2]}
        )
        assert response["error"]["code"] == proto.INVALID_PARAMS

    def test_notifications_never_get_an_error_response(self) -> None:
        for message in (
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "nope"}},
            {"jsonrpc": "2.0", "method": "unknown/method"},
            {"jsonrpc": "1.0", "method": "ping"},
        ):
            response, _ = _dispatch(message)
            assert response is None, message


class TestBatch:
    def test_batch_returns_only_the_responses(self) -> None:
        call_tool, calls = _stub_tool()
        status, body = proto.handle_payload(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ],
            allow=allow_list("public"),
            call_tool=call_tool,
        )
        assert status == 200
        assert [item["id"] for item in body] == [1, 2]
        assert calls == []

    def test_all_notifications_is_202_with_no_body(self) -> None:
        call_tool, _ = _stub_tool()
        status, body = proto.handle_payload(
            [{"jsonrpc": "2.0", "method": "notifications/initialized"}],
            allow=allow_list("public"),
            call_tool=call_tool,
        )
        assert (status, body) == (202, None)

    def test_empty_batch_is_invalid_request(self) -> None:
        call_tool, _ = _stub_tool()
        status, body = proto.handle_payload(
            [], allow=allow_list("public"), call_tool=call_tool
        )
        assert status == 200
        assert body["error"]["code"] == proto.INVALID_REQUEST


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


def _initialize() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}


def _tools_list(msg_id: int = 2) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/list"}


def _tools_call(name: str, arguments: dict | None = None, msg_id: int = 3) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


class TestMcpHttpSurface:
    def test_route_is_declared_public_on_the_strict_bucket(self) -> None:
        assert ("POST", "/api/v1/mcp") in rbac.PUBLIC_ROUTES
        assert rbac.classify_route("POST", "/api/v1/mcp") == "public"
        assert rbac.is_public_chat_path("POST", "/api/v1/mcp") is True

    def test_route_is_in_the_openapi_surface(self) -> None:
        assert "post" in create_app().openapi()["paths"]["/api/v1/mcp"]

    @pytest.mark.parametrize("mode", ["warn", "enforce"])
    def test_anonymous_handshake_is_open_in_every_mode(self, client, auth_db, monkeypatch, mode) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", mode)
        resp = client.post("/api/v1/mcp", json=_initialize())
        assert resp.status_code == 200, (mode, resp.text)
        body = resp.json()
        assert body["result"]["capabilities"] == {"tools": {"listChanged": False}}
        assert body["result"]["protocolVersion"] == "2025-06-18"

    def test_anonymous_lists_the_retrieval_tools(self, client, auth_db) -> None:
        resp = client.post("/api/v1/mcp", json=_tools_list())
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        assert len(tools) == 8
        assert {t["name"] for t in tools} == set(allow_list("public"))

    def test_member_lists_its_scope(self, client, auth_db, member_key) -> None:
        resp = client.post("/api/v1/mcp", json=_tools_list(), headers=_headers(member_key))
        tools = resp.json()["result"]["tools"]
        assert len(tools) == 26
        assert {t["name"] for t in tools} == set(allow_list("member"))

    def test_admin_lists_its_scope(self, client, auth_db, admin_key) -> None:
        resp = client.post("/api/v1/mcp", json=_tools_list(), headers=_headers(admin_key))
        tools = resp.json()["result"]["tools"]
        assert len(tools) == 31
        assert {t["name"] for t in tools} == set(allow_list("admin"))

    def test_anonymous_cannot_reach_an_admin_tool(self, client, auth_db) -> None:
        resp = client.post("/api/v1/mcp", json=_tools_call("provider_status"))
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -32602

    def test_anonymous_tools_call_executes_against_the_db(self, client, data_db, auth_db) -> None:
        resp = client.post(
            "/api/v1/mcp", json=_tools_call("dictionary_lookup", {"word": "pasian"})
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["result"]
        assert result["isError"] is False
        rows = json.loads(result["content"][0]["text"])
        assert rows and rows[0]["zolai"] == "pasian"

    def test_member_can_reach_a_member_tool_via_the_wire(
        self, client, data_db, auth_db, member_key
    ) -> None:
        """A member-only tool (the generated family) executes over the wire.

        `rag_search` is deliberately avoided — it needs the embedding model,
        which this environment cannot load (a pre-existing failure unrelated to
        the MCP surface; see `test_agent_tools`). The generated family is
        DB-only, deterministic and member-tier, so it proves the widening
        without that dependency.
        """
        resp = client.post(
            "/api/v1/mcp",
            json=_tools_call(
                "foundation_validate_structure", {"sentence": "Pasian in gam a piangsak hi"}
            ),
            headers=_headers(member_key),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["result"]
        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert isinstance(payload["valid"], bool)
        assert {"valid", "errors", "warnings"} <= set(payload)

    def test_client_supplied_scope_does_not_widen_the_tool_set(self, client, auth_db) -> None:
        """A body naming `scope: "admin"` still gets the anonymous refusal."""
        resp = client.post(
            "/api/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "provider_status", "arguments": {}, "scope": "admin"},
            },
        )
        assert resp.json()["error"]["code"] == -32602

    def test_notification_is_202_with_no_body(self, client, auth_db) -> None:
        resp = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp.status_code == 202
        assert resp.content == b""

    def test_batch_is_supported_over_http(self, client, auth_db) -> None:
        resp = client.post("/api/v1/mcp", json=[_initialize(), _tools_list(9)])
        assert resp.status_code == 200
        body = resp.json()
        assert [item["id"] for item in body] == [1, 9]
        assert len(body[1]["result"]["tools"]) == 8

    def test_malformed_json_is_a_400_with_a_jsonrpc_error(self, client, auth_db) -> None:
        resp = client.post(
            "/api/v1/mcp",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == proto.PARSE_ERROR

    def test_anonymous_bucket_is_the_strict_one(self, client, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        monkeypatch.setenv("ZOLAI_PUBLIC_CHAT_RATE_LIMIT_RPM", "2")
        assert client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200
        assert client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"}).status_code == 200
        blocked = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["scope"] == "public_chat"

    def test_the_route_does_not_shadow_the_static_catch_all(self, client) -> None:
        """`/mcp` is a POST route; the GET catch-all still serves unknown paths.

        Exact ``405`` rather than ``in {405, 404}``: the streamable-HTTP binding
        *requires* 405 from a server that offers no SSE stream, and 404 was
        exactly the confusing answer the static catch-all used to give.
        """
        resp = client.get("/api/v1/mcp")
        assert resp.status_code == 405
        assert resp.headers["allow"] == "POST"


class TestMcpRootAlias:
    """Bare ``POST /mcp`` — the path the public MCP host proxies.

    Same handler, same server-side tool scope, same public/strict-bucket
    declaration as ``/api/v1/mcp``; the point of the alias is that a client
    configured against ``mcp.zolai.space/mcp`` keeps working when the DNS
    cutover points that host at core ``:8001``.
    """

    def test_both_paths_are_declared_public_on_the_strict_bucket(self) -> None:
        assert ("POST", "/mcp") in rbac.PUBLIC_ROUTES
        assert rbac.classify_route("POST", "/mcp") == "public"
        assert rbac.is_public_chat_path("POST", "/mcp") is True
        assert rbac.is_public_path("POST", "/mcp") is True

    def test_the_bare_path_is_gated_like_the_versioned_one(self) -> None:
        from zolai.api import auth_middleware as mw

        assert mw.is_protected_path("/mcp") is True
        assert mw.is_protected_path("/api/v1/mcp") is True
        # the middleware's own exemption list must not silently drop it
        assert "/mcp" not in mw.EXEMPT_PATHS

    def test_the_bare_path_is_in_the_openapi_surface(self) -> None:
        assert "post" in create_app().openapi()["paths"]["/mcp"]

    def test_anonymous_tools_list_over_the_bare_path(self, client, auth_db) -> None:
        resp = client.post("/mcp", json=_tools_list())
        assert resp.status_code == 200, resp.text
        tools = resp.json()["result"]["tools"]
        assert len(tools) == 8
        assert {t["name"] for t in tools} == set(allow_list("public"))

    def test_a_presented_key_widens_the_bare_path_tool_set(
        self, client, auth_db, member_key
    ) -> None:
        """The alias must resolve a presented key, not degrade to anonymous."""
        resp = client.post("/mcp", json=_tools_list(), headers=_headers(member_key))
        assert resp.status_code == 200, resp.text
        tools = resp.json()["result"]["tools"]
        assert len(tools) == 26
        assert {t["name"] for t in tools} == set(allow_list("member"))

    def test_anonymous_handshake_over_the_bare_path(self, client, auth_db) -> None:
        resp = client.post("/mcp", json=_initialize())
        assert resp.status_code == 200
        assert resp.json()["result"]["protocolVersion"] == "2025-06-18"

    def test_the_bare_path_shares_the_strict_anonymous_bucket(
        self, client, auth_db, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        monkeypatch.setenv("ZOLAI_PUBLIC_CHAT_RATE_LIMIT_RPM", "2")
        for msg_id in (1, 2):
            assert client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": msg_id, "method": "ping"}
            ).status_code == 200
        blocked = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["scope"] == "public_chat"

    def test_the_bare_path_does_not_shadow_the_static_catch_all(self, client) -> None:
        resp = client.get("/mcp")
        assert resp.status_code == 405
        assert resp.headers["allow"] == "POST"

class TestCrossClientCompatibility:
    """Leniency a heterodox MCP client relies on, without a false claim.

    Claude, ChatGPT, Cursor and OpenCode all probe beyond ``tools/*``: several
    issue ``resources/list`` or ``prompts/list`` before deciding what to render,
    and some send ``logging/setLevel``.  Answering ``-32601`` is legal but makes
    a client log a hard error — Cursor will drop the server over it.  An empty,
    truthful list is both legal and harmless, and the server still advertises
    *only* the ``tools`` capability: leniency is not a claim of support.
    """

    @pytest.mark.parametrize(
        ("method", "key"),
        [
            ("resources/list", "resources"),
            ("resources/templates/list", "resourceTemplates"),
            ("prompts/list", "prompts"),
        ],
    )
    def test_an_unimplemented_list_method_answers_with_an_empty_list(
        self, method: str, key: str
    ) -> None:
        response, _ = _dispatch({"jsonrpc": "2.0", "id": 1, "method": method})
        assert "error" not in response, response
        assert response["result"] == {key: []}

    def test_logging_set_level_is_accepted_as_a_no_op(self) -> None:
        response, _ = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logging/setLevel",
                "params": {"level": "info"},
            }
        )
        assert "error" not in response, response
        assert response["result"] == {}

    def test_leniency_is_not_advertised_as_a_capability(self) -> None:
        response, _ = _dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert set(response["result"]["capabilities"]) == {"tools"}

    def test_initialize_carries_server_instructions(self) -> None:
        """Legacy clients render ``result.instructions``; omitting it wastes it."""
        response, _ = _dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert response["result"]["instructions"] == proto.SERVER_INSTRUCTIONS
        assert response["result"]["instructions"]

    def test_a_dict_result_also_carries_structured_content(self) -> None:
        response, _ = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "dictionary_lookup", "arguments": {}},
            },
            result=ToolResult(tool_name="dictionary_lookup", ok=True, data={"zolai": "pasian"}),
        )
        assert response["result"]["structuredContent"] == {"zolai": "pasian"}

    def test_a_non_dict_result_carries_no_structured_content(self) -> None:
        """``structuredContent`` is defined as a JSON object — a list gets none."""
        response, _ = _dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "dictionary_lookup", "arguments": {}},
            },
            result=ToolResult(tool_name="dictionary_lookup", ok=True, data=["pasian", "gam"]),
        )
        assert "structuredContent" not in response["result"]
        assert response["result"]["isError"] is False


class TestFailuresAreTypedAndHumanReadable:
    """No raw exception text may reach a client, and every failure is typed.

    A tool failure is the text a chat client renders to a *person*.  A Python
    exception repr names internal types and can echo SQL, file paths or provider
    payloads, so it is guarded at the publishing boundary
    (``zolai.agent.tools.errors.safe_message``) whether or not the caller
    classified the failure itself.  The raw detail stays in the server log.
    """

    RAW = (
        "TypeError: Pooling.__init__() missing 1 required positional argument: "
        "'embedding_dimension'"
    )

    @pytest.mark.parametrize(
        "result",
        [
            ToolResult(
                tool_name="rag_search", ok=False, error=RAW, error_code=ToolErrorCode.INTERNAL
            ),
            ToolResult(tool_name="rag_search", ok=False, error=RAW),
            ToolResult(
                tool_name="rag_search",
                ok=False,
                error='Traceback (most recent call last):\n  File "/srv/x.py", line 9',
                error_code=ToolErrorCode.INTERNAL,
            ),
        ],
        ids=["raw-with-code", "raw-without-code", "traceback"],
    )
    def test_a_raw_exception_is_replaced_at_the_boundary(self, result: ToolResult) -> None:
        payload = proto.tool_result_payload(result)
        blob = json.dumps(payload)
        assert "Pooling" not in blob
        assert "Traceback" not in blob
        assert "/srv/x.py" not in blob
        assert payload["isError"] is True
        assert payload["content"][0]["text"], "a failure must still say something"

    def test_a_failure_carries_a_machine_readable_code(self) -> None:
        payload = proto.tool_result_payload(
            ToolResult(tool_name="web_search", ok=False, error_code=ToolErrorCode.TIMEOUT)
        )
        message = payload["content"][0]["text"]
        assert payload["structuredContent"]["error"] == {
            "code": "timeout",
            "message": message,
            "tool": "web_search",
        }

    def test_an_authored_argument_message_survives_intact(self) -> None:
        """The specific, actionable messages handlers write must not be lost."""
        message = "PhonologyAnalysisRequest: text: Field required"
        payload = proto.tool_result_payload(
            ToolResult(
                tool_name="foundation_analyze_phonology",
                ok=False,
                error=message,
                error_code=ToolErrorCode.INVALID_ARGUMENT,
            )
        )
        assert payload["content"][0]["text"] == message

    def test_the_executor_classifies_a_handler_that_raises(self, monkeypatch) -> None:
        """End to end: a raising handler yields a typed code, not ``Type: text``."""
        from zolai.agent.tools import executor

        def boom(_input: dict) -> dict:
            raise TypeError(self.RAW)

        monkeypatch.setitem(executor.HANDLERS, "dictionary_lookup", boom)
        result = executor.execute_tool(
            ToolCall(name="dictionary_lookup", input={}, source="mcp"), allow_list("public")
        )
        assert result.ok is False
        assert result.error_code is ToolErrorCode.INTERNAL
        assert result.error and "TypeError" not in result.error
        assert result.error and "Pooling" not in result.error
        assert "Pooling" not in json.dumps(proto.tool_result_payload(result))
