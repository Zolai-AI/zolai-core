"""Database-driven agent + skill registry.

Agent and skill definitions live in SQLite (``agents``, ``agent_skills``,
``agent_tasks``, ``agent_discussions``, ``discussion_messages``) instead of
inline ``AgentConfig(...)`` literals inside every agent class, so operators can
read, tune, enable/disable and create agents and skills at runtime.

Additive by design (same convention as ``zolai.learning.feedback_store``):

* tables are created lazily with ``CREATE TABLE IF NOT EXISTS``;
* seeding uses ``INSERT OR IGNORE`` so admin edits are never overwritten;
* every read falls back to the built-in seed values when the row is missing or
  the database is unreachable, so importing or instantiating an agent never
  fails because of the registry.

``AgentRole`` / ``AgentSkill`` stay the vocabulary; only the *assignment and
tuning* of agents and skills live in the database.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ...data.repositories import get_engine
from .base_agent import AgentConfig, AgentRole, AgentSkill

logger = logging.getLogger(__name__)

# Config fields that may be tuned per agent from the database.
TUNABLE_FIELDS: tuple[str, ...] = (
    "team_id",
    "learning_rate",
    "confidence_threshold",
    "max_concurrent_tasks",
    "bible_priority",
)

# Defaults mirroring AgentConfig, used when a DB row omits a tunable.
DEFAULT_TUNING: dict[str, Any] = {
    "team_id": None,
    "learning_rate": 0.1,
    "confidence_threshold": 0.7,
    "max_concurrent_tasks": 5,
    "bible_priority": True,
}

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        skills TEXT NOT NULL DEFAULT '[]',
        config TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_skills (
        skill_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        config_schema TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_tasks (
        task_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        team_id TEXT,
        task_type TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        payload TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_discussions (
        discussion_id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        team_id TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        consensus TEXT,
        participants TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discussion_messages (
        message_id TEXT PRIMARY KEY,
        discussion_id TEXT NOT NULL,
        from_agent TEXT NOT NULL,
        to_agent TEXT,
        message_type TEXT NOT NULL DEFAULT 'discussion',
        content TEXT NOT NULL DEFAULT '',
        metadata TEXT NOT NULL DEFAULT '{}',
        reply_to TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role)",
    "CREATE INDEX IF NOT EXISTS idx_agents_enabled ON agents(enabled)",
    "CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent ON agent_tasks(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_tasks_created ON agent_tasks(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_discussion_messages_discussion ON discussion_messages(discussion_id)",
)

# Skill catalogue seeded from the AgentSkill enum (id -> metadata).
SKILL_CATALOGUE: dict[str, dict[str, str]] = {
    AgentSkill.BIBLE_PATTERN_EXTRACTION.value: {
        "category": "bible",
        "description": "Extract word-order and grammar patterns from the Bible parallel corpus",
    },
    AgentSkill.BIBLE_TRANSLATION_GROUNDING.value: {
        "category": "bible",
        "description": "Ground translations in attested Bible wording",
    },
    AgentSkill.POS_TAGGING.value: {
        "category": "grammar",
        "description": "Assign part-of-speech tags to Zolai tokens",
    },
    AgentSkill.MORPHOLOGY_ANALYSIS.value: {
        "category": "grammar",
        "description": "Analyse Zolai morphology and inflections",
    },
    AgentSkill.SENTENCE_PATTERN_LEARNING.value: {
        "category": "bible",
        "description": "Learn reusable sentence patterns from the Bible corpus",
    },
    AgentSkill.TRANSLATION_VALIDATION.value: {
        "category": "translation",
        "description": "Validate translations against Bible patterns",
    },
    AgentSkill.GRAMMAR_VALIDATION.value: {
        "category": "grammar",
        "description": "Validate ZVS 2018 grammar compliance",
    },
    AgentSkill.VOCABULARY_EXPANSION.value: {
        "category": "vocabulary",
        "description": "Expand the Zolai lexicon with related words",
    },
    AgentSkill.CONTEXT_ANALYSIS.value: {
        "category": "context",
        "description": "Analyse discourse and situational context",
    },
    AgentSkill.DISCUSSION_FACILITATION.value: {
        "category": "collaboration",
        "description": "Facilitate multi-agent discussions and consensus",
    },
    AgentSkill.CONSENSUS_BUILDING.value: {
        "category": "collaboration",
        "description": "Synthesise agent positions into a consensus",
    },
    AgentSkill.KNOWLEDGE_SYNTHESIS.value: {
        "category": "collaboration",
        "description": "Synthesise knowledge across agents",
    },
    AgentSkill.ERROR_DETECTION.value: {
        "category": "quality",
        "description": "Detect grammar and translation errors",
    },
    AgentSkill.FEEDBACK_INTEGRATION.value: {
        "category": "learning",
        "description": "Fold learning signals back into agent behaviour",
    },
    AgentSkill.CONTINUOUS_LEARNING.value: {
        "category": "learning",
        "description": "Keep learning continuously from corpus and feedback",
    },
}

# The four agents that used to hardcode their AgentConfig inline.
# These values are the *fallback* and the initial seed — after that the
# database is the source of truth.
AGENT_DEFAULTS: dict[str, dict[str, Any]] = {
    "bible_learner_001": {
        "name": "BibleLearner",
        "role": AgentRole.BIBLE_LEARNER.value,
        "skills": [
            AgentSkill.BIBLE_PATTERN_EXTRACTION.value,
            AgentSkill.BIBLE_TRANSLATION_GROUNDING.value,
            AgentSkill.SENTENCE_PATTERN_LEARNING.value,
            AgentSkill.CONTINUOUS_LEARNING.value,
        ],
        "config": {"bible_priority": True, "confidence_threshold": 0.85},
    },
    "translator_001": {
        "name": "Translator",
        "role": AgentRole.TRANSLATOR.value,
        "skills": [
            AgentSkill.BIBLE_TRANSLATION_GROUNDING.value,
            AgentSkill.TRANSLATION_VALIDATION.value,
            AgentSkill.CONTEXT_ANALYSIS.value,
            AgentSkill.CONTINUOUS_LEARNING.value,
        ],
        "config": {"bible_priority": True, "confidence_threshold": 0.8},
    },
    "grammar_expert_001": {
        "name": "GrammarExpert",
        "role": AgentRole.GRAMMAR_EXPERT.value,
        "skills": [
            AgentSkill.GRAMMAR_VALIDATION.value,
            AgentSkill.ERROR_DETECTION.value,
            AgentSkill.CONTINUOUS_LEARNING.value,
        ],
        "config": {"bible_priority": True, "confidence_threshold": 0.9},
    },
    "vocabulary_builder_001": {
        "name": "VocabularyBuilder",
        "role": AgentRole.VOCABULARY_BUILDER.value,
        "skills": [
            AgentSkill.VOCABULARY_EXPANSION.value,
            AgentSkill.CONTEXT_ANALYSIS.value,
            AgentSkill.CONTINUOUS_LEARNING.value,
        ],
        "config": {"bible_priority": True, "confidence_threshold": 0.8},
    },
}


def _now() -> str:
    """UTC timestamp for the bookkeeping columns."""
    return datetime.now(timezone.utc).isoformat()


def _coerce_role(value: AgentRole | str) -> AgentRole:
    if isinstance(value, AgentRole):
        return value
    try:
        return AgentRole(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown agent role: {value!r}") from exc


def _coerce_skill(value: AgentSkill | str) -> AgentSkill:
    if isinstance(value, AgentSkill):
        return value
    try:
        return AgentSkill(str(value))
    except ValueError as exc:
        raise ValueError(
            f"unknown skill: {value!r} — add it to the AgentSkill enum before assigning it"
        ) from exc


def _coerce_skills(values: Iterable[AgentSkill | str]) -> list[str]:
    return [_coerce_skill(value).value for value in values]


def _normalise_tuning(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate + coerce the tunable config fields."""
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if key not in TUNABLE_FIELDS:
            raise ValueError(
                f"unknown agent config field: {key!r} (expected one of {', '.join(TUNABLE_FIELDS)})"
            )
        if key == "team_id":
            clean[key] = None if value is None else str(value)
        elif key == "bible_priority":
            clean[key] = bool(value)
        elif key == "max_concurrent_tasks":
            clean[key] = int(value)
        else:
            clean[key] = float(value)
    return clean


def _decode_json(raw: Any, fallback: Any) -> Any:
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("agent registry: undecodable JSON column %r — using default", raw)
        return fallback


def _decode_tuning(raw: Any) -> dict[str, Any]:
    stored = _decode_json(raw, {})
    if not isinstance(stored, dict):
        return {}
    known = {k: v for k, v in stored.items() if k in TUNABLE_FIELDS}
    dropped = sorted(set(stored) - set(known))
    if dropped:
        logger.warning("agent registry: ignoring unknown config fields %s", ", ".join(dropped))
    try:
        return _normalise_tuning(known)
    except (TypeError, ValueError) as exc:
        logger.warning("agent registry: invalid stored config (%s) — using defaults", exc)
        return {}


def build_default_config(agent_id: str) -> AgentConfig:
    """Build the built-in fallback config for a seeded agent.

    Only used when the database has no usable row (fresh DB, unreachable DB,
    or an agent id that has never been registered).
    """
    try:
        spec = AGENT_DEFAULTS[agent_id]
    except KeyError as exc:
        raise KeyError(f"unknown agent id: {agent_id!r}") from exc
    tuning = dict(DEFAULT_TUNING)
    tuning.update(spec["config"])
    return AgentConfig(
        agent_id=agent_id,
        name=spec["name"],
        role=_coerce_role(spec["role"]),
        skills=[_coerce_skill(value) for value in spec["skills"]],
        **tuning,
    )


@dataclass
class SkillRecord:
    """A row of ``agent_skills``."""

    skill_id: str
    name: str
    description: str = ""
    category: str = ""
    config_schema: dict = field(default_factory=dict)
    enabled: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "config_schema": self.config_schema,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


@dataclass
class AgentRecord:
    """A row of ``agents`` plus its tunable config."""

    agent_id: str
    name: str
    role: AgentRole
    skills: list[AgentSkill] = field(default_factory=list)
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_agent_config(self) -> AgentConfig:
        tuning = dict(DEFAULT_TUNING)
        tuning.update(self.config)
        return AgentConfig(
            agent_id=self.agent_id,
            name=self.name,
            role=self.role,
            skills=list(self.skills),
            **tuning,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "skills": [skill.value for skill in self.skills],
            "enabled": self.enabled,
            "config": dict(self.config),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AgentRegistry:
    """Read/write access to the database-driven agent + skill catalogue."""

    def __init__(self, engine: Optional[Engine] = None, seed: bool = True):
        self.engine = engine or get_engine()
        self._agents: dict[str, AgentRecord] = {}
        self._skills: dict[str, SkillRecord] = {}
        self.ensure_schema()
        if seed:
            self.seed_defaults()
        self.refresh()

    # ── schema + seed ───────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create the registry tables if they do not exist (idempotent)."""
        with self.engine.begin() as conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(text(statement))

    def seed_defaults(self) -> dict[str, int]:
        """Insert the built-in agents + skills, never overwriting existing rows.

        Uses ``INSERT OR IGNORE`` so admin edits (tuning, renames, disabling)
        survive re-seeding.
        """
        now = _now()
        created_skills = 0
        created_agents = 0
        with self.engine.begin() as conn:
            for skill_id, meta in SKILL_CATALOGUE.items():
                result = conn.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO agent_skills
                            (skill_id, name, description, category, config_schema, enabled, created_at)
                        VALUES (:skill_id, :name, :description, :category, :config_schema, 1, :created_at)
                        """
                    ),
                    {
                        "skill_id": skill_id,
                        "name": skill_id.replace("_", " ").title(),
                        "description": meta["description"],
                        "category": meta["category"],
                        "config_schema": json.dumps({}),
                        "created_at": now,
                    },
                )
                created_skills += result.rowcount or 0

            for agent_id, spec in AGENT_DEFAULTS.items():
                result = conn.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO agents
                            (agent_id, name, role, skills, config, enabled, created_at, updated_at)
                        VALUES (:agent_id, :name, :role, :skills, :config, 1, :created_at, :updated_at)
                        """
                    ),
                    {
                        "agent_id": agent_id,
                        "name": spec["name"],
                        "role": spec["role"],
                        "skills": json.dumps(spec["skills"]),
                        "config": json.dumps(spec["config"]),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                created_agents += result.rowcount or 0

        if created_agents or created_skills:
            logger.info(
                "agent registry seeded: %d agent(s), %d skill(s) inserted",
                created_agents,
                created_skills,
            )
        return {"agents": created_agents, "skills": created_skills}

    # ── cache ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload the agent + skill caches from the database."""
        self._skills = {
            row["skill_id"]: SkillRecord(
                skill_id=row["skill_id"],
                name=row["name"],
                description=row["description"],
                category=row["category"],
                config_schema=_decode_json(row["config_schema"], {}),
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
            )
            for row in self._query(
                "SELECT skill_id, name, description, category, config_schema, enabled, created_at "
                "FROM agent_skills ORDER BY skill_id"
            )
        }
        self._agents = {
            row["agent_id"]: self._row_to_record(row)
            for row in self._query(
                "SELECT agent_id, name, role, skills, config, enabled, created_at, updated_at "
                "FROM agents ORDER BY agent_id"
            )
        }

    def _query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings()]

    def _execute(self, sql: str, params: Optional[dict] = None):
        with self.engine.begin() as conn:
            return conn.execute(text(sql), params or {})

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> AgentRecord:
        skills: list[AgentSkill] = []
        for value in _decode_json(row["skills"], []):
            try:
                skills.append(_coerce_skill(value))
            except ValueError:
                logger.warning("agent registry: unknown skill %r on agent %s", value, row["agent_id"])
        return AgentRecord(
            agent_id=row["agent_id"],
            name=row["name"],
            role=_coerce_role(row["role"]),
            skills=skills,
            enabled=bool(row["enabled"]),
            config=_decode_tuning(row["config"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── reads ───────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        """Return the agent row, or None when it is not registered."""
        return self._agents.get(agent_id)

    def get_config(self, agent_id: str) -> Optional[AgentConfig]:
        """Return the resolved ``AgentConfig``, or None when not registered."""
        record = self.get(agent_id)
        return record.to_agent_config() if record is not None else None

    def get_all(self, include_disabled: bool = True) -> list[AgentRecord]:
        """All registered agents, ordered by agent id."""
        records = [r for r in self._agents.values() if include_disabled or r.enabled]
        return sorted(records, key=lambda r: r.agent_id)

    def list_by_role(self, role: AgentRole | str, include_disabled: bool = True) -> list[AgentRecord]:
        """Agents holding a given role."""
        wanted = _coerce_role(role)
        return [r for r in self.get_all(include_disabled) if r.role is wanted]

    def list_by_skill(self, skill_id: AgentSkill | str, include_disabled: bool = True) -> list[AgentRecord]:
        """Agents that have a given skill assigned."""
        wanted = _coerce_skill(skill_id)
        return [r for r in self.get_all(include_disabled) if wanted in r.skills]

    def is_enabled(self, agent_id: str) -> bool:
        """True when the agent exists and is not disabled."""
        record = self.get(agent_id)
        return bool(record and record.enabled)

    def resolve_config(self, agent_id: str) -> AgentConfig:
        """Config from the database, falling back to the built-in seed values."""
        record = self.get(agent_id)
        if record is not None:
            return record.to_agent_config()
        logger.warning(
            "agent registry: no row for %r — falling back to built-in defaults", agent_id
        )
        return build_default_config(agent_id)

    # ── agent writes ────────────────────────────────────────────────────

    def create_agent(
        self,
        *,
        agent_id: str,
        name: str,
        role: AgentRole | str,
        skills: Iterable[AgentSkill | str] = (),
        **tuning: Any,
    ) -> AgentRecord:
        """Register a new agent row at runtime."""
        if not agent_id:
            raise ValueError("agent_id is required")
        if self.get(agent_id) is not None:
            raise ValueError(f"agent already registered: {agent_id!r}")
        role_value = _coerce_role(role).value
        skill_values = _coerce_skills(skills)
        config = _normalise_tuning(tuning)
        now = _now()
        self._execute(
            """
            INSERT INTO agents
                (agent_id, name, role, skills, config, enabled, created_at, updated_at)
            VALUES (:agent_id, :name, :role, :skills, :config, 1, :created_at, :updated_at)
            """,
            {
                "agent_id": agent_id,
                "name": name,
                "role": role_value,
                "skills": json.dumps(skill_values),
                "config": json.dumps(config),
                "created_at": now,
                "updated_at": now,
            },
        )
        record = AgentRecord(
            agent_id=agent_id,
            name=name,
            role=AgentRole(role_value),
            skills=[_coerce_skill(value) for value in skill_values],
            enabled=True,
            config=config,
            created_at=now,
            updated_at=now,
        )
        self._agents[agent_id] = record
        logger.info("agent registry: created agent %s (%s)", agent_id, role_value)
        return record

    def update_agent(
        self,
        agent_id: str,
        *,
        name: Optional[str] = None,
        role: AgentRole | str | None = None,
        skills: Iterable[AgentSkill | str] | None = None,
        **tuning: Any,
    ) -> AgentRecord:
        """Update name/role/skills/tuning of an existing agent."""
        record = self.get(agent_id)
        if record is None:
            raise KeyError(f"unknown agent: {agent_id!r}")

        new_name = record.name if name is None else name
        new_role = record.role if role is None else _coerce_role(role)
        new_skills = record.skills if skills is None else [_coerce_skill(s) for s in skills]
        new_config = dict(record.config)
        new_config.update(_normalise_tuning(tuning))
        now = _now()

        self._execute(
            """
            UPDATE agents
            SET name = :name, role = :role, skills = :skills, config = :config, updated_at = :updated_at
            WHERE agent_id = :agent_id
            """,
            {
                "agent_id": agent_id,
                "name": new_name,
                "role": new_role.value,
                "skills": json.dumps([s.value for s in new_skills]),
                "config": json.dumps(new_config),
                "updated_at": now,
            },
        )
        updated = AgentRecord(
            agent_id=agent_id,
            name=new_name,
            role=new_role,
            skills=list(new_skills),
            enabled=record.enabled,
            config=new_config,
            created_at=record.created_at,
            updated_at=now,
        )
        self._agents[agent_id] = updated
        logger.info("agent registry: updated agent %s", agent_id)
        return updated

    def _set_enabled(self, agent_id: str, enabled: bool) -> AgentRecord:
        record = self.get(agent_id)
        if record is None:
            raise KeyError(f"unknown agent: {agent_id!r}")
        now = _now()
        self._execute(
            "UPDATE agents SET enabled = :enabled, updated_at = :updated_at WHERE agent_id = :agent_id",
            {"agent_id": agent_id, "enabled": 1 if enabled else 0, "updated_at": now},
        )
        record.enabled = enabled
        record.updated_at = now
        logger.info("agent registry: agent %s %s", agent_id, "enabled" if enabled else "disabled")
        return record

    def enable_agent(self, agent_id: str) -> AgentRecord:
        """Re-enable a soft-deleted agent."""
        return self._set_enabled(agent_id, True)

    def disable_agent(self, agent_id: str) -> AgentRecord:
        """Soft delete: the row stays, but the agent is not used any more."""
        return self._set_enabled(agent_id, False)

    def delete_agent(self, agent_id: str) -> None:
        """Hard delete (only when the agent must disappear from the registry)."""
        record = self.get(agent_id)
        if record is None:
            raise KeyError(f"unknown agent: {agent_id!r}")
        self._execute("DELETE FROM agents WHERE agent_id = :agent_id", {"agent_id": agent_id})
        self._agents.pop(agent_id, None)
        logger.info("agent registry: deleted agent %s", agent_id)

    # ── skills ──────────────────────────────────────────────────────────

    def get_skill(self, skill_id: AgentSkill | str) -> Optional[SkillRecord]:
        key = skill_id.value if isinstance(skill_id, AgentSkill) else str(skill_id)
        return self._skills.get(key)

    def list_skills(
        self, category: Optional[str] = None, include_disabled: bool = True
    ) -> list[SkillRecord]:
        """All known skills, optionally filtered by category."""
        records = [s for s in self._skills.values() if include_disabled or s.enabled]
        if category is not None:
            records = [s for s in records if s.category == category]
        return sorted(records, key=lambda s: s.skill_id)

    def create_skill(
        self,
        *,
        skill_id: AgentSkill | str,
        name: Optional[str] = None,
        description: str = "",
        category: str = "",
        config_schema: Optional[Mapping[str, Any]] = None,
        enabled: bool = True,
    ) -> SkillRecord:
        """Register a skill in the catalogue (idempotent)."""
        key = skill_id.value if isinstance(skill_id, AgentSkill) else str(skill_id)
        if not key:
            raise ValueError("skill_id is required")
        existing = self._skills.get(key)
        now = _now()
        schema = dict(config_schema or {})
        self._execute(
            """
            INSERT OR IGNORE INTO agent_skills
                (skill_id, name, description, category, config_schema, enabled, created_at)
            VALUES (:skill_id, :name, :description, :category, :config_schema, :enabled, :created_at)
            """,
            {
                "skill_id": key,
                "name": name or key.replace("_", " ").title(),
                "description": description,
                "category": category,
                "config_schema": json.dumps(schema),
                "enabled": 1 if enabled else 0,
                "created_at": now,
            },
        )
        record = existing or SkillRecord(
            skill_id=key,
            name=name or key.replace("_", " ").title(),
            description=description,
            category=category,
            config_schema=schema,
            enabled=enabled,
            created_at=now,
        )
        self._skills[key] = record
        return record

    def set_skill_enabled(self, skill_id: AgentSkill | str, enabled: bool) -> SkillRecord:
        """Enable/disable a skill in the catalogue."""
        key = skill_id.value if isinstance(skill_id, AgentSkill) else str(skill_id)
        if key not in self._skills:
            raise KeyError(f"unknown skill: {key!r}")
        self._execute(
            "UPDATE agent_skills SET enabled = :enabled WHERE skill_id = :skill_id",
            {"skill_id": key, "enabled": 1 if enabled else 0},
        )
        self._skills[key].enabled = enabled
        return self._skills[key]

    def assign_skill(self, agent_id: str, skill_id: AgentSkill | str) -> AgentRecord:
        """Give an agent an extra skill."""
        skill = _coerce_skill(skill_id)
        record = self.get(agent_id)
        if record is None:
            raise KeyError(f"unknown agent: {agent_id!r}")
        if skill in record.skills:
            return record
        return self.update_agent(agent_id, skills=[*record.skills, skill])

    def remove_skill(self, agent_id: str, skill_id: AgentSkill | str) -> AgentRecord:
        """Take a skill away from an agent."""
        skill = _coerce_skill(skill_id)
        record = self.get(agent_id)
        if record is None:
            raise KeyError(f"unknown agent: {agent_id!r}")
        return self.update_agent(agent_id, skills=[s for s in record.skills if s is not skill])

    # ── activity records ────────────────────────────────────────────────

    def record_task(
        self,
        agent_id: str,
        *,
        task_type: str = "",
        status: str = "pending",
        payload: Optional[Mapping[str, Any]] = None,
        result: Optional[Mapping[str, Any]] = None,
        error: Optional[str] = None,
        task_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Persist a task handed to an agent; returns the task id."""
        now = _now()
        new_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        self._execute(
            """
            INSERT OR REPLACE INTO agent_tasks
                (task_id, agent_id, team_id, task_type, status, payload, result, error,
                 created_at, updated_at)
            VALUES (:task_id, :agent_id, :team_id, :task_type, :status, :payload, :result,
                    :error, :created_at, :updated_at)
            """,
            {
                "task_id": new_id,
                "agent_id": agent_id,
                "team_id": team_id,
                "task_type": task_type,
                "status": status,
                "payload": json.dumps(dict(payload or {})),
                "result": None if result is None else json.dumps(dict(result)),
                "error": error,
                "created_at": now,
                "updated_at": now,
            },
        )
        return new_id

    def list_tasks(self, agent_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Recent tasks, newest first."""
        if agent_id:
            return self._query(
                "SELECT * FROM agent_tasks WHERE agent_id = :agent_id "
                "ORDER BY created_at DESC LIMIT :limit",
                {"agent_id": agent_id, "limit": limit},
            )
        return self._query(
            "SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT :limit", {"limit": limit}
        )

    def record_discussion(
        self,
        topic: str,
        *,
        team_id: Optional[str] = None,
        participants: Iterable[str] = (),
        discussion_id: Optional[str] = None,
    ) -> str:
        """Persist a discussion; returns the discussion id."""
        now = _now()
        new_id = discussion_id or f"disc_{uuid.uuid4().hex[:12]}"
        self._execute(
            """
            INSERT OR REPLACE INTO agent_discussions
                (discussion_id, topic, team_id, status, consensus, participants, created_at, updated_at)
            VALUES (:discussion_id, :topic, :team_id, 'active', NULL, :participants, :created_at, :updated_at)
            """,
            {
                "discussion_id": new_id,
                "topic": topic,
                "team_id": team_id,
                "participants": json.dumps(list(participants)),
                "created_at": now,
                "updated_at": now,
            },
        )
        return new_id

    def record_message(
        self,
        discussion_id: str,
        *,
        from_agent: str,
        content: str,
        to_agent: Optional[str] = None,
        message_type: str = "discussion",
        metadata: Optional[Mapping[str, Any]] = None,
        reply_to: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> str:
        """Persist a discussion message; returns the message id."""
        new_id = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        self._execute(
            """
            INSERT OR REPLACE INTO discussion_messages
                (message_id, discussion_id, from_agent, to_agent, message_type, content,
                 metadata, reply_to, created_at)
            VALUES (:message_id, :discussion_id, :from_agent, :to_agent, :message_type,
                    :content, :metadata, :reply_to, :created_at)
            """,
            {
                "message_id": new_id,
                "discussion_id": discussion_id,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "message_type": message_type,
                "content": content,
                "metadata": json.dumps(dict(metadata or {})),
                "reply_to": reply_to,
                "created_at": _now(),
            },
        )
        return new_id

    def list_discussions(self, limit: int = 50) -> list[dict]:
        """Recent discussions, newest first."""
        return self._query(
            "SELECT * FROM agent_discussions ORDER BY created_at DESC LIMIT :limit", {"limit": limit}
        )


# ── module-level access ─────────────────────────────────────────────────

_shared_registry: Optional[AgentRegistry] = None
_engine_registries: dict[int, tuple[Engine, AgentRegistry]] = {}
_MAX_CACHED_ENGINES = 8


def get_agent_registry(engine: Optional[Engine] = None) -> AgentRegistry:
    """Return the shared registry, or one bound to ``engine``.

    Registries are cached per engine so the schema/seed work happens once.
    """
    global _shared_registry
    if engine is None:
        if _shared_registry is None:
            _shared_registry = AgentRegistry()
        return _shared_registry

    cached = _engine_registries.get(id(engine))
    if cached is not None and cached[0] is engine:
        return cached[1]

    registry = AgentRegistry(engine)
    if len(_engine_registries) >= _MAX_CACHED_ENGINES:
        _engine_registries.clear()
    _engine_registries[id(engine)] = (engine, registry)
    return registry


def reset_agent_registry() -> None:
    """Drop cached registries (tests / re-pointing the engine)."""
    global _shared_registry
    _shared_registry = None
    _engine_registries.clear()


def resolve_agent_config(agent_id: str, engine: Optional[Engine] = None) -> AgentConfig:
    """Resolve an agent's config from the registry — never raises.

    Falls back to the built-in seed values when the database has no row for
    ``agent_id`` or cannot be reached at all, so agent construction keeps
    working offline.
    """
    try:
        return get_agent_registry(engine).resolve_config(agent_id)
    except Exception as exc:  # pragma: no cover - defensive, DB may be missing
        logger.warning(
            "agent registry unavailable (%s) — using built-in defaults for %s", exc, agent_id
        )
        return build_default_config(agent_id)