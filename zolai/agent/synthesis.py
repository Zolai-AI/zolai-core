"""Provider-bound chat closure + rule-mode draft (P3 §C build phase).

``ZOLAI_ENGINE_MODE=rule`` (or ``hybrid|ai`` without a key) short-circuits
**before any provider resolution** — :func:`zolai.engines.llm_allowed` is
checked by the callers (orchestrator, assistant router), so a rule-mode run
opens zero sockets.  Resolution failures surface as
:class:`zolai.llm.adapter.ProviderError` with a stable code; callers degrade
to the deterministic draft instead of guessing a model.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from ..llm import adapter as adapter_mod
from ..llm import provider_settings as settings

logger = logging.getLogger(__name__)

#: Tool-call shape the loop consumes: ``{"text", "tool_calls", "latency_ms"}``.
ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], dict[str, Any]]


def make_chat_fn(
    *,
    assistant: str = "public",
    provider: str | None = None,
    model: str | None = None,
    native: bool | None = None,
    timeout_s: float | None = None,
) -> tuple[ChatFn, dict[str, Any]]:
    """Resolve the assistant's provider once and bind a chat closure.

    Args:
        assistant: ``'public'`` | ``'admin'`` — pin lookup key.
        provider: Optional per-request provider override (Phase B §9).  A DB
            read only — validated against the row (unknown/disabled provider →
            ``PROVIDER_UNKNOWN``/``PROVIDER_INACTIVE``), never a socket.
        model: Optional per-request model override; must be listed on the
            chosen row (``PROVIDER_MODEL_UNKNOWN`` otherwise).  Blank/None
            falls back to the row's selected model, then its first catalog
            model.
        native: Force the native-``tools`` decision; ``None`` derives it from
            the resolved adapter (``openai``/``openrouter`` only — the brain
            path never receives a ``tools`` key).
        timeout_s: Per-request timeout override.

    Returns:
        ``(chat_fn, meta)`` where ``meta`` carries ``catalog_id``/``model``/
        ``native`` for the response header.

    Raises:
        ProviderError: ``NO_ACTIVE_PROVIDER`` / ``MODEL_NOT_CONFIGURED`` /
            ``ASSISTANT_*`` / ``PROVIDER_*`` — callers degrade to
            retrieval-only or map to 4xx before a run starts.
    """
    if provider or model:
        resolved = settings.resolve_selection(provider, model, assistant=assistant)
    else:
        resolved = settings.resolve_assistant_ai(assistant)
    row: dict[str, Any] = resolved["row"]
    model_id: str = resolved["model"]
    adapter_name = str(row.get("adapter") or "openai")
    use_native = (
        adapter_mod.supports_native_tools(adapter_name) if native is None else bool(native)
    )

    def chat(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = adapter_mod.chat(row, model_id, messages, tools=tools, timeout_s=timeout_s)
        return {
            "text": result.get("text") or "",
            "tool_calls": result.get("tool_calls") or [],
            "latency_ms": result.get("latency_ms") or 0.0,
        }

    meta = {
        "catalog_id": str(row.get("catalog_id") or ""),
        "model": model_id,
        "adapter": adapter_name,
        "native": use_native,
        "chat": chat,
    }
    return chat, meta


#: Keys consulted (in order) to turn a tool item into citable text.
_TEXT_KEYS = ("text", "english", "en", "zo", "translation", "object", "subject", "definition")


def _item_text(item: dict[str, Any]) -> str:
    for key in _TEXT_KEYS:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def rule_draft(goal: str, evidence: Sequence[dict[str, Any]]) -> str:
    """Deterministic evidence-joined draft (rule-mode build, zero sockets)."""
    goal = str(goal or "").strip()
    if not evidence:
        return (
            f"{goal}\n\n"
            "No supporting evidence was found in the knowledge base, so this "
            "answer stays an unverified draft — verify before use."
        )
    lines = [f"{goal}", "", "Evidence:"]
    for i, item in enumerate(evidence[:8], 1):
        source = item.get("source") or item.get("table") or "kb"
        ref = item.get("ref") or item.get("id") or item.get("zolai") or ""
        text = _item_text(item)[:200]
        head = f"[{i}] {source}" + (f" {ref}" if ref else "")
        lines.append(f"{head}: {text}")
    lines += ["", "Draft: the evidence above is consistent with the goal (rule mode — no LLM)."]
    return "\n".join(lines)


def citations_from_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten tool results into ``{source, ref, text, score}`` citations.

    Handles both shapes the handlers return: list-of-dicts (``rag_search``)
    and a single dict (``verse_lookup``, ``dictionary_lookup``) — dictionary
    glosses are citable evidence too.  Explicit misses (``found: false``) are
    never cited.
    """
    citations: list[dict[str, Any]] = []
    for res in results:
        if not res.get("ok"):
            continue
        data = res.get("data")
        items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict) or item.get("found") is False:
                continue
            text = _item_text(item)
            ref = str(item.get("ref") or item.get("id") or "")
            if not text and not ref:
                continue
            score = item.get("score", 0.0)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0
            citations.append(
                {
                    "source": item.get("source") or res.get("name") or "kb",
                    "ref": ref,
                    "text": text[:300],
                    "score": score,
                }
            )
    # de-dup by (source, ref, text[:80])
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for c in citations:
        key = (str(c["source"]), str(c["ref"]), c["text"][:80])
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique
