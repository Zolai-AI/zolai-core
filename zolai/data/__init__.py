"""Canonical data schemas and database access for Zolai data files."""

from __future__ import annotations

from .database import DatabaseManager, get_manager, init_db
from .schemas import (
    BibleVerse,
    DictionaryEntry,
    GrammarPattern,
    PhraseEntry,
    ProvenanceEntry,
    TranslationPair,
    VocabEntry,
    WordUsageProfile,
    validate_file,
)

__all__ = [
    "BibleVerse",
    "DatabaseManager",
    "DictionaryEntry",
    "GrammarPattern",
    "PhraseEntry",
    "ProvenanceEntry",
    "TranslationPair",
    "VocabEntry",
    "WordUsageProfile",
    "get_manager",
    "init_db",
    "validate_file",
]
