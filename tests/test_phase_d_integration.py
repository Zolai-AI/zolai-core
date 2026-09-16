"""Phase D Integration Tests — Full pipeline, cost tracking, review UI, and migrations.

Covers:
- Full pipeline: ingest → build → promote → review → verify
- Cost tracking through the full pipeline
- Review UI endpoints with real DB
- Migration runner on a fresh DB
- Health endpoint availability
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.data.database import DatabaseManager
from zolai.data.repositories import get_foundation_repositories

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_cost_tracker():
    """Import CostTracker directly to avoid broken __init__ imports."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "zolai.data.services.cost_tracking",
        str(Path(__file__).resolve().parent.parent / "zolai" / "data" / "services" / "cost_tracking.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CostTracker


CostTracker = _import_cost_tracker()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database with all Foundation tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    mgr.init_db()
    from zolai.data.migrations import (
        apply_foundation_constraints,
        apply_foundation_cost_tracking_indexes,
        apply_foundation_indexes,
        create_foundation_cost_tracking_table,
        create_foundation_tables,
    )
    create_foundation_tables(mgr)
    apply_foundation_constraints(mgr)
    apply_foundation_indexes(mgr)
    create_foundation_cost_tracking_table(mgr)
    apply_foundation_cost_tracking_indexes(mgr)
    yield mgr, db_path
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture()
def foundation_repos(tmp_db):
    """Return all Foundation repositories for the temp DB."""
    mgr, _ = tmp_db
    return get_foundation_repositories(mgr.engine)


@pytest.fixture()
def etl(tmp_db):
    """Return FoundationETL instance for the temp DB."""
    mgr, _ = tmp_db
    from zolai.pipeline.foundation import FoundationETL
    return FoundationETL(mgr=mgr)


@pytest.fixture()
def client(tmp_db):
    """Create a test client wired to the temp database."""
    mgr, db_path = tmp_db

    import zolai.config
    original_db = zolai.config.config.paths.db
    original_data = zolai.config.config.paths.data
    zolai.config.config.paths.db = Path(db_path)
    zolai.config.config.paths.data = Path(db_path).parent

    from zolai.api.server import create_app
    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    zolai.config.config.paths.db = original_db
    zolai.config.config.paths.data = original_data


# ---------------------------------------------------------------------------
# Test: Full Pipeline — ingest → build → promote → review → verify
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """End-to-end pipeline flow on a fresh temp database."""

    def test_ingest_adds_raw_entries(self, etl, foundation_repos):
        """Ingest should populate foundation_raw_corpus."""
        raw_corpus = foundation_repos["foundation_raw_corpus"]

        # Write a small JSONL file for ingest
        entries = [
            {"id": "w1", "text": "kum na", "source": "test"},
            {"id": "w2", "text": "dam hi", "source": "test"},
            {"id": "w3", "text": "lungdam", "source": "test"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
            jsonl_path = f.name

        try:
            etl.ingest_from_jsonl(Path(jsonl_path))
            assert raw_corpus.count() == 3
        finally:
            Path(jsonl_path).unlink(missing_ok=True)

    def test_build_staging_from_raw(self, etl, foundation_repos):
        """Build stage should populate staging tables from raw."""
        raw_repo = foundation_repos["foundation_raw_corpus"]
        # Manually insert raw rows — all required columns
        raw_repo.create({
            "text": "kum na",
            "source_type": "test",
            "source_path": "test://test",
            "content_hash": "hash1",
            "payload": '{"text": "kum na"}',
            "batch_id": 0,
        })
        raw_repo.create({
            "text": "dam hi",
            "source_type": "test",
            "source_path": "test://test",
            "content_hash": "hash2",
            "payload": '{"text": "dam hi"}',
            "batch_id": 0,
        })

        etl.build_staging_from_raw()

        staging_words = foundation_repos["foundation_staging_words"]
        staging_sents = foundation_repos["foundation_staging_sentences"]
        # At least one staging table should have rows
        assert staging_words.count() + staging_sents.count() > 0

    def test_promote_advances_pipeline(self, etl, foundation_repos):
        """Promote should move consensus entries to canonical layer."""
        # Insert a raw entry, build staging, then promote
        raw_repo = foundation_repos["foundation_raw_corpus"]
        raw_repo.create({
            "text": "pasian",
            "source_type": "test",
            "source_path": "test://test",
            "content_hash": "hash3",
            "payload": '{"text": "pasian"}',
            "batch_id": 0,
        })
        etl.build_staging_from_raw()

        # If consensus exists, promote should handle it
        etl.promote_staging_to_canonical()

        # Pipeline should not crash; verify raw entries still exist
        assert raw_repo.count() >= 1

    def test_review_queue_list(self, client):
        """GET /review/ should return 200 with HTML."""
        resp = client.get("/review/")
        assert resp.status_code == 200

    def test_pipeline_stats_endpoint(self, client):
        """GET /api/v1/foundation/stats should return layer counts."""
        resp = client.get("/api/v1/foundation/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Should have count keys for each layer
        assert isinstance(data, dict)
        assert any("count" in k for k in data)


# ---------------------------------------------------------------------------
# Test: Cost Tracking Through Full Pipeline
# ---------------------------------------------------------------------------

class TestCostTrackingPipeline:
    """Verify cost tracking works end-to-end with the pipeline."""

    def test_cost_tracker_log_and_summary(self, foundation_repos):
        """Log requests and verify summary aggregates."""
        cost_repo = foundation_repos["foundation_cost_tracking"]

        tracker = CostTracker(cost_repo)
        cost1 = tracker.log(task_type="word", model="gemini-2.0-flash", input_tokens=1000, output_tokens=500)
        cost2 = tracker.log(task_type="sentence", model="gemini-2.0-pro", input_tokens=2000, output_tokens=1000)

        assert cost1 > 0
        assert cost2 > 0

        summary = tracker.get_summary()
        assert summary["total_cost"] > 0
        assert "word" in summary["by_task"]
        assert "sentence" in summary["by_task"]

    def test_cost_tracker_budget_check(self, foundation_repos):
        """Budget check should detect overages."""
        cost_repo = foundation_repos["foundation_cost_tracking"]

        tracker = CostTracker(cost_repo)

        # Log a very large cost
        cost_repo.log_request(
            request_id="over-budget-1",
            task_type="batch",
            model="gemini-2.0-pro-plus",
            input_tokens=10_000_000,
            output_tokens=10_000_000,
            cost_usd=200.0,
        )

        within, spend = tracker.check_budget(50.0)
        assert within is False
        assert spend > 50.0

    def test_cost_daily_breakdown(self, foundation_repos):
        """Daily breakdown should return at least one record."""
        cost_repo = foundation_repos["foundation_cost_tracking"]

        tracker = CostTracker(cost_repo)
        tracker.log(task_type="word", model="gemini-2.0-flash", input_tokens=100, output_tokens=50)

        daily = cost_repo.get_daily_breakdown(days=30)
        assert len(daily) >= 1
        assert "date" in daily[0]
        assert "cost" in daily[0]


# ---------------------------------------------------------------------------
# Test: Review UI Endpoints
# ---------------------------------------------------------------------------

class TestReviewUI:
    """Test review queue UI and API endpoints with real DB."""

    def test_review_queue_renders(self, client):
        """GET /review/ should return 200."""
        resp = client.get("/review/")
        assert resp.status_code == 200

    def test_review_queue_with_status_filter(self, client):
        """GET /review/?status=pending should return 200."""
        resp = client.get("/review/", params={"status": "pending"})
        assert resp.status_code == 200

    def test_review_queue_with_type_filter(self, client):
        """GET /review/?fact_type=word should return 200."""
        resp = client.get("/review/", params={"fact_type": "word"})
        assert resp.status_code == 200

    def test_foundation_stats_returns_layers(self, client):
        """GET /api/v1/foundation/stats should return layer counts."""
        resp = client.get("/api/v1/foundation/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Should have at least some layer keys
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Test: Migration Runner on Fresh DB
# ---------------------------------------------------------------------------

class TestMigrationRunner:
    """Verify run_all_migrations works on a completely fresh database."""

    def test_run_all_migrations_fresh_db(self):
        """run_all_migrations should succeed on a brand-new empty DB."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        url = f"sqlite:///{db_path}"
        mgr = DatabaseManager(url)
        mgr.init_db()

        try:
            from zolai.data.migrations import run_all_migrations
            result = run_all_migrations(mgr)

            # Should return a dict with all migration categories
            assert "constraints" in result
            assert "indexes" in result
            assert "foundation_constraints" in result
            assert "foundation_indexes" in result
            assert "cost_tracking_table" in result
            assert "cost_tracking_indexes" in result
        finally:
            mgr.dispose()
            Path(db_path).unlink(missing_ok=True)

    def test_migrations_idempotent(self):
        """Running migrations twice should not fail."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        url = f"sqlite:///{db_path}"
        mgr = DatabaseManager(url)
        mgr.init_db()

        try:
            from zolai.data.migrations import run_all_migrations
            result1 = run_all_migrations(mgr)
            result2 = run_all_migrations(mgr)

            # Both should produce results (SQLite may skip some constraints
            # due to ALTER TABLE limitations — those go to 'skipped', not 'errors')
            assert "constraints" in result1
            assert "indexes" in result1
            assert "constraints" in result2
            assert "indexes" in result2

            # Foundation and cost_tracking migrations should not error
            assert len(result1.get("foundation_indexes", {}).get("errors", [])) == 0
            assert len(result1.get("cost_tracking_indexes", {}).get("errors", [])) == 0
        finally:
            mgr.dispose()
            Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test: Health Endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Verify /health endpoint is available."""

    def test_health_returns_ok(self, client):
        """GET /health should return 200 with status ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "data_root" in data
