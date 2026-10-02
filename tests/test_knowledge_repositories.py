"""Tests for the Phase 1 knowledge repositories (Master Prompt §36).

Covers:
- claim create → ``data_audit_log`` row exists
- evidence gate: SUPPORTED/VERIFIED claims require linked ``claim_evidence``
  rows (create + update paths); links written atomically with the claim
- evidence join query (``get_evidence``) and kind-scoped hypothesis queries
  ('pos' / 'morph_relation')
- optimistic locking conflict raises ``OptimisticLockError``
- contract ``model_dump()`` writes (view-only fields dropped, JSON columns)
- ``get_repositories`` registers claims / hypotheses / knowledge_versions
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from zolai.data.database import DatabaseManager
from zolai.data.repositories import (
    ClaimRepository,
    HypothesisRepository,
    KnowledgeVersionRepository,
    get_repositories,
)
from zolai.data.repositories.base import OptimisticLockError
from zolai.shared.contracts import (
    EvidenceGateError,
    KnowledgeClaim,
    KnowledgeStatus,
    KnowledgeVersion,
    MorphologicalRelation,
    POSHypothesis,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEED_EVIDENCE = (
    "INSERT INTO foundation_evidence "
    "(fact_type, fact_key, tier, source, confidence, provenance_hash, payload, created_at) "
    "VALUES ('word', 'word:gam', 1, 'bible_verses', 0.9, 'h1', '{}', '2026-10-01T00:00:00+00:00')",
    "INSERT INTO foundation_evidence "
    "(fact_type, fact_key, tier, source, confidence, provenance_hash, payload, created_at) "
    "VALUES ('word', 'word:gam', 4, 'corpus', 0.7, 'h2', '{}', '2026-10-01T00:00:00+00:00')",
)


@pytest.fixture()
def repo_db(tmp_path: Path):
    """Temporary DB with the full ORM schema + seeded foundation evidence."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'knowledge.db'}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        for stmt in SEED_EVIDENCE:
            conn.execute(text(stmt))
    yield mgr
    mgr.dispose()


@pytest.fixture()
def claims(repo_db: DatabaseManager) -> ClaimRepository:
    return ClaimRepository(repo_db.engine)


@pytest.fixture()
def hypotheses(repo_db: DatabaseManager) -> HypothesisRepository:
    return HypothesisRepository(repo_db.engine)


@pytest.fixture()
def versions(repo_db: DatabaseManager) -> KnowledgeVersionRepository:
    return KnowledgeVersionRepository(repo_db.engine)


def _audit_rows(mgr: DatabaseManager, table_name: str, row_id: int) -> list:
    with mgr.engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT field, old_value, new_value, reason FROM data_audit_log "
                "WHERE table_name = :t AND row_id = :r ORDER BY id"
            ),
            {"t": table_name, "r": row_id},
        ).all()


def _base_claim(**overrides) -> dict:
    data = {
        "claim_type": "lexicon",
        "subject": "word:gam",
        "predicate": "means",
        "object": "earth",
        "status": KnowledgeStatus.OBSERVED.value,
        "evidence_ids": [],
        "source_ids": [],
        "notes": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# ClaimRepository
# ---------------------------------------------------------------------------


class TestClaimAuditTrail:
    def test_create_writes_audit_row(self, claims: ClaimRepository, repo_db: DatabaseManager) -> None:
        claim_id = claims.create(_base_claim(), user="tester")
        rows = _audit_rows(repo_db, "knowledge_claims", claim_id)
        assert len(rows) == 1
        field, old_value, new_value, reason = rows[0]
        assert field == "create"
        assert old_value is None
        assert "lexicon" in (new_value or "")
        assert reason == "repo:create:tester"

    def test_update_writes_audit_row(
        self, claims: ClaimRepository, repo_db: DatabaseManager
    ) -> None:
        claim_id = claims.create(_base_claim(), user="tester")
        claims.update(claim_id, {"notes": "reviewed"}, user="tester", expected_version=1)
        fields = [r.field for r in _audit_rows(repo_db, "knowledge_claims", claim_id)]
        assert fields == ["create", "update"]

    def test_contract_model_dump_is_writable(
        self, claims: ClaimRepository, repo_db: DatabaseManager
    ) -> None:
        claim = KnowledgeClaim(
            claim_type="lexicon",
            subject="word:vantung",
            predicate="means",
            object="heaven",
            confidence=1.0,
            evidence_ids=[1],
            status=KnowledgeStatus.SUPPORTED,
        )
        claim_id = claims.create(claim.model_dump(), user="contract")
        row = claims.get_by_id(claim_id)
        assert row is not None
        assert row["status"] == "SUPPORTED"
        assert row["confidence"] == 1.0
        assert claims.get_evidence_ids(claim_id) == [1]


class TestClaimEvidenceGate:
    def test_gated_status_without_evidence_raises(
        self, claims: ClaimRepository, repo_db: DatabaseManager
    ) -> None:
        for status in ("SUPPORTED", "VERIFIED"):
            with pytest.raises(EvidenceGateError):
                claims.create(_base_claim(status=status), user="gate")
        # Nothing was written.
        assert claims.count() == 0
        assert _audit_rows(repo_db, "knowledge_claims", 1) == []

    @pytest.mark.parametrize("status", ["OBSERVED", "CANDIDATE", "REJECTED", "DEPRECATED"])
    def test_open_statuses_do_not_require_evidence(
        self, claims: ClaimRepository, status: str
    ) -> None:
        claim_id = claims.create(_base_claim(status=status), user="gate")
        assert claim_id > 0

    def test_gated_status_with_evidence_creates_link(
        self, claims: ClaimRepository, repo_db: DatabaseManager
    ) -> None:
        claim_id = claims.create(
            _base_claim(status="SUPPORTED", evidence_ids=[1]), user="gate"
        )
        assert claims.get_evidence_ids(claim_id) == [1]
        evidence = claims.get_evidence(claim_id)
        assert evidence[0]["fact_key"] == "word:gam"
        assert evidence[0]["tier"] == 1
        assert evidence[0]["role"] == "supports"

    def test_update_gate_rejects_bare_status_change(
        self, claims: ClaimRepository
    ) -> None:
        claim_id = claims.create(_base_claim(), user="gate")
        with pytest.raises(EvidenceGateError):
            claims.update(claim_id, {"status": "SUPPORTED"}, expected_version=1)
        assert claims.get_by_id(claim_id)["status"] == "OBSERVED"

    def test_update_gate_accepts_status_with_evidence(
        self, claims: ClaimRepository
    ) -> None:
        claim_id = claims.create(_base_claim(), user="gate")
        ok = claims.update(
            claim_id,
            {"status": "SUPPORTED", "evidence_ids": [1]},
            expected_version=1,
        )
        assert ok is True
        assert claims.get_by_id(claim_id)["status"] == "SUPPORTED"
        assert claims.get_evidence_ids(claim_id) == [1]

    def test_evidence_only_update_preserves_status_and_links(
        self, claims: ClaimRepository, repo_db: DatabaseManager
    ) -> None:
        claim_id = claims.create(
            _base_claim(status="SUPPORTED", evidence_ids=[1]), user="gate"
        )
        ok = claims.update(claim_id, {"evidence_ids": [1, 2]}, expected_version=1)
        assert ok is True
        row = claims.get_by_id(claim_id)
        assert row["status"] == "SUPPORTED"  # status not silently reset
        assert claims.get_evidence_ids(claim_id) == [1, 2]
        fields = [r.field for r in _audit_rows(repo_db, "knowledge_claims", claim_id)]
        assert "link_evidence" in fields

    def test_unknown_evidence_id_hits_foreign_key(self, claims: ClaimRepository) -> None:
        with pytest.raises(IntegrityError):
            claims.create(
                _base_claim(status="VERIFIED", evidence_ids=[99999]), user="gate"
            )
        assert claims.count() == 0  # rolled back

    def test_duplicate_claim_key_rejected(self, claims: ClaimRepository) -> None:
        claims.create(_base_claim(), user="dup")
        with pytest.raises(IntegrityError):
            claims.create(_base_claim(notes="again"), user="dup")
        assert claims.count() == 1


class TestClaimOptimisticLocking:
    def test_stale_expected_version_raises(
        self, claims: ClaimRepository, repo_db: DatabaseManager
    ) -> None:
        claim_id = claims.create(_base_claim(), user="lock")
        with pytest.raises(OptimisticLockError):
            claims.update(claim_id, {"notes": "stale"}, expected_version=99)
        assert claims.get_by_id(claim_id)["version"] == 1
        assert claims.get_by_id(claim_id)["notes"] == ""

    def test_correct_expected_version_increments(
        self, claims: ClaimRepository
    ) -> None:
        claim_id = claims.create(_base_claim(), user="lock")
        assert claims.get_by_id(claim_id)["version"] == 1
        claims.update(claim_id, {"notes": "v2"}, expected_version=1)
        row = claims.get_by_id(claim_id)
        assert row["version"] == 2
        assert row["notes"] == "v2"
        with pytest.raises(OptimisticLockError):
            claims.update(claim_id, {"notes": "v3"}, expected_version=1)

    def test_find_by_type(self, claims: ClaimRepository) -> None:
        claims.create(_base_claim(), user="q")
        claims.create(
            _base_claim(subject="word:van", predicate="means", object="sky"), user="q"
        )
        claims.create(
            _base_claim(claim_type="grammar", subject="pattern:x", predicate="matches"),
            user="q",
        )
        assert len(claims.find_by_type("lexicon")) == 2
        assert len(claims.find_by_type("grammar")) == 1
        assert len(claims.find_by_type("lexicon", status="OBSERVED")) == 2
        assert claims.find_by_type("lexicon", status="VERIFIED") == []


# ---------------------------------------------------------------------------
# HypothesisRepository
# ---------------------------------------------------------------------------


class TestHypothesisKindScopedQueries:
    @pytest.fixture()
    def seeded(self, hypotheses: HypothesisRepository) -> HypothesisRepository:
        hypotheses.create(
            {
                "kind": "pos",
                "subject": "word:gam",
                "predicate": "pos:NOUN",
                "status": KnowledgeStatus.CANDIDATE,
                "evidence_ids": [1],
                "extras": {"probability": 0.9},
            },
            user="seed",
        )
        hypotheses.create(
            {
                "kind": "pos",
                "subject": "word:van",
                "predicate": "pos:NOUN",
            },
            user="seed",
        )
        hypotheses.create(
            {
                "kind": "morph_relation",
                "subject": "word:piangsak",
                "predicate": "morph:suffix",
                "object": "piang",
                "extras": {"type": "suffix", "function": "causative"},
            },
            user="seed",
        )
        return hypotheses

    def test_find_by_kind_separates_views(self, seeded: HypothesisRepository) -> None:
        pos_rows = seeded.find_by_kind("pos")
        morph_rows = seeded.find_by_kind("morph_relation")
        assert len(pos_rows) == 2
        assert len(morph_rows) == 1
        assert all(r["kind"] == "pos" for r in pos_rows)
        assert morph_rows[0]["predicate"] == "morph:suffix"

    def test_find_pos_scoped_to_headword(self, seeded: HypothesisRepository) -> None:
        rows = seeded.find_pos(word="gam")
        assert [r["subject"] for r in rows] == ["word:gam"]
        assert len(seeded.find_pos()) == 2

    def test_find_morph_relations_scoped_to_surface(
        self, seeded: HypothesisRepository
    ) -> None:
        rows = seeded.find_morph_relations(surface="piangsak")
        assert len(rows) == 1
        assert rows[0]["object"] == "piang"

    def test_kind_scoped_status_filter(self, seeded: HypothesisRepository) -> None:
        assert len(seeded.find_by_kind("pos", status="CANDIDATE")) == 1
        assert seeded.find_by_kind("pos", status="VERIFIED") == []

    def test_create_normalizes_contract_dump(
        self, hypotheses: HypothesisRepository, repo_db: DatabaseManager
    ) -> None:
        pos = POSHypothesis(
            word="pasian", pos="NOUN", probability=0.95, evidence_ids=[1]
        )
        hyp_id = hypotheses.create(pos.model_dump(), user="contract")
        row = hypotheses.get_by_id(hyp_id)
        # View-only contract fields (word/pos) are dropped; canonical SPO kept.
        assert "word" not in row
        assert row["subject"] == "word:pasian"
        assert row["predicate"] == "pos:NOUN"
        assert row["kind"] == "pos"
        assert row["evidence_ids"] == "[1]"
        assert row["status"] == "OBSERVED"
        # audit row from BaseRepository.create
        assert _audit_rows(repo_db, "hypotheses", hyp_id)[0].field == "create"

    def test_morph_relation_contract_dump(
        self, hypotheses: HypothesisRepository
    ) -> None:
        rel = MorphologicalRelation(
            surface="piangsak",
            root="piang",
            relation_type="suffix",
            function="causative",
        )
        hyp_id = hypotheses.create(rel.model_dump(), user="contract")
        row = hypotheses.get_by_id(hyp_id)
        assert row["subject"] == "word:piangsak"
        assert row["predicate"] == "morph:suffix"
        assert row["object"] == "piang"
        assert row["extras"] == '{"type": "suffix", "function": "causative"}'
        assert row["kind"] == "morph_relation"

    def test_optimistic_locking_on_hypotheses(
        self, hypotheses: HypothesisRepository
    ) -> None:
        hyp_id = hypotheses.create(
            {"kind": "pos", "subject": "word:x", "predicate": "pos:NOUN"}, user="lock"
        )
        with pytest.raises(OptimisticLockError):
            hypotheses.update(hyp_id, {"probability": 0.5}, expected_version=42)


# ---------------------------------------------------------------------------
# KnowledgeVersionRepository
# ---------------------------------------------------------------------------


class TestKnowledgeVersionRepository:
    def test_create_and_fetch_by_version(
        self, versions: KnowledgeVersionRepository, repo_db: DatabaseManager
    ) -> None:
        snapshot = KnowledgeVersion(
            version="2026.10.0",
            git_commit="abc1234",
            row_counts={"vocabulary": 104906},
            quality={"syllable_accuracy": 0.9849},
        )
        vid = versions.create(snapshot.model_dump(), user="release")
        row = versions.get_by_version("2026.10.0")
        assert row is not None
        assert row["id"] == vid
        assert row["row_counts"] == '{"vocabulary": 104906}'
        assert row["status"] == "OBSERVED"
        assert row["row_version"] == 1
        assert _audit_rows(repo_db, "knowledge_versions", vid)[0].field == "create"

    def test_duplicate_release_identifier_rejected(
        self, versions: KnowledgeVersionRepository
    ) -> None:
        versions.create({"version": "2026.10.0"}, user="release")
        with pytest.raises(IntegrityError):
            versions.create({"version": "2026.10.0"}, user="release")

    def test_optimistic_lock_uses_row_version_not_version(
        self, versions: KnowledgeVersionRepository
    ) -> None:
        """The TEXT ``version`` release id must never be used as a counter."""
        vid = versions.create({"version": "2026.10.0"}, user="release")
        with pytest.raises(OptimisticLockError):
            versions.update(vid, {"notes": "x"}, expected_version=99)
        row = versions.get_by_id(vid)
        assert row["version"] == "2026.10.0"  # untouched release id
        versions.update(vid, {"manifest_hash": "deadbeef"}, expected_version=1)
        row = versions.get_by_id(vid)
        assert row["row_version"] == 2
        assert row["version"] == "2026.10.0"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRepositoryRegistration:
    def test_get_repositories_registers_knowledge_repos(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "registered.db")
        repos = get_repositories(db_path)
        assert isinstance(repos["claims"], ClaimRepository)
        assert isinstance(repos["hypotheses"], HypothesisRepository)
        assert isinstance(repos["knowledge_versions"], KnowledgeVersionRepository)
        # Same classes as the package exports (no duplicate layer).
        assert type(repos["claims"]) is ClaimRepository
        assert type(repos["hypotheses"]) is HypothesisRepository
        assert type(repos["knowledge_versions"]) is KnowledgeVersionRepository

    def test_package_exports(self) -> None:
        import zolai.data.repositories as pkg

        assert pkg.ClaimRepository is ClaimRepository
        assert "ClaimRepository" in pkg.__all__
        assert "HypothesisRepository" in pkg.__all__
        assert "KnowledgeVersionRepository" in pkg.__all__
