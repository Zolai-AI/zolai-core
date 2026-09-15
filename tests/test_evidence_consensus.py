"""Tests for Foundation Evidence and Consensus modules."""
from __future__ import annotations

from zolai.foundation import (
    Candidate,
    Confidence,
    Evidence,
    EvidenceThresholdVerifier,
    EvidenceTier,
    NullVerifier,
    adaptive_consensus,
    build_candidates_from_evidence,
    majority_vote,
    run_consensus,
    threshold_gate_consensus,
    weighted_evidence_consensus,
)


class TestEvidence:
    """Test Evidence dataclass and factory."""

    def test_evidence_creation(self) -> None:
        """Test Evidence factory creates proper provenance hash."""
        evidence = Evidence.create(
            fact_type="word",
            fact_key="word:pasian",
            tier=EvidenceTier.BIBLE_PARALLEL,
            source="bible_verses",
            confidence=0.95,
            payload={"verse": "GEN 1:1", "zolai": "Pasian"},
        )

        assert evidence.fact_type == "word"
        assert evidence.fact_key == "word:pasian"
        assert evidence.tier == EvidenceTier.BIBLE_PARALLEL
        assert evidence.source == "bible_verses"
        assert evidence.confidence == 0.95
        assert len(evidence.provenance_hash) == 16  # SHA256 truncated

    def test_evidence_tier_weights(self) -> None:
        """Test evidence tier weights are correct."""
        assert EvidenceTier.BIBLE_PARALLEL.weight() == 1.0
        assert EvidenceTier.DICTIONARY.weight() == 0.9
        assert EvidenceTier.GRAMMAR_PATTERNS.weight() == 0.8
        assert EvidenceTier.CORPUS_ATTESTATION.weight() == 0.7
        assert EvidenceTier.LLM_GENERATION.weight() == 0.4


class TestCandidate:
    """Test Candidate dataclass."""

    def test_candidate_aggregate_confidence(self) -> None:
        """Test candidate aggregate confidence calculation."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {"verse": "GEN 1:1"})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {"entry": "pasian"})

        candidate = Candidate(
            fact_type="word",
            fact_key="word:pasian",
            value={"form": "pasian", "pos": "N.PROPER"},
            evidence=(e1, e2),
            source="analyzer",
        )

        # Weighted: (1.0 * 0.95 + 0.9 * 0.90) / (1.0 + 0.9) = 1.76 / 1.9 ≈ 0.926
        conf = candidate.aggregate_confidence()
        assert 0.92 < conf < 0.93

    def test_candidate_evidence_by_tier(self) -> None:
        """Test grouping evidence by tier."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        candidate = Candidate(
            fact_type="word",
            fact_key="word:pasian",
            value={"form": "pasian"},
            evidence=(e1, e2),
            source="analyzer",
        )

        by_tier = candidate.evidence_by_tier()
        assert EvidenceTier.BIBLE_PARALLEL in by_tier
        assert EvidenceTier.DICTIONARY in by_tier
        assert len(by_tier[EvidenceTier.BIBLE_PARALLEL]) == 1
        assert len(by_tier[EvidenceTier.DICTIONARY]) == 1

    def test_candidate_has_tier(self) -> None:
        """Test checking for tier presence."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})

        candidate = Candidate(
            fact_type="word",
            fact_key="word:pasian",
            value={"form": "pasian"},
            evidence=(e1,),
            source="analyzer",
        )

        assert candidate.has_tier(EvidenceTier.BIBLE_PARALLEL) is True
        assert candidate.has_tier(EvidenceTier.DICTIONARY) is False


class TestConfidence:
    """Test Confidence assessment."""

    def test_confidence_from_candidate(self) -> None:
        """Test Confidence factory from candidate."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        candidate = Candidate(
            fact_type="word",
            fact_key="word:pasian",
            value={"form": "pasian"},
            evidence=(e1, e2),
            source="analyzer",
        )

        confidence = Confidence.from_candidate(candidate, threshold=0.7)

        assert confidence.overall > 0.9
        assert confidence.threshold_met is True
        assert confidence.threshold == 0.7
        assert EvidenceTier.BIBLE_PARALLEL in confidence.by_tier
        assert EvidenceTier.DICTIONARY in confidence.by_tier


class TestVerifiers:
    """Test Verifier implementations."""

    def test_null_verifier(self) -> None:
        """Test NullVerifier always passes with neutral confidence."""
        verifier = NullVerifier()
        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={"form": "test"},
            evidence=(),
            source="test",
        )

        passed, conf, notes = verifier.verify(candidate)
        assert passed is True
        assert conf == 0.5
        assert "null_verifier" in notes

        results = verifier.verify_batch([candidate, candidate])
        assert len(results) == 2
        assert all(r[0] is True for r in results)

    def test_evidence_threshold_verifier_pass(self) -> None:
        """Test EvidenceThresholdVerifier passes with sufficient evidence."""
        verifier = EvidenceThresholdVerifier(
            required_tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY],
            min_tier_count=2,
            min_confidence=0.7,
        )

        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        candidate = Candidate(
            fact_type="word",
            fact_key="word:pasian",
            value={"form": "pasian"},
            evidence=(e1, e2),
            source="analyzer",
        )

        passed, conf, notes = verifier.verify(candidate)
        assert passed is True
        assert conf > 0.9

    def test_evidence_threshold_verifier_fail_missing_tier(self) -> None:
        """Test EvidenceThresholdVerifier fails with missing required tier."""
        verifier = EvidenceThresholdVerifier(
            required_tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY],
            min_tier_count=2,
            min_confidence=0.7,
        )

        e1 = Evidence.create("word", "word:test", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        candidate = Candidate(
            fact_type="word",
            fact_key="word:test",
            value={"form": "test"},
            evidence=(e1,),
            source="analyzer",
        )

        passed, conf, notes = verifier.verify(candidate)
        assert passed is False


class TestConsensus:
    """Test consensus algorithms."""

    def make_candidates(self, values: list[dict], tier: EvidenceTier = EvidenceTier.DICTIONARY) -> list[Candidate]:
        """Helper to create candidates with evidence."""
        candidates = []
        for i, value in enumerate(values):
            e = Evidence.create("word", "word:test", tier, f"source_{i}", 0.9, {"data": value})
            c = Candidate(
                fact_type="word",
                fact_key="word:test",
                value=value,
                evidence=(e,),
                source="test",
            )
            candidates.append(c)
        return candidates

    def test_majority_vote_clear_winner(self) -> None:
        """Test majority vote with clear winner."""
        candidates = self.make_candidates([
            {"form": "pasian", "pos": "N.PROPER"},
            {"form": "pasian", "pos": "N.PROPER"},
            {"form": "pathian", "pos": "N.PROPER"},  # Wrong
        ])

        result = majority_vote(candidates, threshold=0.5)

        assert result.decision["form"] == "pasian"
        assert result.agreeing_candidates == 2
        assert result.confidence > 0.8

    def test_majority_vote_no_clear_winner(self) -> None:
        """Test majority vote with split votes."""
        candidates = self.make_candidates([
            {"form": "pasian", "pos": "N.PROPER"},
            {"form": "pathian", "pos": "N.PROPER"},
            {"form": "pasian2", "pos": "N.PROPER"},
        ])

        result = majority_vote(candidates, threshold=0.5)

        # First one wins (tie-break by order)
        assert result.agreeing_candidates == 1
        assert "below threshold" in " ".join(result.notes)

    def test_weighted_evidence_consensus(self) -> None:
        """Test weighted evidence consensus picks highest weight."""
        e1 = Evidence.create("word", "word:test", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})
        e2 = Evidence.create("word", "word:test", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        # Candidate with Bible evidence should win
        c1 = Candidate(fact_type="word", fact_key="word:test",
                      value={"form": "pasian"}, evidence=(e1, e2), source="test")
        c2 = Candidate(fact_type="word", fact_key="word:test",
                      value={"form": "pathian"}, evidence=(e2,), source="test")  # Only dict

        result = weighted_evidence_consensus([c1, c2], threshold=0.7)

        assert result.decision["form"] == "pasian"
        assert result.method == "weighted_evidence"

    def test_threshold_gate_consensus_pass(self) -> None:
        """Test threshold gate passes qualified candidate."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        c1 = Candidate(fact_type="word", fact_key="word:pasian",
                      value={"form": "pasian"}, evidence=(e1, e2), source="test")

        result = threshold_gate_consensus([c1], threshold=0.7)

        assert result.decision["form"] == "pasian"
        assert result.confidence > 0.9

    def test_threshold_gate_consensus_fail_no_evidence(self) -> None:
        """Test threshold gate fails with no evidence."""
        c1 = Candidate(fact_type="word", fact_key="word:test",
                      value={"form": "test"}, evidence=(), source="test")

        result = threshold_gate_consensus([c1], threshold=0.7)

        assert result.decision == {}
        assert result.confidence == 0.0
        assert result.agreeing_candidates == 0

    def test_adaptive_consensus_word(self) -> None:
        """Test adaptive consensus for word type."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {})

        candidates = [
            Candidate(fact_type="word", fact_key="word:pasian",
                     value={"form": "pasian"}, evidence=(e1, e2), source="test"),
        ]

        result = adaptive_consensus(candidates, "word")

        assert result.decision["form"] == "pasian"

    def test_adaptive_consensus_paragraph_flags_review(self) -> None:
        """Test adaptive consensus flags paragraph for human review if low confidence."""
        e1 = Evidence.create("paragraph", "paragraph:test", EvidenceTier.DICTIONARY,
                           "dictionary", 0.6, {})

        candidates = [
            Candidate(fact_type="paragraph", fact_key="paragraph:test",
                     value={"text": "test"}, evidence=(e1,), source="test"),
        ]

        result = adaptive_consensus(candidates, "paragraph")

        assert "FLAG_FOR_HUMAN_REVIEW" in " ".join(result.notes)

    def test_run_consensus_dispatches(self) -> None:
        """Test run_consensus dispatches to adaptive_consensus."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {})

        candidates = [
            Candidate(fact_type="word", fact_key="word:pasian",
                     value={"form": "pasian"}, evidence=(e1,), source="test"),
        ]

        result = run_consensus(candidates)

        assert result.decision["form"] == "pasian"


class TestBuildCandidatesFromEvidence:
    """Test candidate building from evidence groups."""

    def test_build_candidates(self) -> None:
        """Test building candidates from evidence groups."""
        e1 = Evidence.create("word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                           "bible_verses", 0.95, {"verse": "GEN 1:1"})
        e2 = Evidence.create("word", "word:pasian", EvidenceTier.DICTIONARY,
                           "dictionary", 0.90, {"entry": "pasian"})

        # Use JSON string as key (dict not hashable)
        import json
        evidence_groups = {
            json.dumps({"form": "pasian", "pos": "N.PROPER"}, sort_keys=True): [e1, e2],
            json.dumps({"form": "pathian", "pos": "N.PROPER"}, sort_keys=True): [e2],
        }

        candidates = build_candidates_from_evidence("word", "word:pasian", evidence_groups)

        assert len(candidates) == 2
        assert all(isinstance(c, Candidate) for c in candidates)
        assert candidates[0].fact_type == "word"
        assert candidates[0].fact_key == "word:pasian"
