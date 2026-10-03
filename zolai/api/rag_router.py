"""RAG API Router (Phase 6 §24).

13 new /api/v1 endpoints for language intelligence.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from zolai.api.auth import require_scope
from zolai.data.repositories import get_engine
from zolai.rag.retrieve import UnifiedRetriever, SearchResult

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


class RAGResponse(BaseModel):
    question: str
    context: str
    citations: list[dict[str, Any]]
    answer: str
    retrieved_count: int


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
    """Get full word detail per §20 (forms, frequency, POS, morphology, examples, collocations, grammar usage, sources, confidence)."""
    pack = retriever.query_word(word)
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
    forms = retriever.query_word_forms(word, limit)
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
    contexts = retriever.query_word_contexts(word, limit)
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
    collocations = retriever.query_word_collocations(word, limit)
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
    patterns = retriever.query_word_patterns(word, limit)
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
    evidence = retriever.query_word_evidence(word, limit)
    return WordEvidenceResponse(word=word, evidence=evidence)


@router.post(
    "/analyze/word",
    response_model=AnalyzeWordResponse,
    dependencies=[Depends(require_scope("rag:read"))],
)
async def analyze_word(
    request: AnalyzeWordRequest,
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> AnalyzeWordResponse:
    """Analyze a single word (POS, morphology, attestation)."""
    result = retriever.analyze_sentence(request.word)  # Reuse sentence analyzer for single word
    return AnalyzeWordResponse(
        word=request.word,
        tokens=result.get("tokens", []),
        pos=result.get("pos", []),
        morphology=result.get("morphology", {}),
        attestation=result.get("attestation", []),
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
    result = retriever.analyze_sentence(request.text)
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
    result = retriever.analyze_paragraph(request.text)
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
    """Bilingual search (lexical + vector fallback)."""
    results = retriever.search(request.query, request.limit, request.source_filter)
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
    """Full RAG: question → retrieval → answer with citations."""
    result = retriever.rag_query(request.question, request.limit)
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
    return KnowledgeVersionResponse(**retriever.get_knowledge_version())


@router.get(
    "/knowledge/statistics",
    response_model=KnowledgeStatsResponse,
    dependencies=[Depends(require_scope("dataset:read"))],
)
async def get_knowledge_statistics(
    retriever: UnifiedRetriever = Depends(get_retriever),
) -> KnowledgeStatsResponse:
    """Get aggregate knowledge statistics."""
    return KnowledgeStatsResponse(stats=retriever.get_knowledge_statistics())
