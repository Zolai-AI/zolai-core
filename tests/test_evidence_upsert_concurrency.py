"""Regression tests for concurrent discovery evidence upserts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.data.migrations import apply_foundation_constraints, create_foundation_tables
from zolai.learning.discovery.evidence import upsert_evidence


def _create_legacy_evidence_table(mgr: DatabaseManager) -> None:
    create_foundation_tables(mgr)
    with mgr.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_fev_fact_source_method_unique"))


def test_evidence_identity_index_preserves_legacy_null_methods(tmp_path) -> None:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'evidence.db'}")
    _create_legacy_evidence_table(mgr)
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO foundation_evidence "
                "(fact_type, fact_key, tier, source, confidence, provenance_hash, payload, created_at) "
                "VALUES ('word', 'word:legacy', 1, 'bible_verses', 1.0, '', '{}', 'legacy')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO foundation_evidence "
                "(fact_type, fact_key, tier, source, confidence, provenance_hash, payload, created_at) "
                "VALUES ('word', 'word:legacy', 1, 'bible_verses', 1.0, '', '{}', 'legacy')"
            )
        )

    result = apply_foundation_constraints(mgr)
    assert not result["errors"], result["errors"]

    with mgr.engine.connect() as conn:
        index_sql = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'ix_fev_fact_source_method_unique'"
            )
        ).scalar_one()
    assert "method IS NOT NULL" in index_sql
    mgr.dispose()


def test_concurrent_evidence_upserts_share_one_row(tmp_path) -> None:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'evidence.db'}")
    _create_legacy_evidence_table(mgr)
    result = apply_foundation_constraints(mgr)
    assert not result["errors"], result["errors"]

    workers = 8
    barrier = Barrier(workers)

    def write(index: int) -> int:
        barrier.wait()
        return upsert_evidence(
            mgr.engine,
            "word",
            "pasian",
            1,
            "bible_verses",
            "discovery",
            "test",
            {"worker": index},
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ids = list(pool.map(write, range(workers)))

    with mgr.engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM foundation_evidence "
                "WHERE fact_type = 'word' AND fact_key = 'word:pasian' "
                "AND source = 'bible_verses' AND method = 'discovery'"
            )
        ).scalar_one()
    assert count == 1
    assert len(set(ids)) == 1
    mgr.dispose()
