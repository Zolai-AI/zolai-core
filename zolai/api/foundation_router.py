"""Foundation Review Queue API — REST endpoints for review queue management and linguistic analysis.

Provides endpoints for:
- Review queue management (list, detail, approve, reject, assign, bulk)
- Pipeline statistics and cost summary
- Corpus analysis (n-gram, collocation, register)
- Phonological analysis (syllable validation, tone sandhi, phonotactics)
- Enhanced translation (3-tier confidence, morphology-aware)
- Morphological decomposition
- Adaptive difficulty computation
- Ranked search, cross-lingual search, topical Bible search
- Search analytics
- Sentence structure validation (SOV/ergative)
- Polysemy disambiguation
- Learning streak tracking
- Error categorization and breakdown
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..config import config
from ..data.repositories import get_repositories
from .errors import internal_error_envelope, internal_http_error

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

    model_config = ConfigDict(extra="allow")
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

    model_config = ConfigDict(extra="allow")
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

    model_config = ConfigDict(extra="allow")
    total_cost: float = 0.0
    by_task: dict = {}
    by_model: dict = {}
    daily: list[dict] = []
    budget_check: dict = {}


# ---------------------------------------------------------------------------
# Response models (R-batch)
#
# Every endpoint below used to be annotated with nothing, so FastAPI published
# **no** schema for it: OpenAPI carried an empty response contract, a client
# generator had nothing to emit, and a renamed key broke callers silently.
#
# Two rules keep adding them byte-compatible:
#   1. a value the handler writes as a *literal* (``total``, ``page_size``,
#      ``item_id``) gets a precise type;
#   2. a value that comes straight out of a repository row or an analyzer
#      result object stays ``Any`` — those producers are not under this
#      router's control, and typing them tighter could coerce or reject a
#      value the endpoint already returns, which would turn a 200 into a 500.
#
# :class:`FoundationResponse` carries the third rule, the one that actually
# keeps the bytes identical: ``extra="allow"`` passes a producer's optional key
# through untouched, and only the keys a producer *always* emits are declared —
# a declared field would be materialised from its default on every response,
# so declaring one a producer sometimes omits would *add* a key that was never
# there. ``tests/test_foundation_response_models.py`` pins all of this.
# ---------------------------------------------------------------------------


class FoundationResponse(BaseModel):
    """Base for the response models below — see the block comment above."""

    model_config = ConfigDict(extra="allow")


class ReviewQueueListResponse(FoundationResponse):
    """``GET /foundation/review/queue`` — one page of queue items."""

    items: list[dict[str, Any]] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
    total_pages: int = 1


class ReviewApproveResponse(FoundationResponse):
    """``POST /foundation/review/{item_id}/approve`` — promotion result."""

    success: bool = False
    item_id: int = 0
    status: str = "approved"
    promoted_by: str = ""
    records_promoted: int = 0


class ReviewRejectResponse(FoundationResponse):
    """``POST /foundation/review/{item_id}/reject`` — the recorded reason."""

    success: bool = False
    item_id: int = 0
    status: str = "rejected"
    reason: str = ""
    rejected_by: str = ""


class ReviewAssignResponse(FoundationResponse):
    """``POST /foundation/review/{item_id}/assign`` — the new assignee."""

    success: bool = False
    item_id: int = 0
    assignee: str = ""


class BulkReviewResponse(FoundationResponse):
    """``POST /foundation/review/bulk`` — per-item outcome for a batch."""

    success: bool = False
    processed: int = 0
    errors: int = 0
    results: list[dict[str, Any]] = []
    error_details: list[dict[str, Any]] = []


class CollocationOut(BaseModel):
    """One PMI-ranked collocation inside a corpus analysis."""

    word1: Any = None
    word2: Any = None
    pmi: Any = None
    freq: Any = 0


class CorpusAnalysisResponse(FoundationResponse):
    """``POST /foundation/analyze/corpus`` — n-grams through register guess."""

    ngrams: dict[str, Any] = {}
    collocations: list[CollocationOut] = []
    freq_distribution: dict[str, Any] = {}
    # `register` is a deprecated attribute on pydantic's BaseModel, so the field
    # is aliased; FastAPI serialises responses with by_alias=True, so the wire
    # name is still exactly "register".
    register_: Any = Field(default="", alias="register")
    total_words_analyzed: int = 0


class SyllableStructureOut(BaseModel):
    """One syllable of a phonological analysis."""

    syllable: Any = ""
    onset: Any = ""
    nucleus: Any = ""
    coda: Any = ""
    tone: Any = ""
    valid: Any = False


class PhonologyAnalysisResponse(FoundationResponse):
    """``POST /foundation/analyze/phonology`` — syllables, tones, sandhi."""

    syllable_structures: list[SyllableStructureOut] = []
    tone_patterns: list[Any] = []
    sandhi_applied: list[Any] = []
    phonotactic_valid: Any = False
    stress_pattern: list[Any] = []
    violations: list[Any] = []


class MorphologyAnalysisResponse(FoundationResponse):
    """``POST /foundation/analyze/morphology`` — agglutinative decomposition."""

    segments: list[Any] = []
    directional: Any = ""
    stem: Any = ""
    aspect: Any = ""
    particle: Any = ""
    is_valid: Any = False
    violations: list[Any] = []
    compound_parts: list[Any] = []
    prefix: Any = ""
    suffix: Any = ""


class EnhancedTranslationResponse(FoundationResponse):
    """``POST /foundation/translate/enhanced`` — translation plus evidence."""

    translation: Any = ""
    confidence: Any = 0
    tier: Any = "none"
    sources: Any = []
    evidence_chain: Any = []
    zolai: Any = None
    english: Any = None
    pos: Any = None
    note: Any = None
    morphology_breakdown: Any = None


class AdaptiveDifficultyResponse(FoundationResponse):
    """``POST /foundation/progress/adaptive-difficulty`` — the tracker's plan.

    ``error_rate`` / ``avg_ease`` / ``avg_complexity`` are only present once the
    learner has review history, so they default to ``None`` and stay absent in
    the "no history" response exactly as the tracker omits them.
    """

    model_config = ConfigDict(extra="allow")

    model_config = ConfigDict(extra="allow")

    model_config = ConfigDict(extra="allow")

    difficulty: Any = ""
    frequency_tier: Any = ""
    morphology_threshold: Any = 0.0
    include_tone_questions: Any = False
    recommended_count: Any = 0
    reason: Any = ""


class RankedSearchResponse(FoundationResponse):
    """``POST /foundation/search/ranked`` — relevance-scored hits."""

    query: str = ""
    results: list[Any] = []
    total: int = 0


class CrossLingualSearchResponse(FoundationResponse):
    """``POST /foundation/search/cross-lingual`` — hits plus the direction used."""

    query: str = ""
    source_lang: str = "en"
    target_lang: str = "zo"
    results: list[Any] = []
    total: int = 0


class TopicalSearchResponse(FoundationResponse):
    """``POST /foundation/search/topical`` — thematically matching verses."""

    theme: str = ""
    results: list[Any] = []
    total: int = 0


class SearchAnalyticsResponse(FoundationResponse):
    """``GET /foundation/search/analytics`` — query frequency rollup."""

    analytics: list[Any] = []
    total_queries: int = 0


class StructureValidationResponse(FoundationResponse):
    """``POST /foundation/grammar/validate-structure`` — SOV/ZVS verdict.

    ``tokens`` / ``verb_position`` / ``zvs_compliant`` are only present once the
    editor tokenized the sentence, hence their defaults.
    """

    valid: bool = False
    errors: list[Any] = []
    warnings: list[Any] = []


class DisambiguationResponse(FoundationResponse):
    """``POST /foundation/translation/disambiguate`` — ranked senses."""

    candidates: list[Any] = []
    word: str = ""
    direction: str = "zo-en"


class StreakResponse(FoundationResponse):
    """``GET /foundation/progress/streak/{user_id}`` — current and longest."""

    user_id: Any = ""
    streak_type: Any = ""
    current_streak: Any = 0
    longest_streak: Any = 0
    last_activity_date: Any = None
    is_active: Any = False


class ErrorCategorizationResponse(FoundationResponse):
    """``POST /foundation/progress/error-categorize`` — the classified error."""

    category: Any = ""
    subcategory: Any = ""
    rule: Any = ""


class ErrorBreakdownResponse(FoundationResponse):
    """``GET /foundation/progress/error-breakdown/{user_id}`` — counts by class."""

    user_id: Any = ""
    total_errors: Any = 0
    categories: dict[str, Any] = {}
    most_common: Any = None


# ---------------------------------------------------------------------------
# Linguistic-analysis request models (W1 contract hardening)
#
# These 11 endpoints used to take an untyped dict body and read their keys by
# hand, so OpenAPI published an opaque ``{"type": "object"}`` for each of them
# and a wrong or missing key silently fell back to a default. A real model per
# endpoint fixes all three at once: FastAPI publishes the JSON Schema, a bad key
# is a 422 instead of a surprise, and the MCP layer can generate a tool's input
# schema from this definition instead of hand-writing one.
#
# Semantics are preserved exactly (additive only):
#   * a key the handler read with a falsy default and then rejected with a 400
#     is declared **required** here — a *missing* key is now a 422 — and the
#     original 400 guard is kept so an explicitly empty string still 400s;
#   * every key the handler read is still read, with the same default and type;
#   * extra keys are still ignored (Pydantic's default), so nothing that used
#     to be accepted has been removed.
#
# Every field carries a real ``description``: these strings are what an LLM
# reads when the same models back an MCP tool.
# ---------------------------------------------------------------------------


class CorpusAnalysisRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/analyze/corpus``."""

    text: str = Field(
        ...,
        description=(
            "Text to analyse at corpus level, in Zolai or English. The analyzer extracts "
            "n-grams, PMI-ranked collocations, a Zipf frequency distribution and a register "
            "guess (formal / common / literary) from it."
        ),
    )
    ngram_size: int = Field(
        2,
        description=(
            "Order of the n-grams to extract (2 = bigrams, 3 = trigrams). Accepted for "
            "forward compatibility — the analyzer currently returns its own default n-gram set "
            "and ignores this value."
        ),
    )
    top_k: int = Field(
        100,
        description=(
            "Maximum number of n-grams and collocations to return. Accepted for forward "
            "compatibility — the analyzer currently applies its own default cut-off and ignores "
            "this value."
        ),
    )


class PhonologyAnalysisRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/analyze/phonology``."""

    word: str = Field(
        ...,
        description=(
            "A single Zolai word to analyse phonologically. Returns the syllable split "
            "(onset / nucleus / coda) with a tone per syllable, the tone pattern, any sandhi "
            "resyllabifications, phonotactic validity, the stress pattern and any violations."
        ),
    )
    apply_sandhi: bool = Field(
        True,
        description=(
            "Whether tone-sandhi resyllabification is applied before the phonological checks. "
            "Accepted for forward compatibility — the analyzer always applies the 19 documented "
            "sandhi rules today."
        ),
    )


class MorphologyAnalysisRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/analyze/morphology``."""

    word: str = Field(
        ...,
        description=(
            "A single Zolai word to decompose. Returns the morpheme segments, directional "
            "prefix, stem, aspect suffix, particle, affix prefix/suffix, compound parts, and a "
            "ZVS 2018 validity flag with any violations."
        ),
    )


class EnhancedTranslationRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/translate/enhanced``."""

    text: str = Field(
        ...,
        description=(
            "The word or sentence to translate. Confidence is scored over three tiers "
            "(dictionary, Bible parallel, corpus) and the response carries the evidence chain "
            "plus a morphology-aware breakdown."
        ),
    )
    direction: str = Field(
        "auto",
        description=(
            "Translation direction. 'auto' detects it from the text; 'zo-en' translates Zolai to "
            "English and 'en-zo' translates English to Zolai."
        ),
    )
    context: Optional[str] = Field(
        None,
        description=(
            "Optional context that narrows polysemous words — a register or corpus label such as "
            "'bible', 'daily' or 'poetry', or the sentence the word was seen in. Leave unset to "
            "rank senses by frequency alone."
        ),
    )


class AdaptiveDifficultyRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/progress/adaptive-difficulty``."""

    user_id: Optional[str] = Field(
        "default",
        description=(
            "Learner whose difficulty is computed from their history: word frequency, morphology "
            "complexity, tone sensitivity and historical error rate. Defaults to 'default'; send "
            "null to use the tracker's own default profile."
        ),
    )


class RankedSearchRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/search/ranked``."""

    query: str = Field(
        ...,
        description=(
            "The word or phrase to search for across the dictionary, the Bible corpus and the "
            "grammar-pattern sources; results come back with a relevance score."
        ),
    )
    category: Optional[str] = Field(
        None,
        description=(
            "Restrict the search to one source family: 'vocabulary' (dictionary), 'grammar' "
            "(grammar patterns) or 'bible'. Leave unset to search all of them."
        ),
    )
    limit: int = Field(
        10,
        description="Maximum number of ranked results to return.",
    )


class CrossLingualSearchRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/search/cross-lingual``."""

    query: str = Field(
        ...,
        description="The headword to look up, written in the source language.",
    )
    source_lang: str = Field(
        "en",
        description=(
            "Language of 'query', and so the dictionary table searched: 'en' queries the "
            "English-to-Zolai table, 'zo' the Zolai-to-English table."
        ),
    )
    target_lang: str = Field(
        "zo",
        description=(
            "Language the results are translated into ('en' or 'zo'). Echoed back on every hit "
            "and part of the cache key."
        ),
    )
    limit: int = Field(
        10,
        description="Maximum number of cross-lingual matches to return.",
    )


class TopicalSearchRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/search/topical``."""

    theme: str = Field(
        ...,
        description=(
            "Theme keywords matched against Bible verse text in both English and Zolai, e.g. "
            "'love', 'creation' or 'prayer'."
        ),
    )
    limit: int = Field(
        10,
        description="Maximum number of thematically matching verses to return.",
    )


class StructureValidationRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/grammar/validate-structure``."""

    sentence: str = Field(
        ...,
        description=(
            "The Zolai sentence to check. Validated for SOV verb position, ergative 'in' "
            "placement, negation ('kei') and question ('hiam') patterns, and ZVS 2018 compliance."
        ),
    )


class DisambiguationRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/translation/disambiguate``."""

    word: str = Field(
        ...,
        description=(
            "A polysemous Zolai word to disambiguate. Every dictionary sense is collected and "
            "ranked, optionally weighted by per-book frequency from the word_usage table."
        ),
    )
    context: Optional[str] = Field(
        None,
        description=(
            "Optional context that biases the ranking — a corpus or register label ('bible'), a "
            "book name, or the sentence the word appears in. Leave unset to rank by frequency "
            "alone."
        ),
    )
    direction: str = Field(
        "zo-en",
        description=(
            "Translation direction for the candidates: 'zo-en' (default) or 'en-zo'."
        ),
    )


class ErrorCategorizationRequest(BaseModel):
    """Body of ``POST /api/v1/foundation/progress/error-categorize``."""

    original: str = Field(
        ...,
        description="The uncorrected Zolai text the learner submitted.",
    )
    corrected: str = Field(
        ...,
        description=(
            "The corrected Zolai text. The pair is classified as an orthographic (ZVS forbidden "
            "form), word-order (SOV), negation, question-form, tone-sandhi or compound-word error."
        ),
    )


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

@router.get("/review/queue", response_model=ReviewQueueListResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.review_queue.list",
            code="review_queue_unavailable",
            message=(
                "The review queue could not be read. Retry in a few seconds; if it keeps failing, the ingestion "
                "pipeline needs a look."
            ),
        ) from exc


@router.get("/review/{item_id}", response_model=ReviewDetailResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.review_queue.get",
            code="review_item_unreadable",
            message=(
                "That review item could not be read. Retry, then confirm the item is still in the review queue."
            ),
            log_context={"item_id": item_id},
        ) from exc


@router.post("/review/{item_id}/approve", response_model=ReviewApproveResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.review_queue.approve",
            code="review_approval_failed",
            message=(
                "That review item could not be approved: promoting it to the canonical layer failed, so nothing was "
                "promoted. Retry, then check the ingestion logs."
            ),
            log_context={"item_id": item_id},
        ) from exc


@router.post("/review/{item_id}/reject", response_model=ReviewRejectResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.review_queue.reject",
            code="review_rejection_failed",
            message=(
                "That review item could not be rejected and its status was left unchanged. Retry, then check the "
                "review queue."
            ),
            log_context={"item_id": item_id},
        ) from exc


@router.post("/review/{item_id}/assign", response_model=ReviewAssignResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.review_queue.assign",
            code="review_assignment_failed",
            message=(
                "That review item could not be assigned and no assignee was written. Retry, then check the review "
                "queue."
            ),
            log_context={"item_id": item_id},
        ) from exc


@router.post("/review/bulk", response_model=BulkReviewResponse)
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
        except Exception as exc:
            # The per-item failure is a 200 body, not an HTTPException, but it
            # reaches the caller just the same - so it gets the same envelope
            # and the same full log line (previously the raw exception text went
            # to the response and nowhere else).
            errors.append(
                {
                    "id": item_id,
                    **internal_error_envelope(
                        exc,
                        logger=logger,
                        operation="foundation.review_queue.bulk_item",
                        code="item_failed",
                        message=(
                            "This item could not be processed; the other items in the batch were unaffected. Retry "
                            "this one after checking the ingestion logs."
                        ),
                        item_id=item_id,
                        action=request.action,
                    ),
                },
            )

    return {
        "success": len(errors) == 0,
        "processed": len(results),
        "errors": len(errors),
        "results": results,
        "error_details": errors,
    }


@router.get("/stats", response_model=PipelineStatsResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.stats",
            code="stats_unavailable",
            message=(
                "Pipeline statistics are unavailable right now. Retry in a few seconds."
            ),
        ) from exc


@router.get("/costs/summary", response_model=CostSummaryResponse)
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.costs.summary",
            code="cost_summary_unavailable",
            message=(
                "The cost summary is unavailable right now. Retry in a few seconds."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Linguistic Analysis Endpoints
# ---------------------------------------------------------------------------

@router.post("/analyze/corpus", response_model=CorpusAnalysisResponse)
async def analyze_corpus(request: CorpusAnalysisRequest):
    """Run corpus-level analysis on input text.

    Returns n-grams, collocations, frequency distribution, and register detection.
    """
    try:
        from ..foundation.corpus import get_corpus_analyzer
        analyzer = get_corpus_analyzer()

        text = request.text
        _ngram_size = request.ngram_size
        _top_k = request.top_k

        if not text:
            raise HTTPException(status_code=400, detail="Text is required")

        result = analyzer.analyze(text)

        return {
            "ngrams": {str(k): v for k, v in result.ngrams.items()},
            "collocations": [
                {"word1": c.word1, "word2": c.word2, "pmi": c.pmi, "freq": c.freq}
                for c in result.collocations
            ],
            "freq_distribution": result.freq_distribution,
            "register": result.register,
            "total_words_analyzed": result.total_words_analyzed,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.analyze.corpus",
            code="analysis_failed",
            message=(
                "Corpus analysis could not be completed. Retry with a shorter text - a very long input is the usual "
                "cause."
            ),
        ) from exc


@router.post("/analyze/phonology", response_model=PhonologyAnalysisResponse)
async def analyze_phonology(request: PhonologyAnalysisRequest):
    """Run phonological analysis on a Zolai word.

    Returns syllable structures, tone patterns, sandhi results, phonotactic validity, stress pattern.
    """
    try:
        from ..foundation.phonology import get_phonological_analyzer
        analyzer = get_phonological_analyzer()

        word = request.word
        _apply_sandhi = request.apply_sandhi

        if not word:
            raise HTTPException(status_code=400, detail="Word is required")

        result = analyzer.analyze(word)

        return {
            "syllable_structures": [
                {
                    "syllable": s.syllable,
                    "onset": s.onset,
                    "nucleus": s.nucleus,
                    "coda": s.coda,
                    "tone": s.tone,
                    "valid": s.valid,
                }
                for s in result.syllable_structures
            ],
            "tone_patterns": list(result.tone_patterns),
            "sandhi_applied": [list(pair) for pair in result.sandhi_applied],
            "phonotactic_valid": result.phonotactic_valid,
            "stress_pattern": list(result.stress_pattern),
            "violations": list(result.violations),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.analyze.phonology",
            code="analysis_failed",
            message=(
                "Phonological analysis could not be completed. Check the word is a single Zolai word, then retry."
            ),
        ) from exc


@router.post("/analyze/morphology", response_model=MorphologyAnalysisResponse)
async def analyze_morphology(request: MorphologyAnalysisRequest):
    """Run enhanced morphological analysis on a Zolai word.

    Returns morpheme decomposition with directional, aspect, particle detection and ZVS validation.
    """
    try:
        from ..foundation.morphology import get_enhanced_morphology
        analyzer = get_enhanced_morphology()

        word = request.word
        if not word:
            raise HTTPException(status_code=400, detail="Word is required")

        result = analyzer.decompose(word)

        return {
            "segments": list(result.segments),
            "directional": result.directional,
            "stem": result.stem,
            "aspect": result.aspect,
            "particle": result.particle,
            "is_valid": result.is_valid,
            "violations": list(result.violations),
            "compound_parts": list(result.compound_parts),
            "prefix": result.prefix,
            "suffix": result.suffix,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.analyze.morphology",
            code="analysis_failed",
            message=(
                "Morphological analysis could not be completed. Check the word is a single Zolai word, then retry."
            ),
        ) from exc


@router.post("/translate/enhanced", response_model=EnhancedTranslationResponse)
async def translate_enhanced(request: EnhancedTranslationRequest):
    """Translate with 3-tier confidence scoring and evidence chain.

    Returns translation with confidence tier, evidence sources, and morphology breakdown.
    """
    try:
        from ..learning.translation import TranslationEngine
        engine = TranslationEngine()

        text = request.text
        direction = request.direction
        context = request.context

        if not text:
            raise HTTPException(status_code=400, detail="Text is required")

        result = engine.translate(text, direction=direction, context=context)

        return {
            "translation": result.get("translation", ""),
            "confidence": result.get("confidence", 0),
            "tier": result.get("tier", "none"),
            "sources": result.get("sources", []),
            "evidence_chain": result.get("evidence_chain", []),
            "zolai": result.get("zolai"),
            "english": result.get("english"),
            "pos": result.get("pos"),
            "note": result.get("note"),
            "morphology_breakdown": result.get("morphology_breakdown"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.translate.enhanced",
            code="translation_failed",
            message=(
                "Translation could not be completed. Check the text and that direction is 'auto', 'zo-en' or 'en-zo', "
                "then retry."
            ),
        ) from exc


@router.post("/progress/adaptive-difficulty", response_model=AdaptiveDifficultyResponse)
async def get_adaptive_difficulty(request: AdaptiveDifficultyRequest):
    """Compute adaptive quiz difficulty based on user's learning profile.

    Returns difficulty settings considering word frequency, morphology complexity,
    tone sensitivity, and historical error rate.
    """
    try:
        from ..learning.progress import ProgressTracker

        user_id = request.user_id
        tracker = ProgressTracker(user_id=user_id)

        result = tracker.get_adaptive_difficulty(user_id=user_id)

        return result
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.progress.adaptive_difficulty",
            code="difficulty_computation_failed",
            message=(
                "Adaptive difficulty could not be computed for this learner. Retry, or omit user_id to use the "
                "default profile."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Search Endpoints
# ---------------------------------------------------------------------------

@router.post("/search/ranked", response_model=RankedSearchResponse)
async def search_ranked(request: RankedSearchRequest):
    """Search with TF-IDF-inspired relevance ranking.

    Returns results ranked by relevance score from dictionary, Bible,
    and grammar pattern sources.
    """
    try:
        from ..learning.online_search import get_online_search
        search = get_online_search()

        query = request.query
        category = request.category
        limit = request.limit

        if not query:
            raise HTTPException(status_code=400, detail="Query is required")

        results = search.search_ranked(query, category=category, limit=limit)

        return {
            "query": query,
            "results": results,
            "total": len(results),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.search.ranked",
            code="search_failed",
            message=(
                "Ranked search could not be completed. Retry, or narrow the query and lower limit."
            ),
        ) from exc


@router.post("/search/cross-lingual", response_model=CrossLingualSearchResponse)
async def search_cross_lingual(request: CrossLingualSearchRequest):
    """Cross-lingual dictionary search between English and Zolai.

    When source_lang="en", queries dictionary_en_zo table.
    When source_lang="zo", queries dictionary table.
    """
    try:
        from ..learning.online_search import get_online_search
        search = get_online_search()

        query = request.query
        source_lang = request.source_lang
        target_lang = request.target_lang
        limit = request.limit

        if not query:
            raise HTTPException(status_code=400, detail="Query is required")

        if source_lang not in ("en", "zo"):
            raise HTTPException(status_code=400, detail="source_lang must be 'en' or 'zo'")

        results = search.search_cross_lingual(
            query, source_lang=source_lang, target_lang=target_lang, limit=limit,
        )

        return {
            "query": query,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "results": results,
            "total": len(results),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.search.cross_lingual",
            code="search_failed",
            message=(
                "Cross-lingual search could not be completed. Retry, or lower limit."
            ),
        ) from exc


@router.post("/search/topical", response_model=TopicalSearchResponse)
async def search_topical(request: TopicalSearchRequest):
    """Search Bible verses by topic/theme keywords across books.

    Matches theme keywords against English and Zolai text to find
    thematically related verses.
    """
    try:
        from ..learning.online_search import get_online_search
        search = get_online_search()

        theme = request.theme
        limit = request.limit

        if not theme:
            raise HTTPException(status_code=400, detail="Theme is required")

        results = search.search_topical(theme, limit=limit)

        return {
            "theme": theme,
            "results": results,
            "total": len(results),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.search.topical",
            code="search_failed",
            message=(
                "Topical Bible search could not be completed. Retry, or lower limit."
            ),
        ) from exc


@router.get("/search/analytics", response_model=SearchAnalyticsResponse)
async def get_search_analytics():
    """Get search analytics sorted by count (descending).

    Returns query frequency, category breakdown, and last-seen timestamps.
    """
    try:
        from ..learning.online_search import get_online_search
        search = get_online_search()

        analytics = search.get_analytics()

        return {
            "analytics": analytics,
            "total_queries": sum(a.get("count", 0) for a in analytics),
        }
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.search.analytics",
            code="analytics_unavailable",
            message=(
                "Search analytics are unavailable right now. Retry in a few seconds."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Grammar Validation Endpoints
# ---------------------------------------------------------------------------

@router.post("/grammar/validate-structure", response_model=StructureValidationResponse)
async def validate_structure(request: StructureValidationRequest):
    """Validate SOV sentence structure and grammar markers.

    Checks verb position, ergative 'in' placement, negation patterns,
    question marker position, and ZVS 2018 compliance.
    """
    try:
        from ..learning.grammar_editor import GrammarEditor
        editor = GrammarEditor()

        sentence = request.sentence
        if not sentence:
            raise HTTPException(status_code=400, detail="Sentence is required")

        result = editor.validate_sentence_structure(sentence)

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.grammar.validate_structure",
            code="validation_failed",
            message=(
                "Sentence structure validation could not be completed. Retry; a very long or unusual sentence is the "
                "usual cause."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Translation Disambiguation Endpoints
# ---------------------------------------------------------------------------

@router.post("/translation/disambiguate", response_model=DisambiguationResponse)
async def disambiguate_translation(request: DisambiguationRequest):
    """Disambiguate polysemous words using dictionary + word_usage context.

    Returns ranked candidates with confidence scores and evidence sources.
    """
    try:
        from ..learning.translation import TranslationEngine
        engine = TranslationEngine()

        word = request.word
        context = request.context
        direction = request.direction

        if not word:
            raise HTTPException(status_code=400, detail="Word is required")

        result = engine._disambiguate_polysemy(word, context=context, direction=direction)

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.translation.disambiguate",
            code="disambiguation_failed",
            message=(
                "Word disambiguation could not be completed. Check the word is a single dictionary headword, then "
                "retry."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Progress / Streak Endpoints
# ---------------------------------------------------------------------------

@router.get("/progress/streak/{user_id}", response_model=StreakResponse)
async def get_user_streak(user_id: str, streak_type: str = "daily"):
    """Get learning streak for a user.

    Returns current streak, longest streak, and activity status.
    """
    try:
        from ..learning.progress import ProgressTracker
        tracker = ProgressTracker(user_id=user_id)

        result = tracker.get_streak(user_id=user_id, streak_type=streak_type)

        return result
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.progress.streak",
            code="streak_unavailable",
            message=(
                "The learning streak could not be read. Retry in a few seconds."
            ),
        ) from exc


@router.post("/progress/error-categorize", response_model=ErrorCategorizationResponse)
async def categorize_error(request: ErrorCategorizationRequest):
    """Categorize the type of error in a correction.

    Checks for ZVS forbidden forms, SOV/negation/question patterns,
    tone sandhi violations, and compound decomposition errors.
    """
    try:
        from ..learning.progress import ProgressTracker

        original = request.original
        corrected = request.corrected

        if not original or not corrected:
            raise HTTPException(status_code=400, detail="Both original and corrected are required")

        result = ProgressTracker.categorize_correction(original, corrected)

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.progress.error_categorize",
            code="categorization_failed",
            message=(
                "That correction could not be categorised. Send a shorter original/corrected pair and retry."
            ),
        ) from exc


@router.get("/progress/error-breakdown/{user_id}", response_model=ErrorBreakdownResponse)
async def get_error_breakdown(user_id: str):
    """Get error breakdown by category for a user.

    Aggregates from user_reviews table and categorizes each correction.
    """
    try:
        from ..learning.progress import ProgressTracker
        tracker = ProgressTracker(user_id=user_id)

        result = tracker.get_error_breakdown(user_id=user_id)

        return result
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="foundation.progress.error_breakdown",
            code="error_breakdown_failed",
            message=(
                "The error breakdown could not be read. Retry in a few seconds."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Machine-readable tool contracts (W1)
#
# One declarative record per typed endpoint above, so the permission an MCP tool
# must carry is *declared* here rather than hand-written as a string in the
# transport layer — and so the declaration can be diffed against the real
# routes (tests/test_foundation_contract.py does exactly that).
#
# This registry is **metadata only**: it documents the scope a caller is
# expected to hold. It grants nothing and enforces nothing on its own. HTTP
# authorisation stays where it already lives — ``rbac.classify_route``, which
# every ``/api/v1/foundation/*`` route satisfies as ``member`` (401 anonymous
# under ``ZOLAI_API_AUTH=enforce``) except the public ``GET /stats`` — and the
# scope strings below are drawn from the frozen 33-action vocabulary in
# :data:`zolai.api.auth.VALID_ACTIONS`.
#
# Consumers (a later wave): the MCP server derives each tool's input schema from
# the route's request model and its declared permissions from ``required_scope``;
# ``GET /api/v1/mcp-manifest`` serves the same data. ``path`` is the
# router-relative path exactly as written in the ``@router.post(...)`` decorator
# (``server.py`` mounts this router with ``prefix="/api/v1"``).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoundationToolContract:
    """Declarative contract for one foundation endpoint exposed as a tool."""

    #: HTTP method of the route.
    method: str
    #: Router-relative path, matching the ``@router.post(...)`` decorator verbatim.
    path: str
    #: Stable tool name (also the handler name); safe to expose to a model.
    operation_id: str
    #: Frozen-vocabulary scope a caller must hold; ``""`` marks the route public.
    required_scope: str
    #: One line an LLM reads to decide whether to call this tool.
    description: str


#: Read-only analysis and search over the canonical linguistic data. No write
#: scope appears here because none of these 11 routes writes.
_FOUNDATION_READ_SCOPE = "dataset:read"

#: Public marker for :attr:`FoundationToolContract.required_scope`.
PUBLIC_SCOPE = ""


FOUNDATION_TOOL_CONTRACTS: tuple[FoundationToolContract, ...] = (
    FoundationToolContract(
        method="POST",
        path="/analyze/corpus",
        operation_id="analyze_corpus",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Corpus-level text analysis: n-grams, PMI collocations, Zipf frequency distribution "
            "and a formal/common/literary register guess."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/analyze/phonology",
        operation_id="analyze_phonology",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Phonological analysis of one Zolai word: syllable structure with tones, tone "
            "sandhi, phonotactic validity and stress pattern."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/analyze/morphology",
        operation_id="analyze_morphology",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Morphological decomposition of one Zolai word into directional prefix, stem, aspect "
            "suffix and particle, with ZVS 2018 validation."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/translate/enhanced",
        operation_id="translate_enhanced",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Translate Zolai/English with three-tier confidence (dictionary, Bible parallel, "
            "corpus), an evidence chain and a morphology-aware breakdown."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/progress/adaptive-difficulty",
        operation_id="get_adaptive_difficulty",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Compute a learner's next quiz difficulty from word frequency, morphology complexity, "
            "tone sensitivity and error rate."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/search/ranked",
        operation_id="search_ranked",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Relevance-ranked bilingual search across the dictionary, Bible and grammar-pattern "
            "sources."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/search/cross-lingual",
        operation_id="search_cross_lingual",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Dictionary lookup between English and Zolai; pick the source language to choose "
            "which dictionary direction is searched."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/search/topical",
        operation_id="search_topical",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Find Bible verses by theme keyword, matching both English and Zolai text."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/grammar/validate-structure",
        operation_id="validate_structure",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Check a Zolai sentence for SOV verb position, ergative 'in', negation and question "
            "markers, and ZVS 2018 compliance."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/translation/disambiguate",
        operation_id="disambiguate_translation",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Disambiguate a polysemous Zolai word by ranking its dictionary senses, optionally "
            "weighted by per-book usage."
        ),
    ),
    FoundationToolContract(
        method="POST",
        path="/progress/error-categorize",
        operation_id="categorize_error",
        required_scope=_FOUNDATION_READ_SCOPE,
        description=(
            "Classify a learner's correction as orthographic, word-order, negation, "
            "question-form, tone-sandhi or compound-word error."
        ),
    ),
)
