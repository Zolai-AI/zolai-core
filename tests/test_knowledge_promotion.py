"""Tests for the Phase 4 promotion engine (Master Prompt §36).

Covers the Hypothesis → Claim promotion path:
- per-kind claim expression encoding (pos / morph_relation / collocation)
- evidence gate: SUPPORTED only with linked evidence, else CANDIDATE
- confidence comes only from ``confidence_from_evidence`` (≤ 2 dp)
- ``claim_evidence`` links + audit row written through ``ClaimRepository``
- idempotency via the expression-unique S/P/O index (second run creates nothing)
- ``dry_run`` plans without writing; unsupported kinds are refused
- hypothesis rows are never mutated

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
from zolai.knowledge import promotion

NOW = "2026-10-03T00:00:00+00:00"


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Temporary SQLite DB with the full ORM schema (no live-DB dependency)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'promotion.db'}")
    mgr.init_db()
    try:
        yield mgr.engine
    finally:
        mgr.dispose()


# ── helpers ──────────────────────────────────────────────────────────────────

def _evidence(
    engine: Engine,
    tier: int = 1,
    confidence: float = 0.8,
    source: str = "bible_verses",
    fact_type: str = "word",
    fact_key: str = "word:pasian",
) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO foundation_evidence "
                "(fact_type, fact_key, tier, source, confidence, provenance_hash, payload, created_at) "
                "VALUES (:ft, :fk, :tier, :src, :conf, :phash, '{}', :now)"
            ),
            {
                "ft": fact_type,
                "fk": fact_key,
                "tier": tier,
                "src": source,
                "conf": confidence,
                "phash": f"hash-{tier}-{source}",
                "now": NOW,
            },
        )
        return int(res.lastrowid)


def _hypothesis(
    engine: Engine,
    kind: str,
    subject: str,
    predicate: str | None = None,
    obj: str | None = None,
    evidence_ids: tuple[int, ...] | list[int] = (),
    extras: dict | None = None,
    confidence: float = 0.99,
) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO hypotheses "
                "(kind, subject, predicate, object, probability, confidence, "
                "evidence_ids, extras, status, created_at, updated_at) "
                "VALUES (:kind, :subject, :predicate, :object, 0.5, :conf, "
                ":eids, :extras, 'OBSERVED', :now, :now)"
            ),
            {
                "kind": kind,
                "subject": subject,
                "predicate": predicate or "",
                "object": obj,
                "conf": confidence,
                "eids": json.dumps(list(evidence_ids)),
                "extras": json.dumps(extras or {}),
                "now": NOW,
            },
        )
        return int(res.lastrowid)


def _claims(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row._mapping)
            for row in conn.execute(text("SELECT * FROM knowledge_claims ORDER BY id"))
        ]


def _links(engine: Engine, claim_id: int) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row._mapping)
            for row in conn.execute(
                text("SELECT * FROM claim_evidence WHERE claim_id = :c ORDER BY id"),
                {"c": claim_id},
            )
        ]


# ── encoding ────────────────────────────────────────────────────────────────

def test_pos_hypothesis_promotes_to_lexicon_claim(engine: Engine) -> None:
    bible = _evidence(engine, tier=1, source="bible_verses")
    dictionary = _evidence(engine, tier=2, source="dictionary")
    _hypothesis(
        engine,
        kind="pos",
        subject="word:word:pasian",  # repeated prefix collapses to one
        predicate="pos:N",
        evidence_ids=[bible, dictionary],
    )

    summary = promotion.promote_hypotheses_to_claims(engine)

    assert summary.hypotheses_considered == 1
    assert summary.claims_planned == 1
    assert summary.claims_created == 1
    assert summary.errors == []

    [claim] = _claims(engine)
    assert claim["claim_type"] == "lexicon"
    assert claim["subject"] == "word:pasian"
    assert claim["predicate"] == "pos:N"
    assert claim["object"] is None
    # tier weights 1.0 and 0.9 → average 0.95 (never the hypothesis' 0.99).
    assert claim["confidence"] == 0.95
    assert claim["status"] == "SUPPORTED"
    assert claim["notes"] == "kind=pos"
    assert json.loads(claim["evidence_ids"]) == [bible, dictionary]

    assert {link["evidence_id"] for link in _links(engine, claim["id"])} == {bible, dictionary}
    with engine.connect() as conn:
        audit = conn.execute(
            text("SELECT * FROM data_audit_log WHERE table_name = 'knowledge_claims'")
        ).fetchall()
    assert len(audit) == 1


def test_morph_relation_encodes_root_object(engine: Engine) -> None:
    evidence_id = _evidence(engine, tier=3, source="corpus")
    _hypothesis(
        engine,
        kind="morph_relation",
        subject="word:vantung",
        predicate=None,
        obj="van",  # plain root gets the ``root:`` prefix
        extras={"type": "compound"},
        evidence_ids=[evidence_id],
    )
    _hypothesis(
        engine,
        kind="morph_relation",
        subject="word:leitung",
        predicate="morph:derive",
        obj="root:lei",  # already-prefixed roots are not doubled
        evidence_ids=[evidence_id],
    )

    summary = promotion.promote_hypotheses_to_claims(engine)

    assert summary.claims_created == 2
    by_subject = {claim["subject"]: claim for claim in _claims(engine)}
    assert by_subject["word:vantung"]["claim_type"] == "morphology"
    assert by_subject["word:vantung"]["predicate"] == "morph:compound"
    assert by_subject["word:vantung"]["object"] == "root:van"
    assert by_subject["word:leitung"]["object"] == "root:lei"


def test_collocation_requires_object(engine: Engine) -> None:
    evidence_id = _evidence(engine, tier=2, source="dictionary")
    _hypothesis(
        engine,
        kind="collocation",
        subject="word:pasian",
        predicate="co_occurs_with",
        obj="word:leitung",
        evidence_ids=[evidence_id],
    )
    _hypothesis(  # no object → unmappable
        engine,
        kind="collocation",
        subject="word:gam",
        predicate="co_occurs_with",
        obj=None,
        evidence_ids=[evidence_id],
    )

    summary = promotion.promote_hypotheses_to_claims(engine)

    assert summary.claims_created == 1
    assert summary.claims_skipped_unmappable == 1
    [claim] = _claims(engine)
    assert claim["claim_type"] == "lexicon"
    assert claim["object"] == "word:leitung"


def test_pos_without_predicate_or_extras_is_unmappable(engine: Engine) -> None:
    _hypothesis(engine, kind="pos", subject="word:gam", predicate=None)

    summary = promotion.promote_hypotheses_to_claims(engine)

    assert summary.claims_skipped_unmappable == 1
    assert summary.claims_created == 0


def test_unsupported_kinds_are_refused(engine: Engine) -> None:
    _hypothesis(engine, kind="generic", subject="anything", predicate="is", obj="thing")

    summary = promotion.promote_hypotheses_to_claims(engine, kinds=["pos", "generic"])

    assert summary.kinds == ["pos"]
    assert summary.unsupported_kinds == ["generic"]
    # The generic row is never scanned, so it is not "considered".
    assert summary.hypotheses_considered == 0


# ── evidence gate ───────────────────────────────────────────────────────────

def test_dangling_and_insufficient_evidence_are_refused(engine: Engine) -> None:
    valid = _evidence(engine, tier=1)
    _hypothesis(
        engine, kind="pos", subject="word:dangling", predicate="pos:N", evidence_ids=[999_999]
    )
    _hypothesis(
        engine, kind="pos", subject="word:one", predicate="pos:N", evidence_ids=[valid]
    )

    strict = promotion.promote_hypotheses_to_claims(engine, min_evidence=2)

    assert strict.claims_skipped_no_evidence == 2
    assert strict.claims_created == 0
    assert _claims(engine) == []

    relaxed = promotion.promote_hypotheses_to_claims(engine, min_evidence=1)

    assert relaxed.claims_created == 1
    assert relaxed.claims_skipped_no_evidence == 1
    [claim] = _claims(engine)
    assert claim["subject"] == "word:one"


def test_confidence_gate_uses_evidence_not_hypothesis_confidence(engine: Engine) -> None:
    weak = _evidence(engine, tier=5, source="llm")  # tier weight 0.4
    _hypothesis(
        engine,
        kind="pos",
        subject="word:pasian",
        predicate="pos:N",
        evidence_ids=[weak],
        confidence=0.99,
    )

    gated = promotion.promote_hypotheses_to_claims(engine, min_confidence=0.5)

    assert gated.claims_skipped_low_confidence == 1
    assert gated.claims_created == 0

    passed = promotion.promote_hypotheses_to_claims(engine, min_confidence=0.0)

    assert passed.claims_created == 1
    [claim] = _claims(engine)
    assert claim["confidence"] == 0.4


def test_ungated_promotion_yields_candidate_never_observed(engine: Engine) -> None:
    _hypothesis(engine, kind="pos", subject="word:gam", predicate="pos:N")

    summary = promotion.promote_hypotheses_to_claims(engine, min_evidence=0)

    assert summary.claims_created == 1
    [claim] = _claims(engine)
    assert claim["status"] == "CANDIDATE"
    assert claim["confidence"] == 0.0
    assert json.loads(claim["evidence_ids"]) == []


# ── idempotency ─────────────────────────────────────────────────────────────

def test_second_run_and_legacy_encoding_are_duplicates(engine: Engine) -> None:
    evidence_id = _evidence(engine, tier=1)
    _hypothesis(engine, kind="pos", subject="word:pasian", predicate="pos:N", evidence_ids=[evidence_id])
    # Same normalized expression, legacy claim_type already in the table.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO knowledge_claims "
                "(claim_type, subject, predicate, object, confidence, status, "
                "evidence_ids, source_ids, notes, version, created_at, updated_at) "
                "VALUES ('pos', 'word:leitung', 'pos:N', NULL, 0.9, 'SUPPORTED', "
                "'[]', '[]', '', 1, :now, :now)"
            ),
            {"now": NOW},
        )
    _hypothesis(engine, kind="pos", subject="word:leitung", predicate="pos:N", evidence_ids=[evidence_id])

    first = promotion.promote_hypotheses_to_claims(engine)

    assert first.claims_created == 1
    assert first.claims_skipped_duplicate == 1  # legacy ``pos`` claim matched

    second = promotion.promote_hypotheses_to_claims(engine)

    assert second.claims_created == 0
    assert second.claims_skipped_duplicate == 2
    assert len(_claims(engine)) == 2


# ── dry run / stats / pagination ────────────────────────────────────────────

def test_dry_run_plans_without_writing_or_mutating_hypotheses(engine: Engine) -> None:
    evidence_id = _evidence(engine, tier=1)
    hypothesis_id = _hypothesis(
        engine, kind="pos", subject="word:pasian", predicate="pos:N", evidence_ids=[evidence_id]
    )
    before = _claims(engine)
    with engine.connect() as conn:
        row_before = dict(
            conn.execute(
                text("SELECT * FROM hypotheses WHERE id = :id"), {"id": hypothesis_id}
            ).one()._mapping
        )

    summary = promotion.promote_hypotheses_to_claims(engine, dry_run=True)

    assert summary.dry_run is True
    assert summary.claims_planned == 1
    assert summary.claims_created == 0
    assert _claims(engine) == before
    with engine.connect() as conn:
        row_after = dict(
            conn.execute(
                text("SELECT * FROM hypotheses WHERE id = :id"), {"id": hypothesis_id}
            ).one()._mapping
        )
    assert row_after == row_before


def test_pagination_considers_every_hypothesis(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(promotion, "PAGE_SIZE", 2)
    evidence_id = _evidence(engine, tier=1)
    for index in range(5):
        _hypothesis(
            engine,
            kind="pos",
            subject=f"word:w{index}",
            predicate="pos:N",
            evidence_ids=[evidence_id],
        )

    summary = promotion.promote_hypotheses_to_claims(engine)

    assert summary.hypotheses_considered == 5
    assert summary.claims_created == 5
    assert len(_claims(engine)) == 5


def test_get_promotion_stats_reports_each_supported_kind(engine: Engine) -> None:
    evidence_id = _evidence(engine, tier=1)
    _hypothesis(engine, kind="pos", subject="word:pasian", predicate="pos:N", evidence_ids=[evidence_id])
    _hypothesis(engine, kind="morph_relation", subject="word:vantung", predicate="morph:compound")
    _hypothesis(
        engine,
        kind="collocation",
        subject="word:gam",
        predicate="co_occurs_with",
        obj="word:leitung",
        evidence_ids=[evidence_id],
    )

    stats = promotion.get_promotion_stats(engine)

    assert set(stats) == set(promotion.KIND_TO_CLAIM_TYPE)
    assert stats["pos"]["total"] == 1
    assert stats["pos"]["with_evidence"] == 1
    assert stats["pos"]["statuses"] == {"OBSERVED": 1}
    assert stats["morph_relation"]["total"] == 1
    assert stats["morph_relation"]["with_evidence"] == 0
    assert stats["collocation"]["with_evidence"] == 1


# ── normalizers ─────────────────────────────────────────────────────────────

def test_parse_id_list_never_raises_on_junk() -> None:
    assert promotion.parse_id_list(None) == []
    assert promotion.parse_id_list("") == []
    assert promotion.parse_id_list("not json") == []
    assert promotion.parse_id_list({"id": 1}) == []
    assert promotion.parse_id_list("[1, 2]") == [1, 2]
    assert promotion.parse_id_list([1, "2", None, "x", 3.0]) == [1, 2, 3]
