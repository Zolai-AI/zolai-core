"""AI provider adapter — brain | openai | openrouter | custom dispatch (P1 §A).

Python port of ``pcore-assistant/server/src/services/ai.ts``:

- Every adapter speaks the **OpenAI wire** (``POST …/chat/completions``).
- **Brain is first-class**: URL from ``AI_BRAIN_URL`` / ``PCORE_BRAIN_URL``
  (default ``https://pcore-brain.peterlianpi.site/v1``), key from its
  ``env_keys`` order (``AI_BRAIN_API_KEY`` → ``PCORE_BRIDGE_API_KEY`` →
  ``AI_API_KEY``), and its request body **structurally omits the ``tools``
  key** — the brain path has no native function-calling support.
- Native ``tools`` passthrough only for adapters in
  :data:`NATIVE_TOOL_ADAPTERS` (``openai`` / ``openrouter``).  The
  ``anthropic`` and ``google-gemini`` catalog rows are ``adapter="openai"`` on
  those vendors' OpenAI-compatibility endpoints, so they receive ``tools``
  through this same branch — there is deliberately **no** Gemini/Anthropic wire
  adapter, and the brain branch structurally omits ``tools``.
  See ``docs/specs/tool-wire-formats.md`` for the proven compat path and its
  caveats (notably: the compatibility layer documents ``max_tokens`` as
  required, so a caller targeting the ``anthropic`` row must pass it).
- :func:`pick_provider` reads the DB **every call** (no boot cache, no
  first-row reroute) and fails with the stable machine-readable codes
  :data:`NO_ACTIVE_PROVIDER` / :data:`MODEL_NOT_CONFIGURED` — callers never
  string-match messages and an empty model id never reaches a provider.
- Every call travels over **one pooled keep-alive ``httpx.Client``**
  (:func:`get_http_client`) — no per-request TLS handshake — with the
  per-request ``timeout=`` and the circuit-breaker/error mapping unchanged,
  and logs exactly one structured ``INFO`` line per call
  (``llm_call provider=… model=… turns=… latency_ms=… status=…``): no keys,
  no prompts, no completions.

``ZOLAI_ENGINE_MODE`` / ``llm_allowed()`` (founder directive D2) are checked
by *call sites* (agent synthesis, assistant chat) before any socket is
opened; this module is the transport, not the gate.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from typing import Any

import httpx

from ..resilience import CircuitBreakerError, get_circuit_breaker
from .catalog import (
    catalog_default_base_url,
    find_catalog,
)

logger = logging.getLogger(__name__)

# ── Stable machine-readable error codes (callers branch on these) ────────────

NO_ACTIVE_PROVIDER = "NO_ACTIVE_PROVIDER"
MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
PROVIDER_KEY_MISSING = "PROVIDER_KEY_MISSING"
PROVIDER_REQUEST_FAILED = "PROVIDER_REQUEST_FAILED"

#: Provider types that speak the OpenAI ``tools`` array natively.
NATIVE_TOOL_ADAPTERS: frozenset[str] = frozenset({"openai", "openrouter"})

# ── Pooled transport ─────────────────────────────────────────────────────────
#
# One ``httpx.Client`` per process: every provider call reuses the
# keep-alive connection instead of paying a fresh TLS handshake on each
# request (``httpx.post`` built a new client per call).  ``httpx.Client`` is
# safe for concurrent requests, so a single shared instance is thread-safe;
# the lock only guards lazy construction and teardown.

_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()

_CLIENT_LIMITS = httpx.Limits(
    max_keepalive_connections=20,
    max_connections=100,
    keepalive_expiry=60.0,
)


def get_http_client() -> httpx.Client:
    """The process-wide pooled client (built once, reused on every call)."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(
                    limits=_CLIENT_LIMITS,
                    # Backstop only — :func:`chat` always passes a per-request
                    # ``timeout=`` that overrides this value.
                    timeout=httpx.Timeout(60.0),
                )
    return _CLIENT


def close_http_client() -> None:
    """Close the pooled client (teardown/test helper; the next call rebuilds it)."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _CLIENT.close()
            _CLIENT = None


def _post(
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response:
    """POST ``body`` over the pooled transport with a per-request ``timeout``.

    Deliberately module-level: offline tests stub **this** function instead of
    monkeypatching ``httpx.post`` (which the pooled client never calls), so the
    default suite opens no sockets.
    """
    return get_http_client().post(url, json=body, headers=headers, timeout=timeout)


#: Attempts per provider request (1 disables retry). 429 / 5xx and transport
#: errors are transient by definition; retrying them *inside* the breaker call
#: keeps the breaker scoring the final outcome instead of an intermediate one.
LLM_RETRY_ATTEMPTS = max(1, int(os.environ.get("ZOLAI_LLM_RETRY_ATTEMPTS", "3")))
LLM_RETRY_BASE_S = max(0.0, float(os.environ.get("ZOLAI_LLM_RETRY_BASE_S", "0.5")))
LLM_RETRY_CAP_S = max(0.0, float(os.environ.get("ZOLAI_LLM_RETRY_CAP_S", "4.0")))


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Full-jitter exponential backoff, honouring ``Retry-After`` when sane."""
    if response is not None:
        raw = str(response.headers.get("retry-after") or "").strip()
        if raw:
            try:
                return min(LLM_RETRY_CAP_S, max(0.0, float(raw)))
            except ValueError:
                pass
    ceiling = min(LLM_RETRY_CAP_S, LLM_RETRY_BASE_S * (2**attempt))
    return random.uniform(0.0, ceiling)


def _sleep(seconds: float) -> None:
    """Module-level seam so offline tests can observe backoff without waiting."""
    if seconds > 0:
        time.sleep(seconds)


def _log_call(
    catalog_id: str,
    model: str,
    turns: int,
    latency_ms: float,
    status: str,
) -> None:
    """One structured INFO line per LLM call: identity + timing + outcome.

    Deliberately excludes the API key, the request body and the completion —
    prompts/completions are user content and keys are secrets, so neither
    reaches the log.  ``status`` is the HTTP status when a response arrived,
    else a symbolic token (``circuit_open`` / ``transport_error`` /
    ``invalid_body``).
    """
    logger.info(
        "llm_call provider=%s model=%s turns=%d latency_ms=%.1f status=%s",
        catalog_id,
        model,
        turns,
        latency_ms,
        status,
    )


def _ms_since(started: float) -> float:
    """Milliseconds elapsed since ``started`` (``time.monotonic``), 2 dp."""
    return round((time.monotonic() - started) * 1000, 2)


class ProviderError(Exception):
    """Provider failure carrying a stable ``code`` (never parse the message)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _UnhealthyProvider(Exception):
    """HTTP status meaning the provider is down or throttling us (429 / 5xx).

    Raised *inside* the breaker-wrapped callable on purpose: ``CircuitBreaker.call``
    scores whatever the callable returns, so a status checked afterwards was
    invisible to it — a provider answering 503 to every request recorded only
    successes and the circuit never opened, leaving each caller to pay the full
    timeout. A 4xx that is not 429 stays a plain response: our payload or key is
    wrong, and tripping the breaker would only hide that behind "circuit open".
    """

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def supports_native_tools(adapter: str) -> bool:
    """Whether ``adapter`` may receive a native OpenAI ``tools`` array.

    The brain path has **no** native tools support and must never receive a
    ``tools`` key in its request body — asserted by unit test.
    """
    return adapter in NATIVE_TOOL_ADAPTERS


def to_wire_model(model: str) -> str:
    """Strip the ``openrouter/`` platform prefix before the wire call."""
    m = str(model or "").strip()
    if m.startswith("openrouter/"):
        return m[len("openrouter/"):]
    return m


def _models(row: dict[str, Any]) -> list[str]:
    raw = row.get("models") or "[]"
    if isinstance(raw, list):
        return [str(m) for m in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(m) for m in parsed] if isinstance(parsed, list) else []


def resolve_completions_url(row: dict[str, Any]) -> str:
    """Row ``base_url`` override → catalog default (config for the brain)."""
    override = str(row.get("base_url") or "").strip()
    if override:
        return override
    entry = find_catalog(str(row.get("catalog_id") or ""))
    if entry is not None:
        default = catalog_default_base_url(entry)
        if default:
            return default
    raise ProviderError(
        PROVIDER_REQUEST_FAILED,
        f"provider {row.get('catalog_id')!r} has no endpoint configured",
    )


def resolve_api_key(row: dict[str, Any]) -> str:
    """API key for ``row``: ``env:NAME`` → env, ``enc:v1:`` → decrypt, else catalog env order."""
    from .provider_settings import read_secret_ref  # lazy: avoids import cycle

    direct = read_secret_ref(row)
    if direct:
        return direct
    entry = find_catalog(str(row.get("catalog_id") or ""))
    if entry is None:
        return ""
    from .provider_settings import read_catalog_env_fallback

    return read_catalog_env_fallback(entry)


def pick_provider(manager: Any = None) -> dict[str, Any]:
    """The enabled provider row to use for this request (read every call).

    Preference: the single ``is_active=1`` row, else the first enabled row by
    id (operator data — never a hardcoded model or first-row reroute).

    Raises:
        ProviderError: ``NO_ACTIVE_PROVIDER`` when nothing is enabled.
    """
    from .provider_settings import get_enabled_provider_rows

    rows = get_enabled_provider_rows(manager)
    if not rows:
        raise ProviderError(
            NO_ACTIVE_PROVIDER,
            "no AI provider is enabled — enable one in Admin → Settings.",
        )
    for row in rows:
        if row.get("is_active"):
            return row
    return rows[0]


def resolve_model(row: dict[str, Any], wanted: str | None = None) -> str:
    """Model id to send: explicit → row's ``selected_model`` → row's first model.

    Every candidate must come from operator data (the row); an empty result is
    a refusal (:data:`MODEL_NOT_CONFIGURED`), never a guessed model id.

    Raises:
        ProviderError: ``MODEL_NOT_CONFIGURED`` when ``wanted`` is not one of
        the row's models, or when the row offers no model at all.
    """
    models = _models(row)
    if wanted:
        wanted = str(wanted).strip()
        if wanted in models:
            return wanted
        raise ProviderError(
            MODEL_NOT_CONFIGURED,
            f"{wanted!r} is not in the model list of provider {row.get('catalog_id')!r}",
        )
    selected = str(row.get("selected_model") or "").strip()
    if selected:
        return selected
    if models:
        return models[0]
    raise ProviderError(
        MODEL_NOT_CONFIGURED,
        f"provider {row.get('catalog_id')!r} has no selected model — "
        "choose one on its card in Admin → Settings.",
    )


def build_chat_body(
    row: dict[str, Any],
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Assemble the OpenAI-wire request body.

    **Structural guarantee:** the brain adapter branch never writes a
    ``tools`` key — even if the caller passes one.  Native ``tools`` are only
    forwarded for adapters in :data:`NATIVE_TOOL_ADAPTERS`.
    """
    body: dict[str, Any] = {
        "model": to_wire_model(model),
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    adapter = str(row.get("adapter") or "openai")
    if adapter == "brain":
        # Structural omission: the brain path never sees native tools.
        pass
    elif tools and supports_native_tools(adapter):
        body["tools"] = tools
    return body


def chat(
    row: dict[str, Any],
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    timeout_s: float | None = None,
    user_api_key: str | None = None,
) -> dict[str, Any]:
    """One synchronous chat-completions call against ``row``'s endpoint.

    Returns:
        ``{"status", "text", "tool_calls", "latency_ms", "provider", "model"}``
        where each ``tool_calls`` entry is
        ``{"id", "name", "input", "arguments"}`` — ``id``/``arguments`` are the
        raw wire values, so a native multi-turn replay can echo the assistant
        tool-call turn verbatim instead of re-serialising a parsed dict.

    Raises:
        ProviderError: ``PROVIDER_KEY_MISSING`` / ``PROVIDER_REQUEST_FAILED``.

    ``user_api_key`` is an optional per-request key override (user-provided,
    not stored server-side). If provided, it takes precedence over the
    row's stored key or env fallback.

    ``max_tokens`` is forwarded to the wire **only when set** (nothing is sent
    that the caller did not ask for).  Some OpenAI-compatible endpoints require
    it — Anthropic's compatibility layer documents ``max_tokens`` as a required
    field — so a caller targeting such a row must supply it here.

    Transport is the pooled keep-alive client (:func:`get_http_client`) with
    ``timeout`` applied per request, so calls reuse a live connection instead
    of handshaking every time.  Exactly one structured ``INFO`` line is logged
    per call (:func:`_log_call`): provider, model, turns, latency, status —
    never a key, a prompt or a completion.
    """
    url = resolve_completions_url(row)
    # User-provided key takes precedence over stored key
    key = user_api_key or resolve_api_key(row)
    requires_key = bool(row.get("requires_key", 1))
    if requires_key and not key:
        raise ProviderError(
            PROVIDER_KEY_MISSING,
            f"provider {row.get('catalog_id')!r} needs an API key — provide one in the "
            "request or paste one in Admin → Settings or point it at an env: reference.",
        )

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    body = build_chat_body(row, model, messages, tools=tools, max_tokens=max_tokens)
    timeout = float(timeout_s or row.get("timeout_s") or 60)

    catalog_id = str(row.get("catalog_id") or "unknown")
    cb = get_circuit_breaker(f"llm_{catalog_id}")
    turns = len(messages)

    def _do_request() -> httpx.Response:
        # Retry *inside* the breaker call so the breaker scores the outcome the
        # caller actually sees, not an intermediate throttle.
        for attempt in range(LLM_RETRY_ATTEMPTS):
            last_attempt = attempt + 1 >= LLM_RETRY_ATTEMPTS
            try:
                response = _post(url, body=body, headers=headers, timeout=timeout)
            except httpx.TransportError:
                if last_attempt:
                    raise
                _sleep(_retry_delay(None, attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if last_attempt:
                    raise _UnhealthyProvider(response)
                _sleep(_retry_delay(response, attempt))
                continue
            return response
        raise AssertionError("unreachable: retry loop always returns or raises")

    started = time.monotonic()
    try:
        response = cb.call(_do_request)
    except CircuitBreakerError as exc:
        _log_call(catalog_id, model, turns, _ms_since(started), "circuit_open")
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} circuit breaker open: {exc}",
        ) from exc
    except _UnhealthyProvider as exc:
        response = exc.response
        _log_call(catalog_id, model, turns, _ms_since(started), str(response.status_code))
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} returned HTTP {response.status_code}: "
            f"{response.text[:300]}",
        ) from exc
    except httpx.HTTPError as exc:
        _log_call(catalog_id, model, turns, _ms_since(started), "transport_error")
        raise ProviderError(
            PROVIDER_REQUEST_FAILED, f"{catalog_id!r} request failed: {exc}"
        ) from exc
    latency_ms = _ms_since(started)

    if response.status_code >= 400:
        _log_call(catalog_id, model, turns, latency_ms, str(response.status_code))
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} returned HTTP {response.status_code}: "
            f"{response.text[:300]}",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        _log_call(catalog_id, model, turns, latency_ms, "invalid_body")
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} returned a non-JSON body",
        ) from exc

    _log_call(catalog_id, model, turns, latency_ms, str(response.status_code))

    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    native_calls = message.get("tool_calls") or []
    tool_calls = [
        {
            "id": str(c.get("id") or ""),
            "name": ((c.get("function") or {}).get("name") or c.get("name") or ""),
            "input": _tool_call_input(c),
            "arguments": _tool_call_arguments(c),
        }
        for c in native_calls
        if isinstance(c, dict)
    ]
    return {
        "status": response.status_code,
        "text": text,
        "tool_calls": tool_calls,
        "latency_ms": latency_ms,
        "provider": catalog_id,
        "model": model,
    }


def _tool_call_arguments(call: dict[str, Any]) -> str:
    """The raw ``function.arguments`` JSON string of a native tool call.

    Kept verbatim (never re-serialised from :func:`_tool_call_input`) so a
    native multi-turn replay hands the provider back exactly what it emitted.
    """
    fn = call.get("function") or {}
    raw = fn.get("arguments") if isinstance(fn, dict) else None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    fallback = call.get("input") or call.get("arguments")
    if isinstance(fallback, dict):
        return json.dumps(fallback, ensure_ascii=False)
    return ""


def _tool_call_input(call: dict[str, Any]) -> dict[str, Any]:
    """Decode an OpenAI native ``tool_calls`` entry's arguments (JSON string)."""
    fn = call.get("function") or {}
    raw = fn.get("arguments") if isinstance(fn, dict) else None
    if not raw:
        raw = call.get("input") or call.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    return {}
