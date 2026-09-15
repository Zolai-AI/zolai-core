"""Tests for evidence gating pre-check."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from zolai.foundation.evidence import Candidate, Evidence, EvidenceTier
from zolai.foundation.verifiers import EvidenceGateError, EvidenceGatingVerifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate_with_tiers(*tiers: EvidenceTier) -> Candidate:
    """Build a candidate with the given evidence tiers."""
    evidence = []
    for tier in tiers:
        evidence.append(
            Evidence.create(
                fact_type="word",
                fact_key="word:test",
                tier=tier,
                source=f"{tier.name.lower()}_table",
                confidence=0.85,
                payload={},
            )
        )
    return Candidate(
        fact_type="word",
        fact_key="word:test",
        value={"form": "test", "pos": "N"},
        evidence=tuple(evidence),
        source="test",
    )


# ---------------------------------------------------------------------------
# Gate logic tests
# ---------------------------------------------------------------------------


class TestEvidenceGateLogic:
    """Test the core evidence gate logic."""

    def test_min_tiers_2_with_2_tiers(self) -> None:
        """Gate passes with exactly 2 distinct tiers."""
        candidate = _make_candidate_with_tiers(
            EvidenceTier.BIBLE_PARALLEL,
            EvidenceTier.DICTIONARY,
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, notes = gate.verify(candidate)
        assert passed is True
        assert "2 tiers" in notes

    def test_min_tiers_2_with_1_tier(self) -> None:
        """Gate fails with only 1 tier."""
        candidate = _make_candidate_with_tiers(EvidenceTier.BIBLE_PARALLEL)
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, notes = gate.verify(candidate)
        assert passed is False
        assert "Insufficient evidence tiers" in notes

    def test_min_tiers_3_with_2_tiers(self) -> None:
        """Gate fails when min_tiers=3 but only 2 present."""
        candidate = _make_candidate_with_tiers(
            EvidenceTier.BIBLE_PARALLEL,
            EvidenceTier.DICTIONARY,
        )
        gate = EvidenceGatingVerifier(min_tiers=3)
        passed, _, notes = gate.verify(candidate)
        assert passed is False
        assert "2 < 3" in notes

    def test_min_tiers_1_with_1_tier(self) -> None:
        """Gate passes with min_tiers=1 and 1 tier present."""
        candidate = _make_candidate_with_tiers(EvidenceTier.LLM_GENERATION)
        gate = EvidenceGatingVerifier(min_tiers=1)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_duplicate_tiers_count_once(self) -> None:
        """Same tier appearing twice should count as 1 distinct tier."""
        evidence = [
            Evidence.create(
                "word", "word:test", EvidenceTier.BIBLE_PARALLEL,
                "bible", 0.9, {},
            ),
            Evidence.create(
                "word", "word:test", EvidenceTier.BIBLE_PARALLEL,
                "bible_v2", 0.85, {},
            ),
        ]
        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={"form": "test"},
            evidence=tuple(evidence),
            source="test",
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, notes = gate.verify(candidate)
        assert passed is False
        assert "1 < 2" in notes


# ---------------------------------------------------------------------------
# Confidence threshold tests
# ---------------------------------------------------------------------------


class TestConfidenceThreshold:
    """Test minimum confidence gating."""

    def test_high_confidence_passes(self) -> None:
        candidate = _make_candidate_with_tiers(
            EvidenceTier.BIBLE_PARALLEL,
            EvidenceTier.DICTIONARY,
        )
        gate = EvidenceGatingVerifier(min_tiers=2, min_confidence=0.5)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_low_confidence_fails(self) -> None:
        """Low confidence evidence should fail the gate."""
        evidence = [
            Evidence.create(
                "word", "word:test", EvidenceTier.LLM_GENERATION,
                "llm", 0.2, {},
            ),
            Evidence.create(
                "word", "word:test", EvidenceTier.CORPUS_ATTESTATION,
                "corpus", 0.3, {},
            ),
        ]
        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={"form": "test"},
            evidence=tuple(evidence),
            source="test",
        )
        gate = EvidenceGatingVerifier(min_tiers=2, min_confidence=0.5)
        passed, _, notes = gate.verify(candidate)
        assert passed is False
        assert "Aggregate confidence" in notes


# ---------------------------------------------------------------------------
# Integration with inner verifier
# ---------------------------------------------------------------------------


class TestGateWithInnerVerifier:
    """Test gate + inner verifier delegation."""

    def test_gate_passes_inner_called(self) -> None:
        """When gate passes, inner verifier should be called."""
        mock_inner = MagicMock()
        mock_inner.verify.return_value = (True, 0.9, "inner_ok")
        mock_inner.name.return_value = "MockVerifier"

        candidate = _make_candidate_with_tiers(
            EvidenceTier.BIBLE_PARALLEL,
            EvidenceTier.DICTIONARY,
        )
        gate = EvidenceGatingVerifier(inner=mock_inner, min_tiers=2)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is True
        assert confidence == 0.9
        mock_inner.verify.assert_called_once()

    def test_gate_fails_inner_not_called(self) -> None:
        """When gate fails, inner verifier should NOT be called."""
        mock_inner = MagicMock()
        mock_inner.verify.return_value = (True, 0.9, "should_not_reach")

        candidate = _make_candidate_with_tiers(EvidenceTier.LLM_GENERATION)
        gate = EvidenceGatingVerifier(inner=mock_inner, min_tiers=2)
        passed, _, notes = gate.verify(candidate)

        assert passed is False
        assert "Insufficient" in notes
        mock_inner.verify.assert_not_called()


# ---------------------------------------------------------------------------
# EvidenceGateError tests
# ---------------------------------------------------------------------------


class TestEvidenceGateError:
    """Test the EvidenceGateError exception."""

    def test_can_raise(self) -> None:
        with pytest.raises(EvidenceGateError):
            raise EvidenceGateError("test gate error")

    def test_message_preserved(self) -> None:
        try:
            raise EvidenceGateError("custom message")
        except EvidenceGateError as e:
            assert str(e) == "custom message"
