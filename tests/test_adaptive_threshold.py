"""Tests for adaptive verification thresholds."""
from __future__ import annotations

from zolai.foundation.evidence import Candidate, Evidence, EvidenceTier
from zolai.foundation.verifiers import EvidenceGatingVerifier

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _candidate_with_confidence(confidence: float) -> Candidate:
    """Build a candidate with a specific aggregate confidence."""
    evidence = Evidence.create(
        fact_type="word",
        fact_key="word:test",
        tier=EvidenceTier.BIBLE_PARALLEL,
        source="bible",
        confidence=confidence,
        payload={},
    )
    return Candidate(
        fact_type="word",
        fact_key="word:test",
        value={"form": "test"},
        evidence=(evidence,),
        source="test",
    )


def _candidate_with_tiers(
    tiers: list[EvidenceTier],
    confidences: list[float] | None = None,
) -> Candidate:
    """Build a candidate with specific tiers and optional per-tier confidences."""
    if confidences is None:
        confidences = [0.9] * len(tiers)

    evidence = []
    for tier, conf in zip(tiers, confidences):
        evidence.append(
            Evidence.create(
                fact_type="word",
                fact_key="word:test",
                tier=tier,
                source=f"{tier.name.lower()}",
                confidence=conf,
                payload={},
            )
        )

    return Candidate(
        fact_type="word",
        fact_key="word:test",
        value={"form": "test"},
        evidence=tuple(evidence),
        source="test",
    )


# ---------------------------------------------------------------------------
# Adaptive threshold tests
# ---------------------------------------------------------------------------


class TestAdaptiveThresholds:
    """Test the 3-tier adaptive verification thresholds."""

    def test_tier1_auto_promote(self) -> None:
        """Tier 1: confidence ≥ 0.95 should auto-promote."""
        candidate = _candidate_with_confidence(0.95)
        gate = EvidenceGatingVerifier(min_tiers=1, min_confidence=0.95)
        passed, confidence, notes = gate.verify(candidate)

        # With min_confidence=0.95, this should pass the gate
        assert passed is True
        assert confidence >= 0.95

    def test_tier2_batch_verify(self) -> None:
        """Tier 2: 0.70 ≤ confidence < 0.95 should need batch verify."""
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY],
            confidences=[0.85, 0.80],
        )

        # With default min_confidence=0.5, the gate should pass
        gate = EvidenceGatingVerifier(min_tiers=2, min_confidence=0.5)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is True
        assert 0.5 <= confidence < 0.95

    def test_tier3_queue_for_review(self) -> None:
        """Tier 3: confidence < 0.70 should queue for review."""
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.LLM_GENERATION],
            confidences=[0.30],
        )

        # Gate should fail with min_confidence=0.7
        gate = EvidenceGatingVerifier(min_tiers=1, min_confidence=0.7)
        passed, confidence, notes = gate.verify(candidate)

        assert passed is False
        assert confidence < 0.70

    def test_threshold_boundary_095(self) -> None:
        """Test exact boundary at 0.95."""
        candidate = _candidate_with_confidence(0.95)
        gate = EvidenceGatingVerifier(min_tiers=1, min_confidence=0.95)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_threshold_boundary_070(self) -> None:
        """Test exact boundary at 0.70."""
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY],
            confidences=[0.70, 0.70],
        )
        gate = EvidenceGatingVerifier(min_tiers=2, min_confidence=0.70)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_threshold_below_boundary(self) -> None:
        """Test just below boundary."""
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY],
            confidences=[0.69, 0.69],
        )
        gate = EvidenceGatingVerifier(min_tiers=2, min_confidence=0.70)
        passed, _, _ = gate.verify(candidate)
        assert passed is False


# ---------------------------------------------------------------------------
# Tier combination tests
# ---------------------------------------------------------------------------


class TestTierCombinations:
    """Test various evidence tier combinations."""

    def test_bible_dictionary_passes(self) -> None:
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY],
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_dictionary_corpus_passes(self) -> None:
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.DICTIONARY, EvidenceTier.CORPUS_ATTESTATION],
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_grammar_bible_passes(self) -> None:
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.GRAMMAR_PATTERNS, EvidenceTier.BIBLE_PARALLEL],
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_llm_only_fails(self) -> None:
        candidate = _candidate_with_tiers(
            tiers=[EvidenceTier.LLM_GENERATION],
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, _ = gate.verify(candidate)
        assert passed is False

    def test_three_tiers_passes(self) -> None:
        candidate = _candidate_with_tiers(
            tiers=[
                EvidenceTier.BIBLE_PARALLEL,
                EvidenceTier.DICTIONARY,
                EvidenceTier.GRAMMAR_PATTERNS,
            ],
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, _ = gate.verify(candidate)
        assert passed is True

    def test_all_five_tiers_passes(self) -> None:
        candidate = _candidate_with_tiers(
            tiers=[
                EvidenceTier.BIBLE_PARALLEL,
                EvidenceTier.DICTIONARY,
                EvidenceTier.GRAMMAR_PATTERNS,
                EvidenceTier.CORPUS_ATTESTATION,
                EvidenceTier.LLM_GENERATION,
            ],
        )
        gate = EvidenceGatingVerifier(min_tiers=2)
        passed, _, _ = gate.verify(candidate)
        assert passed is True
