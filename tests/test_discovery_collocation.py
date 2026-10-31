"""Tests for Collocation Discovery."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.knowledge.promotion import promote_hypotheses_to_claims
from zolai.learning.discovery.collocation import CollocationDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name

    manager = DatabaseManager(f"sqlite:///{path}")
    manager.init_db()
    with manager.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO word_collocations "
                "(word1, word2, frequency, pmiproxy) "
                "VALUES ('pasian', 'gam', 12, 4.5)"
            )
        )

    yield manager.engine
    manager.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def test_collocation_discovery_runs(db_engine: Engine) -> None:
    """Test that collocation discovery runs without error (small limit)."""
    discovery = CollocationDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    assert "collocations_written" in result
    assert "status_counts" in result
    assert result["collocations_written"] >= 0


def test_collocation_hypotheses_written_have_evidence(db_engine: Engine) -> None:
    """Test that written collocation hypotheses have evidence_ids and correct status."""
    discovery = CollocationDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, subject, predicate, object, evidence_ids, status, "
                "confidence, extras FROM hypotheses WHERE kind = 'collocation'"
            )
        ).fetchall()

    assert rows, "no collocation hypotheses to check"
    for row in rows:
        assert row.kind == "collocation"
        assert row.subject.startswith("word:")
        assert row.predicate == "collocates_with"
        assert row.object.startswith("word:")
        assert row.evidence_ids is not None
        evidence_ids = json.loads(row.evidence_ids)
        assert evidence_ids
        with db_engine.connect() as conn:
            resolved = conn.execute(
                text(
                    "SELECT COUNT(*) FROM foundation_evidence "
                    "WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": evidence_ids},
            ).scalar_one()
        assert resolved == len(evidence_ids)
        assert row.status == "CANDIDATE"  # Corpus only -> CANDIDATE
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0
        # extras should have pmi, count, window
        extras = json.loads(row.extras) if isinstance(row.extras, str) else row.extras
        assert "pmi" in extras
        assert "count" in extras
        assert "window" in extras

    summary = promote_hypotheses_to_claims(db_engine, kinds=["collocation"])
    assert summary.claims_created > 0


def test_collocation_confidence_is_two_dp(db_engine: Engine) -> None:
    """Test that confidence is rounded to 2 decimal places."""
    discovery = CollocationDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT confidence FROM hypotheses WHERE kind = 'collocation'")
        ).fetchall()

    for row in rows:
        if row.confidence is not None:
            conf = float(row.confidence)
            assert round(conf, 2) == conf


def test_collocation_no_supported_verified(db_engine: Engine) -> None:
    """Test that discovery never writes SUPPORTED/VERIFIED status."""
    discovery = CollocationDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status FROM hypotheses WHERE kind = 'collocation'")
        ).fetchall()

    for row in rows:
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.status not in ("SUPPORTED", "VERIFIED", "REJECTED", "DEPRECATED")
