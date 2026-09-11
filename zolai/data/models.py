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
    source: str = Column(String, nullable=False, default="")
    pos: str = Column(String, nullable=False, default="")

    __table_args__ = (
        Index("ix_dict_zolai_source", "zolai", "source"),
    )

    def __repr__(self) -> str:
        return f"<DictionaryEntry(zolai={self.zolai!r})>"


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
    frequency: int = Column(Integer, nullable=False, default=0)
    examples: str = Column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<PhraseEntry(zo={self.zo!r})>"


class VocabEntry(Base):
    """Vocabulary index entries from Bible.

    Source: vocab_index_full.jsonl
    """

    __tablename__ = "vocab"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    headword: str = Column(String, nullable=False, index=True)
    english: str = Column(String, nullable=False, default="")
    frequency: int = Column(Integer, nullable=False, default=0)
    books: str = Column(Text, nullable=False, default="[]")
    examples: str = Column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<VocabEntry(headword={self.headword!r})>"


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

    def __repr__(self) -> str:
        return f"<ProvenanceFile(filename={self.filename!r})>"


# ---------------------------------------------------------------------------
# Model registry for migration/export
# ---------------------------------------------------------------------------
MODEL_REGISTRY: dict[str, type[Base]] = {
    "dictionary": DictionaryEntry,
    "bible_verses": BibleVerse,
    "grammar_patterns": GrammarPattern,
    "phrases": PhraseEntry,
    "vocab": VocabEntry,
    "translations": TranslationPair,
    "word_usage": WordUsageProfile,
    "provenance": ProvenanceFile,
}
