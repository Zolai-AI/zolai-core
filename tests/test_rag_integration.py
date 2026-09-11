"""End-to-end RAG integration tests — full pipeline from query to evidence.

Tests the complete chain: query → ZolaiRAG.retrieve() → EvidencePack → to_prompt()
and feedback override integration.  Uses real data files (not mocked).
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from zolai.knowledge.rag_contract import EvidencePack, ZolaiRAG
from zolai.learning.feedback import FeedbackStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rag():
    """Shared ZolaiRAG instance — lazy-loaded once per module."""
    return ZolaiRAG()


@pytest.fixture()
def tmp_feedback(tmp_path):
    """Temporary FeedbackStore backed by a temp file, cleaned up after test."""
    store = FeedbackStore(path=tmp_path / "corrections.jsonl")
    yield store


# ---------------------------------------------------------------------------
# 1. Full pipeline — Zolai word
# ---------------------------------------------------------------------------

class TestFullPipelineZolaiWord:
    """Query a Zolai word → vocab + bible evidence returned."""

    def test_full_pipeline_zolai_word(self, rag):
        pack = rag.retrieve("pasian")

        # EvidencePack type
        assert isinstance(pack, EvidencePack)
        assert pack.query == "pasian"

        # Vocabulary evidence found
        assert len(pack.vocabulary) > 0, "Expected vocabulary evidence for 'pasian'"
        assert any("God" in e.text or "pasian" in e.text.lower()
                   for e in pack.vocabulary)

        # Bible evidence found
        assert len(pack.bible) > 0, "Expected Bible verses for 'pasian'"

        # No ZVS flags (correct form)
        assert len(pack.zvs) == 0

        # to_prompt produces non-empty string
        prompt = pack.to_prompt()
        assert len(prompt) > 50
        assert "pasian" in prompt.lower()


# ---------------------------------------------------------------------------
# 2. Full pipeline — English word
# ---------------------------------------------------------------------------

class TestFullPipelineEnglishWord:
    """Query an English word → EN→ZO dictionary evidence returned."""

    def test_full_pipeline_english_word(self, rag):
        pack = rag.retrieve("God")

        assert isinstance(pack, EvidencePack)
        assert pack.query == "God"

        # Should have vocabulary from EN→ZO dict
        assert len(pack.vocabulary) > 0, "Expected EN→ZO evidence for 'God'"

        prompt = pack.to_prompt()
        assert len(prompt) > 20


# ---------------------------------------------------------------------------
# 3. Full pipeline — sentence
# ---------------------------------------------------------------------------

class TestFullPipelineSentence:
    """Query a full Zolai sentence → grammar + vocab evidence returned."""

    def test_full_pipeline_sentence(self, rag):
        pack = rag.retrieve("Ka pai kei hi")

        assert isinstance(pack, EvidencePack)
        assert pack.query == "Ka pai kei hi"

        # Should find vocabulary for individual words
        assert len(pack.vocabulary) > 0, "Expected vocab evidence for sentence words"

        prompt = pack.to_prompt()
        assert len(prompt) > 30

        # Valid sentence should not produce ZVS violations
        assert len(pack.zvs) == 0


# ---------------------------------------------------------------------------
# 4. Full pipeline — forbidden form
# ---------------------------------------------------------------------------

class TestFullPipelineForbiddenForm:
    """Query containing a forbidden form → ZVS violation flagged."""

    def test_full_pipeline_forbidden_form(self, rag):
        pack = rag.retrieve("pathian")

        assert isinstance(pack, EvidencePack)
        assert pack.query == "pathian"

        # ZVS should flag the forbidden form
        assert len(pack.zvs) > 0, "Expected ZVS violation for 'pathian'"
        assert any("FORBIDDEN" in e.text for e in pack.zvs)
        assert any("pasian" in e.text for e in pack.zvs)

        # Metadata should contain forbidden/correct mapping
        flagged = pack.zvs[0]
        assert flagged.metadata["forbidden"] == "pathian"
        assert flagged.metadata["correct"] == "pasian"


# ---------------------------------------------------------------------------
# 5. EvidencePack.to_prompt() formatting
# ---------------------------------------------------------------------------

class TestPromptFormatting:
    """EvidencePack.to_prompt() produces valid prompt text."""

    def test_prompt_formatting(self):
        pack = EvidencePack(
            query="test",
            vocabulary=[
                {"id": "d1", "text": "pasian = God", "source": "dict",
                 "type": "vocabulary", "confidence": 0.95, "metadata": {}},
            ],
            grammar=[],
            phrases=[],
            bible=[],
            zvs=[],
            context=[],
        )
        # Build from raw dicts using the Evidence dataclass
        from zolai.knowledge.rag_contract import Evidence
        pack2 = EvidencePack(
            query="test",
            vocabulary=[
                Evidence(id="d1", text="pasian = God", source="dict",
                         type="vocabulary", confidence=0.95),
            ],
        )
        prompt = pack2.to_prompt()
        assert "Vocabulary Evidence" in prompt
        assert "pasian = God" in prompt
        # Confidence shown
        assert "0.95" in prompt


# ---------------------------------------------------------------------------
# 6. Prompt contains expected sections
# ---------------------------------------------------------------------------

class TestPromptContainsSections:
    """Prompt has Vocabulary, Bible, Grammar sections when evidence exists."""

    def test_prompt_contains_sections(self):
        from zolai.knowledge.rag_contract import Evidence

        pack = EvidencePack(
            query="test",
            vocabulary=[
                Evidence(id="d1", text="gam = earth", source="dict",
                         type="vocabulary", confidence=0.95),
            ],
            grammar=[
                Evidence(id="g1", text="SOV word order", source="grammar",
                         type="grammar_pattern", confidence=0.80),
            ],
            bible=[
                Evidence(id="b1", text="GEN 1:1: Gam om hi / The earth existed",
                         source="bible", type="verse", confidence=0.75),
            ],
        )
        prompt = pack.to_prompt()
        assert "## Vocabulary Evidence" in prompt
        assert "## Grammar Evidence" in prompt
        assert "## Bible Examples" in prompt

    def test_zvs_section_present_when_forbidden(self):
        from zolai.knowledge.rag_contract import Evidence

        pack = EvidencePack(
            query="pathian",
            zvs=[
                Evidence(id="zvs:pathian",
                         text="FORBIDDEN: 'pathian' → use 'pasian' instead",
                         source="zvs", type="forbidden_form", confidence=1.0),
            ],
        )
        prompt = pack.to_prompt()
        assert "## ZVS Compliance" in prompt
        assert "FORBIDDEN" in prompt


# ---------------------------------------------------------------------------
# 7. Feedback override applied
# ---------------------------------------------------------------------------

class TestFeedbackOverrideApplied:
    """Record a correction → RAG returns the overridden translation."""

    def test_feedback_override_applied(self, rag, tmp_feedback):
        # Inject our temp store into the RAG instance
        rag._feedback = tmp_feedback

        # Record a correction: "gam" should be "land" (not "earth")
        tmp_feedback.record(
            word="gam",
            original="earth",
            corrected="land",
            user="tester",
            reason="integration test",
        )

        pack = rag.retrieve("gam")

        # Should include feedback override
        feedback_ev = [e for e in pack.vocabulary if e.source == "feedback"]
        assert len(feedback_ev) > 0, "Expected feedback override in evidence"
        assert "land" in feedback_ev[0].text
        assert "user correction" in feedback_ev[0].text
        assert feedback_ev[0].confidence == 0.99

        # Clean up: reset to default for other tests
        rag._feedback = None


# ---------------------------------------------------------------------------
# 8. Feedback override removed
# ---------------------------------------------------------------------------

class TestFeedbackOverrideRemoved:
    """Verify override can be cleared — RAG returns dict results only."""

    def test_feedback_override_removed(self, rag, tmp_feedback):
        # Inject temp store (empty)
        rag._feedback = tmp_feedback

        pack = rag.retrieve("gam")

        # No feedback overrides in empty store
        feedback_ev = [e for e in pack.vocabulary if e.source == "feedback"]
        assert len(feedback_ev) == 0, "Expected no feedback overrides in empty store"

        # Should still have dictionary evidence
        dict_ev = [e for e in pack.vocabulary if e.source == "dictionary"]
        assert len(dict_ev) > 0

        # Clean up
        rag._feedback = None


# ---------------------------------------------------------------------------
# 9. Pipeline latency
# ---------------------------------------------------------------------------

class TestPipelineLatency:
    """Full retrieve + to_prompt in < 5 seconds."""

    def test_pipeline_latency(self, rag):
        start = time.monotonic()
        pack = rag.retrieve("pasian")
        prompt = pack.to_prompt()
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"Pipeline took {elapsed:.1f}s (>5s limit)"
        assert len(prompt) > 0

    def test_pipeline_latency_english(self, rag):
        start = time.monotonic()
        pack = rag.retrieve("God")
        prompt = pack.to_prompt()
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"Pipeline took {elapsed:.1f}s (>5s limit)"

    def test_pipeline_latency_sentence(self, rag):
        start = time.monotonic()
        pack = rag.retrieve("Ka pai kei hi")
        prompt = pack.to_prompt()
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"Pipeline took {elapsed:.1f}s (>5s limit)"


# ---------------------------------------------------------------------------
# 10. Empty query handled
# ---------------------------------------------------------------------------

class TestEmptyQueryHandled:
    """Empty string doesn't crash the pipeline."""

    def test_empty_query_handled(self, rag):
        pack = rag.retrieve("")
        assert isinstance(pack, EvidencePack)
        assert pack.query == ""
        assert pack.total() == 0

        # to_prompt on empty pack should return empty string
        prompt = pack.to_prompt()
        assert prompt == ""

    def test_whitespace_query_handled(self, rag):
        pack = rag.retrieve("   ")
        assert isinstance(pack, EvidencePack)
        assert pack.total() == 0
