"""Tests for Discovery Pipeline."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.learning.discovery.pipeline import DiscoveryPipeline, build_discovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    return get_engine()


def test_discovery_pipeline_runs(db_engine: Engine) -> None:
    """Test that the full discovery pipeline runs (small limit)."""
    pipeline = DiscoveryPipeline(db_engine, capabilities=["pos", "morphology"], limit=5)
    result = pipeline.build()

    assert result["pipeline_version"] == "phase3-v1"
    assert "capabilities_run" in result
    assert "pos" in result["capabilities_run"]
    assert "morphology" in result["capabilities_run"]
    assert "pos" in result
    assert "morphology" in result


def test_discovery_pipeline_dry_run(db_engine: Engine) -> None:
    """Test that dry-run returns summary without writing."""
    pipeline = DiscoveryPipeline(db_engine, capabilities=["pos"], limit=5, dry_run=True)
    result = pipeline.build()

    assert result["dry_run"] is True
    assert result["capabilities_run"] == []


def test_build_discovery_convenience(db_engine: Engine) -> None:
    """Test the module-level build_discovery wrapper."""
    result = build_discovery(db_engine, capabilities=["collocation"], limit=5)

    assert result["pipeline_version"] == "phase3-v1"
    assert "collocation" in result["capabilities_run"]


def test_discovery_idempotent(db_engine: Engine) -> None:
    """Test that running discovery twice adds no new rows (idempotent)."""
    # Run once
    pipeline = DiscoveryPipeline(db_engine, capabilities=["pos"], limit=5)
    result1 = pipeline.build()
    count1 = result1.get("pos", {}).get("hypotheses_written", 0)

    # Run again
    result2 = pipeline.build()
    count2 = result2.get("pos", {}).get("hypotheses_written", 0)

    # Second run should write 0 new rows (all upserts)
    assert count2 == 0


def test_hypotheses_status_only_observed_candidate(db_engine: Engine) -> None:
    """Test that all hypotheses have status OBSERVED or CANDIDATE only."""
    pipeline = DiscoveryPipeline(
        db_engine, capabilities=["pos", "morphology", "collocation"], limit=5
    )
    pipeline.build()

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT status FROM hypotheses WHERE kind IN ('pos', 'morph_relation', 'collocation')")
        ).fetchall()

    statuses = {row.status for row in rows}
    assert statuses.issubset({"OBSERVED", "CANDIDATE"})
    assert "SUPPORTED" not in statuses
    assert "VERIFIED" not in statuses


def test_foundation_evidence_has_method_extractor(db_engine: Engine) -> None:
    """Test that all discovery evidence rows have method and extractor fields."""
    pipeline = DiscoveryPipeline(
        db_engine, capabilities=["pos", "morphology", "collocation"], limit=5
    )
    pipeline.build()

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT method, extractor FROM foundation_evidence WHERE extractor = 'discovery'")
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.method
        assert row.extractor == "discovery"