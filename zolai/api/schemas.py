"""Pydantic models for Foundation API endpoints.

Provides request/response schemas for:
- Corpus analysis
- Phonological analysis
- Enhanced translation
"""
from __future__ import annotations

from typing import Any, Optional

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
