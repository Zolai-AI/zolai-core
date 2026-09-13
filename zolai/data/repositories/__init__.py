"""Repository layer for Zolai data access.

Provides domain-specific repositories with common CRUD operations,
transactions, version tracking, and audit logging.
"""

from .alignment import AlignmentRepository
from .base import BaseRepository
from .bible import BibleContextRepository, BibleRepository
from .dictionary import DictionaryEnZoRepository, DictionaryRepository
from .exercise import ExerciseRepository
from .extended import (
    CorrectionRepository,
    GrammarInstructionRepository,
    KnowledgeVectorRepository,
    NgramRepository,
    ParticleRepository,
    SimbuRepository,
    TrainingValidationRepository,
    VerbRepository,
)
from .grammar import GrammarRepository, WordCollocationRepository
from .phrase import PhraseRepository
from .provenance import AuditRepository, ProvenanceRepository
from .translation import TranslationRepository, WordAlignmentRepository
from .vocabulary import VocabularyRepository, WordUsageRepository

__all__ = [
    "AlignmentRepository",
    "AuditRepository",
    "BaseRepository",
    "BibleContextRepository",
    "BibleRepository",
    "CorrectionRepository",
    "DictionaryEnZoRepository",
    "DictionaryRepository",
    "ExerciseRepository",
    "get_engine",
    "get_repositories",
    "GrammarInstructionRepository",
    "GrammarRepository",
    "KnowledgeVectorRepository",
    "NgramRepository",
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
    "WordUsageRepository",
]


def get_engine(db_path=None):
    """Shared SQLAlchemy engine for the repository layer.

    Applies the v2 schema (missing tables + unified metadata columns) to the
    canonical DB unless ``db_path`` is given.
    """
    from ..schema_v2 import get_engine as _get_engine

    return _get_engine(db_path)


def get_repositories(db_path=None) -> dict[str, BaseRepository]:
    """Return an ``{entity_name: repository}`` map for both legacy + extended repos.

    Convenience factory used by serving modules and the API to instantiate every
    repository against a single engine.
    """
    engine = get_engine(db_path)
    return {
        "dictionary": DictionaryRepository(engine),
        "dictionary_en_zo": DictionaryEnZoRepository(engine),
        "vocabulary": VocabularyRepository(engine),
        "grammar": GrammarRepository(engine),
        "bible": BibleRepository(engine),
        "bible_context": BibleContextRepository(engine),
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
    }
