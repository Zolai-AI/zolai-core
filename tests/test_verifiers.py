"""Tests for GeminiVerifier and EvidenceGatingVerifier."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zolai.foundation.evidence import Candidate, Evidence, EvidenceTier
from zolai.foundation.verifiers import (
    EvidenceGateError,
    EvidenceGatingVerifier,
    GeminiVerifier,
    ModelRouter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_candidate(
    fact_type: str = "word",
    fact_key: str = "word:pasian",
    tiers: tuple[EvidenceTier, ...] = (EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY),
    confidence: float = 0.9,
) -> Candidate:
    """Build a minimal Candidate for testing."""
    evidence = []
    for tier in tiers:
        evidence.append(
            Evidence.create(
                fact_type=fact_type,
                fact_key=fact_key,
                tier=tier,
                source=f"{tier.name.lower()}_table",
                confidence=confidence,
                payload={"test": True},
            )
        )
    return Candidate(
        fact_type=fact_type,
        fact_key=fact_key,
        value={"form": "pasian", "pos": "N.PROPER"},
        evidence=tuple(evidence),
        source="test",
    )


# ---------------------------------------------------------------------------
# ModelRouter tests
# ---------------------------------------------------------------------------


class TestModelRouter:
    """Test ModelRouter routes correctly."""

    def test_route_word(self) -> None:
        router = ModelRouter()
        model = router.route(fact_type="word")
        assert model.name  # should have a name

    def test_route_grammar(self) -> None:
        router = ModelRouter()
        model = router.route(fact_type="grammar")
        assert model.name

    def test_route_sentence(self) -> None:
        router = ModelRouter()
        model = router.route(fact_type="sentence")
        assert model.name


# ---------------------------------------------------------------------------
# GeminiVerifier tests
# ---------------------------------------------------------------------------


class TestGeminiVerifier:
    """Test GeminiVerifier with mocked client."""

    def test_verify_passes(self) -> None:
        """Test that a passing verification returns (True, confidence, notes)."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.confidence = 0.92
        mock_result.evidence_assessment = ["Word matches Bible attestation"]
        mock_result.disagreements = []
        mock_result.requires_human_review = False

        mock_client.generate_structured = AsyncMock(return_value=mock_result)

        verifier = GeminiVerifier(client=mock_client)
        candidate = _make_candidate()

        passed, confidence, notes = verifier.verify(candidate)

        assert passed is True
        assert confidence == 0.92
        assert "gemini_verified" in notes or "assessment" in notes

    def test_verify_fails(self) -> None:
        """Test that a failing verification returns (False, ...)."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_valid = False
        mock_result.confidence = 0.3
        mock_result.evidence_assessment = ["Word not found in corpus"]
        mock_result.disagreements = ["Dictionary and Bible conflict"]
        mock_result.requires_human_review = True

        mock_client.generate_structured = AsyncMock(return_value=mock_result)

        verifier = GeminiVerifier(client=mock_client)
        candidate = _make_candidate()

        passed, confidence, notes = verifier.verify(candidate)

        assert passed is False
        assert confidence == 0.3
        assert "disagreements" in notes

    def test_verify_client_error(self) -> None:
        """Test graceful handling of client errors."""
        mock_client = AsyncMock()
        mock_client.generate_structured = AsyncMock(
            side_effect=RuntimeError("API key invalid")
        )

        verifier = GeminiVerifier(client=mock_client)
        candidate = _make_candidate()

        passed, confidence, notes = verifier.verify(candidate)

        assert passed is False
        assert confidence == 0.0
        assert "gemini_error" in notes

    def test_verify_no_client_raises(self) -> None:
        """Test that missing client raises RuntimeError."""
        verifier = GeminiVerifier(client=None)
        candidate = _make_candidate()

        with pytest.raises(RuntimeError, match="GeminiClient not injected"):
            verifier.verify(candidate)

    def test_name(self) -> None:
        verifier = GeminiVerifier(client=MagicMock())
        assert verifier.name() == "GeminiVerifier"

    def test_evidence_summary(self) -> None:
        verifier = GeminiVerifier(client=MagicMock())
        candidate = _make_candidate()
        summary = verifier._evidence_summary(candidate)
        assert "BIBLE_PARALLEL" in summary
        assert "DICTIONARY" in summary

    def test_evidence_summary_empty(self) -> None:
        verifier = GeminiVerifier(client=MagicMock())
        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={},
            evidence=(),
            source="test",
        )
        summary = verifier._evidence_summary(candidate)
        assert "no evidence" in summary


# ---------------------------------------------------------------------------
# EvidenceGatingVerifier tests
# ---------------------------------------------------------------------------


class TestEvidenceGatingVerifier:
    """Test EvidenceGatingVerifier gate logic."""

    def test_gate_passes_with_sufficient_tiers(self) -> None:
        """Test gate passes with ≥2 tiers."""
        candidate = _make_candidate(
            tiers=(EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY)
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is True
        assert confidence > 0
        assert "Gate passed" in notes

    def test_gate_fails_with_insufficient_tiers(self) -> None:
        """Test gate fails with <2 tiers."""
        candidate = _make_candidate(
            tiers=(EvidenceTier.LLM_GENERATION,)
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is False
        assert "Insufficient evidence tiers" in notes

    def test_gate_fails_with_no_evidence(self) -> None:
        """Test gate fails with no evidence."""
        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={},
            evidence=(),
            source="test",
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is False
        assert confidence == 0.0

    def test_gate_fails_low_confidence(self) -> None:
        """Test gate fails when aggregate confidence is below threshold."""
        evidence = Evidence.create(
            fact_type="word",
            fact_key="word:test",
            tier=EvidenceTier.LLM_GENERATION,
            source="llm",
            confidence=0.3,
            payload={},
        )
        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={},
            evidence=(evidence,),
            source="test",
        )
        gate = EvidenceGatingVerifier(min_tiers=1, min_confidence=0.7)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is False
        assert "Aggregate confidence" in notes

    def test_gate_delegates_to_inner_verifier(self) -> None:
        """Test that passing gate delegates to inner verifier."""
        mock_inner = MagicMock()
        mock_inner.verify.return_value = (True, 0.95, "inner_pass")
        mock_inner.name.return_value = "MockVerifier"

        candidate = _make_candidate(
            tiers=(EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY)
        )
        gate = EvidenceGatingVerifier(inner=mock_inner, min_tiers=2)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is True
        assert confidence == 0.95
        assert notes == "inner_pass"
        mock_inner.verify.assert_called_once_with(candidate)

    def test_gate_no_inner_returns_gate_result(self) -> None:
        """Test gate without inner verifier returns gate result."""
        candidate = _make_candidate(
            tiers=(EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY)
        )
        gate = EvidenceGatingVerifier(inner=None, min_tiers=2)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is True
        assert "Gate passed" in notes

    def test_name(self) -> None:
        gate = EvidenceGatingVerifier()
        assert gate.name() == "EvidenceGatingVerifier"

    def test_verify_batch(self) -> None:
        """Test batch verification."""
        candidates = [
            _make_candidate(tiers=(EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY)),
            _make_candidate(
                fact_key="word:test",
                tiers=(EvidenceTier.LLM_GENERATION,),
            ),
        ]
        gate = EvidenceGatingVerifier(min_tiers=2)
        results = gate.verify_batch(candidates)

        assert len(results) == 2
        assert results[0][0] is True   # first passes
        assert results[1][0] is False  # second fails (only 1 tier)


# ---------------------------------------------------------------------------
# EvidenceGateError tests
# ---------------------------------------------------------------------------


class TestEvidenceGateError:
    """Test EvidenceGateError exception."""

    def test_error_message(self) -> None:
        err = EvidenceGateError("test message")
        assert str(err) == "test message"

    def test_is_exception(self) -> None:
        assert issubclass(EvidenceGateError, Exception)
