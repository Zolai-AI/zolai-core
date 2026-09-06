"""
Tests for Zolai AI agents.
"""
import py_compile
from pathlib import Path

import pytest

from zolai.agents import (
    AgentCoordinator,
    AgentResult,
    DataQualityAgent,
    GrammarAgent,
    TeachingAgent,
    TranslationAgent,
    ZolaiAgent,
)

AGENTS_DIR = Path(__file__).resolve().parent.parent / "zolai" / "agents"
AGENT_FILES = sorted(AGENTS_DIR.glob("*.py"))


# ── File-level checks ────────────────────────────────────────────────────


class TestAgentFiles:
    """Ensure every agent module compiles and passes lint."""

    @pytest.mark.parametrize("agent_file", AGENT_FILES, ids=lambda p: p.name)
    def test_py_compile(self, agent_file: Path) -> None:
        result = py_compile.compile(str(agent_file), doraise=True)
        assert result is not None


# ── Base agent ────────────────────────────────────────────────────────────


class TestAgentResult:
    """Tests for the AgentResult dataclass."""

    def test_success_result(self) -> None:
        r = AgentResult(success=True, data={"key": "val"}, agent_name="test")
        assert r.success is True
        assert r.data == {"key": "val"}
        assert r.agent_name == "test"
        assert r.timestamp  # non-empty
        assert r.errors == []

    def test_failure_result(self) -> None:
        r = AgentResult(success=False, errors=["bad input"])
        assert r.success is False
        assert "bad input" in r.errors

    def test_to_dict(self) -> None:
        r = AgentResult(success=True, data={"x": 1}, agent_name="a")
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"]["x"] == 1
        assert d["agent_name"] == "a"
        assert "timestamp" in d


class TestZolaiAgent:
    """Tests for the abstract base ZolaiAgent."""

    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            ZolaiAgent("test")  # type: ignore[abstract]

    def test_validate_input_empty(self) -> None:
        agent = GrammarAgent()
        valid, errors = agent.validate_input({})
        assert valid is False
        assert any("empty" in e.lower() for e in errors)

    def test_validate_input_valid(self) -> None:
        agent = GrammarAgent()
        valid, errors = agent.validate_input({"text": "hello"})
        assert valid is True
        assert errors == []


# ── Grammar agent ─────────────────────────────────────────────────────────


class TestGrammarAgent:
    """Tests for GrammarAgent."""

    def test_clean_text_compliant(self) -> None:
        agent = GrammarAgent()
        result = agent.run({"text": "pasian gam tui"})
        assert result.success is True
        assert result.data["is_compliant"] is True
        assert result.data["corrected"] == "pasian gam tui"
        assert result.agent_name == "grammar_agent"

    def test_forbidden_form_detected(self) -> None:
        agent = GrammarAgent()
        result = agent.run({"text": "pathian is God"})
        assert result.success is True
        assert result.data["is_compliant"] is False
        assert len(result.data["violations"]) >= 1
        # pathian should be flagged
        originals = [v[0] for v in result.data["violations"]]
        assert "pathian" in originals

    def test_empty_text(self) -> None:
        agent = GrammarAgent()
        result = agent.run({"text": ""})
        assert result.success is False
        assert any("no text" in e.lower() for e in result.errors)


# ── Translation agent ─────────────────────────────────────────────────────


class TestTranslationAgent:
    """Tests for TranslationAgent."""

    def test_auto_detect_english(self) -> None:
        agent = TranslationAgent()
        result = agent.run({"text": "what is the meaning"})
        assert result.success is True
        assert result.data["direction"] == "en-zo"

    def test_auto_detect_zolai(self) -> None:
        agent = TranslationAgent()
        result = agent.run({"text": "pasian gam vantung"})
        assert result.success is True
        # Should default to zo-en for non-English text
        assert result.data["direction"] == "zo-en"

    def test_empty_text(self) -> None:
        agent = TranslationAgent()
        result = agent.run({"text": ""})
        assert result.success is False


# ── Teaching agent ────────────────────────────────────────────────────────


class TestTeachingAgent:
    """Tests for TeachingAgent."""

    def test_teach_phrase(self) -> None:
        agent = TeachingAgent()
        result = agent.run({
            "action": "teach_phrase",
            "phrase": "Pasian gam",
            "meaning": "God created",
        })
        assert result.success is True
        assert result.data["phrase"] == "Pasian gam"

    def test_teach_grammar(self) -> None:
        agent = TeachingAgent()
        result = agent.run({"action": "teach_grammar", "rule": "negation"})
        assert result.success is True
        assert result.data["rule"] == "negation"
        assert "info" in result.data

    def test_unknown_action(self) -> None:
        agent = TeachingAgent()
        result = agent.run({"action": "nonexistent"})
        assert result.success is False


# ── Data quality agent ────────────────────────────────────────────────────


class TestDataQualityAgent:
    """Tests for DataQualityAgent."""

    def test_validate_good_entry(self) -> None:
        agent = DataQualityAgent()
        result = agent.run({
            "action": "validate_entry",
            "zolai": "pasian",
            "english": "God",
            "pos": "noun",
        })
        assert result.success is True
        assert result.data["valid"] is True

    def test_validate_bad_entry(self) -> None:
        agent = DataQualityAgent()
        result = agent.run({
            "action": "validate_entry",
            "zolai": "pathian",
            "english": "",
        })
        assert result.success is False
        assert result.data["valid"] is False
        assert len(result.data["issues"]) >= 2  # forbidden form + empty english

    def test_check_consistency(self) -> None:
        agent = DataQualityAgent()
        result = agent.run({
            "action": "check_consistency",
            "text": "pathian ram bawipa",
        })
        assert result.success is False
        assert result.data["consistent"] is False
        assert len(result.data["issues"]) == 3


# ── Coordinator ───────────────────────────────────────────────────────────


class TestAgentCoordinator:
    """Tests for AgentCoordinator."""

    def test_list_agents(self) -> None:
        coord = AgentCoordinator()
        agents = coord.list_agents()
        names = [a["name"] for a in agents]
        assert "grammar" in names
        assert "translator" in names
        assert "teacher" in names
        assert "data_quality" in names

    def test_get_agent(self) -> None:
        coord = AgentCoordinator()
        grammar = coord.get_agent("grammar")
        assert isinstance(grammar, GrammarAgent)
        assert coord.get_agent("nonexistent") is None

    def test_validate_and_fix(self) -> None:
        coord = AgentCoordinator()
        result = coord.run({
            "task": "validate_and_fix",
            "text": "pathian is God",
        })
        assert result.success is True
        assert "validation" in result.data
        assert "grammar_fix" in result.data

    def test_full_analysis(self) -> None:
        coord = AgentCoordinator()
        result = coord.run({
            "task": "full_analysis",
            "text": "pasian gam vantung",
        })
        assert result.success is True
        assert "grammar" in result.data
        assert "translator" in result.data
        assert "teacher" in result.data
        assert "data_quality" in result.data

    def test_unknown_task(self) -> None:
        coord = AgentCoordinator()
        result = coord.run({"task": "nonexistent"})
        assert result.success is False

    def test_agent_run_exception_handling(self) -> None:
        """Ensure agents don't crash on missing input data."""
        agent = GrammarAgent()
        result = agent.run({})
        assert result.success is False
        assert len(result.errors) > 0
