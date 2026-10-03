"""Tests for Morphology Discovery."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.learning.discovery.morphology import MorphologyDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    return get_engine()


def test_morphology_discovery_runs(db_engine: Engine) -> None:
    """Test that morphology discovery runs without error (small limit)."""
    discovery = MorphologyDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    assert "words_analyzed" in result
    assert "relations_written" in result
    assert "status_counts" in result
    assert result["relations_written"] >= 0
    assert result["words_analyzed"] >= 0


def test_morphology_hypotheses_written_have_evidence(db_engine: Engine) -> None:
    """Test that written morphology relations have evidence_ids and correct status."""
    discovery = MorphologyDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, kind, subject, predicate, object, evidence_ids, status, confidence FROM hypotheses WHERE kind = 'morph_relation'")
        ).fetchall()

    for row in rows:
        assert row.kind == "morph_relation"
        assert row.subject.startswith("word:")
        assert row.predicate.startswith("morph:")
        assert row.evidence_ids is not None
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0


def test_morphology_confidence_is_two_dp(db_engine: Engine) -> None:
    """Test that confidence is rounded to 2 decimal places."""
    discovery = MorphologyDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT confidence FROM hypotheses WHERE kind = 'morph_relation'")
        ).fetchall()

    for row in rows:
        if row.confidence is not None:
            conf = float(row.confidence)
            assert round(conf, 2) == conf


def test_morphology_evidence_tiers(db_engine: Engine) -> None:
    """Test that evidence uses correct tiers (DICTIONARY=2, BIBLE=1, CORPUS=4)."""
    discovery = MorphologyDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tier FROM foundation_evidence WHERE extractor = 'discovery' AND fact_type = 'word'")
        ).fetchall()

    for row in rows:
        # Morphology should use tier 1 (BIBLE), 2 (DICTIONARY), or 4 (CORPUS)
        assert row.tier in (1, 2, 4)