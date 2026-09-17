"""Pydantic models for Foundation API endpoints.

Provides request/response schemas for:
- Corpus analysis
- Phonological analysis
- Enhanced translation
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# ── Corpus Analysis ──────────────────────────────────────────────────────────

class CorpusAnalysisRequest(BaseModel):
    """Request for corpus-level analysis."""
    text: str = Field(..., description="Text to analyze")
    ngram_size: int = Field(2, ge=1, le=5, description="N-gram size")
    top_k: int = Field(100, ge=1, le=1000, description="Top-k frequency words")


class CollocationModel(BaseModel):
    """Collocation data."""
    word1: str
    word2: str
    pmi: float
    freq: int


class CorpusAnalysisResponse(BaseModel):
    """Response for corpus analysis."""
    ngrams: dict[str, int] = Field(default_factory=dict)
    collocations: list[CollocationModel] = Field(default_factory=list)
    freq_distribution: dict[str, int] = Field(default_factory=dict)
    register: str = "common"
    total_words_analyzed: int = 0


# ── Phonological Analysis ───────────────────────────────────────────────────

class PhonologicalAnalysisRequest(BaseModel):
    """Request for phonological analysis."""
    word: str = Field(..., description="Zolai word to analyze")
    apply_sandhi: bool = Field(True, description="Apply tone sandhi rules")


class SyllableStructureModel(BaseModel):
    """Syllable structure data."""
    syllable: str
    onset: str
    nucleus: str
    coda: str
    tone: str = "T1"
    valid: bool = True


class PhonologicalAnalysisResponse(BaseModel):
    """Response for phonological analysis."""
    syllable_structures: list[SyllableStructureModel] = Field(default_factory=list)
    tone_patterns: list[str] = Field(default_factory=list)
    sandhi_applied: list[list[str]] = Field(default_factory=list)
    phonotactic_valid: bool = True
    stress_pattern: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


# ── Enhanced Translation ────────────────────────────────────────────────────

class EnhancedTranslationRequest(BaseModel):
    """Request for enhanced translation with 3-tier confidence."""
    text: str = Field(..., description="Text to translate")
    direction: str = Field("auto", description="Translation direction: zo-en, en-zo, auto")
    context: Optional[str] = Field(None, description="Context: bible, daily, etc.")


class EvidenceChainItem(BaseModel):
    """Evidence chain entry."""
    source: str
    confidence: float


class EnhancedTranslationResponse(BaseModel):
    """Response for enhanced translation."""
    translation: str = ""
    confidence: float = 0.0
    tier: str = "none"
    sources: list[str] = Field(default_factory=list)
    evidence_chain: list[EvidenceChainItem] = Field(default_factory=list)
    zolai: Optional[str] = None
    english: Optional[str] = None
    pos: Optional[str] = None
    note: Optional[str] = None
    morphology_breakdown: Optional[list[str]] = None


# ── Morphology Analysis ─────────────────────────────────────────────────────

class MorphologyAnalysisRequest(BaseModel):
    """Request for enhanced morphological analysis."""
    word: str = Field(..., description="Zolai word to decompose")


class MorphemeAnalysisResponse(BaseModel):
    """Response for morphological analysis."""
    segments: list[str] = Field(default_factory=list)
    directional: Optional[str] = None
    stem: str = ""
    aspect: Optional[str] = None
    particle: Optional[str] = None
    is_valid: bool = True
    violations: list[str] = Field(default_factory=list)
    compound_parts: list[str] = Field(default_factory=list)
    prefix: str = ""
    suffix: str = ""


# ── Adaptive Difficulty ─────────────────────────────────────────────────────

class AdaptiveDifficultyRequest(BaseModel):
    """Request for adaptive difficulty computation."""
    user_id: str = Field("default", description="User ID")


class AdaptiveDifficultyResponse(BaseModel):
    """Response for adaptive difficulty."""
    difficulty: str = "beginner"
    frequency_tier: str = "high"
    morphology_threshold: float = 0.2
    include_tone_questions: bool = False
    recommended_count: int = 10
    error_rate: float = 0.0
    avg_ease: float = 2.5
    avg_complexity: float = 0.0
    reason: str = ""


# ── Ranked Search ─────────────────────────────────────────────────────────

class RankedSearchRequest(BaseModel):
    """Request for ranked search with TF-IDF-inspired scoring."""
    query: str = Field(..., description="Search query")
    category: Optional[str] = Field(None, description="Category filter: vocabulary, grammar, bible")
    limit: int = Field(10, ge=1, le=100, description="Maximum results")


class RankedSearchResult(BaseModel):
    """Single ranked search result."""
    source: str = ""
    type: str = ""
    relevance: float = 0.0
    data: dict = Field(default_factory=dict)


class RankedSearchResponse(BaseModel):
    """Response for ranked search."""
    query: str = ""
    results: list[dict] = Field(default_factory=list)
    total: int = 0


# ── Cross-Lingual Search ─────────────────────────────────────────────────

class CrossLingualSearchRequest(BaseModel):
    """Request for cross-lingual dictionary search."""
    query: str = Field(..., description="Search term")
    source_lang: str = Field("en", description="Source language: 'en' or 'zo'")
    target_lang: str = Field("zo", description="Target language: 'en' or 'zo'")
    limit: int = Field(10, ge=1, le=100, description="Maximum results")


class CrossLingualSearchResponse(BaseModel):
    """Response for cross-lingual search."""
    query: str = ""
    source_lang: str = "en"
    target_lang: str = "zo"
    results: list[dict] = Field(default_factory=list)
    total: int = 0


# ── Topical Search ───────────────────────────────────────────────────────

class TopicalSearchRequest(BaseModel):
    """Request for topical Bible verse search."""
    theme: str = Field(..., description="Theme keywords")
    limit: int = Field(10, ge=1, le=100, description="Maximum results")


class TopicalSearchResponse(BaseModel):
    """Response for topical Bible search."""
    theme: str = ""
    results: list[dict] = Field(default_factory=list)
    total: int = 0


# ── Search Analytics ─────────────────────────────────────────────────────

class AnalyticsResponse(BaseModel):
    """Response for search analytics."""
    analytics: list[dict] = Field(default_factory=list)
    total_queries: int = 0


# ── Structure Validation ─────────────────────────────────────────────────

class StructureValidationRequest(BaseModel):
    """Request for SOV sentence structure validation."""
    sentence: str = Field(..., description="Zolai sentence to validate")


class StructureValidationResponse(BaseModel):
    """Response for structure validation."""
    valid: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tokens: list[str] = Field(default_factory=list)
    verb_position: Optional[int] = None
    zvs_compliant: bool = True


# ── Disambiguate ─────────────────────────────────────────────────────────

class DisambiguateRequest(BaseModel):
    """Request for polysemy disambiguation."""
    word: str = Field(..., description="Word to disambiguate")
    context: Optional[str] = Field(None, description="Context for disambiguation")
    direction: str = Field("zo-en", description="Translation direction")


class DisambiguateCandidate(BaseModel):
    """Single disambiguation candidate."""
    meaning: str = ""
    confidence: float = 0.0
    evidence: str = ""
    pos: Optional[str] = None


class DisambiguateResponse(BaseModel):
    """Response for polysemy disambiguation."""
    word: str = ""
    direction: str = "zo-en"
    candidates: list[DisambiguateCandidate] = Field(default_factory=list)
    total_senses: int = 0


# ── Streak ───────────────────────────────────────────────────────────────

class StreakResponse(BaseModel):
    """Response for learning streak."""
    user_id: str = ""
    streak_type: str = "daily"
    current_streak: int = 0
    longest_streak: int = 0
    last_activity_date: Optional[str] = None
    is_active: bool = False


# ── Error Categorize ─────────────────────────────────────────────────────

class ErrorCategorizeRequest(BaseModel):
    """Request for error categorization."""
    original: str = Field(..., description="Original (incorrect) text")
    corrected: str = Field(..., description="Corrected text")


class ErrorCategorizeResponse(BaseModel):
    """Response for error categorization."""
    category: str = ""
    subcategory: str = ""
    rule: str = ""


# ── Error Breakdown ──────────────────────────────────────────────────────

class ErrorBreakdownResponse(BaseModel):
    """Response for error breakdown by category."""
    user_id: str = ""
    total_errors: int = 0
    categories: dict = Field(default_factory=dict)
    most_common: Optional[str] = None
