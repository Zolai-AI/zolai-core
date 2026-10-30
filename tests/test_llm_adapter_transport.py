"""Pooled LLM transport + per-call observability (adapter.py).

Three contracts, all offline (no socket in the default suite):

1. **One pooled keep-alive client** — ``chat()`` goes through the module-level
   :func:`zolai.llm.adapter._post`, never the per-call ``httpx.post`` that used
   to pay a fresh TLS handshake on every provider call, and the per-request
   ``timeout=`` semantics are preserved verbatim.
2. **One structured INFO line per call** — provider, model, turns, latency and
   status; never the API key, the prompt or the completion.
3. **Error mapping unchanged** — HTTP ≥400 and transport failures still raise
   ``ProviderError(PROVIDER_REQUEST_FAILED)`` through the circuit breaker.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from zolai.llm import adapter as adapter_mod

_ROW = {
    "catalog_id": "test-provider",
    "adapter": "openai",
    "base_url": "https://example.invalid/v1/chat/completions",
    "requires_key": 0,
    "models": ["test-model"],
    "selected_model": "test-model",
}


class _Response:
    def __init__(
        self, status: int = 200, payload: dict | None = None, headers: dict | None = None
    ) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": "ok"}}]
        }
        self.headers = headers or {}
        import json as _json

        self.text = _json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _Recorder:
    """Stand-in for the pooled transport call; records what chat() sent."""

    def __init__(self, response: _Response | None = None, error: Exception | None = None) -> None:
        self.response = response or _Response()
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, *, body, headers, timeout):  # noqa: ANN001
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch) -> None:
    """Pin one attempt so exact ``_post`` call counts stay meaningful.

    The retry loop is exercised by :class:`TestTransientRetry`, which re-enables
    multiple attempts explicitly.
    """
    monkeypatch.setattr(adapter_mod, "LLM_RETRY_ATTEMPTS", 1)


@pytest.fixture()
def record(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(adapter_mod, "_post", rec)
    return rec


# ── 1. Pooled transport ──────────────────────────────────────────────────────


class TestPooledTransport:
    def test_one_client_is_built_and_reused(self) -> None:
        first = adapter_mod.get_http_client()
        assert isinstance(first, httpx.Client)
        assert adapter_mod.get_http_client() is first, "client must be built once and reused"

    def test_client_keeps_connections_alive(self) -> None:
        """The pooled client really carries the keep-alive pool configuration."""
        limits = adapter_mod._CLIENT_LIMITS
        assert limits.max_keepalive_connections and limits.max_keepalive_connections > 0
        assert limits.keepalive_expiry and limits.keepalive_expiry > 0
        pool = adapter_mod.get_http_client()._transport._pool
        assert pool._max_connections == limits.max_connections
        assert pool._max_keepalive_connections == limits.max_keepalive_connections
        assert pool._keepalive_expiry == limits.keepalive_expiry

    def test_close_rebuilds_on_next_use(self) -> None:
        first = adapter_mod.get_http_client()
        adapter_mod.close_http_client()
        second = adapter_mod.get_http_client()
        assert second is not first
        assert isinstance(second, httpx.Client)

    def test_chat_goes_through_the_pool_not_httpx_post(
        self, monkeypatch: pytest.MonkeyPatch, record: _Recorder
    ) -> None:
        """The legacy per-call path must be unreachable — it would open a socket."""

        def _boom(*args, **kwargs):  # noqa: ANN001, ANN003
            raise AssertionError("chat() must not call httpx.post directly")

        monkeypatch.setattr(adapter_mod.httpx, "post", _boom)
        out = adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert out["status"] == 200
        assert out["text"] == "ok"
        assert len(record.calls) == 1

    @pytest.mark.parametrize(
        ("timeout_s", "expected"),
        [(12.5, 12.5), (None, 60.0)],
    )
    def test_per_request_timeout_is_preserved(
        self, record: _Recorder, timeout_s, expected
    ) -> None:
        kwargs = {"timeout_s": timeout_s} if timeout_s is not None else {}
        adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}], **kwargs)
        assert record.calls[0]["timeout"] == expected

    def test_row_timeout_s_is_used_when_caller_is_silent(
        self, monkeypatch: pytest.MonkeyPatch, record: _Recorder
    ) -> None:
        row = {**_ROW, "timeout_s": 7}
        adapter_mod.chat(row, "test-model", [{"role": "user", "content": "hi"}])
        assert record.calls[0]["timeout"] == 7.0


# ── 2. One structured INFO line per call ─────────────────────────────────────


class TestCallObservability:
    def test_one_info_line_with_the_five_fields(
        self, monkeypatch: pytest.MonkeyPatch, record: _Recorder, caplog: pytest.LogCaptureFixture
    ) -> None:
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        with caplog.at_level(logging.INFO, logger="zolai.llm.adapter"):
            adapter_mod.chat(_ROW, "test-model", messages)
        lines = [r for r in caplog.records if r.name == "zolai.llm.adapter" and r.levelno == logging.INFO]
        assert len(lines) == 1, f"expected exactly one INFO line, got {len(lines)}"
        text = lines[0].getMessage()
        assert "provider=test-provider" in text
        assert "model=test-model" in text
        assert "turns=2" in text
        assert "latency_ms=" in text
        assert "status=200" in text

    def test_log_carries_never_a_key_nor_the_completion(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Identity + timing only: a secret in the header or the prompt must not log."""
        sentinel_key = "sk-test-abcdefghijklmnop1234"
        sentinel_prompt = "PROMPT_CONTENT_THAT_MUST_NOT_LOG"
        monkeypatch.setattr(adapter_mod, "resolve_api_key", lambda row: sentinel_key)
        rec = _Recorder(response=_Response(payload={"choices": [{"message": {"content": "COMPLETION_MUST_NOT_LOG"}}]}))
        monkeypatch.setattr(adapter_mod, "_post", rec)
        row = {**_ROW, "requires_key": 1}
        with caplog.at_level(logging.INFO, logger="zolai.llm.adapter"):
            adapter_mod.chat(row, "test-model", [{"role": "user", "content": sentinel_prompt}])
        # The key *was* sent on the wire — that is its job …
        assert rec.calls[0]["headers"]["Authorization"] == f"Bearer {sentinel_key}"
        # … and neither it, the prompt nor the completion reached the log.
        text = "\n".join(r.getMessage() for r in caplog.records if r.name == "zolai.llm.adapter")
        assert sentinel_key not in text
        assert sentinel_prompt not in text
        assert "COMPLETION_MUST_NOT_LOG" not in text

    def test_http_error_logs_the_status_and_keeps_the_error_code(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        rec = _Recorder(response=_Response(status=500, payload={"error": "boom"}))
        monkeypatch.setattr(adapter_mod, "_post", rec)
        with caplog.at_level(logging.INFO, logger="zolai.llm.adapter"):
            with pytest.raises(adapter_mod.ProviderError) as exc:
                adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert exc.value.code == adapter_mod.PROVIDER_REQUEST_FAILED
        assert "500" in exc.value.message
        lines = [r.getMessage() for r in caplog.records if r.name == "zolai.llm.adapter" and r.levelno == logging.INFO]
        assert len(lines) == 1
        assert "status=500" in lines[0]

    def test_transport_failure_logs_a_token_and_keeps_the_error_code(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        rec = _Recorder(error=httpx.ConnectError("connection refused"))
        monkeypatch.setattr(adapter_mod, "_post", rec)
        with caplog.at_level(logging.INFO, logger="zolai.llm.adapter"):
            with pytest.raises(adapter_mod.ProviderError) as exc:
                adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert exc.value.code == adapter_mod.PROVIDER_REQUEST_FAILED
        lines = [r.getMessage() for r in caplog.records if r.name == "zolai.llm.adapter" and r.levelno == logging.INFO]
        assert len(lines) == 1
        assert "status=transport_error" in lines[0]

    def test_non_json_body_logs_invalid_body(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _Html(_Response):
            def json(self) -> dict:
                raise ValueError("not json")

        rec = _Recorder(response=_Html())
        monkeypatch.setattr(adapter_mod, "_post", rec)
        with caplog.at_level(logging.INFO, logger="zolai.llm.adapter"):
            with pytest.raises(adapter_mod.ProviderError) as exc:
                adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert exc.value.code == adapter_mod.PROVIDER_REQUEST_FAILED
        lines = [r.getMessage() for r in caplog.records if r.name == "zolai.llm.adapter" and r.levelno == logging.INFO]
        assert len(lines) == 1
        assert "status=invalid_body" in lines[0]


# ── 4. Provider health reaches the circuit breaker ───────────────────────────


@pytest.fixture(autouse=True)
def breaker():
    """A freshly reset breaker for ``_ROW``'s catalog id, for every test here.

    One name is shared by the whole module (``llm_<catalog_id>``) and by the
    error-mapping tests above, so a failure recorded by one test would otherwise
    trip the next and make the suite order-dependent.
    """
    from zolai.resilience import get_circuit_breaker

    instance = get_circuit_breaker(f"llm_{_ROW['catalog_id']}")
    instance.reset()
    yield instance
    instance.reset()


class TestBreakerSeesProviderHealth:
    """429/5xx are provider ill-health and must count against the circuit.

    ``CircuitBreaker.call`` scores whatever the callable returns, so a status
    checked after the call was invisible to it: a provider answering 503 to
    every request recorded only successes, never opened, and every caller paid
    the full timeout.
    """

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_unhealthy_status_is_a_breaker_failure(self, breaker, monkeypatch, status: int) -> None:
        monkeypatch.setattr(
            adapter_mod, "_post", _Recorder(response=_Response(status=status, payload={"error": "down"}))
        )
        with pytest.raises(adapter_mod.ProviderError) as exc:
            adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert str(status) in exc.value.message
        assert breaker.get_stats()["failures"] == 1

    def test_repeated_5xx_opens_the_circuit_and_stops_calling_the_provider(
        self, breaker, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        rec = _Recorder(response=_Response(status=503, payload={"error": "down"}))
        monkeypatch.setattr(adapter_mod, "_post", rec)
        threshold = breaker.get_stats()["config"]["failure_threshold"]
        for _ in range(threshold):
            with pytest.raises(adapter_mod.ProviderError):
                adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert len(rec.calls) == threshold

        with caplog.at_level(logging.INFO, logger="zolai.llm.adapter"):
            with pytest.raises(adapter_mod.ProviderError) as exc:
                adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert "circuit breaker open" in exc.value.message
        assert len(rec.calls) == threshold  # the circuit opened before the transport
        lines = [r.getMessage() for r in caplog.records if r.name == "zolai.llm.adapter" and r.levelno == logging.INFO]
        assert "status=circuit_open" in lines[-1]

    def test_client_error_does_not_trip_the_breaker(self, breaker, monkeypatch) -> None:
        """A 400 is our payload's fault — reporting it must not look like an outage."""
        rec = _Recorder(response=_Response(status=400, payload={"error": "bad_request"}))
        monkeypatch.setattr(adapter_mod, "_post", rec)
        for _ in range(3):
            with pytest.raises(adapter_mod.ProviderError) as exc:
                adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert "400" in exc.value.message
        assert len(rec.calls) == 3
        stats = breaker.get_stats()
        assert stats["failures"] == 0
        assert stats["state"] == "closed"


# ── 5. Transient errors are retried, inside the breaker call ─────────────────


class _Sequence:
    """Stand-in transport answering with a scripted series (then repeating the last)."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def __call__(self, url, *, body, headers, timeout):  # noqa: ANN001
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def slept(monkeypatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(adapter_mod, "_sleep", delays.append)
    return delays


class TestTransientRetry:
    """429/5xx and transport errors retry with backoff; the breaker sees one outcome."""

    def test_429_then_success_returns_the_second_answer(
        self, monkeypatch, slept: list[float]
    ) -> None:
        monkeypatch.setattr(adapter_mod, "LLM_RETRY_ATTEMPTS", 3)
        seq = _Sequence(
            [
                _Response(status=429, payload={"error": "slow down"}),
                _Response(payload={"choices": [{"message": {"content": "second"}}]}),
            ]
        )
        monkeypatch.setattr(adapter_mod, "_post", seq)
        out = adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert out["status"] == 200
        assert out["text"] == "second"
        assert len(seq.calls) == 2
        assert len(slept) == 1

    def test_transport_error_then_success_returns_the_second_answer(
        self, monkeypatch, slept: list[float]
    ) -> None:
        monkeypatch.setattr(adapter_mod, "LLM_RETRY_ATTEMPTS", 3)
        seq = _Sequence([httpx.ConnectError("reset"), _Response()])
        monkeypatch.setattr(adapter_mod, "_post", seq)
        out = adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert out["status"] == 200
        assert len(seq.calls) == 2
        assert len(slept) == 1

    def test_persistent_503_fails_after_the_attempt_budget_and_scores_once(
        self, breaker, monkeypatch, slept: list[float]
    ) -> None:
        monkeypatch.setattr(adapter_mod, "LLM_RETRY_ATTEMPTS", 2)
        seq = _Sequence([_Response(status=503, payload={"error": "down"})])
        monkeypatch.setattr(adapter_mod, "_post", seq)
        with pytest.raises(adapter_mod.ProviderError) as exc:
            adapter_mod.chat(_ROW, "test-model", [{"role": "user", "content": "hi"}])
        assert "503" in exc.value.message
        assert len(seq.calls) == 2
        assert len(slept) == 1
        # Retries happen inside the breaker call: one logical failure, not two.
        assert breaker.get_stats()["failures"] == 1

    def test_retry_after_header_is_honoured_by_the_delay(self) -> None:
        response = _Response(status=429, headers={"retry-after": "1.5"})
        assert adapter_mod._retry_delay(response, 0) == 1.5

    def test_delay_is_capped_and_jittered_without_retry_after(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(adapter_mod, "LLM_RETRY_CAP_S", 0.25)
        monkeypatch.setattr(adapter_mod, "LLM_RETRY_BASE_S", 10.0)
        delay = adapter_mod._retry_delay(None, 5)
        assert 0.0 <= delay <= 0.25
