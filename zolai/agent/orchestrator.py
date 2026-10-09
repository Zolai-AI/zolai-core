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
import time
from collections.abc import Iterable
from typing import Any

from ..engines import engine_mode, llm_allowed
from ..notifications import get_notification_service
from . import store
from .loop import run_agent_loop
from .synthesis import make_chat_fn, rule_draft
from .tools.executor import execute_tool
from .tools.registry import allow_list
from .tools.types import ToolCall

logger = logging.getLogger(__name__)

#: Default tool allow-list for an agent run (admin scope: full set).
DEFAULT_RUN_SCOPE = "admin"

RESEARCH_TOOLS: tuple[str, ...] = ("rag_search", "kb_research")

SYSTEM_PROMPT = (
    "You are the Zolai agent — a careful assistant for the Tedim Zolai (ZVS 2018) "
    "language. Answer from the evidence tools when live data is needed; keep ZVS 2018 "
    "orthography (pasian/gam/tapa/topa), SOV word order and the ergative 'in'. "
    "Never invent tool results."
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
    """Run the retrieval fan-out directly (deterministic, DB-only)."""
    calls: list[ToolCall] = []
    results: list[dict[str, Any]] = []
    if "rag_search" in allow:
        calls.append(ToolCall(name="rag_search", input={"query": goal, "limit": 5}, turn=1))
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
    extra_allow: Iterable[str] | None = None,
    assistant: str = "admin",
    max_turns: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Execute one agent run end-to-end and persist the ``agent_runs`` row.

    Args:
        provider: Optional per-request provider override — validated against
            the catalog row (stable ``PROVIDER_*`` code) before any socket
            opens; ``None`` keeps the assistant's default resolution.
        model: Optional per-request model override (must be listed on the
            chosen row).

    Returns:
        The full run (phases, tool trace, evidence, answer, provider,
        model, turns, latency) — the API returns it as-is.
    """
    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("goal must not be empty")

    started = time.monotonic()
    mode = engine_mode()
    allow = set(allow_list(scope))
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
            "status": "ok",
            "tools": [r["name"] for r in trace],
            "evidence_count": len(flat_evidence),
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
        }

        # ── build ───────────────────────────────────────────────────────
        t0 = time.monotonic()
        if llm_allowed():
            try:
                chat, meta = make_chat_fn(
                    assistant=assistant, provider=provider, model=model
                )
                loop_result = run_agent_loop(
                    system_prompt=SYSTEM_PROMPT,
                    user_message=f"Goal: {goal}",
                    allow=frozenset(allow),
                    chat=chat,
                    max_turns=max_turns,
                    native=bool(meta.get("native")),
                )
                if loop_result.get("ok") and str(loop_result.get("reply") or "").strip():
                    answer = str(loop_result["reply"])
                    turns = int(loop_result.get("turns") or 0)
                    used_provider = str(meta.get("catalog_id") or "")
                    used_model = str(meta.get("model") or "")
                    outcome = "generated"
                    trace.extend(loop_result.get("tool_calls") or [])
                else:
                    answer = rule_draft(goal, flat_evidence)
                    outcome = "rule_draft_fallback"
                    error = str(loop_result.get("error") or "")
            except Exception as exc:  # ProviderError or transport failure → degrade
                logger.warning("agent build degraded to rule draft: %s", exc)
                answer = rule_draft(goal, flat_evidence)
                outcome = "rule_draft_fallback"
                error = f"provider_unavailable: {exc}"
        else:
            answer = rule_draft(goal, flat_evidence)
            turns = 0
            outcome = "rule_draft"
        phases["build"] = {
            "status": "ok",
            "outcome": outcome,
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
            finished_at=_utc_now(),
        )
        return updated or run
    except Exception as exc:
        logger.exception("agent run %s failed", run_id)
        # Emit system event notification for agent run failure
        try:
            service = get_notification_service()
            import asyncio

            exc_type = type(exc).__name__
            exc_msg = str(exc)

            async def _emit() -> None:
                context = {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "event_type": "agent_run_failed",
                    "details": f"Agent run {run_id} failed: {exc_type}: {exc_msg}",
                    "app_name": "Zolai AI",
                    "environment": "production",
                }
                await service.send_admin_alert("system_event", context, dedup=False)

            asyncio.create_task(_emit())
        except Exception:
            pass
        phases["shipped"] = {"status": "failed"}
        updated = store.update_run(
            run_id,
            status="failed",
            phases=phases,
            tool_calls=trace,
            answer=answer,
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            finished_at=_utc_now(),
        )
        return updated or run


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
