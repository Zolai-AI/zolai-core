"""HTTP surface for the DB-driven multi-agent learning system.

Two groups of routes:

* ``/api/v1/learning/agents`` — admin-only management of the agent registry.
  Agents, their skills and their tuning live in SQLite, so these endpoints are
  the only thing needed to retune a live agent without a redeploy.
* ``/api/v1/learning/run`` — invoke the learning orchestrator (translate,
  validate, learn patterns, build vocabulary).

Everything here is synchronous-blocking work against SQLite, so the async
endpoints delegate to a threadpool rather than blocking the event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .rbac import ROLE_ANONYMOUS, require_role, role_for

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/v1/learning", tags=["learning-agents"])

# Management mutates the registry — admin only, never public.
AdminAuth = Depends(require_role("admin", strict=True))

_BLOCKING = {"translate", "validate_grammar", "correct_grammar",
             "learn_bible_patterns", "build_vocabulary", "extract_collocations"}


def _registry(engine=None):
    """Build a registry against the live engine, creating schema on first use."""
    from ..learning.agents.agent_registry import AgentRegistry

    reg = AgentRegistry(engine=engine)
    reg.ensure_schema()
    reg.seed_defaults()
    return reg


def _orchestrator(engine=None):
    from ..data.repositories import get_engine
    from ..learning.agents.learning_orchestrator import LearningOrchestrator

    return LearningOrchestrator(engine or get_engine())


def _run(fn, *args, **kwargs):
    """Call a blocking registry/management function.

    These endpoints are sync ``def``, so Starlette already dispatches them to
    its threadpool — ``asyncio.to_thread`` here would just return an un-awaited
    coroutine.
    """
    return fn(*args, **kwargs)


# ── Schemas ────────────────────────────────────────────────────────────────


class AgentCreateIn(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=64)
    skills: list[str] = Field(default_factory=list)
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    learning_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    bible_priority: bool | None = None


class AgentUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    skills: list[str] | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    learning_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    bible_priority: bool | None = None


class SkillCreateIn(BaseModel):
    skill_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", max_length=64)


class RunIn(BaseModel):
    task: str = Field(..., description="One of the orchestrator task names")
    source_text: str = Field(default="", max_length=8000)
    target_lang: str = Field(default="en", max_length=8)
    source_lang: str = Field(default="zo", max_length=8)
    pattern_type: str = Field(default="sov", max_length=32)
    limit: int = Field(default=10, ge=1, le=5000)
    min_frequency: int = Field(default=1, ge=1)


# ── Management ─────────────────────────────────────────────────────────────


@admin_router.get("/overview")
def overview() -> dict[str, Any]:
    """Summary of the registry: what agents/skills exist and what is enabled."""
    from ..learning.agents import agent_management

    return _run(agent_management.overview)


@admin_router.get("/agents")
def list_agents() -> dict[str, Any]:
    from ..learning.agents import agent_management

    return {"agents": _run(agent_management.list_agents)}


@admin_router.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict[str, Any]:
    from ..learning.agents import agent_management

    record = _run(agent_management.get_agent, agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_agent",
                                                       "agent_id": agent_id})
    return record


@admin_router.post("/agents", status_code=201)
def create_agent(payload: AgentCreateIn, _auth: dict[str, Any] = AdminAuth) -> dict[str, Any]:
    from ..learning.agents import agent_management

    tuning = {k: v for k, v in payload.model_dump().items()
              if k in {"confidence_threshold", "learning_rate", "bible_priority"}
              and v is not None}
    try:
        return _run(
            agent_management.create_agent,
            agent_id=payload.agent_id,
            name=payload.name,
            role=payload.role,
            skills=payload.skills,
            **tuning,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail={"error": "agent_conflict", "message": str(exc)}) from exc


@admin_router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, payload: AgentUpdateIn,
                 _auth: dict[str, Any] = AdminAuth) -> dict[str, Any]:
    from ..learning.agents import agent_management

    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return _run(agent_management.update_agent, agent_id, **changes)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail={"error": "unknown_agent", "agent_id": agent_id}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_tuning", "message": str(exc)}) from exc


@admin_router.post("/agents/{agent_id}/enable")
def enable_agent(agent_id: str, _auth: dict[str, Any] = AdminAuth) -> dict[str, Any]:
    from ..learning.agents import agent_management

    try:
        return _run(agent_management.enable_agent, agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail={"error": "unknown_agent", "agent_id": agent_id}) from exc


@admin_router.post("/agents/{agent_id}/disable")
def disable_agent(agent_id: str, _auth: dict[str, Any] = AdminAuth) -> dict[str, Any]:
    from ..learning.agents import agent_management

    try:
        return _run(agent_management.disable_agent, agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail={"error": "unknown_agent", "agent_id": agent_id}) from exc


@admin_router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str, _auth: dict[str, Any] = AdminAuth) -> None:
    from ..learning.agents import agent_management

    try:
        _run(agent_management.delete_agent, agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail={"error": "unknown_agent", "agent_id": agent_id}) from exc


@admin_router.get("/skills")
def list_skills() -> dict[str, Any]:
    from ..learning.agents import agent_management

    return {"skills": _run(agent_management.list_skills)}


@admin_router.post("/skills", status_code=201)
def create_skill(payload: SkillCreateIn, _auth: dict[str, Any] = AdminAuth) -> dict[str, Any]:
    from ..learning.agents import agent_management

    return _run(
        agent_management.create_skill,
        skill_id=payload.skill_id,
        name=payload.name,
        description=payload.description,
        category=payload.category,
    )


@admin_router.get("/skills/{skill_id}/agents")
def agents_with_skill(skill_id: str) -> dict[str, Any]:
    from ..learning.agents import agent_management

    return {"agents": _run(agent_management.list_agents_by_skill, skill_id)}


# ── Orchestrator ───────────────────────────────────────────────────────────


#: Anonymous callers get a much smaller work budget than authenticated ones.
PUBLIC_MAX_LIMIT = 50
AUTHED_MAX_LIMIT = 5000


def _effective_limit(request: Request, requested: int) -> int:
    """Clamp ``requested`` by caller role.

    ``build_vocabulary`` and ``learn_bible_patterns`` scale with the limit and
    are SQLite/CPU-bound, so an anonymous caller must not be able to ask for
    the same work an admin can.
    """
    record = getattr(request.state, "api_key", None)
    role = role_for(record)
    ceiling = AUTHED_MAX_LIMIT if role != ROLE_ANONYMOUS else PUBLIC_MAX_LIMIT
    return min(requested, ceiling)


@admin_router.post("/run")
async def run_task(payload: RunIn, request: Request) -> dict[str, Any]:
    """Run one orchestrator task and return its result.

    Public: learning is an ask surface like /rag. Anonymous callers are rate
    limited by the chat bucket and have their ``limit`` clamped.
    """
    task = payload.task
    limit = _effective_limit(request, payload.limit)
    orch = _orchestrator()

    if task not in _BLOCKING:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_task", "task": task,
            "supported": sorted(_BLOCKING),
        })

    try:
        if task == "translate":
            result = await orch.translate(payload.source_text,
                                          payload.target_lang, payload.source_lang)
        elif task == "validate_grammar":
            result = await orch.validate_grammar(payload.source_text)
        elif task == "correct_grammar":
            result = await orch.correct_grammar(payload.source_text)
        elif task == "learn_bible_patterns":
            result = await orch.learn_bible_patterns(payload.pattern_type, limit)
        elif task == "build_vocabulary":
            result = await orch.build_vocabulary(limit, payload.min_frequency)
        else:
            result = await orch.extract_collocations(limit)
    except TimeoutError as exc:
        raise HTTPException(status_code=504,
                            detail={"error": "task_timeout", "task": task}) from exc
    except Exception as exc:  # agents raise domain errors; surface them cleanly
        logger.exception("learning task %s failed", task)
        raise HTTPException(status_code=500, detail={
            "error": "task_failed", "task": task,
            "message": f"{type(exc).__name__}: {exc}",
        }) from exc

    return {
        "task": task,
        "result": result,
        "limit_applied": limit,
        "limit_clamped": limit != payload.limit,
    }
