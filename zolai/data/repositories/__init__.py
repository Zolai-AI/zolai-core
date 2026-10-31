"""Repository layer for Zolai data access.

Provides domain-specific repositories with common CRUD operations,
transactions, version tracking, and audit logging.
"""

from pathlib import Path

from ...config import config
from .alignment import AlignmentRepository
from .base import BaseRepository
from .bible import BibleAnalysisRepository, BibleRepository
from .dictionary import DictionaryEnZoRepository, DictionaryRepository
from .exercise import ExerciseRepository
from .extended import (
    CorrectionRepository,
    GrammarInstructionRepository,
    KGRepository,
    KnowledgeVectorRepository,
    NgramRepository,
    ParticleRepository,
    SimbuRepository,
    TrainingValidationRepository,
    VerbRepository,
)
from .foundation import (
    FOUNDATION_REPOSITORIES,
    CanonicalParagraphsRepository,
    CanonicalSentencesRepository,
    CanonicalWordsRepository,
    FoundationBatchesRepository,
    FoundationConsensusRepository,
    FoundationCostTrackingRepository,
    FoundationEvidenceRepository,
    FoundationMetricsRepository,
    FoundationRawCorpusRepository,
    FoundationRawLLMRepository,
    FoundationReviewQueueRepository,
    FoundationStagingEvidenceRepository,
    FoundationStagingParagraphsRepository,
    FoundationStagingSentencesRepository,
    FoundationStagingWordsRepository,
    FoundationVerificationsRepository,
    get_foundation_repositories,
)
from .grammar import GrammarRepository, WordCollocationRepository
from .knowledge import ClaimRepository, HypothesisRepository, KnowledgeVersionRepository
from .observation import (
    AttestationIndexRepository,
    ObservationRepository,
    WordStatsRepository,
)
from .phrase import PhraseRepository
from .provenance import AuditRepository, ProvenanceRepository
from .translation import TranslationRepository, WordAlignmentRepository
from .vocabulary import VocabularyRepository, WordUsageRepository


def _bootstrap_isolated_engine(engine) -> None:
    """Create repository tables for non-canonical, isolated databases only."""
    database = engine.url.database
    if database and Path(database).resolve() == Path(config.paths.zolai_db).resolve():
        return

    from ..models import Base
    from ..schema_v2 import ensure_schema_v2

    Base.metadata.create_all(engine)
    ensure_schema_v2(engine)


__all__ = [
    "AlignmentRepository",
    "AttestationIndexRepository",
    "AuditRepository",
    "BaseRepository",
    "BibleAnalysisRepository",
    "BibleRepository",
    "ClaimRepository",
    "CorrectionRepository",
    "DictionaryEnZoRepository",
    "DictionaryRepository",
    "ExerciseRepository",
    "FOUNDATION_REPOSITORIES",
    "CanonicalParagraphsRepository",
    "CanonicalSentencesRepository",
    "CanonicalWordsRepository",
    "FoundationBatchesRepository",
    "FoundationConsensusRepository",
    "FoundationCostTrackingRepository",
    "FoundationEvidenceRepository",
    "FoundationMetricsRepository",
    "FoundationRawCorpusRepository",
    "FoundationRawLLMRepository",
    "FoundationReviewQueueRepository",
    "FoundationStagingEvidenceRepository",
    "FoundationStagingParagraphsRepository",
    "FoundationStagingSentencesRepository",
    "FoundationStagingWordsRepository",
    "FoundationVerificationsRepository",
    "get_engine",
    "get_foundation_repositories",
    "get_repositories",
    "GrammarInstructionRepository",
    "GrammarRepository",
    "HypothesisRepository",
    "KnowledgeVectorRepository",
    "KnowledgeVersionRepository",
    "NgramRepository",
    "ObservationRepository",
    "ParticleRepository",
    "PhraseRepository",
    "ProvenanceRepository",
    "SimbuRepository",
    "TrainingValidationRepository",
    "TranslationRepository",
    "VerbRepository",
    "VocabularyRepository",
    "WordAlignmentRepository",
    "WordCollocationRepository",
    "WordStatsRepository",
    "WordUsageRepository",
]


def get_engine(db_path=None):
    """Shared SQLAlchemy engine for the repository layer.

    Initializes the ORM and v2 tables for fresh isolated databases. The
    canonical database is expected to have been bootstrapped by its migration
    path and is never rewritten by this factory.
    """
    from ..schema_v2 import get_engine as _get_engine

    engine = _get_engine(db_path)
    _bootstrap_isolated_engine(engine)
    return engine


def get_repositories(db_path=None, engine=None) -> dict[str, BaseRepository]:
    """Return an ``{entity_name: repository}`` map for both legacy + extended repos.

    Convenience factory used by serving modules and the API to instantiate every
    repository against a single engine.
    """
    from ..schema_v2 import get_engine as _get_engine

    if engine is None:
        engine = _get_engine(db_path)
    _bootstrap_isolated_engine(engine)
    repos = {
        "dictionary": DictionaryRepository(engine),
        "dictionary_en_zo": DictionaryEnZoRepository(engine),
        "vocabulary": VocabularyRepository(engine),
        "grammar": GrammarRepository(engine),
        "bible": BibleRepository(engine),
        "bible_analysis": BibleAnalysisRepository(engine),
        "translation": TranslationRepository(engine),
        "word_usage": WordUsageRepository(engine),
        "word_collocation": WordCollocationRepository(engine),
        "word_alignment": WordAlignmentRepository(engine),
        "phrase": PhraseRepository(engine),
        "exercise": ExerciseRepository(engine),
        "provenance": ProvenanceRepository(engine),
        "audit": AuditRepository(engine),
        "ngram": NgramRepository(engine),
        "knowledge_vectors": KnowledgeVectorRepository(engine),
        "particle_database": ParticleRepository(engine),
        "verb_database": VerbRepository(engine),
        "training_validation": TrainingValidationRepository(engine),
        "simbu": SimbuRepository(engine),
        "grammar_instructions": GrammarInstructionRepository(engine),
        "corrections": CorrectionRepository(engine),
        "claims": ClaimRepository(engine),
        "hypotheses": HypothesisRepository(engine),
        "knowledge_versions": KnowledgeVersionRepository(engine),
        "observations": ObservationRepository(engine),
        "word_observation_stats": WordStatsRepository(engine),
        "attestation_index": AttestationIndexRepository(engine),
        "kg": KGRepository(engine),
    }
    # Add foundation repositories
    repos.update(get_foundation_repositories(engine))
    return repos
