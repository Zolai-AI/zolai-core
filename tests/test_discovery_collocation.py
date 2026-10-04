"""Tests for Collocation Discovery."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.learning.discovery.collocation import CollocationDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    return get_engine()


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

    for row in rows:
        assert row.kind == "collocation"
        assert row.subject.startswith("word:")
        assert row.predicate == "collocates_with"
        assert row.object.startswith("word:")
        assert row.evidence_ids is not None
        assert row.status == "CANDIDATE"  # Corpus only -> CANDIDATE
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0
        # extras should have pmi, count, window
        import json
        extras = json.loads(row.extras) if isinstance(row.extras, str) else row.extras
        assert "pmi" in extras
        assert "count" in extras
        assert "window" in extras


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
