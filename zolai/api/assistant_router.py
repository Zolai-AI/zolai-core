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

import json
import logging
import re
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
from .errors import api_error


# ── Role-specific system prompts ──────────────────────────────────────────────

PUBLIC_ASSISTANT_PROMPT = (
    "You are the Zolai Public Assistant — a helpful, honest guide for the Tedim "
    "Zolai (ZVS 2018) language. Your role:\n"
    "1. Answer using ONLY the tools provided — rag_search, word_evidence, "
    "dictionary_lookup, verse_lookup, grammar_check, analyze_text, word_related.\n"
    "2. NEVER invent facts. If tools return no evidence, say so honestly.\n"
    "3. Keep ZVS 2018 orthography: pasian (God), gam (place), tapa (father), "
    "topa (Lord), kumpipa (chief), tua (person/body).\n"
    "4. Use SOV word order. Use ergative 'in' for transitive subjects.\n"
    "5. Cite your evidence with [source ref] after each claim.\n"
    "6. NEVER claim to be in 'retrieval-only mode' or that no LLM provider is\n"
    "   configured — if you are writing this sentence, a provider is active and\n"
    "   the server records the mode itself. Say only that a given tool returned\n"
    "   no evidence for a given point.\n"
    "7. Maximum 4 lines per response. Be concise."
)

ADMIN_ASSISTANT_PROMPT = (
    "You are the Zolai Admin Assistant — full tool access for knowledge "
    "management and debugging. Your role:\n"
    "1. All public tools PLUS: knowledge_stats, kb_research, review_queue_submit, "
    "provider_status, web_search (when enabled).\n"
    "2. Use kb_research to search hypotheses/claims. Use knowledge_stats for "
    "database overview. Use review_queue_submit to propose new facts.\n"
    "3. Show tool trace transparently. Keep ZVS 2018 compliance.\n"
    "4. Maximum 6 lines. Include citations and tool results."
)

AGENT_ORCHESTRATOR_PROMPT = (
    "You are the Zolai Agent Orchestrator — a careful research agent for the "
    "Tedim Zolai (ZVS 2018) language. Your workflow:\n"
    "1. RESEARCH: Use rag_search + kb_research to gather evidence.\n"
    "2. BUILD: Synthesize a draft answer from evidence. Use additional tools as needed.\n"
    "3. REVIEW: Validate against ZVS 2018 (orthography, SOV, ergative 'in').\n"
    "4. SHIP: Deliver final answer with full citations.\n"
    "5. NEVER invent tool results. If evidence is missing, say so.\n"
    "6. Available tools: rag_search, kb_research, word_evidence, word_related, "
    "analyze_text, grammar_check, dictionary_lookup, verse_lookup, knowledge_stats, "
    "review_queue_submit, provider_status, web_search.\n"
    "7. One tool per turn. Max 3 turns. Cite everything."
)

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
admin_router = APIRouter(prefix="/api/v1/admin/assistant", tags=["assistant-admin"])

ADMIN_ROLE_DEP = Depends(rbac.require_role("admin", strict=True))
ADMIN_SCOPE_DEP = Depends(auth.require_scope("agent:run", strict=True))

#: Retrieval-only fallback tools for the public assistant (all public tools).
RETRIEVAL_TOOLS: tuple[str, ...] = (
    "rag_search",
    "word_evidence",
    "word_related",
    "analyze_text",
    "grammar_check",
    "dictionary_lookup",
    "verse_lookup",
)


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

    ``api_key`` / ``user_provider`` are accepted on the **admin** route only
    (gated by the resolved admin role). The public route rejects any
    caller-supplied credential with 400 — see ``PUBLIC_CREDENTIAL_FIELDS``.
    """

    message: str = Field(min_length=1, max_length=8000)
    persist: bool = False
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    user_provider: str | None = None


class ChatFeedbackIn(BaseModel):
    """POST /api/v1/assistant/feedback body.

    Public feedback on an assistant chat response. No authentication required
    since the chat itself is public (rbac.PUBLIC_ROUTES).
    """

    message: str = Field(min_length=1, max_length=8000)
    response: str = Field(min_length=1, max_length=16000)
    mode: str = Field(pattern="^(public|admin)$")
    retrieval_only: bool = False
    provider: str = ""
    model: str = ""
    score: int = Field(ge=-1, le=1)



def _sanitize(answer: str) -> tuple[str, dict[str, Any]]:
    """ZVS 2018 validation + one revise pass before returning to the client."""
    from ..agent.orchestrator import zvs_review

    return zvs_review(answer)


#: How many distinct words of a question get a direct dictionary lookup.
MAX_DICTIONARY_LOOKUPS = 4


def _first_token(text: str) -> str:
    tokens = _content_tokens(text)
    return tokens[0] if tokens else ""


def _content_tokens(text: str) -> list[str]:
    """Distinct, meaningful words of a question, in order, deduplicated.

    The assistant used to look up only the *first* word, so "nang bang hih thei"
    produced one dictionary entry and the model — correctly, from the evidence it
    was given — reported no meaning for the other three while they sat in the
    corpus. Every word gets its own lookup, bounded so a long question cannot
    fan out into a dozen calls.
    """
    from ..rag.retrieve import STOPWORDS

    out: list[str] = []
    seen: set[str] = set()
    for token in str(text or "").split():
        stripped = "".join(ch for ch in token.lower() if ch.isalpha() or ch == "-")
        if len(stripped) < 2 or stripped in STOPWORDS or stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
        if len(out) >= MAX_DICTIONARY_LOOKUPS:
            break
    return out


def _retrieval_only(message: str, allow: frozenset[str]) -> dict[str, Any]:
    """Deterministic retrieval answer — zero sockets, honest labelling."""
    import re
    started = time.monotonic()
    trace: list[dict[str, Any]] = []

    # Detect Bible-related queries
    bible_keywords = (
        "bible", "scripture", "verse", "verses", "biblical",
        "old testament", "new testament", "genesis", "exodus",
        "matthew", "mark", "luke", "john", "psalm", "psalms",
        "proverbs", "isaiah", "jeremiah", "ezekiel", "daniel",
        "romans", "corinthians", "galatians", "ephesians",
        "philippians", "colossians", "thessalonians", "timothy",
        "titus", "philemon", "hebrews", "james", "peter", "john",
        "jude", "revelation"
    )
    message_lower = message.lower()
    needs_bible_filter = any(kw in message_lower for kw in bible_keywords)

    rag_input = {"query": message, "limit": 5}
    if needs_bible_filter:
        rag_input["source_filter"] = ["bible_verses"]

    calls = [ToolCall(name="rag_search", input=rag_input)]
    if "dictionary_lookup" in allow:
        # One lookup per content word, not one per question.
        for token in _content_tokens(message):
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


def _ground_with_evidence(
    message: str,
    *,
    allow: frozenset[str],
    chat: Any,
    system_prompt: str,
    trace: list[dict[str, Any]],
) -> tuple[str, bool] | None:
    """One grounding turn for answers that arrived with no tool call at all.

    The loop returns the moment a model turn contains no ``<<<TOOL>>>`` block,
    so a model that simply answers from memory ships an ungrounded reply with
    zero citations -- which is what happened for "nang bang hih thei": the
    model declined to call tools, then claimed it had no evidence, while all
    four words sat in the dictionary.

    Evidence is gathered here deterministically (same calls the retrieval-only
    path makes) and handed back for a single synthesis turn. Returns
    ``(answer, ok)`` when grounding ran, or ``None`` when nothing was found --
    the caller keeps the model's original reply in that case, because an
    honest "no evidence" is not a defect.
    """
    from ..agent.loop import build_followup_prompt

    calls = [ToolCall(name="rag_search", input={"query": message, "limit": 5})]
    if "dictionary_lookup" in allow:
        for token in _content_tokens(message):
            calls.append(ToolCall(name="dictionary_lookup", input={"word": token}))

    results = []
    for call in calls:
        result = execute_tool(call, allow)
        entry = result.to_dict()
        entry["input"] = call.input
        entry["status"] = "completed" if result.ok else "failed"
        trace.append(entry)
        if result.ok:
            results.append(result)

    if not any(citations_from_results([e]) for e in trace):
        return None  # genuinely nothing to ground on

    try:
        turn = chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_followup_prompt(message, results)},
            ],
            None,
        )
    except Exception:
        return None

    reply = str(turn.get("text") or "").strip()
    return (reply, True) if reply else None


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

    # Select role-specific system prompt
    system_prompt = ADMIN_ASSISTANT_PROMPT if admin else PUBLIC_ASSISTANT_PROMPT

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
        # Stable code only: ProviderError's str() embeds its message, which
        # can carry upstream request/response text (adapter.py wraps httpx
        # errors and response bodies in it) — that must never reach a caller.
        # Non-provider failures get a fixed code; the raw exception already
        # reaches the log below.
        provider_error = getattr(exc, "code", None) or "PROVIDER_UNAVAILABLE"
        logger.info("assistant %s degraded to retrieval-only: %s", assistant, exc)
        out = _retrieval_only(message, allow)
        out["provider_error"] = provider_error
        return _finish(
            out, message, admin=admin, persist=persist, started=started,
            requested_provider=provider, requested_model=model,
            requested_api_key=api_key is not None,
            requested_user_provider=user_provider is not None,
        )

    loop_result = run_agent_loop(
        system_prompt=system_prompt,
        user_message=message,
        allow=allow,
        chat=chat,
        max_turns=AGENT_MAX_TURNS,
        native=bool(meta.get("native")),
    )
    answer = str(loop_result.get("reply") or "").strip()
    trace = list(loop_result.get("tool_calls") or [])
    citations = citations_from_results(trace)

    if answer and not trace:
        # The model answered without consulting the knowledge base. Give it
        # one deterministic evidence pass rather than shipping a reply that
        # asserts things about Zolai it never looked up.
        grounded = _ground_with_evidence(
            message, allow=allow, chat=chat, system_prompt=system_prompt, trace=trace
        )
        if grounded is not None:
            answer, _ok = grounded
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


#: Body fields the **public** chat route refuses (MCP invariant M3: a
#: tool/API must never accept a caller-supplied credential). ``api_key`` /
#: ``user_provider`` are real ``ChatIn`` fields (the admin route legitimately
#: accepts them, gated on the *resolved* admin role — never on a
#: client-declared field); ``token`` / ``authorization`` are rejected at the
#: raw-JSON level so a smuggled credential-shaped key cannot ride along even
#: though the pydantic model would ignore it.
PUBLIC_CREDENTIAL_FIELDS: tuple[str, ...] = (
    "api_key",
    "user_provider",
    "token",
    "authorization",
)


def _supplied_credential_fields(body: "ChatIn", request: Request) -> list[str]:
    """Names (never values) of credential-shaped fields present in the body."""
    supplied: set[str] = set()
    for field in ("api_key", "user_provider"):
        if getattr(body, field, None):
            supplied.add(field)
    try:
        raw = request._body  # cached by FastAPI's own body parsing
        payload = json.loads(raw) if raw else {}
    except Exception:  # unreadable/absent raw body — the model check above stands
        payload = {}
    if isinstance(payload, dict):
        for field in PUBLIC_CREDENTIAL_FIELDS:
            if payload.get(field):
                supplied.add(field)
    return sorted(supplied)


@public_router.post("/chat")
async def public_chat(body: ChatIn, request: Request) -> dict[str, Any]:
    """Anonymous-safe chat — public in every auth mode (rbac.PUBLIC_ROUTES).

    An optional ``provider``/``model`` override is validated **before** the
    run (DB read only): unknown/disabled provider → 404, model not on the
    row → 400, in every engine mode including ``rule``.

    This route is reachable with **no credential at all**, so it never
    accepts one: a body carrying ``api_key`` / ``user_provider`` (or a
    smuggled ``token`` / ``authorization``) is refused with 400 and the
    stable code ``caller_supplied_credentials_forbidden`` — the credential
    value is never echoed, logged or forwarded. Callers who want a per-request
    key use the admin chat, which is gated on the resolved admin role.
    """
    supplied = _supplied_credential_fields(body, request)
    if supplied:
        raise HTTPException(
            status_code=400,
            detail=api_error(
                "caller_supplied_credentials_forbidden",
                "This public endpoint never accepts credentials in the request "
                "body. Server-side providers need no caller key; to use your own "
                "key, sign in and use the admin chat instead.",
                fields=supplied,  # field NAMES only — never the values
            ),
        )

    from .providers_router import validate_selection

    validate_selection(body.provider, body.model, assistant="public")
    return run_assistant(
        body.message, admin=False, persist=False,
        provider=body.provider, model=body.model,
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


# Rate limit bucket for feedback (10 per minute per IP)
_feedback_bucket: dict[str, list[float]] = {}
FEEDBACK_LIMIT = 10
FEEDBACK_WINDOW_S = 60.0


def _check_feedback_rate(request: Request) -> None:
    """Rate limit feedback to 10/min/IP."""
    import time
    client_ip = request.client.host if request.client else "unknown"
    bucket_key = f"fb:{client_ip}"
    now = time.monotonic()
    hits = [t for t in _feedback_bucket.get(bucket_key, []) if now - t < FEEDBACK_WINDOW_S]
    if len(hits) >= FEEDBACK_LIMIT:
        retry_after = max(1, int(FEEDBACK_WINDOW_S - (now - hits[0])))
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "scope": "assistant_feedback", "limit": FEEDBACK_LIMIT, "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _feedback_bucket[bucket_key] = hits


@public_router.post("/feedback")
def assistant_feedback(body: ChatFeedbackIn, request: Request) -> dict[str, Any]:
    """Record feedback on an assistant chat response.

    Public endpoint — no authentication required. Stores the feedback with
    the message/response pair for later analysis.
    Rate limited to 10 requests/minute/IP.
    """
    from ..data.repositories import get_engine
    from sqlalchemy import text as sa_text

    _check_feedback_rate(request)

    # Hash the client IP for basic deduplication (not PII)
    client_ip = request.client.host if request.client else "unknown"
    import hashlib
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    engine = get_engine()
    with engine.begin() as conn:
        # Ensure table exists (lazy creation for tests and fresh deployments)
        conn.execute(
            sa_text("""
                CREATE TABLE IF NOT EXISTS assistant_chat_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    response TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    retrieval_only INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    ip_hash TEXT
                )
            """)
        )
        cursor = conn.execute(
            sa_text("""
                INSERT INTO assistant_chat_feedback
                (message, response, mode, retrieval_only, provider, model, score, ip_hash)
                VALUES (:message, :response, :mode, :retrieval_only, :provider, :model, :score, :ip_hash)
            """),
            {
                "message": body.message,
                "response": body.response,
                "mode": body.mode,
                "retrieval_only": 1 if body.retrieval_only else 0,
                "provider": body.provider,
                "model": body.model,
                "score": body.score,
                "ip_hash": ip_hash,
            },
        )
        feedback_id = cursor.lastrowid

    return {"ok": True, "message": "Feedback recorded", "feedback_id": feedback_id}

@admin_router.get("/feedback/stats", dependencies=[ADMIN_ROLE_DEP, ADMIN_SCOPE_DEP])
def admin_feedback_stats(
    mode: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Get feedback statistics (admin only).

    Query params:
    - mode: filter by 'public' or 'admin'
    - since: ISO datetime string (e.g. '2026-01-01')
    - limit: max rows to return for recent feedback list
    """
    from ..data.repositories import get_engine
    from sqlalchemy import text as sa_text

    engine = get_engine()
    with engine.connect() as conn:
        # Ensure table exists
        conn.execute(
            sa_text("""
                CREATE TABLE IF NOT EXISTS assistant_chat_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    response TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    retrieval_only INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    ip_hash TEXT
                )
            """)
        )

        # Build WHERE clause
        where_clauses = []
        params: dict[str, Any] = {"limit": limit}
        if mode:
            where_clauses.append("mode = :mode")
            params["mode"] = mode
        if since:
            where_clauses.append("created_at >= :since")
            params["since"] = since
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Overall stats
        stats = conn.execute(
            sa_text(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) as negative,
                    SUM(CASE WHEN score = 0 THEN 1 ELSE 0 END) as neutral,
                    COUNT(DISTINCT ip_hash) as unique_ips
                FROM assistant_chat_feedback
                {where_sql}
            """),
            params,
        ).fetchone()

        # Recent feedback
        recent = conn.execute(
            sa_text(f"""
                SELECT id, message, response, mode, retrieval_only, provider, model, score, created_at, ip_hash
                FROM assistant_chat_feedback
                {where_sql}
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            params,
        ).fetchall()

        # By mode breakdown
        by_mode = conn.execute(
            sa_text("""
                SELECT mode, COUNT(*) as count,
                    SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) as negative
                FROM assistant_chat_feedback
                GROUP BY mode
            """)
        ).fetchall()

        # By provider/model breakdown
        by_provider = conn.execute(
            sa_text("""
                SELECT provider, model, COUNT(*) as count,
                    SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) as negative
                FROM assistant_chat_feedback
                WHERE provider != '' OR model != ''
                GROUP BY provider, model
                ORDER BY count DESC
            """)
        ).fetchall()

        # Daily trend (last 30 days)
        daily = conn.execute(
            sa_text("""
                SELECT date(created_at) as day, COUNT(*) as count,
                    SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) as negative
                FROM assistant_chat_feedback
                WHERE created_at >= date('now', '-30 days')
                GROUP BY date(created_at)
                ORDER BY day DESC
            """)
        ).fetchall()

    return {
        "summary": {
            "total": stats.total or 0,
            "positive": stats.positive or 0,
            "negative": stats.negative or 0,
            "neutral": stats.neutral or 0,
            "unique_ips": stats.unique_ips or 0,
            "positive_rate": round((stats.positive or 0) / max(stats.total or 1, 1) * 100, 1),
        },
        "by_mode": [
            {"mode": r.mode, "count": r.count, "positive": r.positive, "negative": r.negative}
            for r in by_mode
        ],
        "by_provider": [
            {"provider": r.provider, "model": r.model, "count": r.count, "positive": r.positive, "negative": r.negative}
            for r in by_provider
        ],
        "daily_trend": [
            {"day": r.day, "count": r.count, "positive": r.positive, "negative": r.negative}
            for r in daily
        ],
        "recent": [
            {
                "id": r.id,
                "message": r.message[:100] + ("..." if len(r.message) > 100 else ""),
                "response": r.response[:100] + ("..." if len(r.response) > 100 else ""),
                "mode": r.mode,
                "retrieval_only": bool(r.retrieval_only),
                "provider": r.provider,
                "model": r.model,
                "score": r.score,
                "created_at": r.created_at,
            }
            for r in recent
        ],
    }


