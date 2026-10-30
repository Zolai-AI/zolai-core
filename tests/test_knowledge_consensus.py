"""Tests for the Phase 4 consensus engine (Master Prompt §36).

Covers claim selection, evidence loading, and consensus computation:
- only claims with ≥2 ``claim_evidence`` links are selected (JSON ``evidence_ids``
  alone never qualifies)
- explicit claim ids are filtered by the same threshold
- evidence tiers are weighted (documented in ``EvidenceTier``)
- unknown consensus methods are rejected
- grammar claims use the grammar tier path
- low-confidence evidence is reported as not meeting threshold
- consensus is read-only (no DB writes, no audit rows)
- results are deterministic and JSON-safe

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
from zolai.knowledge.consensus import (
    ClaimConsensus,
    build_consensus_candidate,
    claim_consensus_fact_type,
    compute_claim_consensus,
    select_consensus_claim_ids,
)
from zolai.shared.contracts import KnowledgeStatus

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


# ── helpers ──────────────────────────────────────────────────────────────────

def _insert_claim(
    engine: Engine,
    claim_type: str = "lexicon",
    subject: str = "word:pasian",
    predicate: str = "pos:N",
    obj: str | None = None,
    evidence_ids: tuple[int, ...] = (),
    confidence: float = 0.9,
) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO knowledge_claims "
                "(claim_type, subject, predicate, object, confidence, status, "
                "evidence_ids, source_ids, notes, version, created_at, updated_at) "
                "VALUES (:ct, :subject, :predicate, :object, :conf, :status, "
                ":eids, '[]', '', 1, :now, :now)"
            ),
            {
                "ct": claim_type,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "conf": confidence,
                "status": KnowledgeStatus.SUPPORTED.value,
                "eids": json.dumps(list(evidence_ids)),
                "now": NOW,
            },
        )
        return int(res.lastrowid)


def _insert_evidence(
    engine: Engine,
    tier: int = 1,
    source: str = "bible_verses",
    confidence: float = 0.8,
    fact_key: str = "word:pasian",
) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO foundation_evidence "
                "(fact_type, fact_key, tier, source, confidence, provenance_hash, payload, created_at) "
                "VALUES ('word', :fk, :tier, :src, :conf, :phash, '{}', :now)"
            ),
            {
                "fk": fact_key,
                "tier": tier,
                "src": source,
                "conf": confidence,
                "phash": f"hash-{tier}-{source}",
                "now": NOW,
            },
        )
        return int(res.lastrowid)


def _link(engine: Engine, claim_id: int, evidence_id: int) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO claim_evidence (claim_id, evidence_id, role, created_at) "
                "VALUES (:c, :e, 'supports', :now)"
            ),
            {"c": claim_id, "e": evidence_id, "now": NOW},
        )
        return int(res.lastrowid)


def _claim_with_evidence(
    engine: Engine,
    *,
    claim_type: str = "lexicon",
    subject: str = "word:pasian",
    predicate: str = "pos:N",
    evidence: list[tuple[int, str, float]],
) -> int:
    """Insert a claim plus one ``claim_evidence`` link per ``(tier, source, conf)``."""
    claim_id = _insert_claim(
        engine, claim_type=claim_type, subject=subject, predicate=predicate
    )
    for tier, source, confidence in evidence:
        _link(engine, claim_id, _insert_evidence(engine, tier=tier, source=source, confidence=confidence))
    return claim_id


def _snapshot(engine: Engine) -> dict[str, list[dict]]:
    tables = ("knowledge_claims", "claim_evidence", "foundation_evidence", "data_audit_log")
    with engine.connect() as conn:
        return {
            name: [
                dict(row._mapping)
                for row in conn.execute(text(f"SELECT * FROM {name} ORDER BY id"))
            ]
            for name in tables
        }


# ── selection ───────────────────────────────────────────────────────────────

def test_only_claims_with_two_linked_rows_are_selected(engine: Engine) -> None:
    two = _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.8), (2, "dictionary", 0.8)],
    )
    one = _claim_with_evidence(
        engine, subject="word:gam", evidence=[(1, "bible_verses", 0.8)]
    )
    _insert_claim(engine, subject="word:leitung")  # no links at all

    assert select_consensus_claim_ids(engine) == [two]
    assert select_consensus_claim_ids(engine, min_evidence_rows=1) == sorted([two, one])


def test_claim_evidence_links_outrank_the_json_evidence_ids_column(engine: Engine) -> None:
    claim_id = _insert_claim(engine, evidence_ids=(1, 2))
    _link(engine, claim_id, _insert_evidence(engine, tier=1))

    assert select_consensus_claim_ids(engine) == []


def test_explicit_claim_ids_are_thresholded(engine: Engine) -> None:
    a = _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.8), (2, "dictionary", 0.8)],
    )
    b = _claim_with_evidence(
        engine, subject="word:gam", evidence=[(1, "bible_verses", 0.8)]
    )
    d = _claim_with_evidence(
        engine,
        subject="word:vantung",
        evidence=[(3, "grammar_patterns", 0.9), (2, "dictionary", 0.8)],
    )

    assert select_consensus_claim_ids(engine, claim_ids=[a, b, d, 999_999]) == [a, d]
    assert select_consensus_claim_ids(engine, claim_ids=[]) == []


# ── computation ─────────────────────────────────────────────────────────────

def test_adaptive_lexicon_claim_uses_weighted_evidence(engine: Engine) -> None:
    claim_id = _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.8), (2, "dictionary", 0.8)],
    )

    [result] = compute_claim_consensus(engine).values()

    assert isinstance(result, ClaimConsensus)
    assert result.claim_id == claim_id
    assert result.fact_type == "word"
    assert result.fact_key == f"claim:{claim_id}"
    assert result.evidence_count == 2
    assert result.source_count == 2
    # Evidence-derived tier average: (1.0 + 0.9) / 2, never the source conf.
    assert result.confidence == 0.95
    assert result.agreement_score == 1.0
    assert result.method == "weighted_evidence"
    assert result.threshold == 0.75
    assert result.threshold_met is True
    assert result.consensus_confidence == 0.8
    assert result.decision["subject"] == "word:pasian"
    assert result.tier_breakdown["BIBLE_PARALLEL"] == {
        "count": 1,
        "tier_weight": 1.0,
        "weight_total": 0.8,
    }
    assert result.tier_breakdown["DICTIONARY"] == {
        "count": 1,
        "tier_weight": 0.9,
        "weight_total": 0.72,
    }
    json.dumps(result.to_dict())  # JSON-safe


def test_low_confidence_evidence_reports_threshold_not_met(engine: Engine) -> None:
    _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.3), (2, "dictionary", 0.3)],
    )

    [result] = compute_claim_consensus(engine).values()

    assert result.threshold_met is False
    assert result.decision == {}
    assert result.consensus_confidence == 0.0
    assert result.notes
    assert all("below" in note for note in result.notes)


def test_weighted_method_uses_the_0_7_threshold(engine: Engine) -> None:
    _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.8), (2, "dictionary", 0.8)],
    )

    [result] = compute_claim_consensus(engine, method="weighted").values()

    assert result.method == "weighted_evidence"
    assert result.threshold == 0.7
    assert result.threshold_met is True
    assert result.consensus_confidence == 0.8


def test_grammar_claim_uses_the_grammar_tier_path(engine: Engine) -> None:
    _claim_with_evidence(
        engine,
        claim_type="grammar",
        subject="pattern:ergative-in",
        predicate="marks:subject",
        evidence=[(3, "grammar_patterns", 0.9), (3, "grammar_patterns", 0.9)],
    )

    [result] = compute_claim_consensus(engine).values()

    assert result.fact_type == "grammar"
    assert result.method == "weighted_evidence"
    assert result.threshold == 0.8
    assert result.threshold_met is True
    assert result.consensus_confidence == 0.9
    assert list(result.tier_breakdown) == ["GRAMMAR_PATTERNS"]


def test_unknown_method_is_rejected(engine: Engine) -> None:
    with pytest.raises(ValueError, match="unknown consensus method"):
        compute_claim_consensus(engine, method="majority")


# ── read-only + determinism ─────────────────────────────────────────────────

def test_consensus_never_writes(engine: Engine) -> None:
    _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.8), (2, "dictionary", 0.8)],
    )
    before = _snapshot(engine)

    compute_claim_consensus(engine, method="adaptive")
    compute_claim_consensus(engine, method="weighted")

    assert _snapshot(engine) == before


def test_results_are_deterministic(engine: Engine) -> None:
    _claim_with_evidence(
        engine,
        evidence=[(1, "bible_verses", 0.8), (2, "dictionary", 0.8)],
    )
    _claim_with_evidence(
        engine,
        claim_type="grammar",
        subject="pattern:ergative-in",
        predicate="marks:subject",
        evidence=[(3, "grammar_patterns", 0.9), (3, "grammar_patterns", 0.9)],
    )

    first = {cid: result.to_dict() for cid, result in compute_claim_consensus(engine).items()}
    second = {cid: result.to_dict() for cid, result in compute_claim_consensus(engine).items()}

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ── pure helpers ────────────────────────────────────────────────────────────

def test_fact_type_mapping() -> None:
    assert claim_consensus_fact_type("lexicon") == "word"
    assert claim_consensus_fact_type("morphology") == "word"
    assert claim_consensus_fact_type("grammar") == "grammar"
    assert claim_consensus_fact_type("collocation") == "collocation"


def test_build_candidate_drops_out_of_range_tiers() -> None:
    claim = {"id": 7, "claim_type": "morphology", "subject": "word:x", "predicate": "p", "object": "o"}
    bad = {"tier": 9, "confidence": 0.9, "source": "x", "fact_type": "word", "fact_key": "word:x", "payload": "{}"}
    good = {"tier": 2, "confidence": 0.5, "source": "dict", "fact_type": "word", "fact_key": "word:x", "payload": "{}"}

    assert build_consensus_candidate(claim, [bad]) is None

    candidate = build_consensus_candidate(claim, [bad, good])
    assert candidate is not None
    assert candidate.fact_type == "word"
    assert candidate.fact_key == "claim:7"
    assert candidate.source == "consensus"
    assert len(candidate.evidence) == 1
    assert candidate.value["subject"] == "word:x"
