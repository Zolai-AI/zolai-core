"""Canonical data schemas for Zolai data files."""

from __future__ import annotations

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
    "DictionaryEntry",
    "GrammarPattern",
    "PhraseEntry",
    "ProvenanceEntry",
    "TranslationPair",
    "VocabEntry",
    "WordUsageProfile",
    "validate_file",
]
