"""Assistants (P4) — one module, two routes, shared runner.

- ``POST /api/v1/assistant/chat`` — **public**: in ``rbac.PUBLIC_ROUTES``, so
  ``ZOLAI_API_AUTH=enforce`` never 401s it; anonymous IP chat bucket (10/min)
  is applied by the middleware.  Tools: retrieval-only set
  (``rag_search``, ``dictionary_lookup``, ``word_evidence``, ``verse_lookup``,
  ``grammar_check``).  Answers always carry ``citations``; with no usable
  provider the response is an honest **``retrieval_only: true``** fallback —
  never fake generation.
- ``POST /api/v1/admin/assistant/chat`` — ``require_role("admin", strict=True)``
  + ``agent:run`` scope; full tool set and the **tool-call trace** for the
  Studio inspector.

Both share the ``AGENT_MAX_TURNS`` budget and persist nothing unless the
caller passes ``persist: true`` (admin only) — chat ≠ run; agent runs stay on
``/api/v1/agent/runs``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agent.loop import AGENT_MAX_TURNS, run_agent_loop
from ..agent.synthesis import citations_from_results, make_chat_fn
from ..agent.tools.executor import execute_tool
from ..agent.tools.registry import allow_list
from ..agent.tools.types import ToolCall
from ..engines import engine_mode, llm_allowed
from . import auth, rbac

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
admin_router = APIRouter(prefix="/api/v1/admin/assistant", tags=["assistant-admin"])

ADMIN_ROLE_DEP = Depends(rbac.require_role("admin", strict=True))
ADMIN_SCOPE_DEP = Depends(auth.require_scope("agent:run", strict=True))

#: Retrieval-only fallback tools for the public assistant.
RETRIEVAL_TOOLS: tuple[str, ...] = ("rag_search", "dictionary_lookup")


class ChatIn(BaseModel):
    """Chat body shared by both routes.

    ``provider`` / ``model`` are an optional per-request override (Phase B §9):
    validated against an **enabled** catalog row *before* the run (a DB read —
    so an unknown/disabled provider is a real 404 even in ``rule`` mode) and
    echoed back as ``requested_provider`` / ``requested_model``.

    ``api_key`` is an optional per-request override for the provider's API key.
    This allows users to use their own API keys without server-side storage.

    ``user_provider`` is an optional per-request provider override for
    user-provided keys (OpenAI, OpenRouter, Gemini, custom). This allows
    users to specify which provider their key belongs to without needing
    a server-side catalog entry.
    """

    message: str = Field(min_length=1, max_length=8000)
    persist: bool = False
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    user_provider: str | None = None


def _sanitize(answer: str) -> tuple[str, dict[str, Any]]:
    """ZVS 2018 validation + one revise pass before returning to the client."""
    from ..agent.orchestrator import zvs_review

    return zvs_review(answer)


def _first_token(text: str) -> str:
    for token in str(text or "").split():
        stripped = "".join(ch for ch in token.lower() if ch.isalpha() or ch == "-")
        if len(stripped) >= 2:
            return stripped
    return ""


def _retrieval_only(message: str, allow: frozenset[str]) -> dict[str, Any]:
    """Deterministic retrieval answer — zero sockets, honest labelling."""
    started = time.monotonic()
    trace: list[dict[str, Any]] = []
    calls = [ToolCall(name="rag_search", input={"query": message, "limit": 5})]
    token = _first_token(message)
    if token and "dictionary_lookup" in allow:
        calls.append(ToolCall(name="dictionary_lookup", input={"word": token}))
    for call in calls:
        result = execute_tool(call, allow)
        entry = result.to_dict()
        entry["input"] = call.input
        entry["status"] = "completed" if result.ok else "failed"
        trace.append(entry)

    citations = citations_from_results(trace)
    if citations:
        lines = [
            f"Retrieval-only answer (no active LLM provider — honest fallback, "
            f"{len(citations)} source(s)):"
        ]
        for i, c in enumerate(citations[:5], 1):
            ref = f" {c['ref']}" if c.get("ref") else ""
            lines.append(f"[{i}] {c['source']}{ref}: {c['text'][:200]}")
        answer = "\n".join(lines)
    else:
        answer = (
            "Retrieval-only mode: I could not find supporting evidence in the "
            "knowledge base for that question, and no LLM provider is active — "
            "I would rather say so than invent an answer."
        )
    return {
        "answer": answer,
        "citations": citations,
        "tool_calls": trace,
        "turns": 0,
        "provider": "",
        "model": "",
        "mode": engine_mode(),
        "retrieval_only": True,
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
    }


def run_assistant(
    message: str,
    *,
    admin: bool = False,
    persist: bool = False,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    user_provider: str | None = None,
) -> dict[str, Any]:
    """Shared runner: resolve provider → loop (or honest retrieval fallback).

    ``provider``/``model`` override the assistant's default target for this
    request only (validated by the route via ``validate_selection`` first).
    The override is echoed in the response as ``requested_provider`` /
    ``requested_model``; ``provider``/``model`` keep meaning *actually used*.

    ``api_key`` is an optional per-request API key override (user-provided key
    for this request only, not stored server-side).
    """
    assistant = "admin" if admin else "public"
    allow = allow_list("admin" if admin else "public")
    started = time.monotonic()

    if not llm_allowed():
        out = _retrieval_only(message, allow)
        return _finish(
            out, message, admin=admin, persist=persist, started=started,
            requested_provider=provider, requested_model=model,
            requested_api_key=api_key is not None,
            requested_user_provider=user_provider is not None,
        )

    try:
        chat, meta = make_chat_fn(assistant=assistant, provider=provider, model=model, user_api_key=api_key, user_provider=user_provider)
    except Exception as exc:  # ProviderError (stable codes) → honest fallback
        logger.info("assistant %s degraded to retrieval-only: %s", assistant, exc)
        out = _retrieval_only(message, allow)
        out["provider_error"] = str(exc)
        return _finish(
            out, message, admin=admin, persist=persist, started=started,
            requested_provider=provider, requested_model=model,
            requested_api_key=api_key is not None,
            requested_user_provider=user_provider is not None,
        )

    loop_result = run_agent_loop(
        system_prompt=(
            "You are the Zolai assistant. Answer in the user's language, keep ZVS 2018 "
            "orthography (pasian/gam/tapa/topa), cite your tool evidence, and never "
            "invent tool results. If you lack evidence, say so."
        ),
        user_message=message,
        allow=allow,
        chat=chat,
        max_turns=AGENT_MAX_TURNS,
        native=bool(meta.get("native")),
    )
    answer = str(loop_result.get("reply") or "").strip()
    trace = list(loop_result.get("tool_calls") or [])
    citations = citations_from_results(trace)
    if not answer:
        # Provider failed mid-loop → honest fallback, never a blank 200.
        out = _retrieval_only(message, allow)
        out["provider_error"] = str(loop_result.get("error") or "empty_reply")
        return _finish(
            out, message, admin=admin, persist=persist, started=started,
            requested_provider=provider, requested_model=model,
            requested_api_key=api_key is not None,
            requested_user_provider=user_provider is not None,
        )

    out = {
        "answer": answer,
        "citations": citations,
        "tool_calls": trace,
        "turns": int(loop_result.get("turns") or 0),
        "provider": str(meta.get("catalog_id") or ""),
        "model": str(meta.get("model") or ""),
        "mode": engine_mode(),
        "retrieval_only": False,
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
    }
    if not loop_result.get("ok"):
        out["loop_error"] = str(loop_result.get("error") or "")
    return _finish(
        out, message, admin=admin, persist=persist, started=started,
        requested_provider=provider, requested_model=model,
    )


def _finish(
    out: dict[str, Any],
    message: str,
    *,
    admin: bool,
    persist: bool,
    started: float,
    requested_provider: str | None = None,
    requested_model: str | None = None,
    requested_api_key: bool = False,
    requested_user_provider: bool = False,
) -> dict[str, Any]:
    """ZVS validation + optional admin persist (chat is not an agent run).

    Also stamps the per-request override echo: ``requested_provider`` /
    ``requested_model`` are what the caller *asked for* (``""`` when no
    override) — ``provider``/``model`` in ``out`` keep meaning *actually used*
    (``""`` in rule/retrieval-only mode). ``requested_api_key`` indicates
    whether the caller provided a per-request API key. ``requested_user_provider``
    indicates whether the caller provided a per-request user provider override.
    """
    out["requested_provider"] = requested_provider or ""
    out["requested_model"] = requested_model or ""
    answer, review = _sanitize(str(out.get("answer") or ""))
    out["answer"] = answer
    out["zvs"] = {k: review[k] for k in ("valid", "violations_before", "violations_after")}
    out["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    out["requested_api_key"] = requested_api_key
    out["requested_user_provider"] = requested_user_provider
    if persist:
        if not admin:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "persist_admin_only",
                    "detail": "persist: true is an admin capability",
                },
            )
        from ..agent import store

        run = store.create_run(message, created_by="admin-chat", mode=str(out.get("mode") or "rule"))
        store.update_run(
            int(run["id"]),
            status="succeeded",
            phases={"chat": {"status": "ok", "retrieval_only": bool(out.get("retrieval_only"))}},
            tool_calls=out.get("tool_calls") or [],
            evidence=out.get("citations") or [],
            answer=str(out.get("answer") or ""),
            provider=str(out.get("provider") or ""),
            model=str(out.get("model") or ""),
            turns=int(out.get("turns") or 0),
            latency_ms=float(out.get("latency_ms") or 0.0),
            outcome="chat",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )
        out["persisted_run_id"] = int(run["id"])
    return out


@public_router.post("/chat")
def public_chat(body: ChatIn) -> dict[str, Any]:
    """Anonymous-safe chat — public in every auth mode (rbac.PUBLIC_ROUTES).

    An optional ``provider``/``model`` override is validated **before** the
    run (DB read only): unknown/disabled provider → 404, model not on the
    row → 400, in every engine mode including ``rule``.

    ``api_key`` is an optional per-request override for the provider's API key
    (user-provided, not stored server-side).

    ``user_provider`` is an optional per-request provider override for
    user-provided keys (OpenAI, OpenRouter, Gemini, custom).
    """
    from .providers_router import validate_selection

    validate_selection(body.provider, body.model, assistant="public")
    return run_assistant(
        body.message, admin=False, persist=False,
        provider=body.provider, model=body.model, api_key=body.api_key, user_provider=body.user_provider,
    )


@admin_router.post("/chat", dependencies=[ADMIN_ROLE_DEP, ADMIN_SCOPE_DEP])
def admin_chat(body: ChatIn, request: Request) -> dict[str, Any]:
    """Admin chat: full tool set + trace; strict role + ``agent:run`` scope."""
    from .providers_router import validate_selection

    validate_selection(body.provider, body.model, assistant="admin")
    return run_assistant(
        body.message, admin=True, persist=body.persist,
        provider=body.provider, model=body.model, api_key=body.api_key, user_provider=body.user_provider,
    )
