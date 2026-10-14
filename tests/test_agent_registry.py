"""Tests for the database-driven agent registry.

The registry is what replaced the per-agent hardcoded ``AgentConfig``
literals, so most of these tests are regression guards: they assert the
seeded rows still reproduce the values those literals used to carry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from zolai.learning.agents import agent_management
from zolai.learning.agents.agent_registry import (
    AgentRegistry,
    build_default_config,
    reset_agent_registry,
    resolve_agent_config,
)

# The exact configuration each agent class used to hardcode inline.
SEEDED = {
    "bible_learner_001": {
        "name": "BibleLearner",
        "role": "bible_learner",
        "confidence_threshold": 0.85,
        "bible_priority": True,
        "skills": 4,
    },
    "translator_001": {
        "name": "Translator",
        "role": "translator",
        "confidence_threshold": 0.8,
        "bible_priority": True,
        "skills": 4,
    },
    "grammar_expert_001": {
        "name": "GrammarExpert",
        "role": "grammar_expert",
        "confidence_threshold": 0.9,
        "bible_priority": True,
        "skills": 3,
    },
    "vocabulary_builder_001": {
        "name": "VocabularyBuilder",
        "role": "vocabulary_builder",
        "confidence_threshold": 0.8,
        "bible_priority": True,
        "skills": 3,
    },
}


@pytest.fixture()
def registry(tmp_path: Path) -> AgentRegistry:
    """A registry backed by a throwaway SQLite file — never the real DB."""
    engine = create_engine(f"sqlite:///{tmp_path / 'registry.db'}")
    reg = AgentRegistry(engine=engine)
    reg.ensure_schema()
    reg.seed_defaults()
    return reg


# ── Schema + seeding ──────────────────────────────────────────────────────


class TestSchemaAndSeed:
    def test_ensure_schema_is_idempotent(self, registry: AgentRegistry) -> None:
        registry.ensure_schema()
        registry.ensure_schema()
        assert len(registry.get_all()) == len(SEEDED)

    def test_seed_defaults_is_idempotent(self, registry: AgentRegistry) -> None:
        second = registry.seed_defaults()
        assert second["agents"] == 0
        assert second["skills"] == 0
        assert len(registry.get_all()) == len(SEEDED)

    def test_seed_matches_previous_hardcoded_config(self, registry: AgentRegistry) -> None:
        """Regression guard for the config each agent used to carry inline."""
        for agent_id, expected in SEEDED.items():
            config = registry.get_config(agent_id)
            assert config is not None, agent_id
            assert config.name == expected["name"]
            assert config.role.value == expected["role"]
            assert config.confidence_threshold == pytest.approx(
                expected["confidence_threshold"]
            )
            assert config.bible_priority is expected["bible_priority"]
            assert len(config.skills) == expected["skills"]

    def test_all_agents_start_enabled(self, registry: AgentRegistry) -> None:
        for agent_id in SEEDED:
            assert registry.is_enabled(agent_id)


# ── CRUD ──────────────────────────────────────────────────────────────────


class TestAgentCrud:
    def test_create_and_read_back(self, registry: AgentRegistry) -> None:
        record = registry.create_agent(
            agent_id="extra_001",
            name="ExtraAgent",
            role="translator",
            skills=["continuous_learning"],
            confidence_threshold=0.42,
        )
        assert record.agent_id == "extra_001"

        config = registry.get_config("extra_001")
        assert config.name == "ExtraAgent"
        assert config.role.value == "translator"
        assert config.confidence_threshold == pytest.approx(0.42)

    def test_create_rejects_duplicate(self, registry: AgentRegistry) -> None:
        registry.create_agent(agent_id="extra_001", name="A", role="translator")
        with pytest.raises(ValueError):
            registry.create_agent(
                agent_id="extra_001", name="B", role="translator"
            )

    def test_create_requires_agent_id(self, registry: AgentRegistry) -> None:
        with pytest.raises(ValueError):
            registry.create_agent(agent_id="", name="A", role="translator")

    def test_update_tuning(self, registry: AgentRegistry) -> None:
        registry.update_agent("translator_001", confidence_threshold=0.77)
        assert registry.get_config(
            "translator_001"
        ).confidence_threshold == pytest.approx(0.77)

    def test_update_rejects_unknown_tuning_field(self, registry: AgentRegistry) -> None:
        """Tuning keys are validated — a typo must not silently persist."""
        with pytest.raises(ValueError):
            registry.update_agent("translator_001", not_a_field=1)

    def test_update_unknown_agent_raises(self, registry: AgentRegistry) -> None:
        with pytest.raises(KeyError):
            registry.update_agent("nope_001", confidence_threshold=0.5)

    def test_changes_persist_across_instances(self, tmp_path: Path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'persist.db'}")
        first = AgentRegistry(engine=engine)
        first.ensure_schema()
        first.seed_defaults()
        first.update_agent("grammar_expert_001", learning_rate=0.33)

        second = AgentRegistry(engine=engine, seed=False)
        assert second.get_config(
            "grammar_expert_001"
        ).learning_rate == pytest.approx(0.33)


# ── Enable / disable ──────────────────────────────────────────────────────


class TestEnableDisable:
    def test_disable_persists(self, registry: AgentRegistry, tmp_path: Path) -> None:
        registry.disable_agent("translator_001")
        assert registry.is_enabled("translator_001") is False

        reopened = AgentRegistry(
            engine=registry.engine, seed=False
        )
        assert reopened.is_enabled("translator_001") is False

    def test_enable_round_trip(self, registry: AgentRegistry) -> None:
        registry.disable_agent("translator_001")
        registry.enable_agent("translator_001")
        assert registry.is_enabled("translator_001") is True

    def test_disabled_agent_is_still_manageable(self, registry: AgentRegistry) -> None:
        """Disabling hides an agent from the team, not from administration."""
        registry.disable_agent("translator_001")
        assert registry.is_enabled("translator_001") is False
        assert registry.get("translator_001") is not None
        assert any(
            a.agent_id == "translator_001" for a in registry.get_all()
        )

    def test_delete_agent_is_a_hard_delete(self, registry: AgentRegistry) -> None:
        """delete_agent removes the row outright, unlike disable_agent."""
        registry.delete_agent("translator_001")
        assert registry.get("translator_001") is None
        assert registry.is_enabled("translator_001") is False
        assert not any(
            a.agent_id == "translator_001" for a in registry.get_all()
        )

    def test_delete_unknown_agent_raises(self, registry: AgentRegistry) -> None:
        with pytest.raises(KeyError):
            registry.delete_agent("nope_001")


# ── Skills ────────────────────────────────────────────────────────────────


class TestSkills:
    def test_assign_and_remove(self, registry: AgentRegistry) -> None:
        before = len(registry.get("grammar_expert_001").skills)
        registry.assign_skill("grammar_expert_001", "consensus_building")
        assert len(registry.get("grammar_expert_001").skills) == before + 1

        registry.remove_skill("grammar_expert_001", "consensus_building")
        assert len(registry.get("grammar_expert_001").skills) == before

    def test_assign_unknown_agent_raises(self, registry: AgentRegistry) -> None:
        with pytest.raises(KeyError):
            registry.assign_skill("nope_001", "continuous_learning")

    def test_list_skills_covers_seeded_agents(self, registry: AgentRegistry) -> None:
        skill_ids = {s.skill_id for s in registry.list_skills()}
        for agent_id in SEEDED:
            for skill in registry.get(agent_id).skills:
                assert skill.value in skill_ids

    def test_list_by_skill_and_role(self, registry: AgentRegistry) -> None:
        by_role = registry.list_by_role("grammar_expert")
        assert [a.agent_id for a in by_role] == ["grammar_expert_001"]

        by_skill = registry.list_by_skill("bible_translation_grounding")
        assert {a.agent_id for a in by_skill} >= {
            "bible_learner_001",
            "translator_001",
        }


# ── Resolution + fallback ─────────────────────────────────────────────────


class TestResolution:
    def test_resolve_uses_database_row(
        self, registry: AgentRegistry, tmp_path: Path
    ) -> None:
        registry.update_agent("grammar_expert_001", confidence_threshold=0.55)
        config = resolve_agent_config(
            "grammar_expert_001", engine=registry.engine
        )
        assert config.confidence_threshold == pytest.approx(0.55)

    def test_resolve_unknown_agent_raises(self, registry: AgentRegistry) -> None:
        with pytest.raises(KeyError):
            resolve_agent_config("does_not_exist_999", engine=registry.engine)

    def test_build_default_config_mirrors_seeded_values(self) -> None:
        config = build_default_config("bible_learner_001")
        assert config.confidence_threshold == pytest.approx(0.85)
        assert config.role.value == "bible_learner"

    def test_build_default_config_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            build_default_config("does_not_exist_999")


# ── Management surface ────────────────────────────────────────────────────


class TestManagementSurface:
    def test_overview_counts(self, registry: AgentRegistry) -> None:
        summary = agent_management.overview(engine=registry.engine)
        assert summary["agents_total"] == len(SEEDED)
        assert summary["agents_enabled"] == len(SEEDED)
        assert summary["agents_disabled"] == 0
        assert summary["skills_total"] > 0

    def test_management_create_enable_disable(self, registry: AgentRegistry) -> None:
        engine = registry.engine
        agent_management.create_agent(
            agent_id="managed_001", name="Managed", role="translator",
            engine=engine,
        )
        assert agent_management.get_agent("managed_001", engine=engine) is not None

        agent_management.disable_agent("managed_001", engine=engine)
        summary = agent_management.overview(engine=engine)
        assert summary["agents_disabled"] == 1

        agent_management.enable_agent("managed_001", engine=engine)
        assert agent_management.overview(engine=engine)["agents_disabled"] == 0

    def test_management_list_agents(self, registry: AgentRegistry) -> None:
        rows = agent_management.list_agents(engine=registry.engine)
        assert {row["agent_id"] for row in rows} >= set(SEEDED)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """The registry module caches a singleton; keep tests isolated."""
    reset_agent_registry()
    yield
    reset_agent_registry()
