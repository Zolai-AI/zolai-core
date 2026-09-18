"""Zolai Foundation — Core analysis, evidence, consensus, verification, regression,
and professional linguistic analysis (corpus, morphology, phonology).

Public API:
- FoundationAnalyzer: orchestrate tokenizer, syllable, POS, morphology
- WordAnalysis, SentenceAnalysis, ParagraphAnalysis: structured results
- Candidate, Evidence, Confidence: evidence gating dataclasses
- Verifier, NullVerifier, EvidenceThresholdVerifier: verification interface
- GeminiVerifier, EvidenceGatingVerifier: LLM-backed verification
- ConsensusResult, run_consensus: consensus decision making
- RegressionSuite, RegressionReport: regression testing
- CorpusAnalyzer: corpus-level n-gram, collocation, register analysis
- EnhancedMorphologyAnalyzer: agglutinative decomposition with ZVS validation
- PhonologicalAnalyzer: syllable validation, tone sandhi, phonotactics
- FoundationETL: Raw → Staging → Canonical data pipeline
- VerificationRunner: adaptive-threshold pipeline health checks
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
from .corpus import (
    Collocation,
    CorpusAnalysis,
    CorpusAnalyzer,
    get_corpus_analyzer,
)
from .etl import (
    FoundationETL,
    get_foundation_etl,
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
from .morphology import (
    EnhancedMorphologyAnalyzer,
    MorphemeAnalysis,
    get_enhanced_morphology,
)
from .phonology import (
    PhonologicalAnalysis,
    PhonologicalAnalyzer,
    SyllableStructure,
    get_phonological_analyzer,
)
from .regression import (
    GrammarRegressionTest,
    RegressionCategoryReport,
    RegressionReport,
    RegressionSuite,
    SyllableRegressionTest,
    ToneRegressionTest,
    ZVSRegressionTest,
)
from .verification_runner import (
    VerificationReport,
    VerificationRunner,
    get_verification_runner,
)
from .verifiers import (
    EvidenceGateError,
    EvidenceGatingVerifier,
    GeminiVerifier,
    ModelRouter,
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
    # Corpus
    "CorpusAnalyzer",
    "CorpusAnalysis",
    "Collocation",
    "get_corpus_analyzer",
    # Enhanced Morphology
    "EnhancedMorphologyAnalyzer",
    "MorphemeAnalysis",
    "get_enhanced_morphology",
    # Phonology
    "PhonologicalAnalyzer",
    "PhonologicalAnalysis",
    "SyllableStructure",
    "get_phonological_analyzer",
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
    # Verifiers
    "GeminiVerifier",
    "EvidenceGatingVerifier",
    "EvidenceGateError",
    "ModelRouter",
    # Consensus
    "ConsensusResult",
    "majority_vote",
    "weighted_evidence_consensus",
    "threshold_gate_consensus",
    "adaptive_consensus",
    "run_consensus",
    "build_candidates_from_evidence",
    # Regression
    "RegressionSuite",
    "RegressionReport",
    "RegressionCategoryReport",
    "ZVSRegressionTest",
    "GrammarRegressionTest",
    "SyllableRegressionTest",
    "ToneRegressionTest",
    # ETL Pipeline
    "FoundationETL",
    "get_foundation_etl",
    # Verification Runner
    "VerificationRunner",
    "VerificationReport",
    "get_verification_runner",
]

__version__ = "0.1.0"
