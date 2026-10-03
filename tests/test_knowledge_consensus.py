"""Tests for the Phase 4 consensus integration (Master Prompt §36).

Covers the claim-level adapter over ``zolai.foundation.consensus``:
- only claims with ≥ 2 linked ``claim_evidence`` rows are reported
- candidates are grouped per distinct evidence source
- tier-weighted aggregate confidence + per-tier breakdown, rounded to 2 dp
- the contract-canonical ``confidence_from_evidence`` value is reported alongside
- explicit ``claim_ids``, unknown method rejection, missing claims, and the
  read-only guarantee (no status / confidence / audit writes)

Isolation: a temporary SQLite database built from the ORM metadata — the tests
never read or write the live ``data/zolai.db``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.foundation.evidence import EvidenceTier
from zolai.knowledge.consensus import (
    CONSENSUS_METHODS,
    ClaimConsensus,
    build_consensus_candidate,
    claim_consensus_fact_type,
    compute_claim_consensus,
    load_claim_evidence,
    select_consensus_claim_ids,
    tier_breakdown,
)
from zolai.shared.contracts import KnowledgeStatus, confidence_from_evidence

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

NOW = "2026-10-03T00:00:00+00:00"


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Temporary SQLite DB with the full ORM schema (no live-DB dependency)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'consensus.db'}")
    mgr.init_db()
    try:
        yield mgr.engine
    finally:
        mgr.dispose()


def add_evidence(
    engine: Engine,
    *,
    fact_key: str,
    tier: EvidenceTier,
    source: str,
    confidence: float = 0.9,
) -> int:
    """Insert one ``foundation_evidence`` row; return its id."""
    with engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "INSERT INTO foundation_evidence "
                    "(fact_type, fact_key, tier, source, confidence, provenance_hash, "
                    "payload, created_at) "
                    "VALUES ('word', :fact_key, :tier, :source, :confidence, 'h', '{}', :now)"
                ),
                {
                    "fact_key": fact_key,
                    "tier": tier.value,
                    "source": source,
                    "confidence": confidence,
                    "now": NOW,
                },
            ).lastrowid
        )


def add_claim(
    engine: Engine,
    *,
    claim_type: str = "lexicon",
    subject: str = "word:pasian",
    predicate: str = "pos:NOUN",
    object_: str | None = None,
    evidence_ids: list[int],
    confidence: float = 0.9,
) -> int:
    """Insert one claim plus its ``claim_evidence`` links; return the claim id."""
    with engine.begin() as conn:
        claim_id = int(
            conn.execute(
                text(
                    "INSERT INTO knowledge_claims "
                    "(claim_type, subject, predicate, object, confidence, status, "
                    "evidence_ids, source_ids, notes, created_at, updated_at) "
                    "VALUES (:claim_type, :subject, :predicate, :object, :confidence, :status, "
                    ":evidence_ids, '[]', '', :now, :now)"
                ),
                {
                    "claim_type": claim_type,
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_,
                    "confidence": confidence,
                    "status": (
                        KnowledgeStatus.SUPPORTED.value
                        if evidence_ids
                        else KnowledgeStatus.CANDIDATE.value
                    ),
                    "evidence_ids": json.dumps(evidence_ids),
                    "now": NOW,
                },
            ).lastrowid
        )
        for evidence_id in evidence_ids:
            conn.execute(
                text(
                    "INSERT INTO claim_evidence (claim_id, evidence_id, role, created_at) "
                    "VALUES (:claim_id, :evidence_id, 'supports', :now)"
                ),
                {"claim_id": claim_id, "evidence_id": evidence_id, "now": NOW},
            )
    return claim_id


def two_source_claim(engine: Engine, **overrides) -> int:
    """A claim corroborated by a Bible-tier and a Dictionary-tier evidence row."""
    bible = add_evidence(
        engine, fact_key="word:pasian", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses", confidence=0.9
    )
    dictionary = add_evidence(
        engine, fact_key="word:pasian", tier=EvidenceTier.DICTIONARY, source="dictionary", confidence=0.8
    )
    return add_claim(engine, evidence_ids=[bible, dictionary], **overrides)


def snapshot(engine: Engine, table: str) -> list[dict]:
    """Full row dump of a table (used for the read-only assertion)."""
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()]


# ---------------------------------------------------------------------------
# Selection + candidate construction
# ---------------------------------------------------------------------------


class TestClaimSelection:
    def test_only_claims_with_two_evidence_rows_are_selected(self, engine: Engine) -> None:
        lone = add_evidence(engine, fact_key="word:a", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        add_claim(engine, subject="word:lone", predicate="pos:NOUN", evidence_ids=[lone])
        corroborated = two_source_claim(engine, subject="word:two")

        assert select_consensus_claim_ids(engine) == [corroborated]

    def test_claim_ids_bypass_the_default_selection(self, engine: Engine) -> None:
        """An explicit single-evidence claim is fetched but then not reported."""
        lone = add_evidence(engine, fact_key="word:a", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        lone_claim = add_claim(engine, subject="word:lone", predicate="pos:NOUN", evidence_ids=[lone])

        assert compute_claim_consensus(engine) == {}
        assert compute_claim_consensus(engine, claim_ids=[lone_claim], min_evidence_rows=1)

    def test_load_claim_evidence_returns_linked_rows(self, engine: Engine) -> None:
        claim_id = two_source_claim(engine, subject="word:load")
        rows = load_claim_evidence(engine, claim_id)
        assert [row["source"] for row in rows] == ["bible_verses", "dictionary"]
        assert rows[0]["role"] == "supports"

    def test_candidate_pools_all_linked_evidence(self, engine: Engine) -> None:
        """One candidate per claim, carrying every linked evidence row."""
        bible = add_evidence(engine, fact_key="word:x", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        second_bible = add_evidence(
            engine, fact_key="word:x", tier=EvidenceTier.CORPUS_ATTESTATION, source="bible_verses"
        )
        dictionary = add_evidence(engine, fact_key="word:x", tier=EvidenceTier.DICTIONARY, source="dictionary")
        claim_id = add_claim(
            engine, subject="word:group", predicate="pos:NOUN", evidence_ids=[bible, second_bible, dictionary]
        )

        with engine.connect() as conn:
            claim = dict(
                conn.execute(
                    text("SELECT * FROM knowledge_claims WHERE id = :id"), {"id": claim_id}
                ).first()._mapping
            )
        candidate = build_consensus_candidate(claim, load_claim_evidence(engine, claim_id))

        assert candidate is not None
        assert len(candidate.evidence) == 3
        assert candidate.fact_type == "word"
        assert candidate.fact_key == f"claim:{claim_id}"
        assert candidate.value == {
            "claim_type": "lexicon",
            "subject": "word:group",
            "predicate": "pos:NOUN",
            "object": None,
        }
        breakdown = tier_breakdown(candidate)
        assert breakdown["BIBLE_PARALLEL"]["count"] == 1
        assert breakdown["CORPUS_ATTESTATION"]["count"] == 1  # same source, different tier
        assert breakdown["DICTIONARY"]["count"] == 1
        assert sum(entry["count"] for entry in breakdown.values()) == 3

    def test_out_of_range_tier_evidence_is_dropped(self, engine: Engine) -> None:
        good = add_evidence(engine, fact_key="word:x", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        with engine.begin() as conn:
            bad = int(
                conn.execute(
                    text(
                        "INSERT INTO foundation_evidence (fact_type, fact_key, tier, source, "
                        "confidence, provenance_hash, payload, created_at) "
                        "VALUES ('word', 'word:x', 7, 'weird', 0.5, 'h', '{}', :now)"
                    ),
                    {"now": NOW},
                ).lastrowid
            )
        claim_id = add_claim(engine, subject="word:tiers", predicate="pos:NOUN", evidence_ids=[good, bad])
        candidate = build_consensus_candidate(
            {"id": claim_id, "claim_type": "lexicon", "subject": "word:tiers", "predicate": "pos:NOUN", "object": None},
            load_claim_evidence(engine, claim_id),
        )
        assert candidate is not None
        assert len(candidate.evidence) == 1

    def test_candidate_is_none_without_resolvable_evidence(self) -> None:
        claim = {"id": 1, "claim_type": "lexicon", "subject": "word:x", "predicate": "pos:NOUN", "object": None}
        assert build_consensus_candidate(claim, []) is None
        assert tier_breakdown.__doc__

    def test_fact_type_mapping(self) -> None:
        assert claim_consensus_fact_type("lexicon") == "word"
        assert claim_consensus_fact_type("morphology") == "word"
        assert claim_consensus_fact_type("grammar") == "grammar"
        assert claim_consensus_fact_type("unheard-of") == "unheard-of"


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------


class TestComputeClaimConsensus:
    def test_tier_weighted_confidence_and_breakdown(self, engine: Engine) -> None:
        claim_id = two_source_claim(engine, subject="word:pasian")
        results = compute_claim_consensus(engine)
        assert list(results) == [claim_id]

        result = results[claim_id]
        assert isinstance(result, ClaimConsensus)
        assert result.evidence_count == 2
        assert result.source_count == 2
        # confidence is the canonical tier-weighted aggregate (<= 2 dp), never hand-set.
        assert result.confidence == confidence_from_evidence(
            [EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY]
        )
        assert result.confidence == 0.95
        assert result.confidence == round(result.confidence, 2)
        assert result.agreement_score == 1.0
        assert set(result.tier_breakdown) == {"BIBLE_PARALLEL", "DICTIONARY"}
        assert result.tier_breakdown["BIBLE_PARALLEL"] == {
            "count": 1,
            "tier_weight": 1.0,
            "weight_total": 0.9,
        }
        assert result.tier_breakdown["DICTIONARY"]["weight_total"] == 0.72
        assert result.decision["subject"] == "word:pasian"
        assert result.threshold_met is True
        # Foundation aggregate: sum(tier_weight * confidence) / sum(tier_weight).
        assert result.consensus_confidence == 0.85

    def test_to_dict_is_json_serializable(self, engine: Engine) -> None:
        claim_id = two_source_claim(engine, subject="word:json")
        payload = compute_claim_consensus(engine)[claim_id].to_dict()
        assert json.loads(json.dumps(payload))["claim_id"] == claim_id
        assert payload["tier_breakdown"]["BIBLE_PARALLEL"]["count"] == 1
        assert payload["notes"] == []

    def test_adaptive_is_the_default_and_weighted_is_selectable(self, engine: Engine) -> None:
        claim_id = two_source_claim(engine, subject="word:method")

        adaptive = compute_claim_consensus(engine)[claim_id]
        assert adaptive.method == "weighted_evidence"  # fact_type 'word' + Bible tier
        assert adaptive.consensus_confidence == 0.85

        weighted = compute_claim_consensus(engine, method="weighted")[claim_id]
        assert weighted.method == "weighted_evidence"
        assert weighted.consensus_confidence == adaptive.consensus_confidence
        assert weighted.confidence == adaptive.confidence

    def test_unknown_method_rejected(self, engine: Engine) -> None:
        with pytest.raises(ValueError, match="unknown consensus method"):
            compute_claim_consensus(engine, method="vibes")

    def test_grammar_claim_uses_grammar_tier_path(self, engine: Engine) -> None:
        grammar = add_evidence(
            engine, fact_key="pattern:x", tier=EvidenceTier.GRAMMAR_PATTERNS, source="grammar_patterns", confidence=0.9
        )
        bible = add_evidence(
            engine, fact_key="pattern:x", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses", confidence=0.9
        )
        claim_id = add_claim(
            engine,
            claim_type="grammar",
            subject="pattern:disc_sp_trans",
            predicate="has_phenomenon",
            object_="transitivity",
            evidence_ids=[grammar, bible],
        )

        result = compute_claim_consensus(engine)[claim_id]
        assert result.method == "weighted_evidence"
        assert result.threshold == 0.8
        assert "GRAMMAR_PATTERNS" in result.tier_breakdown

    def test_low_confidence_evidence_is_reported_as_not_meeting_threshold(self, engine: Engine) -> None:
        """Foundation semantics: a sub-threshold aggregate yields confidence 0.0."""
        weak = add_evidence(
            engine, fact_key="word:weak", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses", confidence=0.2
        )
        other = add_evidence(
            engine, fact_key="word:weak", tier=EvidenceTier.CORPUS_ATTESTATION, source="corpus", confidence=0.3
        )
        claim_id = add_claim(engine, subject="word:weak", predicate="pos:NOUN", evidence_ids=[weak, other])

        result = compute_claim_consensus(engine)[claim_id]
        # The canonical tier-weighted aggregate survives the consensus gate ...
        assert result.confidence == 0.85  # mean of tier weights 1.0 and 0.7
        # ... while the foundation's threshold gate reports the weak evidence mass.
        assert result.consensus_confidence == 0.0
        assert result.threshold_met is False
        assert result.decision == {}
        assert result.notes  # the foundation explains why

    def test_missing_claim_is_skipped(self, engine: Engine) -> None:
        two_source_claim(engine, subject="word:present")
        results = compute_claim_consensus(engine, claim_ids=[999_999])
        assert results == {}

    def test_consensus_is_read_only(self, engine: Engine) -> None:
        """No status, confidence, link, or audit row may change."""
        claim_id = two_source_claim(engine, subject="word:ro")
        before = {
            table: snapshot(engine, table)
            for table in ("knowledge_claims", "claim_evidence", "foundation_evidence", "data_audit_log")
        }

        compute_claim_consensus(engine)

        after = {
            table: snapshot(engine, table)
            for table in ("knowledge_claims", "claim_evidence", "foundation_evidence", "data_audit_log")
        }
        assert after == before
        assert after["knowledge_claims"][0]["id"] == claim_id

    def test_results_are_deterministic(self, engine: Engine) -> None:
        two_source_claim(engine, subject="word:det")
        first = compute_claim_consensus(engine)
        second = compute_claim_consensus(engine)
        assert {cid: r.to_dict() for cid, r in first.items()} == {
            cid: r.to_dict() for cid, r in second.items()
        }

    def test_supported_methods_list(self) -> None:
        assert set(CONSENSUS_METHODS) == {"adaptive", "weighted"}
