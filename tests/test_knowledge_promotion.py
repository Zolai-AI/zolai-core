"""Tests for the Phase 4 promotion engine (Master Prompt §36).

Covers the Hypothesis → Claim promotion path:
- per-kind claim expression encoding (pos / morph_relation / collocation)
- evidence gate: SUPPORTED only with linked evidence, else CANDIDATE
- confidence comes only from ``confidence_from_evidence`` (≤ 2 dp)
- ``claim_evidence`` links + audit row written through ``ClaimRepository``
- idempotency via the expression-unique S/P/O index (second run creates nothing)
- ``dry_run`` plans without writing; unsupported kinds are refused

Isolation: a temporary SQLite database built from the ORM metadata — the tests
never read or write the live ``data/zolai.db``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.foundation.evidence import EvidenceTier
from zolai.knowledge.promotion import (
    DEFAULT_KINDS,
    KIND_TO_CLAIM_TYPE,
    ClaimExpression,
    PromotionSummary,
    build_claim_expression,
    load_linked_evidence,
    parse_id_list,
    promote_hypotheses_to_claims,
)
from zolai.shared.contracts import KnowledgeStatus, confidence_from_evidence

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

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


def add_hypothesis(
    engine: Engine,
    *,
    kind: str,
    subject: str,
    predicate: str,
    object_: str | None = None,
    evidence_ids: list[int] | None = None,
    extras: dict[str, Any] | None = None,
    status: str = KnowledgeStatus.CANDIDATE.value,
) -> int:
    """Insert one ``hypotheses`` row; return its id."""
    with engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "INSERT INTO hypotheses "
                    "(kind, subject, predicate, object, probability, confidence, "
                    "evidence_count, source_count, evidence_ids, extras, status, created_at, updated_at) "
                    "VALUES (:kind, :subject, :predicate, :object, 0.0, NULL, :ev_count, 1, "
                    ":evidence_ids, :extras, :status, :now, :now)"
                ),
                {
                    "kind": kind,
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_,
                    "ev_count": len(evidence_ids or []),
                    "evidence_ids": json.dumps(evidence_ids or []),
                    "extras": json.dumps(extras or {}),
                    "status": status,
                    "now": NOW,
                },
            ).lastrowid
        )


def claims_for(engine: Engine, subject: str) -> list[dict[str, Any]]:
    """All ``knowledge_claims`` rows for one subject, as dicts."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM knowledge_claims WHERE subject = :subject ORDER BY id"),
            {"subject": subject},
        ).all()
    return [dict(row._mapping) for row in rows]


def count(engine: Engine, table: str) -> int:
    """Row count for a table (table names are literals in these tests only)."""
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar())


# ---------------------------------------------------------------------------
# Per-kind claim expressions
# ---------------------------------------------------------------------------


class TestClaimExpressions:
    def test_pos_expression_null_object(self) -> None:
        expression = build_claim_expression(
            {"kind": "pos", "subject": "word:pasian", "predicate": "pos:NOUN", "object": None}
        )
        assert expression == ClaimExpression("lexicon", "word:pasian", "pos:NOUN", None)
        assert expression.key == ("lexicon", "word:pasian", "pos:NOUN", "")

    def test_pos_expression_reads_upos_from_extras(self) -> None:
        """Discovery rows without a canonical predicate fall back to ``extras``."""
        expression = build_claim_expression(
            {
                "kind": "pos",
                "subject": "word:gam",
                "predicate": "",
                "extras": json.dumps({"word": "gam", "pos": "NOUN"}),
            }
        )
        assert expression == ClaimExpression("lexicon", "word:gam", "pos:NOUN", None)

    def test_morph_relation_expression_adds_root_prefix(self) -> None:
        """The stored object is a bare root — promotion encodes ``root:{root}``."""
        expression = build_claim_expression(
            {
                "kind": "morph_relation",
                "subject": "word:piangsak",
                "predicate": "morph:stem",
                "object": "piang",
            }
        )
        assert expression == ClaimExpression(
            "morphology", "word:piangsak", "morph:stem", "root:piang"
        )

    def test_morph_relation_expression_reads_extras(self) -> None:
        expression = build_claim_expression(
            {
                "kind": "morph_relation",
                "subject": "word:leh",
                "predicate": "",
                "object": None,
                "extras": json.dumps({"surface": "leh", "type": "prefix", "root": "le"}),
            }
        )
        assert expression == ClaimExpression(
            "morphology", "word:leh", "morph:prefix", "root:le"
        )

    def test_collocation_expression(self) -> None:
        expression = build_claim_expression(
            {
                "kind": "collocation",
                "subject": "word:pasian",
                "predicate": "collocates_with",
                "object": "word:vantung",
            }
        )
        assert expression == ClaimExpression(
            "lexicon", "word:pasian", "collocates_with", "word:vantung"
        )

    def test_encoding_is_idempotent(self) -> None:
        """Re-promoting an already-encoded hypothesis yields the same expression."""
        once = build_claim_expression(
            {"kind": "collocation", "subject": "word:a", "predicate": "collocates_with", "object": "word:b"}
        )
        twice = build_claim_expression(
            {"kind": "collocation", "subject": "word:a", "predicate": "collocates_with", "object": "word:b"}
        )
        assert once == twice

    def test_unsupported_kind_returns_none(self) -> None:
        assert build_claim_expression({"kind": "generic", "subject": "a", "predicate": "b"}) is None

    def test_missing_slots_return_none(self) -> None:
        assert build_claim_expression({"kind": "pos", "subject": "word:x", "predicate": ""}) is None

    def test_every_default_kind_has_a_claim_type(self) -> None:
        assert set(DEFAULT_KINDS) == set(KIND_TO_CLAIM_TYPE)


class TestParseIdList:
    def test_json_string_and_list(self) -> None:
        assert parse_id_list("[3, 4]") == [3, 4]
        assert parse_id_list([5, 6]) == [5, 6]

    def test_empty_and_junk(self) -> None:
        assert parse_id_list(None) == []
        assert parse_id_list("") == []
        assert parse_id_list("not json") == []
        assert parse_id_list(["7", 8, None]) == [7, 8]


# ---------------------------------------------------------------------------
# Evidence resolution
# ---------------------------------------------------------------------------


class TestLoadLinkedEvidence:
    def test_resolves_existing_rows_in_requested_order(self, engine: Engine) -> None:
        first = add_evidence(engine, fact_key="word:a", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        second = add_evidence(engine, fact_key="word:b", tier=EvidenceTier.DICTIONARY, source="dictionary")

        resolved = load_linked_evidence(engine, [second, first])
        assert [item.evidence_id for item in resolved] == [second, first]
        assert resolved[0].evidence.tier is EvidenceTier.DICTIONARY
        assert resolved[0].evidence.source == "dictionary"

    def test_missing_ids_and_dedupe(self, engine: Engine) -> None:
        only = add_evidence(engine, fact_key="word:a", tier=EvidenceTier.CORPUS_ATTESTATION, source="corpus")
        resolved = load_linked_evidence(engine, [only, only, 999_999])
        assert [item.evidence_id for item in resolved] == [only]

    def test_out_of_range_tier_skipped(self, engine: Engine) -> None:
        with engine.begin() as conn:
            bad = int(
                conn.execute(
                    text(
                        "INSERT INTO foundation_evidence "
                        "(fact_type, fact_key, tier, source, confidence, provenance_hash, "
                        "payload, created_at) VALUES ('word', 'word:x', 9, 'weird', 0.5, 'h', '{}', :now)"
                    ),
                    {"now": NOW},
                ).lastrowid
            )
        assert load_linked_evidence(engine, [bad]) == []

    def test_no_ids_is_empty(self, engine: Engine) -> None:
        assert load_linked_evidence(engine, []) == []


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


class TestPromoteHypothesesToClaims:
    def test_creates_supported_claim_with_evidence_links(self, engine: Engine) -> None:
        """Evidence-bearing hypothesis → SUPPORTED claim + claim_evidence + audit row."""
        bible = add_evidence(engine, fact_key="word:pasian", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        add_hypothesis(
            engine,
            kind="pos",
            subject="word:pasian",
            predicate="pos:NOUN",
            evidence_ids=[bible],
        )

        summary = promote_hypotheses_to_claims(engine, kinds=["pos"])
        assert summary.claims_created == 1
        assert summary.errors == []

        rows = claims_for(engine, "word:pasian")
        assert len(rows) == 1
        claim = rows[0]
        assert claim["status"] == KnowledgeStatus.SUPPORTED.value
        assert claim["claim_type"] == "lexicon"
        assert claim["object"] is None
        assert claim["confidence"] == EvidenceTier.BIBLE_PARALLEL.weight()
        assert json.loads(claim["evidence_ids"]) == [bible]
        assert "kind=pos" in claim["notes"]

        with engine.connect() as conn:
            links = conn.execute(
                text("SELECT evidence_id, role FROM claim_evidence WHERE claim_id = :c"),
                {"c": claim["id"]},
            ).all()
            audit = conn.execute(
                text(
                    "SELECT field FROM data_audit_log "
                    "WHERE table_name = 'knowledge_claims' AND row_id = :c"
                ),
                {"c": claim["id"]},
            ).all()
        assert [(row.evidence_id, row.role) for row in links] == [(bible, "supports")]
        assert [row.field for row in audit] == ["create"]

    def test_confidence_is_tier_weighted_and_rounded(self, engine: Engine) -> None:
        """Confidence is the mean tier weight from ``confidence_from_evidence``."""
        bible = add_evidence(engine, fact_key="word:a", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        llm = add_evidence(engine, fact_key="word:b", tier=EvidenceTier.LLM_GENERATION, source="llm", confidence=0.1)
        add_hypothesis(engine, kind="pos", subject="word:a", predicate="pos:NOUN", evidence_ids=[bible, llm])

        promote_hypotheses_to_claims(engine, kinds=["pos"])
        stored = claims_for(engine, "word:a")[0]["confidence"]
        expected = confidence_from_evidence([EvidenceTier.BIBLE_PARALLEL, EvidenceTier.LLM_GENERATION])
        assert stored == expected == 0.7  # (1.0 + 0.4) / 2
        assert stored == round(stored, 2)

    def test_min_evidence_gate_skips_evidence_less_hypothesis(self, engine: Engine) -> None:
        add_hypothesis(engine, kind="pos", subject="word:lonely", predicate="pos:NOUN", evidence_ids=[])
        summary = promote_hypotheses_to_claims(engine, kinds=["pos"])
        assert summary.claims_skipped_no_evidence == 1
        assert summary.claims_created == 0
        assert count(engine, "knowledge_claims") == 0

    def test_min_evidence_zero_promotes_candidate(self, engine: Engine) -> None:
        """``min_evidence=0`` mints CANDIDATE claims (never OBSERVED)."""
        add_hypothesis(engine, kind="pos", subject="word:solo", predicate="pos:NOUN", evidence_ids=[])
        summary = promote_hypotheses_to_claims(engine, kinds=["pos"], min_evidence=0)
        assert summary.claims_created == 1

        claim = claims_for(engine, "word:solo")[0]
        assert claim["status"] == KnowledgeStatus.CANDIDATE.value
        assert claim["confidence"] == 0.0
        assert claim["status"] in {
            KnowledgeStatus.CANDIDATE.value,
            KnowledgeStatus.SUPPORTED.value,
            KnowledgeStatus.VERIFIED.value,
        }

    def test_dangling_evidence_id_does_not_create_link(self, engine: Engine) -> None:
        """An id with no ``foundation_evidence`` row cannot satisfy the gate."""
        add_hypothesis(
            engine, kind="pos", subject="word:ghost", predicate="pos:NOUN", evidence_ids=[424_242]
        )
        summary = promote_hypotheses_to_claims(engine, kinds=["pos"])
        assert summary.claims_skipped_no_evidence == 1
        assert count(engine, "knowledge_claims") == 0
        assert count(engine, "claim_evidence") == 0

    def test_min_confidence_filter(self, engine: Engine) -> None:
        weak = add_evidence(
            engine, fact_key="word:weak", tier=EvidenceTier.LLM_GENERATION, source="llm", confidence=0.2
        )
        add_hypothesis(engine, kind="pos", subject="word:weak", predicate="pos:NOUN", evidence_ids=[weak])

        skipped = promote_hypotheses_to_claims(engine, kinds=["pos"], min_confidence=0.5)
        assert skipped.claims_skipped_low_confidence == 1
        assert skipped.claims_created == 0

        accepted = promote_hypotheses_to_claims(engine, kinds=["pos"], min_confidence=0.4)
        assert accepted.claims_created == 1

    def test_dry_run_writes_nothing(self, engine: Engine) -> None:
        bible = add_evidence(engine, fact_key="word:dry", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        add_hypothesis(engine, kind="pos", subject="word:dry", predicate="pos:NOUN", evidence_ids=[bible])

        summary = promote_hypotheses_to_claims(engine, kinds=["pos"], dry_run=True)
        assert summary.dry_run is True
        assert summary.claims_planned == 1
        assert summary.claims_created == 0
        assert count(engine, "knowledge_claims") == 0
        assert count(engine, "claim_evidence") == 0
        assert count(engine, "data_audit_log") == 0

    def test_second_run_is_idempotent(self, engine: Engine) -> None:
        """The expression-unique index makes a re-run a no-op."""
        bible = add_evidence(engine, fact_key="word:idem", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        add_hypothesis(engine, kind="pos", subject="word:idem", predicate="pos:NOUN", evidence_ids=[bible])

        first = promote_hypotheses_to_claims(engine, kinds=["pos"])
        assert first.claims_created == 1

        second = promote_hypotheses_to_claims(engine, kinds=["pos"])
        assert second.claims_created == 0
        assert second.claims_skipped_duplicate == 1
        assert count(engine, "knowledge_claims") == 1
        assert count(engine, "claim_evidence") == 1

    def test_two_hypotheses_same_expression_collide(self, engine: Engine) -> None:
        first_ev = add_evidence(engine, fact_key="word:dup", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        second_ev = add_evidence(engine, fact_key="word:dup", tier=EvidenceTier.CORPUS_ATTESTATION, source="corpus")
        add_hypothesis(engine, kind="pos", subject="word:dup", predicate="pos:NOUN", evidence_ids=[first_ev])
        add_hypothesis(engine, kind="pos", subject="word:word:dup", predicate="pos:NOUN", evidence_ids=[second_ev])

        summary = promote_hypotheses_to_claims(engine, kinds=["pos"])
        assert summary.claims_created == 1
        assert summary.claims_skipped_duplicate == 1
        assert count(engine, "knowledge_claims") == 1

    def test_default_kinds_promote_all_three(self, engine: Engine) -> None:
        bible = add_evidence(engine, fact_key="word:mix", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        add_hypothesis(engine, kind="pos", subject="word:mix", predicate="pos:NOUN", evidence_ids=[bible])
        add_hypothesis(
            engine,
            kind="morph_relation",
            subject="word:piangsak",
            predicate="morph:stem",
            object_="piang",
            evidence_ids=[bible],
        )
        add_hypothesis(
            engine,
            kind="collocation",
            subject="word:pasian",
            predicate="collocates_with",
            object_="word:vantung",
            evidence_ids=[bible],
        )

        summary = promote_hypotheses_to_claims(engine)
        assert summary.kinds == list(DEFAULT_KINDS)
        assert summary.claims_created == 3
        assert summary.hypotheses_considered == 3

        claim_types = {
            row["claim_type"] for row in claims_for(engine, "word:mix")
        } | {row["claim_type"] for row in claims_for(engine, "word:piangsak")}
        assert claim_types == {"lexicon", "morphology"}

    def test_unsupported_kind_is_refused(self, engine: Engine) -> None:
        add_hypothesis(engine, kind="generic", subject="a", predicate="b", evidence_ids=[])
        summary = promote_hypotheses_to_claims(engine, kinds=["generic", "pos"])
        assert summary.unsupported_kinds == ["generic"]
        assert summary.hypotheses_considered == 0
        assert count(engine, "knowledge_claims") == 0

    def test_unmappable_row_is_counted_not_fatal(self, engine: Engine) -> None:
        bible = add_evidence(engine, fact_key="word:x", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        good = add_hypothesis(engine, kind="pos", subject="word:good", predicate="pos:NOUN", evidence_ids=[bible])
        bad = add_hypothesis(engine, kind="collocation", subject="word:half", predicate="collocates_with")

        summary = promote_hypotheses_to_claims(engine, kinds=["collocation", "pos"])
        assert summary.hypotheses_considered == 2
        assert summary.claims_skipped_unmappable == 1
        assert summary.claims_created == 1
        assert summary.errors == []
        assert count(engine, "knowledge_claims") == 1
        assert good and bad

    def test_hypothesis_rows_are_not_mutated(self, engine: Engine) -> None:
        """Promotion only writes claims — the discovery row is left alone."""
        bible = add_evidence(engine, fact_key="word:ro", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
        hypothesis_id = add_hypothesis(
            engine,
            kind="morph_relation",
            subject="word:ro",
            predicate="morph:prefix",
            object_="leh",
            evidence_ids=[bible],
            status=KnowledgeStatus.OBSERVED.value,
        )

        promote_hypotheses_to_claims(engine, kinds=["morph_relation"])
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, evidence_ids FROM hypotheses WHERE id = :id"),
                {"id": hypothesis_id},
            ).first()
        assert row.status == KnowledgeStatus.OBSERVED.value
        assert json.loads(row.evidence_ids) == [bible]

    def test_pagination_reads_beyond_one_page(self, engine: Engine) -> None:
        """All rows are promoted even when they exceed one PAGE_SIZE batch."""
        from zolai.knowledge import promotion

        original = promotion.PAGE_SIZE
        promotion.PAGE_SIZE = 2
        try:
            bible = add_evidence(engine, fact_key="word:page", tier=EvidenceTier.BIBLE_PARALLEL, source="bible_verses")
            for index in range(5):
                add_hypothesis(
                    engine,
                    kind="pos",
                    subject=f"word:page{index}",
                    predicate="pos:NOUN",
                    evidence_ids=[bible],
                )
            summary = promote_hypotheses_to_claims(engine, kinds=["pos"])
        finally:
            promotion.PAGE_SIZE = original

        assert summary.hypotheses_considered == 5
        assert summary.claims_created == 5


class TestPromotionSummary:
    def test_to_dict_round_trip(self) -> None:
        summary = PromotionSummary(
            kinds=["pos"],
            dry_run=True,
            hypotheses_considered=3,
            claims_planned=2,
            claims_created=0,
            claims_skipped_duplicate=1,
            errors=["boom"],
        )
        payload = summary.to_dict()
        assert payload["kinds"] == ["pos"]
        assert payload["dry_run"] is True
        assert payload["claims_planned"] == 2
        assert payload["claims_created"] == 0
        assert payload["errors"] == ["boom"]
        assert json.loads(json.dumps(payload))["hypotheses_considered"] == 3
