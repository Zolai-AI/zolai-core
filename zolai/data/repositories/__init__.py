"""Repository layer for Zolai data access.

Provides domain-specific repositories with common CRUD operations,
transactions, version tracking, and audit logging.
"""

from .alignment import AlignmentRepository
from .audit import AuditRepository
from .base import BaseRepository
from .bible import BibleRepository
from .dictionary import DictionaryRepository
from .exercise import ExerciseRepository
from .grammar import GrammarRepository
from .phrase import PhraseRepository
from .provenance import ProvenanceRepository
from .translation import TranslationRepository
from .vocabulary import VocabularyRepository

__all__ = [
    "BaseRepository",
    "DictionaryRepository",
    "BibleRepository",
    "TranslationRepository",
    "VocabularyRepository",
    "PhraseRepository",
    "GrammarRepository",
    "AlignmentRepository",
    "ExerciseRepository",
    "ProvenanceRepository",
    "AuditRepository",
]
