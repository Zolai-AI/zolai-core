"""SQLAlchemy ORM models for Zolai data.

Maps the 8 canonical JSONL data files to relational tables.
Each model mirrors the corresponding Pydantic schema in schemas.py.

Usage:
    from zolai.data.models import Base, DictionaryEntry, BibleVerse
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///zolai.db")
    Base.metadata.create_all(engine)
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class DictionaryEntry(Base):
    """Zolai→English dictionary entries.

    Source: dict_zo_en_master_v1.jsonl
    """

    __tablename__ = "dictionary"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    zolai: str = Column(String, nullable=False, index=True)
    english: str = Column(Text, nullable=False)
    english_clean: str | None = Column(String, nullable=True)
    myanmar: str | None = Column(Text, nullable=True)
    source: str = Column(String, nullable=False, default="")
    pos: str = Column(String, nullable=False, default="")

    __table_args__ = (
        Index("ix_dict_zolai_source", "zolai", "source"),
    )

    def __repr__(self) -> str:
        return f"<DictionaryEntry(zolai={self.zolai!r})>"


class DictionaryEnZoEntry(Base):
    """English→Zolai dictionary entries.

    Source: dict_canonical_clean.jsonl
    """

    __tablename__ = "dictionary_en_zo"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    headword: str = Column(String, nullable=False, index=True)
    translations: str = Column(Text, nullable=False)  # JSON list
    translations_clean: str | None = Column(String, nullable=True)
    pos: str | None = Column(String, nullable=True)
    source: str = Column(String, nullable=False, default="")

    __table_args__ = (
        Index("idx_en_zo_headword", "headword"),
    )

    def __repr__(self) -> str:
        return f"<DictionaryEnZoEntry(headword={self.headword!r})>"


class BibleVerse(Base):
    """Parallel Bible verse data.

    Source: parallel_corpus_v1.jsonl
    """

    __tablename__ = "bible_verses"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    ref: str = Column(String, nullable=False, index=True)
    book: str = Column(String, nullable=False, index=True)
    chapter: int = Column(Integer, nullable=False)
    verse: int = Column(Integer, nullable=False)
    zo_tdb77: str | None = Column(Text, nullable=True)
    zo_tedim2010: str | None = Column(Text, nullable=True)
    en_kJV: str | None = Column(Text, nullable=True)
    myanmar: str | None = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_bible_book_chapter_verse", "book", "chapter", "verse"),
    )

    def __repr__(self) -> str:
        return f"<BibleVerse(ref={self.ref!r})>"


class GrammarPattern(Base):
    """Grammar patterns extracted from Bible.

    Source: grammar_patterns_v2.jsonl
    """

    __tablename__ = "grammar_patterns"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    pattern_id: str = Column(String, nullable=False, index=True)
    pattern: str = Column(String, nullable=False)
    description: str | None = Column(Text, nullable=True)
    function: str = Column(String, nullable=False, default="")
    examples: str = Column(Text, nullable=False, default="[]")
    frequency: int = Column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<GrammarPattern(pattern={self.pattern!r})>"


class PhraseEntry(Base):
    """Multi-word phrase entries.

    Source: phrases_v1.jsonl
    """

    __tablename__ = "phrases"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    zo: str = Column(String, nullable=False, index=True)
    english: str = Column(String, nullable=False, default="")
    myanmar: str | None = Column(Text, nullable=True)
    frequency: int = Column(Integer, nullable=False, default=0)
    examples: str = Column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<PhraseEntry(zo={self.zo!r})>"


class VocabularyEntry(Base):
    """Vocabulary index entries from Bible.

    Source: vocab_index_full.jsonl
    """

    __tablename__ = "vocabulary"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    headword: str = Column(String, nullable=False, index=True)
    english: str = Column(String, nullable=False, default="")
    myanmar: str | None = Column(Text, nullable=True)
    frequency: int = Column(Integer, nullable=False, default=0)
    books: str = Column(Text, nullable=False, default="[]")
    examples: str = Column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<VocabularyEntry(headword={self.headword!r})>"


class TranslationPair(Base):
    """Translation sentence pairs (ZO↔EN).

    Source: translation_pairs_v1.jsonl
    """

    __tablename__ = "translations"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    source: str = Column(Text, nullable=False)
    target: str = Column(Text, nullable=False)
    direction: str = Column(String, nullable=False, default="")
    reference: str = Column(String, nullable=False, default="")
    confidence: float = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_trans_direction_ref", "direction", "reference"),
    )

    def __repr__(self) -> str:
        return f"<TranslationPair(direction={self.direction!r})>"


class WordUsageProfile(Base):
    """Per-book word usage profiles.

    Source: word_usage_profiles.jsonl
    """

    __tablename__ = "word_usage"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    word: str = Column(String, nullable=False, index=True)
    book: str = Column(String, nullable=False, default="")
    total_freq: int = Column(Integer, nullable=False, default=0)
    meaning_shifts: str = Column(Text, nullable=False, default="[]")
    co_occurring_words: str = Column(Text, nullable=False, default="[]")

    __table_args__ = (
        Index("ix_usage_word_book", "word", "book"),
    )

    def __repr__(self) -> str:
        return f"<WordUsageProfile(word={self.word!r})>"


class ProvenanceFile(Base):
    """Data file provenance tracking.

    Source: provenance.json (individual file records)
    """

    __tablename__ = "provenance"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    filename: str = Column(String, nullable=False, index=True)
    size_bytes: int = Column(Integer, nullable=False, default=0)
    sha256: str = Column(String, nullable=False, default="")
    row_count: int = Column(Integer, nullable=False, default=0)
    source: str = Column(String, nullable=False, default="")
    generator_script: str = Column(String, nullable=False, default="")
    version: str = Column(String, nullable=False, default="1.0")
    status: str = Column(String, nullable=False, default="active")
    updated_at: str = Column(String, nullable=False, default="")
    change_log: str = Column(Text, nullable=False, default="[]")  # JSON array

    def __repr__(self) -> str:
        return f"<ProvenanceFile(filename={self.filename!r})>"


class DataAuditLog(Base):
    """Tracks every change to data tables.

    Records: table_name, row_id, field, old_value, new_value, changed_at, reason.
    """

    __tablename__ = "data_audit_log"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    table_name: str = Column(String, nullable=False, index=True)
    row_id: int = Column(Integer, nullable=False)
    field: str = Column(String, nullable=False)
    old_value: str | None = Column(Text, nullable=True)
    new_value: str | None = Column(Text, nullable=True)
    changed_at: str = Column(String, nullable=False)
    reason: str = Column(Text, nullable=False, default="")

    __table_args__ = (
        Index("idx_audit_table_row", "table_name", "row_id"),
    )


class TrainingExercise(Base):
    """Training exercises (negation, question, pronoun, error correction)."""

    __tablename__ = "training_exercises"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    exercise_type: str = Column(String, nullable=False, index=True)
    zolai: str = Column(Text, nullable=False)
    english: str = Column(Text, nullable=False)
    myanmar: str | None = Column(Text, nullable=True)
    source: str = Column(String, nullable=False, default="")
    difficulty: str = Column(String, nullable=False, default="medium")

    __table_args__ = (
        Index("idx_exercise_type", "exercise_type"),
    )


class BibleAnalysis(Base):
    """Per-book and per-chapter Bible context analysis."""

    __tablename__ = "bible_analysis"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    book: str = Column(String, nullable=False, index=True)
    chapter: int | None = Column(Integer, nullable=True)
    analysis_type: str = Column(String, nullable=False)
    data: str = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_bible_analysis_book", "book"),
    )


class WordAlignment(Base):
    """Word-level alignments between Zolai and English."""

    __tablename__ = "word_alignments"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    ref: str = Column(String, nullable=False, index=True)
    zolai_word: str = Column(String, nullable=False)
    english_word: str = Column(String, nullable=False)
    position: int = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_alignment_ref", "ref"),
    )


class WordCollocation(Base):
    """Word co-occurrence pairs."""

    __tablename__ = "word_collocations"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    word1: str = Column(String, nullable=False, index=True)
    word2: str = Column(String, nullable=False)
    frequency: int = Column(Integer, nullable=False, default=0)
    pmiproxy: float = Column(Float, nullable=False, default=0.0)


class Proverb(Base):
    """Proverbs and sayings."""

    __tablename__ = "proverbs"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    zolai: str = Column(Text, nullable=False)
    english: str | None = Column(Text, nullable=True)
    myanmar: str | None = Column(Text, nullable=True)
    source: str = Column(String, nullable=False, default="")
    category: str | None = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Model registry for migration/export
# ---------------------------------------------------------------------------
MODEL_REGISTRY: dict[str, type[Base]] = {
    "dictionary": DictionaryEntry,
    "dictionary_en_zo": DictionaryEnZoEntry,
    "bible_verses": BibleVerse,
    "grammar_patterns": GrammarPattern,
    "phrases": PhraseEntry,
    "vocabulary": VocabularyEntry,
    "translations": TranslationPair,
    "word_usage": WordUsageProfile,
    "provenance": ProvenanceFile,
    "data_audit_log": DataAuditLog,
    "training_exercises": TrainingExercise,
    "bible_analysis": BibleAnalysis,
    "word_alignments": WordAlignment,
    "word_collocations": WordCollocation,
    "proverbs": Proverb,
}
