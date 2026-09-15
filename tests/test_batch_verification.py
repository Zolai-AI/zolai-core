"""Tests for batch verification flows."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from zolai.foundation.evidence import Candidate, Evidence, EvidenceTier
from zolai.foundation.verifiers import EvidenceGatingVerifier, GeminiVerifier

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_candidates(
    count: int = 3,
    tiers: tuple[EvidenceTier, ...] = (EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY),
) -> list[Candidate]:
    """Build a list of test candidates."""
    candidates = []
    for i in range(count):
        evidence = []
        for tier in tiers:
            evidence.append(
                Evidence.create(
                    fact_type="word",
                    fact_key=f"word:word_{i}",
                    tier=tier,
                    source=f"{tier.name.lower()}_table",
                    confidence=0.85,
                    payload={"index": i},
                )
            )
        candidates.append(
            Candidate(
                fact_type="word",
                fact_key=f"word:word_{i}",
                value={"form": f"word_{i}", "pos": "N"},
                evidence=tuple(evidence),
                source="test",
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# GeminiVerifier batch tests
# ---------------------------------------------------------------------------


class TestGeminiVerifierBatch:
    """Test GeminiVerifier batch verification."""

    def test_verify_batch_with_mocked_client(self) -> None:
        """Test batch verification with mocked client."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.confidence = 0.88
        mock_result.evidence_assessment = ["All good"]
        mock_result.disagreements = []
        mock_result.requires_human_review = False
        mock_client.generate_structured = AsyncMock(return_value=mock_result)

        verifier = GeminiVerifier(client=mock_client)
        candidates = _make_candidates(count=5)

        results = verifier.verify_batch(candidates)

        assert len(results) == 5
        for passed, confidence, notes in results:
            assert passed is True
            assert confidence == 0.88


# ---------------------------------------------------------------------------
# EvidenceGatingVerifier batch tests
# ---------------------------------------------------------------------------


class TestEvidenceGatingVerifierBatch:
    """Test EvidenceGatingVerifier batch verification."""

    def test_batch_filters_low_evidence(self) -> None:
        """Test batch filters candidates with insufficient evidence."""
        candidates = _make_candidates(count=5)

        # Replace last 2 candidates with single-tier evidence
        for i in range(3, 5):
            evidence = Evidence.create(
                fact_type="word",
                fact_key=f"word:word_{i}",
                tier=EvidenceTier.LLM_GENERATION,
                source="llm",
                confidence=0.6,
                payload={},
            )
            candidates[i] = Candidate(
                fact_type="word",
                fact_key=f"word:word_{i}",
                value={"form": f"word_{i}"},
                evidence=(evidence,),
                source="test",
            )

        gate = EvidenceGatingVerifier(min_tiers=2)
        results = gate.verify_batch(candidates)

        assert len(results) == 5
        # First 3 should pass (2 tiers), last 2 should fail (1 tier)
        assert sum(1 for r in results if r[0]) == 3
        assert sum(1 for r in results if not r[0]) == 2

    def test_batch_empty(self) -> None:
        """Test batch with empty list."""
        gate = EvidenceGatingVerifier(min_tiers=2)
        results = gate.verify_batch([])
        assert results == []

    def test_batch_single_candidate(self) -> None:
        """Test batch with single candidate."""
        candidates = _make_candidates(count=1)
        gate = EvidenceGatingVerifier(min_tiers=2)
        results = gate.verify_batch(candidates)
        assert len(results) == 1
        assert results[0][0] is True


# ---------------------------------------------------------------------------
# Mixed batch tests
# ---------------------------------------------------------------------------


class TestMixedBatch:
    """Test mixed batches with varying evidence quality."""

    def test_mixed_evidence_tiers(self) -> None:
        """Test candidates with different tier combinations."""
        # Candidate 1: strong evidence (Bible + Dictionary)
        c1 = Candidate(
            fact_type="word",
            fact_key="word:pasian",
            value={"form": "pasian"},
            evidence=(
                Evidence.create(
                    "word", "word:pasian", EvidenceTier.BIBLE_PARALLEL,
                    "bible", 0.95, {"verse": "GEN 1:1"},
                ),
                Evidence.create(
                    "word", "word:pasian", EvidenceTier.DICTIONARY,
                    "dict", 0.90, {"entry": "pasian"},
                ),
            ),
            source="test",
        )

        # Candidate 2: medium evidence (Dictionary + Corpus)
        c2 = Candidate(
            fact_type="word",
            fact_key="word:gam",
            value={"form": "gam"},
            evidence=(
                Evidence.create(
                    "word", "word:gam", EvidenceTier.DICTIONARY,
                    "dict", 0.85, {"entry": "gam"},
                ),
                Evidence.create(
                    "word", "word:gam", EvidenceTier.CORPUS_ATTESTATION,
                    "corpus", 0.75, {"freq": 10},
                ),
            ),
            source="test",
        )

        # Candidate 3: weak evidence (LLM only)
        c3 = Candidate(
            fact_type="word",
            fact_key="word:unknown",
            value={"form": "unknown"},
            evidence=(
                Evidence.create(
                    "word", "word:unknown", EvidenceTier.LLM_GENERATION,
                    "llm", 0.60, {"prompt": "verify"},
                ),
            ),
            source="test",
        )

        gate = EvidenceGatingVerifier(min_tiers=2)
        results = gate.verify_batch([c1, c2, c3])

        assert results[0][0] is True   # Bible + Dictionary
        assert results[1][0] is True   # Dictionary + Corpus
        assert results[2][0] is False  # LLM only
