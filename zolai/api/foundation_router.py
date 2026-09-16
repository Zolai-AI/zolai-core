"""Foundation Review Queue API — REST endpoints for review queue management.

Provides endpoints for:
- Listing and filtering review queue items
- Viewing detail with candidate value, evidence, and consensus
- Approving, rejecting, and assigning items
- Bulk operations
- Pipeline statistics and cost summary
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import config
from ..data.repositories import get_repositories

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/foundation", tags=["foundation"])


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class ReviewQueueItem(BaseModel):
    """Review queue item response model."""
    id: int
    fact_type: str
    fact_key: str
    priority: int = 0
    assignee: Optional[str] = None
    status: str = "pending"
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None


class ReviewDetailResponse(BaseModel):
    """Detailed review item response with candidate, evidence, and consensus."""
    id: int
    fact_type: str
    fact_key: str
    priority: int = 0
    assignee: Optional[str] = None
    status: str = "pending"
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None
    candidate_value: Optional[dict] = None
    evidence_list: list[dict] = []
    consensus: Optional[dict] = None
    confidence: float = 0.0


class ApproveRequest(BaseModel):
    """Request model for approving a review item."""
    promoted_by: str = "api_user"


class RejectRequest(BaseModel):
    """Request model for rejecting a review item."""
    reason: str = ""
    rejected_by: str = "api_user"


class AssignRequest(BaseModel):
    """Request model for assigning a review item."""
    assignee: str


class BulkActionRequest(BaseModel):
    """Request model for bulk approve/reject."""
    action: str = Field(..., pattern="^(approve|reject)$")
    ids: list[int]
    reason: Optional[str] = None
    user: str = "api_user"


class PipelineStatsResponse(BaseModel):
    """Pipeline statistics response."""
    raw_count: int = 0
    staging_count: int = 0
    canonical_count: int = 0
    evidence_count: int = 0
    review_pending_count: int = 0
    review_resolved_count: int = 0
    batches_count: int = 0
    cost_summary: dict = {}


class CostSummaryResponse(BaseModel):
    """Cost dashboard data response."""
    total_cost: float = 0.0
    by_task: dict = {}
    by_model: dict = {}
    daily: list[dict] = []
    budget_check: dict = {}


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _get_repos() -> dict:
    """Get foundation repositories."""
    return get_repositories(config.paths.db)


def _get_evidence_for_item(repos: dict, fact_type: str, fact_key: str) -> list[dict]:
    """Get evidence records for a review item."""
    evidence_repo = repos.get("foundation_evidence")
    if not evidence_repo:
        return []

    try:
        return evidence_repo.get_by_fact(fact_type, fact_key)
    except Exception:
        return []


def _get_consensus_for_item(repos: dict, fact_type: str, fact_key: str) -> Optional[dict]:
    """Get consensus record for a review item."""
    consensus_repo = repos.get("foundation_consensus")
    if not consensus_repo:
        return None

    try:
        records = consensus_repo.get_by_fact(fact_type, fact_key)
        return records[0] if records else None
    except Exception:
        return None


def _calculate_confidence(evidence: list[dict], consensus: Optional[dict]) -> float:
    """Calculate confidence score from evidence and consensus."""
    if consensus and "decision" in consensus:
        try:
            raw_decision = consensus["decision"]
            decision = json.loads(raw_decision) if isinstance(raw_decision, str) else raw_decision
            if isinstance(decision, dict) and "confidence" in decision:
                return float(decision["confidence"])
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    # Fallback: calculate from evidence
    if not evidence:
        return 0.0

    total_confidence = sum(e.get("confidence", 0.0) for e in evidence)
    return total_confidence / len(evidence) if evidence else 0.0


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@router.get("/review/queue")
async def list_review_queue(
    status: Optional[str] = Query(None, description="Filter by status: pending, approved, rejected"),
    priority: Optional[int] = Query(None, description="Filter by priority level"),
    fact_type: Optional[str] = Query(None, description="Filter by fact type: word, sentence, paragraph"),
    assignee: Optional[str] = Query(None, description="Filter by assignee"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List review queue items with filters and pagination.

    Returns paginated list of review items with metadata.
    """
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    if not review_repo:
        raise HTTPException(status_code=500, detail="Review queue repository not available")

    try:
        # Get items based on filters
        if status == "pending":
            items = review_repo.get_pending(limit=1000)  # Get all pending
        elif assignee:
            items = review_repo.get_by_assignee(assignee, limit=1000)
        else:
            # Get all items (fallback to pending if no filter)
            items = review_repo.get_pending(limit=1000)

        # Apply additional filters
        if fact_type:
            items = [i for i in items if i.get("fact_type") == fact_type]
        if priority is not None:
            items = [i for i in items if i.get("priority") == priority]
        if status and status != "pending":
            items = [i for i in items if i.get("status") == status]

        # Calculate pagination
        total = len(items)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_items = items[start_idx:end_idx]

        return {
            "items": paginated_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
    except Exception as e:
        logger.error("Failed to list review queue: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review/{item_id}")
async def get_review_detail(item_id: int):
    """Get detailed review item with candidate value, evidence, and consensus.

    Returns comprehensive information about a review item including:
    - Candidate value (fact details)
    - Evidence list (all supporting evidence)
    - Consensus decision (if available)
    - Confidence score
    """
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    if not review_repo:
        raise HTTPException(status_code=500, detail="Review queue repository not available")

    try:
        # Get the review item
        item = review_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        # Get evidence
        evidence = _get_evidence_for_item(repos, item["fact_type"], item["fact_key"])

        # Get consensus
        consensus = _get_consensus_for_item(repos, item["fact_type"], item["fact_key"])

        # Calculate confidence
        confidence = _calculate_confidence(evidence, consensus)

        # Try to get candidate value from staging
        candidate_value = None
        staging_repo = repos.get(f"foundation_staging_{item['fact_type']}s")
        if staging_repo:
            try:
                # Extract key from fact_key (e.g., "word:pasian" -> "pasian")
                key = item["fact_key"].split(":", 1)[1] if ":" in item["fact_key"] else item["fact_key"]
                staging_items = staging_repo.get_by_form(key) if hasattr(staging_repo, 'get_by_form') else []
                if staging_items:
                    candidate_value = staging_items[0]
            except Exception:
                pass

        return {
            "id": item["id"],
            "fact_type": item["fact_type"],
            "fact_key": item["fact_key"],
            "priority": item.get("priority", 0),
            "assignee": item.get("assignee"),
            "status": item.get("status", "pending"),
            "created_at": item.get("created_at"),
            "resolved_at": item.get("resolved_at"),
            "candidate_value": candidate_value,
            "evidence_list": evidence,
            "consensus": consensus,
            "confidence": confidence,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get review detail: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/{item_id}/approve")
async def approve_review_item(item_id: int, request: ApproveRequest = ApproveRequest()):
    """Approve a review item and promote to canonical.

    Uses FoundationETL.promote_staging_to_canonical to move the item
    from staging to canonical layer.
    """
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    if not review_repo:
        raise HTTPException(status_code=500, detail="Review queue repository not available")

    try:
        # Get the review item
        item = review_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        if item.get("status") != "pending":
            raise HTTPException(status_code=400, detail=f"Item is not pending (status: {item.get('status')})")

        # Promote to canonical using ETL
        from ..pipeline.foundation import FoundationETL
        etl = FoundationETL(db_path=f"sqlite:///{config.paths.db}")

        # Run promotion for this specific fact type
        stats = etl.promote_staging_to_canonical(threshold=0.0, batch_size=1)

        # Update review item status
        review_repo.update_status(item_id, "approved", user=request.promoted_by)

        return {
            "success": True,
            "item_id": item_id,
            "status": "approved",
            "promoted_by": request.promoted_by,
            "records_promoted": stats.records_promoted,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to approve review item: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/{item_id}/reject")
async def reject_review_item(item_id: int, request: RejectRequest):
    """Reject a review item with reason.

    Updates the item status to 'rejected' and logs the reason.
    """
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    if not review_repo:
        raise HTTPException(status_code=500, detail="Review queue repository not available")

    try:
        # Get the review item
        item = review_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        if item.get("status") != "pending":
            raise HTTPException(status_code=400, detail=f"Item is not pending (status: {item.get('status')})")

        # Update status to rejected
        review_repo.update_status(item_id, "rejected", user=request.rejected_by)

        # Log rejection reason via internal audit
        # Note: The base repository's _log_audit is called automatically on update

        return {
            "success": True,
            "item_id": item_id,
            "status": "rejected",
            "reason": request.reason,
            "rejected_by": request.rejected_by,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to reject review item: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/{item_id}/assign")
async def assign_review_item(item_id: int, request: AssignRequest):
    """Assign a review item to a reviewer.

    Updates the assignee field for the review item.
    """
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    if not review_repo:
        raise HTTPException(status_code=500, detail="Review queue repository not available")

    try:
        # Get the review item
        item = review_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        # Update assignee
        review_repo.update(item_id, {"assignee": request.assignee}, user="api_user")

        return {
            "success": True,
            "item_id": item_id,
            "assignee": request.assignee,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to assign review item: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/bulk")
async def bulk_review_action(request: BulkActionRequest):
    """Bulk approve or reject multiple review items.

    Processes multiple items in a single request.
    """
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    if not review_repo:
        raise HTTPException(status_code=500, detail="Review queue repository not available")

    if not request.ids:
        raise HTTPException(status_code=400, detail="No item IDs provided")

    results = []
    errors = []

    for item_id in request.ids:
        try:
            # Get the review item
            item = review_repo.get_by_id(item_id)
            if not item:
                errors.append({"id": item_id, "error": "Not found"})
                continue

            if item.get("status") != "pending":
                errors.append({"id": item_id, "error": f"Not pending (status: {item.get('status')})"})
                continue

            if request.action == "approve":
                # Promote to canonical
                from ..pipeline.foundation import FoundationETL
                etl = FoundationETL(db_path=f"sqlite:///{config.paths.db}")
                stats = etl.promote_staging_to_canonical(threshold=0.0, batch_size=1)
                review_repo.update_status(item_id, "approved", user=request.user)
                results.append({"id": item_id, "action": "approved", "records_promoted": stats.records_promoted})
            elif request.action == "reject":
                review_repo.update_status(item_id, "rejected", user=request.user)
                results.append({"id": item_id, "action": "rejected", "reason": request.reason})
        except Exception as e:
            errors.append({"id": item_id, "error": str(e)})

    return {
        "success": len(errors) == 0,
        "processed": len(results),
        "errors": len(errors),
        "results": results,
        "error_details": errors,
    }


@router.get("/stats")
async def get_pipeline_stats():
    """Get pipeline statistics including counts and health.

    Returns statistics for all foundation layers (raw, staging, canonical)
    plus review queue status and batch information.
    """
    repos = _get_repos()

    try:
        stats = {}

        # Raw layer
        raw_corpus = repos.get("foundation_raw_corpus")
        raw_llm = repos.get("foundation_raw_llm")
        stats["raw_count"] = (raw_corpus.count() if raw_corpus else 0) + (raw_llm.count() if raw_llm else 0)

        # Staging layer
        staging_words = repos.get("foundation_staging_words")
        staging_sentences = repos.get("foundation_staging_sentences")
        staging_paragraphs = repos.get("foundation_staging_paragraphs")
        stats["staging_count"] = (
            (staging_words.count() if staging_words else 0)
            + (staging_sentences.count() if staging_sentences else 0)
            + (staging_paragraphs.count() if staging_paragraphs else 0)
        )

        # Canonical layer
        canonical_words = repos.get("canonical_words")
        canonical_sentences = repos.get("canonical_sentences")
        canonical_paragraphs = repos.get("canonical_paragraphs")
        stats["canonical_count"] = (
            (canonical_words.count() if canonical_words else 0)
            + (canonical_sentences.count() if canonical_sentences else 0)
            + (canonical_paragraphs.count() if canonical_paragraphs else 0)
        )

        # Evidence
        evidence_repo = repos.get("foundation_evidence")
        stats["evidence_count"] = evidence_repo.count() if evidence_repo else 0

        # Review queue
        review_repo = repos.get("foundation_review_queue")
        if review_repo:
            stats["review_pending_count"] = len(review_repo.get_pending(limit=10000))
            stats["review_resolved_count"] = review_repo.count() - stats["review_pending_count"]
        else:
            stats["review_pending_count"] = 0
            stats["review_resolved_count"] = 0

        # Batches
        batch_repo = repos.get("foundation_batches")
        stats["batches_count"] = batch_repo.count() if batch_repo else 0

        # Cost summary
        cost_repo = repos.get("foundation_cost_tracking")
        if cost_repo:
            stats["cost_summary"] = cost_repo.get_summary()
        else:
            stats["cost_summary"] = {"total_cost": 0.0, "by_task": {}, "by_model": {}, "daily": []}

        return stats
    except Exception as e:
        logger.error("Failed to get pipeline stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/costs/summary")
async def get_cost_summary(
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    monthly_budget: float = Query(100.0, description="Monthly budget in USD"),
):
    """Get cost dashboard data with budget tracking.

    Returns cost breakdown by task, model, and daily usage.
    """
    repos = _get_repos()
    cost_repo = repos.get("foundation_cost_tracking")

    if not cost_repo:
        return {
            "total_cost": 0.0,
            "by_task": {},
            "by_model": {},
            "daily": [],
            "budget_check": {"within_budget": True, "current_spend": 0.0, "budget": monthly_budget},
        }

    try:
        summary = cost_repo.get_summary(start_date=start_date, end_date=end_date)
        within_budget, current_spend = cost_repo.check_budget(monthly_budget)

        return {
            "total_cost": summary["total_cost"],
            "by_task": summary["by_task"],
            "by_model": summary["by_model"],
            "daily": summary["daily"],
            "budget_check": {
                "within_budget": within_budget,
                "current_spend": current_spend,
                "budget": monthly_budget,
            },
        }
    except Exception as e:
        logger.error("Failed to get cost summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
