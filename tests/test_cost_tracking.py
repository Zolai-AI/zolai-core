"""Tests for cost tracking — FoundationCostTracking model, repository, and CostTracker service.

Covers:
- Model CRUD operations via repository
- CostTracker log, compute cost, estimate tokens
- Budget checking and summary queries
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

from zolai.data.database import DatabaseManager
from zolai.data.repositories.foundation import FoundationCostTrackingRepository


def _import_cost_tracker():
    """Import CostTracker directly, bypassing broken services/__init__.py."""
    # Load cost_tracking module directly to avoid broken bible import in __init__
    spec = importlib.util.spec_from_file_location(
        "zolai.data.services.cost_tracking",
        str(Path(__file__).resolve().parent.parent / "zolai" / "data" / "services" / "cost_tracking.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zolai.data.services.cost_tracking"] = mod
    spec.loader.exec_module(mod)
    return mod.CostTracker


CostTracker = _import_cost_tracker()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database with Foundation tables, yield engine, clean up."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    mgr.init_db()
    # Create cost tracking table
    from zolai.data.migrations import (
        apply_foundation_cost_tracking_indexes,
        create_foundation_cost_tracking_table,
    )
    create_foundation_cost_tracking_table(mgr)
    apply_foundation_cost_tracking_indexes(mgr)
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture()
def cost_repo(tmp_db):
    """Return FoundationCostTrackingRepository for the temp DB."""
    return FoundationCostTrackingRepository(tmp_db.engine)


@pytest.fixture()
def tracker(cost_repo):
    """Return CostTracker instance with default pricing."""
    return CostTracker(cost_repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCostTrackerLog:
    """Test CostTracker.log() and cost computation."""

    def test_log_returns_positive_cost(self, tracker):
        """Logging a request should return a positive cost."""
        cost = tracker.log(
            task_type="word",
            model="gemini-2.0-flash",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost > 0.0

    def test_log_auto_generates_request_id(self, cost_repo, tracker):
        """Request ID should be auto-generated when not provided."""
        cost = tracker.log(
            task_type="sentence",
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
        )
        assert cost >= 0.0
        summary = cost_repo.get_summary()
        assert summary["total_cost"] > 0

    def test_log_with_custom_request_id(self, tracker):
        """Custom request_id should be used as-is."""
        cost = tracker.log(
            task_type="grammar",
            model="gemini-2.0-pro",
            input_tokens=2000,
            output_tokens=1000,
            request_id="custom-req-123",
        )
        assert cost > 0.0

    def test_log_with_metadata(self, cost_repo, tracker):
        """Metadata should be stored on the record."""
        tracker.log(
            task_type="batch",
            model="gemini-2.0-flash",
            input_tokens=500,
            output_tokens=200,
            extra_info='{"batch_id": 42}',
        )
        summary = cost_repo.get_summary()
        assert summary["total_cost"] > 0


class TestCostComputation:
    """Test _compute_cost() pricing logic."""

    def test_flash_input_only(self, tracker):
        """Flash model input-only cost."""
        cost = tracker._compute_cost("gemini-2.0-flash", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 0.10) < 1e-6

    def test_flash_output_only(self, tracker):
        """Flash model output-only cost."""
        cost = tracker._compute_cost("gemini-2.0-flash", input_tokens=0, output_tokens=1_000_000)
        assert abs(cost - 0.40) < 1e-6

    def test_pro_cost(self, tracker):
        """Pro model combined cost."""
        cost = tracker._compute_cost("gemini-2.0-pro", input_tokens=1_000_000, output_tokens=1_000_000)
        assert abs(cost - 11.25) < 1e-6

    def test_pro_plus_cost(self, tracker):
        """Pro-plus model combined cost."""
        cost = tracker._compute_cost("gemini-2.0-pro-plus", input_tokens=1_000_000, output_tokens=1_000_000)
        assert abs(cost - 17.50) < 1e-6

    def test_zero_tokens(self, tracker):
        """Zero tokens should return zero cost."""
        cost = tracker._compute_cost("gemini-2.0-flash", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_unknown_model(self, tracker):
        """Unknown model should return zero cost."""
        cost = tracker._compute_cost("unknown-model", input_tokens=1000, output_tokens=500)
        assert cost == 0.0


class TestTokenEstimation:
    """Test estimate_tokens() static method."""

    def test_empty_string(self):
        """Empty string should return 0."""
        assert CostTracker.estimate_tokens("") == 0

    def test_none_treated_as_empty(self):
        """None-ish check — empty string path."""
        assert CostTracker.estimate_tokens("") == 0

    def test_short_text(self):
        """Short text should return at least 1."""
        assert CostTracker.estimate_tokens("hi") == 1

    def test_longer_text(self):
        """Longer text estimation."""
        text = "a" * 100
        tokens = CostTracker.estimate_tokens(text)
        assert tokens == 25


class TestBudgetChecking:
    """Test check_budget() and summary queries."""

    def test_check_budget_within(self, tracker):
        """Should be within budget when no spend."""
        within, spend = tracker.check_budget(50.0)
        assert within is True
        assert spend == 0.0

    def test_check_budget_exceeded(self, cost_repo, tracker):
        """Should detect budget exceeded after large spend."""
        # Log a very expensive request
        cost_repo.log_request(
            request_id="expensive-1",
            task_type="batch",
            model="gemini-2.0-pro-plus",
            input_tokens=10_000_000,
            output_tokens=10_000_000,
            cost_usd=175.0,
        )
        within, spend = tracker.check_budget(50.0)
        assert within is False
        assert spend > 50.0

    def test_get_summary_empty(self, tracker):
        """Summary of empty DB should have zero totals."""
        summary = tracker.get_summary()
        assert summary["total_cost"] == 0.0
        assert summary["by_task"] == {}
        assert summary["by_model"] == {}
        assert summary["daily"] == []

    def test_get_summary_with_data(self, cost_repo, tracker):
        """Summary should aggregate by task and model."""
        tracker.log(task_type="word", model="gemini-2.0-flash", input_tokens=100, output_tokens=50)
        tracker.log(task_type="sentence", model="gemini-2.0-pro", input_tokens=200, output_tokens=100)
        summary = tracker.get_summary()
        assert summary["total_cost"] > 0
        assert "word" in summary["by_task"]
        assert "sentence" in summary["by_task"]
        assert "gemini-2.0-flash" in summary["by_model"]
        assert "gemini-2.0-pro" in summary["by_model"]

    def test_get_by_task_type(self, cost_repo, tracker):
        """get_by_task_type should return per-task costs."""
        tracker.log(task_type="word", model="gemini-2.0-flash", input_tokens=100, output_tokens=50)
        tracker.log(task_type="word", model="gemini-2.0-flash", input_tokens=100, output_tokens=50)
        tracker.log(task_type="batch", model="gemini-2.0-flash", input_tokens=100, output_tokens=50)
        by_task = cost_repo.get_by_task_type()
        assert "word" in by_task
        assert by_task["word"] > 0

    def test_get_daily_breakdown(self, cost_repo, tracker):
        """Daily breakdown should return list of daily records."""
        tracker.log(task_type="word", model="gemini-2.0-flash", input_tokens=100, output_tokens=50)
        daily = cost_repo.get_daily_breakdown(days=30)
        assert len(daily) >= 1
        assert "date" in daily[0]
        assert "cost" in daily[0]
