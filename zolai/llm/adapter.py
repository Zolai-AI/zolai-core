"""AI provider adapter — brain | openai | openrouter | custom dispatch (P1 §A).

Python port of ``pcore-assistant/server/src/services/ai.ts``:

- Every adapter speaks the **OpenAI wire** (``POST …/chat/completions``).
- **Brain is first-class**: URL from ``AI_BRAIN_URL`` / ``PCORE_BRAIN_URL``
  (default ``https://pcore-brain.peterlianpi.site/v1``), key from its
  ``env_keys`` order (``AI_BRAIN_API_KEY`` → ``PCORE_BRIDGE_API_KEY`` →
  ``AI_API_KEY``), and its request body **structurally omits the ``tools``
  key** — the brain path has no native function-calling support.
- Native ``tools`` passthrough only for adapters in
  :data:`NATIVE_TOOL_ADAPTERS` (``openai`` / ``openrouter``).
- :func:`pick_provider` reads the DB **every call** (no boot cache, no
  first-row reroute) and fails with the stable machine-readable codes
  :data:`NO_ACTIVE_PROVIDER` / :data:`MODEL_NOT_CONFIGURED` — callers never
  string-match messages and an empty model id never reaches a provider.

``ZOLAI_ENGINE_MODE`` / ``llm_allowed()`` (founder directive D2) are checked
by *call sites* (agent synthesis, assistant chat) before any socket is
opened; this module is the transport, not the gate.
"""

from __future__ import annotations

import json
import logging
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


class ProviderError(Exception):
    """Provider failure carrying a stable ``code`` (never parse the message)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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
    timeout_s: float | None = None,
    user_api_key: str | None = None,
) -> dict[str, Any]:
    """One synchronous chat-completions call against ``row``'s endpoint.

    Returns:
        ``{"status", "text", "tool_calls", "latency_ms", "provider", "model"}``.

    Raises:
        ProviderError: ``PROVIDER_KEY_MISSING`` / ``PROVIDER_REQUEST_FAILED``.

    ``user_api_key`` is an optional per-request key override (user-provided,
    not stored server-side). If provided, it takes precedence over the
    row's stored key or env fallback.
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

    body = build_chat_body(row, model, messages, tools=tools)
    timeout = float(timeout_s or row.get("timeout_s") or 60)

    catalog_id = str(row.get("catalog_id") or "unknown")
    cb = get_circuit_breaker(f"llm_{catalog_id}")

    def _do_request() -> httpx.Response:
        return httpx.post(url, json=body, headers=headers, timeout=timeout)

    started = time.monotonic()
    try:
        response = cb.call(_do_request)
    except CircuitBreakerError as exc:
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} circuit breaker open: {exc}",
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            PROVIDER_REQUEST_FAILED, f"{catalog_id!r} request failed: {exc}"
        ) from exc
    latency_ms = round((time.monotonic() - started) * 1000, 2)

    if response.status_code >= 400:
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} returned HTTP {response.status_code}: "
            f"{response.text[:300]}",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            PROVIDER_REQUEST_FAILED,
            f"{catalog_id!r} returned a non-JSON body",
        ) from exc

    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    native_calls = message.get("tool_calls") or []
    tool_calls = [
        {
            "name": ((c.get("function") or {}).get("name") or c.get("name") or ""),
            "input": _tool_call_input(c),
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
