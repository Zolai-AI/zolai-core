"""Management surface for the database-driven agent registry.

Full CRUD over agents and skills — the "manage all" entry point. Every
function is a thin, directly callable wrapper around :class:`AgentRegistry`,
so it can be used from a CLI, a notebook, a script or (later) an API router
without changing the registry.

    >>> from zolai.learning.agents.agent_management import list_agents, update_agent
    >>> update_agent("grammar_expert_001", confidence_threshold=0.95)
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Optional

from sqlalchemy.engine import Engine

from .agent_registry import (
    AGENT_DEFAULTS,
    AgentRecord,
    AgentRegistry,
    SkillRecord,
    get_agent_registry,
)
from .base_agent import AgentConfig, AgentRole, AgentSkill

logger = logging.getLogger(__name__)

# role -> (module, class) so a row created at runtime can be instantiated.
AGENT_CLASS_BY_ROLE: dict[str, tuple[str, str]] = {
    AgentRole.BIBLE_LEARNER.value: ("bible_learner_agent", "BibleLearnerAgent"),
    AgentRole.TRANSLATOR.value: ("translation_agent", "TranslationAgent"),
    AgentRole.GRAMMAR_EXPERT.value: ("grammar_agent", "GrammarAgent"),
    AgentRole.VOCABULARY_BUILDER.value: ("vocabulary_agent", "VocabularyAgent"),
}


def _registry(engine: Optional[Engine] = None) -> AgentRegistry:
    return get_agent_registry(engine)


# ── agents ──────────────────────────────────────────────────────────────


def list_agents(
    engine: Optional[Engine] = None,
    *,
    include_disabled: bool = True,
    role: AgentRole | str | None = None,
    skill: AgentSkill | str | None = None,
) -> list[dict[str, Any]]:
    """List registered agents, optionally filtered by role and/or skill."""
    registry = _registry(engine)
    if role is not None:
        records = registry.list_by_role(role, include_disabled=include_disabled)
    elif skill is not None:
        records = registry.list_by_skill(skill, include_disabled=include_disabled)
    else:
        records = registry.get_all(include_disabled=include_disabled)
    return [record.to_dict() for record in records]


def get_agent(agent_id: str, engine: Optional[Engine] = None) -> Optional[dict[str, Any]]:
    """Read one agent row."""
    record = _registry(engine).get(agent_id)
    return record.to_dict() if record is not None else None


def create_agent(
    agent_id: str,
    name: str,
    role: AgentRole | str,
    *,
    skills: Iterable[AgentSkill | str] = (),
    engine: Optional[Engine] = None,
    **tuning: Any,
) -> dict[str, Any]:
    """Create an agent at runtime."""
    return _registry(engine).create_agent(
        agent_id=agent_id, name=name, role=role, skills=skills, **tuning
    ).to_dict()


def update_agent(agent_id: str, *, engine: Optional[Engine] = None, **changes: Any) -> dict[str, Any]:
    """Update name/role/skills/tuning of an agent."""
    return _registry(engine).update_agent(agent_id, **changes).to_dict()


def enable_agent(agent_id: str, engine: Optional[Engine] = None) -> dict[str, Any]:
    """Re-enable a soft-deleted agent."""
    return _registry(engine).enable_agent(agent_id).to_dict()


def disable_agent(agent_id: str, engine: Optional[Engine] = None) -> dict[str, Any]:
    """Soft delete an agent (row is kept, agent stops being used)."""
    return _registry(engine).disable_agent(agent_id).to_dict()


def delete_agent(agent_id: str, engine: Optional[Engine] = None) -> None:
    """Hard delete an agent row."""
    _registry(engine).delete_agent(agent_id)


def set_agent_skills(
    agent_id: str, skills: Iterable[AgentSkill | str], engine: Optional[Engine] = None
) -> dict[str, Any]:
    """Replace the skill list of an agent."""
    return _registry(engine).update_agent(agent_id, skills=list(skills)).to_dict()


def assign_skill(
    agent_id: str, skill_id: AgentSkill | str, engine: Optional[Engine] = None
) -> dict[str, Any]:
    """Give an agent an extra skill."""
    return _registry(engine).assign_skill(agent_id, skill_id).to_dict()


def unassign_skill(
    agent_id: str, skill_id: AgentSkill | str, engine: Optional[Engine] = None
) -> dict[str, Any]:
    """Take a skill away from an agent."""
    return _registry(engine).remove_skill(agent_id, skill_id).to_dict()


def agent_config(agent_id: str, engine: Optional[Engine] = None) -> AgentConfig:
    """The ``AgentConfig`` an agent id currently resolves to."""
    return _registry(engine).resolve_config(agent_id)


# ── skills ──────────────────────────────────────────────────────────────


def list_skills(
    engine: Optional[Engine] = None,
    *,
    category: Optional[str] = None,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """List the skill catalogue."""
    return [s.to_dict() for s in _registry(engine).list_skills(category, include_disabled)]


def create_skill(
    skill_id: AgentSkill | str,
    *,
    name: Optional[str] = None,
    description: str = "",
    category: str = "",
    config_schema: Optional[dict[str, Any]] = None,
    engine: Optional[Engine] = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Register a skill in the catalogue (idempotent)."""
    return _registry(engine).create_skill(
        skill_id=skill_id,
        name=name,
        description=description,
        category=category,
        config_schema=config_schema,
        enabled=enabled,
    ).to_dict()


def set_skill_enabled(
    skill_id: AgentSkill | str, enabled: bool, engine: Optional[Engine] = None
) -> dict[str, Any]:
    """Enable/disable a skill in the catalogue."""
    return _registry(engine).set_skill_enabled(skill_id, enabled).to_dict()


def list_agents_by_skill(
    skill_id: AgentSkill | str,
    engine: Optional[Engine] = None,
    *,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """Which agents hold a given skill."""
    return [r.to_dict() for r in _registry(engine).list_by_skill(skill_id, include_disabled)]


# ── runtime instantiation ───────────────────────────────────────────────


def _load_agent_class(role: AgentRole):
    module_name, class_name = AGENT_CLASS_BY_ROLE[role.value]
    module = __import__(f"{__package__}.{module_name}", fromlist=[class_name])
    return getattr(module, class_name)


def instantiate_agent(
    agent_id: str, engine: Optional[Engine] = None, registry: Optional[AgentRegistry] = None
):
    """Build a live agent instance from its database row.

    Works for rows created at runtime as well as the seeded ones: the row's
    role selects the agent class and the row's config is bound onto it.
    """
    registry = registry or _registry(engine)
    record = registry.get(agent_id)
    if record is None:
        raise KeyError(f"unknown agent: {agent_id!r}")
    if record.role.value not in AGENT_CLASS_BY_ROLE:
        raise ValueError(
            f"no agent class registered for role {record.role.value!r} — "
            "add it to AGENT_CLASS_BY_ROLE before instantiating"
        )
    agent_cls = _load_agent_class(record.role)
    agent = agent_cls(engine=engine)
    agent._bind_config(record.to_agent_config())
    return agent


# ── overview ────────────────────────────────────────────────────────────


def list_bindings(assistant_id: str = "", engine: Optional[Engine] = None):
    """Agent<->assistant bindings (optionally filtered to one assistant)."""
    return _registry(engine).list_bindings(assistant_id or None)


def bind_agent(assistant_id: str, agent_id: str, role: str = "primary",
               engine: Optional[Engine] = None) -> dict[str, Any]:
    """Attach an agent to a named assistant surface."""
    return _registry(engine).bind_assistant_agent(assistant_id, agent_id, role)


def unbind_agent(assistant_id: str, agent_id: str, engine: Optional[Engine] = None) -> None:
    """Detach an agent from an assistant."""
    _registry(engine).unbind_assistant_agent(assistant_id, agent_id)


def list_assistants(engine: Optional[Engine] = None):
    """Distinct assistant ids with at least one bound agent."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in _registry(engine).list_bindings():
        entry = grouped.setdefault(
            row["assistant_id"],
            {"assistant_id": row["assistant_id"], "agents": [], "enabled_agents": 0},
        )
        entry["agents"].append(
            {"agent_id": row["agent_id"], "role": row["role"], "enabled": row["enabled"]}
        )
        if row["enabled"] and row.get("agent_enabled"):
            entry["enabled_agents"] += 1
    return sorted(grouped.values(), key=lambda e: e["assistant_id"])


def overview(engine: Optional[Engine] = None) -> dict[str, Any]:
    """Summary of the whole registry — the "what is configured right now" view."""
    registry = _registry(engine)
    agents = registry.get_all()
    active = [a for a in agents if a.enabled]
    roles: dict[str, int] = {}
    for record in agents:
        roles[record.role.value] = roles.get(record.role.value, 0) + 1
    return {
        "agents_total": len(agents),
        "agents_enabled": len(active),
        "agents_disabled": len(agents) - len(active),
        "skills_total": len(registry.list_skills()),
        "roles": roles,
        "seeded_defaults": sorted(AGENT_DEFAULTS),
        "agents": [a.to_dict() for a in agents],
    }


def registry_record(agent_id: str, engine: Optional[Engine] = None) -> Optional[AgentRecord]:
    """The raw :class:`AgentRecord` for an agent (None when unknown)."""
    return _registry(engine).get(agent_id)


def skill_record(skill_id: AgentSkill | str, engine: Optional[Engine] = None) -> Optional[SkillRecord]:
    """The raw :class:`SkillRecord` for a skill (None when unknown)."""
    return _registry(engine).get_skill(skill_id)
