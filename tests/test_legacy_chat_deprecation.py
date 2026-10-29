"""Legacy ``/chat/*`` answers honestly when its backend is absent — core#20.

The family is a local-Ollama proxy (plus an out-of-tree ``gemini-webapi``
bridge) that the hosted API never runs.  Before core#20 a missing daemon looked
like three different bugs from the outside:

- ``GET /chat/models`` → **500** ``internal_server_error`` (unhandled
  ``httpx.ConnectError``),
- ``POST /chat/zolai`` → **200** whose ``zolai_response`` was
  ``"Sorry, I cannot reach the language model right now. Error: All connection
  attempts failed"`` — exception text in a success body,
- ``POST /chat/gemini`` → **200** with ``"Gemini chat is unavailable"``.

Now every one of them is a **501** with the stable code and the versioned
replacement, and every one is flagged ``deprecated`` in the OpenAPI surface so
clients see the intent, not just the status.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.engines import AI_KEY_ENV_VARS, DEFAULT_ENGINE_MODE, ENGINE_MODE_ENV

#: Nothing listens here, so the Ollama paths take their 501 without a network.
DEAD_BACKEND = "http://127.0.0.1:1"

_LEGACY_CHAT_OPS = (
    ("get", "/chat/models"),
    ("post", "/chat/chat"),
    ("post", "/chat/chat/stream"),
    ("get", "/chat"),
    ("post", "/chat/zolai"),
    ("post", "/chat/gemini"),
)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    monkeypatch.delenv(ENGINE_MODE_ENV, raising=False)
    for var in AI_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "engine_mode", DEFAULT_ENGINE_MODE)
    monkeypatch.setattr("zolai.api.server.OLLAMA_URL", DEAD_BACKEND)
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "zolai.api.prediction_api.load_ngram_tables", lambda: {"unigrams": {}, "bigrams": {}}
    )
    return TestClient(create_app())


def _assert_honest_501(response, code: str) -> None:
    assert response.status_code == 501, response.text
    detail = response.json()["detail"]
    assert detail["error"] == code
    # The point of core#20: the caller is told what to call instead, and the
    # body never carries the exception text it replaced.
    assert "/api/v1/assistant/chat" in detail["message"]
    assert "Error:" not in detail["message"]


def test_chat_models_501_instead_of_an_unhandled_connect_error(client: TestClient) -> None:
    _assert_honest_501(client.get("/chat/models"), "ollama_unavailable")


def test_chat_chat_501(client: TestClient) -> None:
    body = {"messages": [{"role": "user", "content": "khi le"}]}
    _assert_honest_501(client.post("/chat/chat", json=body), "ollama_unavailable")


def test_chat_zolai_501_never_a_success_body_with_the_failure_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core#20 is about the *backend* answer, so the RAG-context prefetch —
    which reflects the whole corpus schema before the request ever reaches
    Ollama — is stubbed here rather than seeded table by table."""
    monkeypatch.setattr("zolai.api.rag_context.build_zolai_context", lambda *a, **k: "")
    response = client.post("/chat/zolai", json={"message": "khi le"})
    _assert_honest_501(response, "ollama_unavailable")
    assert "cannot reach the language model" not in response.text


def test_chat_stream_501s_before_the_sse_headers(client: TestClient) -> None:
    """A stream cannot change its status once it started, so the probe is first."""
    body = {"messages": [{"role": "user", "content": "khi le"}]}
    _assert_honest_501(client.post("/chat/chat/stream", json=body), "ollama_unavailable")


def test_chat_get_delegates_to_the_same_501(client: TestClient) -> None:
    _assert_honest_501(client.get("/chat?q=khi+le"), "ollama_unavailable")


def test_chat_gemini_501_when_the_bridge_is_missing(client: TestClient) -> None:
    """The bridge needs the out-of-tree ``zolai-ai-local`` package."""
    _assert_honest_501(client.post("/chat/gemini", json={"message": "khi le"}), "gemini_unavailable")


def test_the_whole_family_is_marked_deprecated_in_openapi(client: TestClient) -> None:
    schema = client.app.openapi()
    for method, path in _LEGACY_CHAT_OPS:
        assert path in schema["paths"], f"{path} vanished from the schema"
        operation = schema["paths"][path][method]
        assert operation.get("deprecated") is True, f"{method.upper()} {path} is not deprecated"
