"""Agent API (P3 §C) — ``/api/v1/agent`` runs, feedback, health.

Mounted **before** the catch-all in :mod:`zolai.api.server` (ce04c72
contract).  Every route is ``strict`` scope-gated: an anonymous caller is 401
in both ``warn`` and ``enforce``; a present key without the scope is 403.

- ``POST /runs`` → ``agent:run`` strict + in-process 5 runs/min bucket (429)
- ``GET  /runs``, ``GET /runs/{id}`` → ``agent:read`` strict (404 unknown)
- ``POST /runs/{id}/feedback`` → ``agent:run`` strict; ``score >= 1`` fires
  the **learn** phase (``hypotheses`` + review-queue candidates only)
- ``GET  /health`` → ``agent:read`` strict
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

RUN_DEP = Depends(auth.require_scope("agent:run", strict=True))
READ_DEP = Depends(auth.require_scope("agent:read", strict=True))

#: In-process run budget: 5 runs / minute / key (plan §C).
RUN_LIMIT = 5
RUN_WINDOW_S = 60.0
_RUN_BUCKET: dict[str, list[float]] = {}


def _reset_run_bucket() -> None:
    """Clear the in-process run rate bucket (tests + ops)."""
    _RUN_BUCKET.clear()


def _client_key(request: Request) -> str:
    record: dict[str, Any] | None = getattr(request.state, "api_key", None)
    if record and record.get("key_prefix"):
        return f"key:{record['key_prefix']}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


def _check_run_rate(request: Request) -> None:
    bucket_key = _client_key(request)
    now = time.monotonic()
    hits = [t for t in _RUN_BUCKET.get(bucket_key, []) if now - t < RUN_WINDOW_S]
    if len(hits) >= RUN_LIMIT:
        retry_after = max(1, int(RUN_WINDOW_S - (now - hits[0])))
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "scope": "agent_run", "limit": RUN_LIMIT},
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _RUN_BUCKET[bucket_key] = hits


class RunIn(BaseModel):
    """POST /runs body.

    ``provider`` / ``model`` are an optional per-request override (Phase B §9),
    validated against an enabled catalog row before the run starts (a DB read
    — a real 404/400 even in ``rule`` mode) and echoed back as
    ``requested_provider`` / ``requested_model``.

    ``api_key`` is an optional per-request API key override (user-provided,
    not stored server-side). If provided, it takes precedence over the
    row's stored key or env fallback.
    """

    goal: str = Field(min_length=1, max_length=4000)
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None


class FeedbackIn(BaseModel):
    """POST /runs/{id}/feedback body (thumbs: -1 | 0 | 1)."""

    score: float = Field(ge=-1, le=1)


def _created_by(request: Request) -> str:
    record: dict[str, Any] | None = getattr(request.state, "api_key", None)
    if record and record.get("key_prefix"):
        return str(record["key_prefix"])
    return "anonymous"


@router.post("/runs", dependencies=[RUN_DEP])
def create_run(body: RunIn, request: Request) -> dict[str, Any]:
    """Run the agent synchronously (≤60s) and return the full run + trace.

    An optional ``provider``/``model`` override is validated before the run
    (unknown/disabled provider → 404, unknown model → 400) and echoed in the
    response as ``requested_provider`` / ``requested_model``.

    ``api_key`` is an optional per-request API key override (user-provided,
    not stored server-side). If provided, it takes precedence over the
    row's stored key or env fallback.
    """
    from ..agent.orchestrator import run_agent_goal
    from .providers_router import validate_selection

    _check_run_rate(request)
    validate_selection(body.provider, body.model, assistant="admin")
    try:
        run = run_agent_goal(
            body.goal,
            created_by=_created_by(request),
            provider=body.provider,
            model=body.model,
            user_api_key=body.api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_goal", "detail": str(exc)}) from exc
    run["requested_provider"] = body.provider or ""
    run["requested_model"] = body.model or ""
    run["requested_api_key"] = body.api_key is not None
    return run


@router.get("/runs", dependencies=[READ_DEP])
def list_runs(limit: int = 20) -> dict[str, Any]:
    """Most recent runs first."""
    from ..agent import store

    runs = store.list_runs(limit=limit)
    return {"items": runs, "count": len(runs)}


@router.get("/runs/{run_id}", dependencies=[READ_DEP])
def get_run(run_id: int) -> dict[str, Any]:
    """One run — unknown id is a real 404 (ce04c72 contract)."""
    from ..agent import store

    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "run_id": run_id})
    return run


@router.post("/runs/{run_id}/feedback", dependencies=[RUN_DEP])
def send_feedback(run_id: int, body: FeedbackIn, request: Request) -> dict[str, Any]:
    """Record a thumbs score; ``score >= 1`` fires the learn phase."""
    from ..agent import learn, store

    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "run_id": run_id})
    if run.get("status") not in {"succeeded", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={"error": "run_not_finished", "run_id": run_id, "status": run.get("status")},
        )
    updated = store.set_feedback(run_id, body.score)
    learning: dict[str, Any] = {"learned": False, "reason": "score_below_threshold"}
    if body.score >= 1:
        learning = learn.learn_from_run(run_id, body.score)
    return {
        "run_id": run_id,
        "feedback_score": body.score,
        "run": updated,
        "learn": learning,
    }


@router.get("/health", dependencies=[READ_DEP])
def agent_health() -> dict[str, Any]:
    """Agent surface health (mode, budgets, table availability)."""
    from ..agent import store
    from ..engines import engine_mode

    try:
        store.ensure_agent_runs_table()
        ready = True
    except Exception:
        ready = False
    from ..agent.loop import AGENT_MAX_STEPS, AGENT_MAX_TURNS, AGENT_TIMEOUT_S

    return {
        "status": "ok" if ready else "degraded",
        "mode": engine_mode(),
        "max_turns": AGENT_MAX_TURNS,
        "max_steps": AGENT_MAX_STEPS,
        "timeout_s": AGENT_TIMEOUT_S,
        "store_ready": ready,
    }
