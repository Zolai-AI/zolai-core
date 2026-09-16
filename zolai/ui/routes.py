"""UI Routes — FastAPI routes serving HTML templates for review queue.

Provides HTMX-powered interfaces for reviewing foundation pipeline items.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import config
from ..data.repositories import get_repositories

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/review", tags=["ui"])

# Template directory
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_repos() -> dict:
    """Get foundation repositories."""
    return get_repositories(config.paths.db)


@router.get("/", response_class=HTMLResponse)
async def review_queue_list(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status"),
    fact_type: Optional[str] = Query(None, description="Filter by fact type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Render the review queue list view with filters and pagination."""
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    items = []
    total = 0
    total_pages = 1

    if review_repo:
        try:
            # Get items based on filters
            if status == "pending":
                all_items = review_repo.get_pending(limit=1000)
            else:
                all_items = review_repo.get_pending(limit=1000)

            # Apply additional filters
            if fact_type:
                all_items = [i for i in all_items if i.get("fact_type") == fact_type]
            if status and status != "pending":
                all_items = [i for i in all_items if i.get("status") == status]

            total = len(all_items)
            total_pages = max(1, (total + page_size - 1) // page_size)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            items = all_items[start_idx:end_idx]
        except Exception as e:
            logger.error("Failed to load review queue: %s", e)

    return templates.TemplateResponse(
        "review_queue.html",
        {
            "request": request,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_filter": status,
            "fact_type_filter": fact_type,
        },
    )


@router.get("/{item_id}", response_class=HTMLResponse)
async def review_detail(request: Request, item_id: int):
    """Render the review detail view for a specific item."""
    repos = _get_repos()
    review_repo = repos.get("foundation_review_queue")

    item = None
    evidence = []
    consensus = None
    confidence = 0.0
    candidate_value = None

    if review_repo:
        try:
            item = review_repo.get_by_id(item_id)
            if item:
                # Get evidence
                evidence_repo = repos.get("foundation_evidence")
                if evidence_repo:
                    try:
                        evidence = evidence_repo.get_by_fact(item["fact_type"], item["fact_key"])
                    except Exception:
                        pass

                # Get consensus
                consensus_repo = repos.get("foundation_consensus")
                if consensus_repo:
                    try:
                        consensus_records = consensus_repo.get_by_fact(item["fact_type"], item["fact_key"])
                        consensus = consensus_records[0] if consensus_records else None
                    except Exception:
                        pass

                # Calculate confidence
                if consensus and "decision" in consensus:
                    try:
                        raw_decision = consensus["decision"]
                        decision = json.loads(raw_decision) if isinstance(raw_decision, str) else raw_decision
                        if isinstance(decision, dict) and "confidence" in decision:
                            confidence = float(decision["confidence"])
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass

                if evidence and confidence == 0.0:
                    total_confidence = sum(e.get("confidence", 0.0) for e in evidence)
                    confidence = total_confidence / len(evidence) if evidence else 0.0

                # Try to get candidate value from staging
                staging_repo = repos.get(f"foundation_staging_{item['fact_type']}s")
                if staging_repo:
                    try:
                        key = item["fact_key"].split(":", 1)[1] if ":" in item["fact_key"] else item["fact_key"]
                        if hasattr(staging_repo, 'get_by_form'):
                            staging_items = staging_repo.get_by_form(key)
                            if staging_items:
                                candidate_value = staging_items[0]
                    except Exception:
                        pass
        except Exception as e:
            logger.error("Failed to load review detail: %s", e)

    if not item:
        return HTMLResponse(content="<h1>Item not found</h1>", status_code=404)

    return templates.TemplateResponse(
        "review_detail.html",
        {
            "request": request,
            "item": item,
            "evidence": evidence,
            "consensus": consensus,
            "confidence": confidence,
            "candidate_value": candidate_value,
        },
    )


@router.get("/stats", response_class=HTMLResponse)
async def review_stats(request: Request):
    """Render pipeline statistics dashboard."""
    repos = _get_repos()

    stats = {}
    try:
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
    except Exception as e:
        logger.error("Failed to load stats: %s", e)

    return templates.TemplateResponse(
        "review_queue.html",
        {
            "request": request,
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "total_pages": 1,
            "status_filter": None,
            "fact_type_filter": None,
            "show_stats": True,
            "stats": stats,
        },
    )
