"""RAG API Router (Phase 6 §24).

13 new /api/v1 endpoints for language intelligence.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from zolai.api.auth import require_scope
from zolai.api.errors import api_error
from zolai.data.repositories import get_engine
from zolai.rag.retrieve import UnifiedRetriever

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["RAG"])


# --- Request/Response Models ---
class WordDetailResponse(BaseModel):
    word: str
    forms: list[str] = []
    frequency: int = 0
    document_frequency: int = 0
    sentence_frequency: int = 0
    pos: list[str] = []
    morphology: dict[str, Any] = {}
    examples: list[str] = []
    collocations: list[dict[str, Any]] = []
    grammar_usage: list[dict[str, Any]] = []
    sources: list[str] = []
    confidence: float = 0.0


class WordFormsResponse(BaseModel):
    word: str
    forms: list[str]


class WordContextsResponse(BaseModel):
    word: str
    contexts: list[dict[str, Any]]


class WordCollocationsResponse(BaseModel):
    word: str
    collocations: list[dict[str, Any]]


class WordPatternsResponse(BaseModel):
    word: str
    patterns: list[dict[str, Any]]


class WordEvidenceResponse(BaseModel):
    word: str
    evidence: list[dict[str, Any]]


class RelatedWord(BaseModel):
    word: str
    pos: str | None = None
    frequency: int = 0
    relation: str
    shared_prefix: int = 0


class WordRelatedResponse(BaseModel):
    word: str
    related: list[RelatedWord]


class AnalyzeWordRequest(BaseModel):
    word: str


class AnalyzeWordResponse(BaseModel):
    word: str
    tokens: list[str] = []
    pos: list[str] = []
    morphology: dict[str, Any] = {}
    attestation: list[dict[str, Any]] = []


class AnalyzeSentenceRequest(BaseModel):
    text: str


class AnalyzeSentenceResponse(BaseModel):
    text: str
    tokens: list[str]
    pos: list[str] = []
    grammar: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []


class AnalyzeParagraphRequest(BaseModel):
    text: str


class AnalyzeParagraphResponse(BaseModel):
    text: str
    sentences: list[str]
    analysis: dict[str, Any]


class SearchRequest(BaseModel):
    query: str
    limit: int = 20
    source_filter: list[str] | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]


class RAGRequest(BaseModel):
    question: str
    limit: int = 5
    #: Optional per-request provider / model override, resolved exactly as the
    #: assistant resolves them (one shared path, validated against the row).
    provider: str | None = None
    model: str | None = None


class RAGResponse(BaseModel):
    question: str
    context: str
    citations: list[dict[str, Any]]
    answer: str
    retrieved_count: int
    #: True when a model wrote ``answer``; False when it is retrieved evidence.
    generated: bool = False
    #: True when no model was called. Mirrors the assistant's contract so a
    #: caller can never mistake the two, and never has to parse prose to tell.
    retrieval_only: bool = True
    #: Which provider/model actually wrote the answer ("" when none resolved).
    provider: str = ""
    model: str = ""
    provider_error: str = ""


class KnowledgeVersionResponse(BaseModel):
    version: str
    git_commit: str
    created_at: str


class KnowledgeStatsResponse(BaseModel):
    stats: dict[str, int]


def get_retriever() -> UnifiedRetriever:
    engine = get_engine()
    return UnifiedRetriever(engine)


# --- Endpoints ---
@router.get(
    "/word/{word}",
    response_model=WordDetailResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_detail(
    word: str,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordDetailResponse:
    """Full word detail per §20: forms, frequency, POS, morphology,
        examples, collocations, grammar usage, sources, confidence.

    Unknown lemmas are a real **404** (core#4), not a 200 whose every count is
    zero: ``query_word`` answers an empty evidence pack for a word no table
    knows, and a client branching on ``res.ok`` cannot tell "empty entry" from
    "no such word". The body is the house envelope
    (``{"error": ..., "message": ...}``, under FastAPI's ``detail``), matching
    the real-404 precedent of ``ce04c72``. ``/api/v1/lexicon/{word}`` keeps its
    own documented ``found`` flag — that contract is asserted by its own tests
    and is deliberately not changed here.

    The retriever is synchronous SQLite; every handler in this module hands the
    call to a worker thread so one lookup cannot freeze the event loop
    (core#24's disease, measured here at 8–32s on the search path).
    """
    pack = await run_in_threadpool(retriever.query_word, word)
    if not getattr(pack, "evidence_items", None):
        raise HTTPException(
            status_code=404,
            detail=api_error(
                "word_not_found",
                f"'{word}' is not in the lexicon or any corpus. "
                "Check the spelling, or search instead of looking the word up.",
            ),
        )
    return WordDetailResponse(**pack.__dict__)


@router.get(
    "/word/{word}/forms",
    response_model=WordFormsResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_forms(
    word: str,
    limit: int = Query(20, ge=1, le=100),
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordFormsResponse:
    """Get word forms (surface variants)."""
    forms = await run_in_threadpool(retriever.query_word_forms, word, limit)
    return WordFormsResponse(word=word, forms=forms)


@router.get(
    "/word/{word}/contexts",
    response_model=WordContextsResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_contexts(
    word: str,
    limit: int = Query(20, ge=1, le=100),
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordContextsResponse:
    """Get contexts (left/right/sentence/document) for a word."""
    contexts = await run_in_threadpool(retriever.query_word_contexts, word, limit)
    return WordContextsResponse(word=word, contexts=contexts)


@router.get(
    "/word/{word}/collocations",
    response_model=WordCollocationsResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_collocations(
    word: str,
    limit: int = Query(20, ge=1, le=100),
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordCollocationsResponse:
    """Get collocations with PMI for a word."""
    collocations = await run_in_threadpool(retriever.query_word_collocations, word, limit)
    return WordCollocationsResponse(word=word, collocations=collocations)


@router.get(
    "/word/{word}/patterns",
    response_model=WordPatternsResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_patterns(
    word: str,
    limit: int = Query(20, ge=1, le=100),
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordPatternsResponse:
    """Get grammar patterns involving a word."""
    patterns = await run_in_threadpool(retriever.query_word_patterns, word, limit)
    return WordPatternsResponse(word=word, patterns=patterns)


@router.get(
    "/word/{word}/evidence",
    response_model=WordEvidenceResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_evidence(
    word: str,
    limit: int = Query(50, ge=1, le=200),
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordEvidenceResponse:
    """Get evidence chain for a word (attestation_index + foundation_evidence)."""
    evidence = await run_in_threadpool(retriever.query_word_evidence, word, limit)
    return WordEvidenceResponse(word=word, evidence=evidence)


@router.get(
    "/word/{word}/related",
    response_model=WordRelatedResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_word_related(
    word: str,
    limit: int = Query(12, ge=1, le=50),
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> WordRelatedResponse:
    """Related words, for display *after* the exact match.

    Same word family first (shared prefix / extension), then same-POS
    neighbours. Never includes the query word itself.
    """
    related = await run_in_threadpool(retriever.related_words, word, limit)
    return WordRelatedResponse(word=word, related=related)


@router.post(
    "/analyze/word",
    response_model=AnalyzeWordResponse,
    dependencies=[Depends(require_scope("rag:read"))],
)
async def analyze_word(
    request: AnalyzeWordRequest,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> AnalyzeWordResponse:
    """Analyze a single word (POS, morphology, attestation).

    POS, morphology and the attestation list come from **the same evidence
    source as ``GET /api/v1/word/{word}``** (``query_word`` → dictionary POS
    columns, Bible attestations, observation stats, claims, KG — core#23), so
    the two routes can never disagree.  ``analyze_sentence`` stays what it is —
    a sentence-level tokenizer — and only supplies the token split.
    """
    tokens = (await run_in_threadpool(retriever.analyze_sentence, request.word)).get("tokens", [])
    pack = await run_in_threadpool(retriever.query_word, request.word)
    return AnalyzeWordResponse(
        word=request.word,
        tokens=tokens or [request.word],
        pos=list(pack.pos),
        morphology=dict(pack.morphology),
        attestation=[
            {
                "source_type": item.source_type,
                "source": item.source,
                "tier": item.tier,
                "confidence": item.confidence,
                "text": item.text,
                "metadata": item.metadata,
            }
            for item in pack.evidence_items
        ],
    )


@router.post(
    "/analyze/sentence",
    response_model=AnalyzeSentenceResponse,
    dependencies=[Depends(require_scope("rag:read"))],
)
async def analyze_sentence(
    request: AnalyzeSentenceRequest,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> AnalyzeSentenceResponse:
    """Analyze a Zolai sentence (tokenize, POS, grammar, entities)."""
    result = await run_in_threadpool(retriever.analyze_sentence, request.text)
    return AnalyzeSentenceResponse(
        text=request.text,
        tokens=result.get("tokens", []),
        pos=result.get("pos", []),
        grammar=result.get("grammar", []),
        entities=result.get("entities", []),
    )


@router.post(
    "/analyze/paragraph",
    response_model=AnalyzeParagraphResponse,
    dependencies=[Depends(require_scope("rag:read"))],
)
async def analyze_paragraph(
    request: AnalyzeParagraphRequest,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> AnalyzeParagraphResponse:
    """Analyze a Zolai paragraph (multi-sentence, discourse)."""
    result = await run_in_threadpool(retriever.analyze_paragraph, request.text)
    return AnalyzeParagraphResponse(
        text=request.text,
        sentences=result.get("sentences", []),
        analysis=result.get("analysis", {}),
    )


@router.post(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def search(
    request: SearchRequest,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> SearchResponse:
    """Bilingual search (lexical + vector fallback).

    A multi-token query lands in the semantic pass, which is 8s warm and 32s
    cold on the production corpus — inline, that froze every other request on
    the server (core#24's failure mode, this time in the retriever).
    """
    results = await run_in_threadpool(retriever.search, request.query, request.limit, request.source_filter)
    return SearchResponse(
        query=request.query,
        results=[r.__dict__ for r in results],
    )


@router.post(
    "/rag",
    response_model=RAGResponse,
    dependencies=[Depends(require_scope("rag:read"))],
)
async def rag_query(
    request: RAGRequest,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> RAGResponse:
    """Full RAG: question → retrieval → a model-written answer with citations.

    Falls back to the retrieved evidence (and says so via ``retrieval_only``)
    when no provider resolves or the call fails.
    """
    result = await run_in_threadpool(
        retriever.rag_query,
        request.question,
        request.limit,
        provider=request.provider,
        model=request.model,
    )
    return RAGResponse(**result)


@router.get(
    "/knowledge/version",
    response_model=KnowledgeVersionResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_knowledge_version(
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> KnowledgeVersionResponse:
    """Get current knowledge version."""
    return KnowledgeVersionResponse(**await run_in_threadpool(retriever.get_knowledge_version))


@router.get(
    "/knowledge/statistics",
    response_model=KnowledgeStatsResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_knowledge_statistics(
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> KnowledgeStatsResponse:
    """Get aggregate knowledge statistics."""
    return KnowledgeStatsResponse(stats=await run_in_threadpool(retriever.get_knowledge_statistics))
