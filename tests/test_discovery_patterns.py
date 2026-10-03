"""Tests for Sentence Pattern and Grammar Discovery."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.learning.discovery.sentence_patterns import SentencePatternDiscovery
from zolai.learning.discovery.grammar import GrammarDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    return get_engine()


def test_sentence_pattern_discovery_runs(db_engine: Engine) -> None:
    """Test that sentence pattern discovery runs without error."""
    discovery = SentencePatternDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    assert "patterns_written" in result
    assert "status_counts" in result
    assert "pattern_types" in result
    assert result["patterns_written"] >= 0


def test_sentence_patterns_use_disc_sp_namespace(db_engine: Engine) -> None:
    """Test that sentence patterns use disc_sp_* namespace."""
    discovery = SentencePatternDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT pattern_id, pattern, normalized, status, confidence, evidence_ids FROM grammar_patterns WHERE pattern_id LIKE 'disc_sp_%'")
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.pattern_id.startswith("disc_sp_")
        assert row.pattern
        assert row.normalized
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0
        assert row.evidence_ids is not None


def test_grammar_discovery_runs(db_engine: Engine) -> None:
    """Test that grammar phenomena discovery runs without error."""
    discovery = GrammarDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    assert "phenomena_found" in result
    assert "patterns_written" in result
    assert "status_counts" in result
    assert result["patterns_written"] >= 0


def test_grammar_patterns_use_disc_g_namespace(db_engine: Engine) -> None:
    """Test that grammar patterns use disc_g_* namespace."""
    discovery = GrammarDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT pattern_id, pattern, function, status, confidence, evidence_ids FROM grammar_patterns WHERE pattern_id LIKE 'disc_g_%'")
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.pattern_id.startswith("disc_g_")
        assert row.pattern
        assert row.function
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0
        assert row.evidence_ids is not None


def test_baseline_grammar_patterns_unchanged(db_engine: Engine) -> None:
    """Test that existing 5,560 baseline grammar_patterns are untouched."""
    # Count before
    with db_engine.connect() as conn:
        before = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE pattern_id NOT LIKE 'disc_%'")
        ).scalar()

    # Run discovery
    discovery = GrammarDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        discovery.run(conn)

    # Count after
    with db_engine.connect() as conn:
        after = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE pattern_id NOT LIKE 'disc_%'")
        ).scalar()

    # Should be unchanged (5,560 baseline)
    assert after == before
    assert after >= 5000  # at least the known baseline