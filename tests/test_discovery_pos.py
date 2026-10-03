"""Tests for POS Discovery."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.learning.discovery.pos import POSDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    return get_engine()


def test_pos_discovery_runs(db_engine: Engine) -> None:
    """Test that POS discovery runs without error (small limit)."""
    discovery = POSDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    assert "words_processed" in result
    assert "hypotheses_written" in result
    assert "status_counts" in result
    assert result["hypotheses_written"] >= 0
    assert result["words_processed"] >= 0


def test_pos_hypotheses_written_have_evidence(db_engine: Engine) -> None:
    """Test that written POS hypotheses have evidence_ids and correct status."""
    discovery = POSDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    # Check hypotheses table
    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, kind, subject, predicate, evidence_ids, status, confidence FROM hypotheses WHERE kind = 'pos'")
        ).fetchall()

    for row in rows:
        assert row.kind == "pos"
        assert row.subject.startswith("word:")
        assert row.predicate.startswith("pos:")
        assert row.evidence_ids is not None
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0


def test_pos_confidence_is_two_dp(db_engine: Engine) -> None:
    """Test that confidence is rounded to 2 decimal places."""
    discovery = POSDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT confidence FROM hypotheses WHERE kind = 'pos'")
        ).fetchall()

    for row in rows:
        if row.confidence is not None:
            conf = float(row.confidence)
            # Check 2 decimal places
            assert round(conf, 2) == conf


def test_pos_evidence_rows_created(db_engine: Engine) -> None:
    """Test that foundation_evidence rows are created for POS hypotheses."""
    discovery = POSDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    # Check evidence table
    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, fact_type, fact_key, tier, source, method, extractor FROM foundation_evidence WHERE extractor = 'discovery'")
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.fact_type in ("word", "grammar")
        assert row.tier in (1, 2, 3, 4, 5)
        assert row.source
        assert row.method
        assert row.extractor == "discovery"