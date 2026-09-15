"""Zolai Foundation — Core analysis, evidence, and consensus layer.

Public API:
- FoundationAnalyzer: orchestrate tokenizer, syllable, POS, morphology
- WordAnalysis, SentenceAnalysis, ParagraphAnalysis: structured results
- Candidate, Evidence, Confidence: evidence gating dataclasses
- Verifier, NullVerifier, EvidenceThresholdVerifier: verification interface
- ConsensusResult, run_consensus: consensus decision making
"""
from __future__ import annotations

from .analysis import (
    FoundationAnalyzer,
    MorphologyInfo,
    ParagraphAnalysis,
    POSInfo,
    SentenceAnalysis,
    SyllableInfo,
    TokenAnalysis,
    WordAnalysis,
    get_foundation_analyzer,
)
from .consensus import (
    ConsensusResult,
    adaptive_consensus,
    build_candidates_from_evidence,
    majority_vote,
    run_consensus,
    threshold_gate_consensus,
    weighted_evidence_consensus,
)
from .evidence import (
    DEFAULT_VERIFIER,
    EVIDENCE_VERIFIER,
    Candidate,
    Confidence,
    Evidence,
    EvidenceThresholdVerifier,
    EvidenceTier,
    NullVerifier,
    Verifier,
    create_paragraph_candidate,
    create_sentence_candidate,
    create_word_candidate,
)

__all__ = [
    # Analysis
    "FoundationAnalyzer",
    "WordAnalysis",
    "SentenceAnalysis",
    "ParagraphAnalysis",
    "TokenAnalysis",
    "SyllableInfo",
    "MorphologyInfo",
    "POSInfo",
    "get_foundation_analyzer",
    # Evidence
    "Candidate",
    "Evidence",
    "Confidence",
    "EvidenceTier",
    "Verifier",
    "NullVerifier",
    "EvidenceThresholdVerifier",
    "create_word_candidate",
    "create_sentence_candidate",
    "create_paragraph_candidate",
    "DEFAULT_VERIFIER",
    "EVIDENCE_VERIFIER",
    # Consensus
    "ConsensusResult",
    "majority_vote",
    "weighted_evidence_consensus",
    "threshold_gate_consensus",
    "adaptive_consensus",
    "run_consensus",
    "build_candidates_from_evidence",
]

__version__ = "0.1.0"
