"""Repository layer for Zolai data access.

Provides domain-specific repositories with common CRUD operations,
transactions, version tracking, and audit logging.
"""

from .alignment import AlignmentRepository
from .base import BaseRepository
from .bible import BibleContextRepository, BibleRepository
from .dictionary import DictionaryEnZoRepository, DictionaryRepository
from .exercise import ExerciseRepository
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
    "DictionaryEnZoRepository",
    "DictionaryRepository",
    "ExerciseRepository",
    "GrammarRepository",
    "PhraseRepository",
    "ProvenanceRepository",
    "TranslationRepository",
    "VocabularyRepository",
    "WordAlignmentRepository",
    "WordCollocationRepository",
    "WordUsageRepository",
]
