"""W0 — the OpenAI-compatible function-calling path, proven end to end.

The founder decision is explicit: **keep the OpenAI-compat path**.  The
``anthropic`` and ``google-gemini`` catalog rows stay ``adapter="openai"`` on
their official OpenAI-compatibility endpoints, and no native Gemini/Anthropic
wire adapter is added.  So "function calling works for Gemini/Anthropic" means
exactly two provable things, both asserted here with no socket:

1. the request body assembled for those rows carries the OpenAI ``tools``
   array (the same array ``registry.openai_tool_defs`` renders), and
2. a captured OpenAI-wire ``tool_calls`` response parses into
   ``{"id", "name", "input", "arguments"}`` through ``chat()``.

Everything is offline: ``httpx.post`` is replaced by a recorder, so the test
proves what would be *sent* and reads what would be *parsed*.
"""

from __future__ import annotations

import json

import pytest

from zolai.agent.tools.registry import allow_list, native_tool_defs, openai_tool_defs
from zolai.llm import adapter as adapter_mod
from zolai.llm import catalog as catalog_mod

_ROW = {
    "catalog_id": "google-gemini",
    "adapter": "openai",
    "base_url": "https://example.invalid/v1/chat/completions",
    "requires_key": 0,
}


def _row(catalog_id: str) -> dict:
    entry = catalog_mod.find_catalog(catalog_id)
    assert entry is not None
    return {
        "catalog_id": entry.id,
        "adapter": entry.adapter,
        "base_url": "https://example.invalid/v1/chat/completions",
        "requires_key": 0,
        "models": list(entry.models),
        "selected_model": entry.models[0] if entry.models else "",
    }


class _Captured:
    """Records the outbound request and replays a canned OpenAI-wire body."""

    def __init__(self, payload: dict | None = None, status: int = 200) -> None:
        self.payload = payload or {}
        self.status = status
        self.url: str | None = None
        self.body: dict | None = None
        self.headers: dict | None = None

    def __call__(self, url, *, json=None, headers=None, timeout=None):  # noqa: ANN001, A002
        self.url = url
        self.body = json
        self.headers = headers
        return _FakeResponse(self.status, self.payload)


class _FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


@pytest.fixture()
def captured(monkeypatch) -> _Captured:
    # No provider credential may leak into the outbound request (or into a
    # failure message) from the ambient environment.
    for name in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "AI_BRAIN_API_KEY",
        "PCORE_BRIDGE_API_KEY",
        "AI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    cap = _Captured()
    monkeypatch.setattr(adapter_mod.httpx, "post", cap)
    return cap


def _tools() -> list[dict]:
    return native_tool_defs(allow_list("public"))


# ---------------------------------------------------------------------------
# 1. The assembled request carries `tools` for the compat rows
# ---------------------------------------------------------------------------


class TestCompatRequestCarriesTools:
    @pytest.mark.parametrize("catalog_id", ["google-gemini", "anthropic"])
    def test_catalog_row_is_adapter_openai_and_gets_tools(self, catalog_id: str) -> None:
        row = _row(catalog_id)
        assert row["adapter"] == "openai"
        body = adapter_mod.build_chat_body(
            row, "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert body["tools"] == _tools()
        assert body["tools"][0]["type"] == "function"
        assert all(t["function"]["name"] for t in body["tools"])

    @pytest.mark.parametrize("catalog_id", ["openai", "openrouter", "groq", "ollama"])
    def test_every_openai_wire_row_gets_tools(self, catalog_id: str) -> None:
        body = adapter_mod.build_chat_body(
            _row(catalog_id), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert "tools" in body

    def test_brain_still_structurally_omits_tools(self) -> None:
        row = {"catalog_id": "pcore-brain", "adapter": "brain"}
        body = adapter_mod.build_chat_body(
            row, "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert "tools" not in body

    def test_nothing_unnecessary_is_sent(self) -> None:
        """No tools → no `tools` key; no cap → no `max_tokens`; never `tool_choice`."""
        body = adapter_mod.build_chat_body(
            _row("google-gemini"), "m", [{"role": "user", "content": "hi"}]
        )
        assert set(body) == {"model", "messages"}
        with_tools = adapter_mod.build_chat_body(
            _row("google-gemini"), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert "tool_choice" not in with_tools
        assert "max_tokens" not in with_tools
        assert "max_completion_tokens" not in with_tools

    def test_max_tokens_forwarded_only_when_asked(self) -> None:
        row = _row("anthropic")
        assert "max_tokens" not in adapter_mod.build_chat_body(
            row, "m", [{"role": "user", "content": "hi"}]
        )
        assert (
            adapter_mod.build_chat_body(
                row, "m", [{"role": "user", "content": "hi"}], max_tokens=512
            )["max_tokens"]
            == 512
        )


# ---------------------------------------------------------------------------
# 2. The full call: request on the wire, parsed tool_calls on the way back
# ---------------------------------------------------------------------------


class TestCompatCallRoundTrip:
    @pytest.mark.parametrize("catalog_id", ["google-gemini", "anthropic"])
    def test_request_on_the_wire_carries_tools(
        self, captured: _Captured, catalog_id: str
    ) -> None:
        captured.payload = {"choices": [{"message": {"content": "ok"}}]}
        out = adapter_mod.chat(
            _row(catalog_id), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert captured.url == "https://example.invalid/v1/chat/completions"
        assert captured.body is not None
        assert captured.body["tools"] == _tools()
        # No credential is attached for a keyless row — and never logged here.
        assert set(captured.headers) == {"Content-Type"}
        assert out["tool_calls"] == []
        assert out["text"] == "ok"
        assert out["provider"] == catalog_id

    def test_openai_wire_tool_calls_parse_into_name_and_input(
        self, captured: _Captured
    ) -> None:
        """A captured OpenAI-wire response → `{name, input}` the loop can run."""
        captured.payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "rag_search",
                                    "arguments": '{"query": "pasian", "limit": 3}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        out = adapter_mod.chat(
            _row("google-gemini"), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert out["tool_calls"] == [
            {
                "id": "call_abc123",
                "name": "rag_search",
                "input": {"query": "pasian", "limit": 3},
                "arguments": '{"query": "pasian", "limit": 3}',
            }
        ]
        assert out["text"] == ""

    def test_arguments_raw_string_is_preserved_verbatim(self, captured: _Captured) -> None:
        raw = '{ "query" : "gam" , "limit" : 2 }'
        captured.payload = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "rag_search", "arguments": raw},
                            }
                        ]
                    }
                }
            ]
        }
        out = adapter_mod.chat(
            _row("openai"), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert out["tool_calls"][0]["arguments"] == raw
        assert out["tool_calls"][0]["input"] == {"query": "gam", "limit": 2}

    def test_malformed_arguments_degrade_instead_of_raising(
        self, captured: _Captured
    ) -> None:
        captured.payload = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "rag_search", "arguments": "{not json"},
                            }
                        ]
                    }
                }
            ]
        }
        out = adapter_mod.chat(
            _row("openai"), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert out["tool_calls"][0]["name"] == "rag_search"
        assert out["tool_calls"][0]["input"] == {"_raw": "{not json"}

    def test_max_tokens_reaches_the_wire_when_asked(self, captured: _Captured) -> None:
        captured.payload = {"choices": [{"message": {"content": "ok"}}]}
        adapter_mod.chat(
            _row("anthropic"),
            "m",
            [{"role": "user", "content": "hi"}],
            tools=_tools(),
            max_tokens=256,
        )
        assert captured.body is not None
        assert captured.body["max_tokens"] == 256
        assert "tools" in captured.body

    def test_tools_sent_are_the_canonical_render(self, captured: _Captured) -> None:
        captured.payload = {"choices": [{"message": {"content": "ok"}}]}
        adapter_mod.chat(
            _row("google-gemini"), "m", [{"role": "user", "content": "hi"}], tools=_tools()
        )
        assert captured.body is not None
        assert captured.body["tools"] == openai_tool_defs(allow_list("public"))

    def test_provider_http_error_is_a_stable_code(self, captured: _Captured) -> None:
        captured.status = 400
        captured.payload = {"error": {"message": "max_tokens: Field required"}}
        with pytest.raises(adapter_mod.ProviderError) as exc:
            adapter_mod.chat(
                _row("anthropic"), "m", [{"role": "user", "content": "hi"}], tools=_tools()
            )
        assert exc.value.code == adapter_mod.PROVIDER_REQUEST_FAILED
        assert "400" in exc.value.message
