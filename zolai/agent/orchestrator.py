"""Agent orchestrator (P3 §C) — run phases research → build → review → shipped.

``run_agent_goal`` is the 24th engine target
(``zolai.agent.orchestrator:run_agent_goal``): a rule-mode run must complete
**offline** (zero sockets — retrieval tools are DB-only, ``llm_allowed()`` is
checked before any provider resolution).  ``learn`` is deliberately *not* part
of the run: it fires only from a thumbs-up feedback
(:func:`zolai.agent.learn.learn_from_run`).

Notifications
-------------
Emits system_event notifications on agent run failures.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any

from ..engines import engine_mode, llm_allowed
from ..notifications import get_notification_service
from ..shared.clock import stamp
from . import store
from .loop import AGENT_MAX_TOOL_CALLS_PER_TURN, run_agent_loop
from .synthesis import make_chat_fn, rule_draft
from .tools.errors import classify_failure
from .tools.executor import execute_tool
from .tools.registry import allow_list
from .tools.types import ToolCall

logger = logging.getLogger(__name__)

#: Default tool allow-list for an agent run (admin scope: full set).
DEFAULT_RUN_SCOPE = "admin"

RESEARCH_TOOLS: tuple[str, ...] = ("rag_search", "kb_research")

# ── Phase-specific system prompts ─────────────────────────────────────────────

BUILD_PHASE_PROMPT = (
    "You are the Zolai Build Agent. Your task: synthesize a comprehensive answer "
    "from the gathered evidence.\n\n"
    "Input: The research phase has provided evidence items with sources.\n"
    "Output: A clear, well-structured answer that:\n"
    "1. Directly addresses the goal\n"
    "2. Cites evidence using [source ref] format after each claim\n"
    "3. Uses ZVS 2018 orthography: pasian/gam/tapa/topa/kumpipa/tua\n"
    "4. Uses SOV word order, ergative 'in' for transitive subjects\n"
    "5. Distinguishes Stem I/II verbs when relevant\n"
    "6. Acknowledges gaps where evidence is missing\n"
    "7. Maximum 6 lines. Be concise but complete.\n\n"
    "If evidence is insufficient, say so and provide what you can."
)

REVIEW_PHASE_PROMPT = (
    "You are the Zolai Review Agent. Your task: validate the answer against "
    "ZVS 2018 standards.\n\n"
    "Check for:\n"
    "1. Forbidden forms: pathian→pasian, ram→gam, fapa→tapa, bawipa→topa, "
    "siangpahrang→kumpipa, cu/cun→tua, suah→suahtakna, zalenna→suahtakna, "
    "nunnak→nuntakna\n"
    "2. SOV word order compliance\n"
    "3. Ergative 'in' for transitive subjects\n"
    "4. Stem I/II verb distinction\n"
    "5. Proper negation: 'kei' not 'lo'\n"
    "6. Citation format: [source ref] after claims\n\n"
    "If violations found, apply corrections and re-validate. One revision pass only."
)

SYSTEM_PROMPT = (
    "You are the Zolai Agent Orchestrator — a careful research agent for the "
    "Tedim Zolai (ZVS 2018) language. Your workflow:\n"
    "1. RESEARCH: Use rag_search + kb_research + word_evidence + dictionary_lookup "
    "to gather evidence.\n"
    "2. BUILD: Synthesize answer from evidence. Cite everything with [source ref].\n"
    "3. REVIEW: Validate against ZVS 2018 (orthography, SOV, ergative 'in').\n"
    "4. SHIP: Deliver final answer with full citations.\n\n"
    "Available tools: rag_search, kb_research, word_detail, word_evidence, "
    "word_related, "
    "analyze_text, grammar_check, dictionary_lookup, verse_lookup, knowledge_stats, "
    "review_queue_submit, provider_status, web_search.\n"
    f"Rules: Up to {AGENT_MAX_TOOL_CALLS_PER_TURN} tool calls per turn. Max 3 turns. "
    "Never invent results. If evidence "
    "missing, say so. Keep ZVS 2018: pasian/gam/tapa/topa, SOV, ergative 'in'."
)

def zvs_review(answer: str) -> tuple[str, dict[str, Any]]:
    """Validate + one revise pass (forbidden → preferred), then re-validate.

    Returns ``(revised_answer, review)`` where ``review`` records violations
    before/after — a still-failing review makes the run fail (plan: one
    revise pass, then fail).
    """
    from ..zvs import validate
    from ..zvs.rules_data import DIALECT_FORBIDDEN_TO_PREFERRED, STEM_FORBIDDEN_TO_PREFERRED

    first = validate(answer, source="agent:answer", context="modern")
    if first.is_valid:
        return answer, {"valid": True, "violations_before": 0, "violations_after": 0, "revised": False}

    revised = answer
    preferred = {**DIALECT_FORBIDDEN_TO_PREFERRED, **STEM_FORBIDDEN_TO_PREFERRED}
    for v in first.violations:
        target = v.preferred or preferred.get(v.forbidden)
        if target and v.forbidden:
            revised = revised.replace(v.forbidden, target)
    second = validate(revised, source="agent:answer", context="modern")
    review = {
        "valid": second.is_valid,
        "violations_before": first.count,
        "violations_after": second.count,
        "revised": revised != answer,
        "rules": sorted({v.rule_id for v in first.violations}),
    }
    return revised, review


def _research(goal: str, allow: frozenset[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the retrieval fan-out directly (deterministic, DB-only).

    Detects Bible/Scripture/verse references in the goal and adds source_filter
    to rag_search for better precision.
    """
    calls: list[ToolCall] = []
    results: list[dict[str, Any]] = []

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
    goal_lower = goal.lower()
    needs_bible_filter = any(kw in goal_lower for kw in bible_keywords)

    rag_input = {"query": goal, "limit": 5}
    if needs_bible_filter:
        rag_input["source_filter"] = ["bible_verses"]

    if "rag_search" in allow:
        calls.append(ToolCall(name="rag_search", input=rag_input, turn=1))
    if "kb_research" in allow:
        calls.append(ToolCall(name="kb_research", input={"query": goal, "limit": 5}, turn=1))
    for call in calls:
        entry = execute_tool(call, allow, turn=1).to_dict()
        entry["input"] = call.input
        entry["status"] = "completed" if entry.get("ok") else "failed"
        results.append(entry)
    evidence = [r for r in results if r.get("ok") and r.get("data") is not None]
    return results, evidence


def run_agent_goal(
    goal: str,
    *,
    created_by: str = "system",
    scope: str = DEFAULT_RUN_SCOPE,
    granted: Iterable[str] | None = None,
    extra_allow: Iterable[str] | None = None,
    assistant: str = "admin",
    max_turns: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    user_api_key: str | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Execute one agent run end-to-end and persist the ``agent_runs`` row.

    Args:
        provider: Optional per-request provider override — validated against
            the catalog row (stable ``PROVIDER_*`` code) before any socket
            opens; ``None`` keeps the assistant's default resolution.
        model: Optional per-request model override (must be listed on the
            chosen row).
        user_api_key: Optional per-request API key override (user-provided,
            not stored server-side). If provided, it takes precedence over
            the row's stored key or env fallback.
        granted: The caller's own scope set, forwarded to
            :func:`zolai.agent.tools.registry.allow_list`.  It can only
            **narrow** the tier, so a tool whose ``scope`` this caller lacks
            is dropped from the run's tool set rather than offered and then
            refused.  ``None`` (the default) leaves the tier exactly as it
            was.

    Returns:
        The full run (phases, tool trace, evidence, answer, provider,
        model, turns, latency) — the API returns it as-is.
    """
    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("goal must not be empty")

    started = time.monotonic()
    mode = engine_mode()
    allow = set(allow_list(scope, granted=granted))
    if extra_allow:
        allow |= {str(a) for a in extra_allow}

    run = store.create_run(goal, created_by=created_by, mode=mode)
    run_id = int(run["id"])
    phases: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    error = ""
    status = "running"
    answer = ""
    used_provider = ""
    used_model = ""
    turns = 0
    outcome = ""
    stop_reason = ""

    try:
        # ── research ────────────────────────────────────────────────────
        t0 = time.monotonic()

        trace, evidence_rows = _research(goal, frozenset(allow))
        flat_evidence: list[dict[str, Any]] = []
        for res in evidence_rows:
            data = res.get("data")
            if isinstance(data, list):
                flat_evidence.extend(i for i in data if isinstance(i, dict))
            elif isinstance(data, dict):
                flat_evidence.append(data)
        phases["research"] = {
            "status": "ok" if any(r.get("status") == "completed" for r in trace) else "empty",
            "tools": [r["name"] for r in trace],
            "failed_tools": sum(1 for r in trace if r.get("status") == "failed"),
            "evidence_count": len(flat_evidence),
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
        }

        # ── build ───────────────────────────────────────────────────────
        t0 = time.monotonic()
        if llm_allowed():
            try:
                chat, meta = make_chat_fn(
                    assistant=assistant, provider=provider, model=model, user_api_key=user_api_key
                )
                # Use build-phase prompt with evidence context
                evidence_context = "\n".join(
                    f"[{i}] {e.get('source', 'kb')} {e.get('ref', '')}: {str(e.get('text', e))[:200]}"
                    for i, e in enumerate(flat_evidence[:8], 1)
                ) if flat_evidence else "No evidence found."
                build_prompt = BUILD_PHASE_PROMPT + f"\n\nEvidence:\n{evidence_context}"
                loop_result = run_agent_loop(
                    system_prompt=build_prompt,
                    user_message=f"Goal: {goal}",
                    allow=frozenset(allow),
                    chat=chat,
                    max_turns=max_turns,
                    native=bool(meta.get("native")),
                    timeout_s=timeout_s,
                )
                if loop_result.get("ok") and str(loop_result.get("reply") or "").strip():
                    answer = str(loop_result["reply"])
                    turns = int(loop_result.get("turns") or 0)
                    used_provider = str(meta.get("catalog_id") or "")
                    used_model = str(meta.get("model") or "")
                    outcome = "generated"
                    stop_reason = str(loop_result.get("stop_reason") or "")
                    trace.extend(loop_result.get("tool_calls") or [])
                else:
                    answer = rule_draft(goal, flat_evidence)
                    outcome = "rule_draft_fallback"
                    stop_reason = str(loop_result.get("stop_reason") or "")
                    error = str(loop_result.get("error") or "")
            except Exception as exc:  # ProviderError or transport failure → degrade
                logger.warning("agent build degraded to rule draft: %s", exc)
                answer = rule_draft(goal, flat_evidence)
                outcome = "rule_draft_fallback"
                stop_reason = "provider_unavailable"
                error = f"provider_unavailable: {exc}"
        else:
            answer = rule_draft(goal, flat_evidence)
            turns = 0
            outcome = "rule_draft"
            stop_reason = "rule_draft"
        phases["build"] = {
            "status": "ok",
            "outcome": outcome,
            # The loop's own terminal state rides along: "turn budget spent"
            # and "provider timed out" both fall back to rule drafts and must
            # stay distinguishable after the fact.
            "stop_reason": stop_reason,
            "turns": turns,
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
        }

        # ── review (ZVS: one revise pass, then fail) ────────────────────
        t0 = time.monotonic()
        answer, review = zvs_review(answer)
        phases["review"] = {
            "status": "ok" if review["valid"] else "failed",
            **review,
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
        }
        if not review["valid"]:
            status = "failed"
            error = error or "zvs_review_failed"

        # ── shipped ─────────────────────────────────────────────────────
        if status == "running":
            status = "succeeded"
        phases["shipped"] = {"status": "ok" if status == "succeeded" else "skipped"}

        finished = time.monotonic()
        updated = store.update_run(
            run_id,
            status=status,
            phases=phases,
            tool_calls=trace,
            evidence=flat_evidence[:20],
            answer=answer,
            provider=used_provider,
            model=used_model,
            turns=turns,
            latency_ms=round((finished - started) * 1000, 2),
            outcome=outcome,
            error=error,
            finished_at=stamp(),
        )
        return updated or run
    except Exception as exc:
        logger.exception("agent run %s failed", run_id)
        _notify_failure(run_id, exc)
        phases["shipped"] = {"status": "failed"}
        updated = store.update_run(
            run_id,
            status="failed",
            phases=phases,
            tool_calls=trace,
            answer=answer,
            error=classify_failure(exc).message,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            finished_at=stamp(),
        )
        return updated or run


def _notify_failure(run_id: int, exc: BaseException) -> None:
    """Best-effort system event for a failed run.

    ``run_agent_goal`` is synchronous and may be called off the event loop
    (CLI, threadpool), where ``create_task`` raises ``RuntimeError`` and the
    notification would vanish silently — so the loop is probed first and a
    miss is logged at debug, never raised.
    """
    import asyncio

    service = get_notification_service()
    context = {
        "timestamp": stamp(),
        "event_type": "agent_run_failed",
        "details": f"Agent run {run_id} failed: {type(exc).__name__}: {exc}",
        "app_name": "Zolai AI",
        "environment": os.environ.get("ZOLAI_ENV", "production"),
    }

    async def _emit() -> None:
        try:
            await service.send_admin_alert("system_event", context, dedup=False)
        except Exception:  # notification loss must never mask the run failure
            logger.debug("agent failure notification dropped", exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_emit())
    except RuntimeError:
        logger.debug("agent failure notification skipped: no running event loop")
