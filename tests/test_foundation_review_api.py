"""Tests for Foundation Review Queue API — Endpoints, Filters, and Actions.

Covers:
- Review queue listing with filters
- Review detail retrieval
- Approve/reject/assign actions
- Bulk operations
- Pipeline statistics and cost summary
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.data.database import DatabaseManager
from zolai.data.repositories import get_foundation_repositories

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database with Foundation tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    mgr.init_db()
    # Apply Foundation tables
    from zolai.data.migrations import apply_foundation_constraints, apply_foundation_indexes, create_foundation_tables
    create_foundation_tables(mgr)
    apply_foundation_constraints(mgr)
    apply_foundation_indexes(mgr)
    yield mgr, db_path
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture()
def client(tmp_db):
    """Create a test client with mocked dependencies."""
    mgr, db_path = tmp_db

    # Mock the config to use our temp database
    import zolai.config
    original_db = zolai.config.config.paths.db
    zolai.config.config.paths.db = Path(db_path)

    # Create app and override repositories
    from zolai.api.server import create_app
    app = create_app()

    with TestClient(app) as c:
        yield c

    # Restore original config
    zolai.config.config.paths.db = original_db


@pytest.fixture()
def sample_review_items(tmp_db):
    """Create sample review queue items for testing."""
    mgr, db_path = tmp_db
    repos = get_foundation_repositories(mgr.engine)
    review_repo = repos["foundation_review_queue"]

    # Add sample items
    items = []
    for i in range(5):
        item_id = review_repo.add_to_queue(
            fact_type="word" if i % 2 == 0 else "sentence",
            fact_key=f"word:test{i}" if i % 2 == 0 else f"sentence:test{i}",
            priority=i,
            assignee=f"reviewer{i}" if i % 3 == 0 else None,
        )
        items.append(item_id)

    return items


# ---------------------------------------------------------------------------
# Test Review Queue Listing
# ---------------------------------------------------------------------------

class TestReviewQueueListing:
    """Test review queue listing endpoint with filters."""

    def test_list_queue_empty(self, client):
        """Test listing empty review queue."""
        response = client.get("/api/v1/foundation/review/queue")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 0

    def test_list_queue_with_items(self, client, sample_review_items):
        """Test listing review queue with items."""
        response = client.get("/api/v1/foundation/review/queue")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 5

    def test_list_queue_filter_status(self, client, sample_review_items):
        """Test filtering by status."""
        response = client.get("/api/v1/foundation/review/queue?status=pending")
        assert response.status_code == 200
        data = response.json()
        assert all(item["status"] == "pending" for item in data["items"])

    def test_list_queue_filter_fact_type(self, client, sample_review_items):
        """Test filtering by fact type."""
        response = client.get("/api/v1/foundation/review/queue?fact_type=word")
        assert response.status_code == 200
        data = response.json()
        assert all(item["fact_type"] == "word" for item in data["items"])

    def test_list_queue_filter_priority(self, client, sample_review_items):
        """Test filtering by priority."""
        response = client.get("/api/v1/foundation/review/queue?priority=2")
        assert response.status_code == 200
        data = response.json()
        assert all(item["priority"] == 2 for item in data["items"])

    def test_list_queue_pagination(self, client, sample_review_items):
        """Test pagination."""
        response = client.get("/api/v1/foundation/review/queue?page=1&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["total_pages"] == 3

    def test_list_queue_combined_filters(self, client, sample_review_items):
        """Test combined filters."""
        response = client.get("/api/v1/foundation/review/queue?fact_type=word&priority=0")
        assert response.status_code == 200
        data = response.json()
        assert all(
            item["fact_type"] == "word" and item["priority"] == 0
            for item in data["items"]
        )


# ---------------------------------------------------------------------------
# Test Review Detail
# ---------------------------------------------------------------------------

class TestReviewDetail:
    """Test review detail endpoint."""

    def test_get_detail_existing(self, client, sample_review_items):
        """Test getting detail for existing item."""
        item_id = sample_review_items[0]
        response = client.get(f"/api/v1/foundation/review/{item_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == item_id
        assert "fact_type" in data
        assert "fact_key" in data
        assert "evidence_list" in data
        assert "consensus" in data
        assert "confidence" in data

    def test_get_detail_nonexistent(self, client):
        """Test getting detail for non-existent item."""
        response = client.get("/api/v1/foundation/review/99999")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test Approve Action
# ---------------------------------------------------------------------------

class TestApproveAction:
    """Test approve action endpoint."""

    def test_approve_pending_item(self, client, sample_review_items):
        """Test approving a pending item."""
        item_id = sample_review_items[0]
        response = client.post(
            f"/api/v1/foundation/review/{item_id}/approve",
            json={"promoted_by": "test_user"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == "approved"

    def test_approve_nonexistent_item(self, client):
        """Test approving non-existent item."""
        response = client.post(
            "/api/v1/foundation/review/99999/approve",
            json={"promoted_by": "test_user"},
        )
        assert response.status_code == 404

    def test_approve_already_approved(self, client, sample_review_items):
        """Test approving already approved item."""
        item_id = sample_review_items[0]
        # Approve first
        client.post(f"/api/v1/foundation/review/{item_id}/approve", json={})
        # Try to approve again
        response = client.post(
            f"/api/v1/foundation/review/{item_id}/approve",
            json={"promoted_by": "test_user"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Test Reject Action
# ---------------------------------------------------------------------------

class TestRejectAction:
    """Test reject action endpoint."""

    def test_reject_pending_item(self, client, sample_review_items):
        """Test rejecting a pending item."""
        item_id = sample_review_items[0]
        response = client.post(
            f"/api/v1/foundation/review/{item_id}/reject",
            json={"reason": "Test rejection", "rejected_by": "test_user"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == "rejected"
        assert data["reason"] == "Test rejection"

    def test_reject_nonexistent_item(self, client):
        """Test rejecting non-existent item."""
        response = client.post(
            "/api/v1/foundation/review/99999/reject",
            json={"reason": "Test", "rejected_by": "test_user"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test Assign Action
# ---------------------------------------------------------------------------

class TestAssignAction:
    """Test assign action endpoint."""

    def test_assign_item(self, client, sample_review_items):
        """Test assigning an item."""
        item_id = sample_review_items[0]
        response = client.post(
            f"/api/v1/foundation/review/{item_id}/assign",
            json={"assignee": "reviewer1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["assignee"] == "reviewer1"

    def test_assign_nonexistent_item(self, client):
        """Test assigning non-existent item."""
        response = client.post(
            "/api/v1/foundation/review/99999/assign",
            json={"assignee": "reviewer1"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test Bulk Operations
# ---------------------------------------------------------------------------

class TestBulkOperations:
    """Test bulk approve/reject endpoint."""

    def test_bulk_approve(self, client, sample_review_items):
        """Test bulk approve."""
        response = client.post(
            "/api/v1/foundation/review/bulk",
            json={
                "action": "approve",
                "ids": sample_review_items[:3],
                "user": "test_user",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["processed"] == 3
        assert data["errors"] == 0

    def test_bulk_reject(self, client, sample_review_items):
        """Test bulk reject."""
        response = client.post(
            "/api/v1/foundation/review/bulk",
            json={
                "action": "reject",
                "ids": sample_review_items[:2],
                "reason": "Bulk rejection",
                "user": "test_user",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["processed"] == 2
        assert data["errors"] == 0

    def test_bulk_empty_ids(self, client):
        """Test bulk with empty IDs."""
        response = client.post(
            "/api/v1/foundation/review/bulk",
            json={"action": "approve", "ids": []},
        )
        assert response.status_code == 400

    def test_bulk_invalid_action(self, client, sample_review_items):
        """Test bulk with invalid action."""
        response = client.post(
            "/api/v1/foundation/review/bulk",
            json={"action": "invalid", "ids": sample_review_items[:1]},
        )
        assert response.status_code == 422  # Validation error


# ---------------------------------------------------------------------------
# Test Pipeline Statistics
# ---------------------------------------------------------------------------

class TestPipelineStats:
    """Test pipeline statistics endpoint."""

    def test_get_stats(self, client):
        """Test getting pipeline stats."""
        response = client.get("/api/v1/foundation/stats")
        assert response.status_code == 200
        data = response.json()
        assert "raw_count" in data
        assert "staging_count" in data
        assert "canonical_count" in data
        assert "evidence_count" in data
        assert "review_pending_count" in data
        assert "review_resolved_count" in data
        assert "batches_count" in data
        assert "cost_summary" in data


# ---------------------------------------------------------------------------
# Test Cost Summary
# ---------------------------------------------------------------------------

class TestCostSummary:
    """Test cost summary endpoint."""

    def test_get_cost_summary(self, client):
        """Test getting cost summary."""
        response = client.get("/api/v1/foundation/costs/summary")
        assert response.status_code == 200
        data = response.json()
        assert "total_cost" in data
        assert "by_task" in data
        assert "by_model" in data
        assert "daily" in data
        assert "budget_check" in data
        assert "within_budget" in data["budget_check"]
        assert "current_spend" in data["budget_check"]
        assert "budget" in data["budget_check"]

    def test_cost_summary_with_dates(self, client):
        """Test cost summary with date filters."""
        response = client.get(
            "/api/v1/foundation/costs/summary?start_date=2024-01-01&end_date=2024-12-31"
        )
        assert response.status_code == 200

    def test_cost_summary_with_budget(self, client):
        """Test cost summary with custom budget."""
        response = client.get("/api/v1/foundation/costs/summary?monthly_budget=500.0")
        assert response.status_code == 200
        data = response.json()
        assert data["budget_check"]["budget"] == 500.0
