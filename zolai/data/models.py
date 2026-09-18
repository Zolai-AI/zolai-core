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

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
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
    Column name aligns with live ``data/zolai.db`` (``zolai``, not ``zo``).
    """

    __tablename__ = "phrases"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    zolai: str = Column(String, nullable=False, index=True)
    english: str = Column(String, nullable=False, default="")
    myanmar: str | None = Column(Text, nullable=True)
    frequency: int = Column(Integer, nullable=False, default=0)
    examples: str = Column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:
        return f"<PhraseEntry(zolai={self.zolai!r})>"


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


# =============================================================================
# FOUNDATION LAYER MODELS (Phase B)
# =============================================================================

# --- Raw Layer (append-only, immutable) ---

class FoundationRawCorpus(Base):
    """Raw corpus imports from web, PDF, Bible USX, etc.

    Immutable append-only log of raw source data.
    """

    __tablename__ = "foundation_raw_corpus"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    source_type: str = Column(String, nullable=False, index=True)  # 'bible_usx' | 'web_crawl' | 'pdf' | 'dict_jsonl'
    source_path: str = Column(String, nullable=False)
    content_hash: str = Column(String, nullable=False, index=True)  # SHA256 of content
    payload: str = Column(Text, nullable=False)  # JSON raw content
    imported_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fraw_source_hash", "content_hash"),
        Index("ix_fraw_source_type", "source_type"),
    )

    def __repr__(self) -> str:
        return f"<FoundationRawCorpus(source_type={self.source_type!r}, hash={self.content_hash[:8]}...)>"


class FoundationRawLLM(Base):
    """Raw LLM outputs from batch generation jobs.

    Immutable log of LLM candidate generations.
    """

    __tablename__ = "foundation_raw_llm"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    model: str = Column(String, nullable=False, index=True)
    prompt_hash: str = Column(String, nullable=False, index=True)  # SHA256 of prompt
    response_json: str = Column(Text, nullable=False)
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_frawllm_model_prompt", "model", "prompt_hash"),
    )

    def __repr__(self) -> str:
        return f"<FoundationRawLLM(model={self.model!r}, prompt={self.prompt_hash[:8]}...)>"


# --- Staging Layer (rebuildable, transient) ---

class FoundationStagingWord(Base):
    """Cleaned word candidates with analysis from FoundationAnalyzer.

    Source: FoundationAnalyzer.analyze_word() output.
    """

    __tablename__ = "foundation_staging_words"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    form: str = Column(String, nullable=False, index=True)  # Zolai word form (ZVS 2018)
    syllables: str = Column(Text, nullable=False)  # JSON: ["pa", "sian"]
    pos: str = Column(String, nullable=False, default="X")  # NOUN, VERB, ADJ, etc.
    morphology: str = Column(Text, nullable=False, default="{}")  # JSON: {prefix, root, suffix, compound_parts}
    meanings: str = Column(Text, nullable=False, default="[]")  # JSON: [{"en": "God", "source": "bible"}, ...]
    tone_profile: str = Column(Text, nullable=True)  # JSON: {"ambiguous": true, "notes": "T1=lie, T3=thin"}
    zvs_compliant: bool = Column(Integer, nullable=False, default=1)  # SQLite bool
    frequency: int = Column(Integer, nullable=False, default=0)
    source_hash: str = Column(String, nullable=False)  # SHA256 of promoting evidence
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fstg_word_form", "form"),
        Index("ix_fstg_word_pos", "pos"),
        Index("ix_fstg_word_source_hash", "source_hash"),
    )

    def __repr__(self) -> str:
        return f"<FoundationStagingWord(form={self.form!r}, pos={self.pos!r})>"


class FoundationStagingSentence(Base):
    """Cleaned sentence analyses with structure from FoundationAnalyzer.

    Source: FoundationAnalyzer.analyze_sentence() output.
    """

    __tablename__ = "foundation_staging_sentences"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    text: str = Column(Text, nullable=False, index=True)  # Full Zolai sentence
    tokens: str = Column(Text, nullable=False)  # JSON: [{"form": "Pasian", "pos": "N.PROPER", ...}]
    pos_tags: str = Column(Text, nullable=False)  # JSON: ["N.PROPER", "PART.ERG", ...]
    structure: str = Column(Text, nullable=False, default="{}")  # JSON: {dependencies, grammar_features}
    translation: str = Column(Text, nullable=True)  # JSON: {"en": "...", "my": "..."}
    grammar: str = Column(Text, nullable=False, default="{}")  # JSON: {tense, negation, question_type, sov_valid}
    source_hash: str = Column(String, nullable=False)
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fstg_sent_text", "text"),
        Index("ix_fstg_sent_source_hash", "source_hash"),
    )

    def __repr__(self) -> str:
        return f"<FoundationStagingSentence(text={self.text[:40]!r}...)>"


class FoundationStagingParagraph(Base):
    """Cleaned paragraph analyses with style profile from FoundationAnalyzer.

    Source: FoundationAnalyzer.analyze_paragraph() output.
    """

    __tablename__ = "foundation_staging_paragraphs"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    text: str = Column(Text, nullable=False)
    sentences: str = Column(Text, nullable=False)  # JSON: [{"sentence": "...", "tense": "past", ...}]
    style_profile: str = Column(Text, nullable=False, default="{}")  # JSON: {"narrative": 0.7, "dialogue": 0.2, ...}
    source_hash: str = Column(String, nullable=False)
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fstg_para_source_hash", "source_hash"),
    )

    def __repr__(self) -> str:
        return f"<FoundationStagingParagraph(text={self.text[:40]!r}...)>"


class FoundationStagingEvidence(Base):
    """Evidence bundles per candidate fact in staging.

    Auto-created when staging records are written.
    """

    __tablename__ = "foundation_staging_evidence"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    fact_type: str = Column(String, nullable=False, index=True)  # 'word' | 'sentence' | 'paragraph' | 'grammar'
    fact_key: str = Column(String, nullable=False, index=True)  # e.g. 'word:pasian', 'sentence:GEN 1:1'
    candidate_value: str = Column(Text, nullable=False)  # JSON: the proposed canonical value
    evidence: str = Column(Text, nullable=False)  # JSON: list of evidence records
    tier: int = Column(Integer, nullable=False)  # 1=T1(Bible), 2=T2(Dict), 3=T3(Corpus), 4=T4(Grammar), 5=T5(LLM)
    confidence: float = Column(Float, nullable=False, default=0.0)
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fstg_ev_fact", "fact_type", "fact_key"),
        Index("ix_fstg_ev_tier", "tier"),
    )

    def __repr__(self) -> str:
        return f"<FoundationStagingEvidence(fact_type={self.fact_type!r}, fact_key={self.fact_key!r})>"


# --- Canonical Layer (serving reads, versioned, evidence-gated) ---

class CanonicalWord(Base):
    """Verified word entries for serving layer.

    Promoted from staging via consensus. Versioned, audited, evidence-gated.
    """

    __tablename__ = "canonical_words"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    form: str = Column(String, nullable=False, index=True)  # Zolai word form (ZVS 2018)
    syllables: str = Column(Text, nullable=False)  # JSON: ["pa", "sian"]
    syllable_count: int = Column(Integer, nullable=False, default=0)
    pos: str = Column(String, nullable=False, default="X")
    morphology: str = Column(Text, nullable=True)  # JSON: {prefix, root, suffix, compound_parts}
    meanings: str = Column(Text, nullable=False)  # JSON: [{"en": "God", "source": "bible"}, ...]
    tone_profile: str = Column(Text, nullable=True)  # JSON: {"ambiguous": true, "notes": "..."}
    zvs_compliant: bool = Column(Integer, nullable=False, default=1)
    frequency: int = Column(Integer, nullable=False, default=0)
    version: int = Column(Integer, nullable=False, default=1)
    source_hash: str = Column(String, nullable=False)
    verified_at: str | None = Column(String, nullable=True)
    verified_by: str | None = Column(String, nullable=True)  # 'consensus:v1' | 'human:reviewer'
    evidence_ids: str = Column(Text, nullable=False, default="[]")  # JSON array of foundation_evidence.rowids
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_cword_form", "form"),
        Index("ix_cword_pos", "pos"),
        Index("ix_cword_version", "version"),
        Index("ix_cword_verified", "verified_at"),
    )

    def __repr__(self) -> str:
        return f"<CanonicalWord(form={self.form!r}, version={self.version})>"


class CanonicalSentence(Base):
    """Verified sentence entries for serving layer.

    Promoted from staging via consensus. Versioned, audited, evidence-gated.
    """

    __tablename__ = "canonical_sentences"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    text: str = Column(Text, nullable=False, index=True)  # Full Zolai sentence
    tokens: str = Column(Text, nullable=False)  # JSON: [{"form": "Pasian", "pos": "N.PROPER", ...}]
    pos_tags: str = Column(Text, nullable=False)  # JSON: ["N.PROPER", "PART.ERG", ...]
    dependencies: str = Column(Text, nullable=True)  # JSON: [{"head": 2, "dep": "nsubj", ...}]
    translation: str = Column(Text, nullable=True)  # JSON: {"en": "...", "my": "..."}
    grammar: str = Column(Text, nullable=False, default="{}")  # JSON: {tense, negation, question_type, sov_valid}
    version: int = Column(Integer, nullable=False, default=1)
    source_hash: str = Column(String, nullable=False)
    verified_at: str | None = Column(String, nullable=True)
    verified_by: str | None = Column(String, nullable=True)
    evidence_ids: str = Column(Text, nullable=False, default="[]")
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_csent_text", "text"),
        Index("ix_csent_version", "version"),
        Index("ix_csent_verified", "verified_at"),
    )

    def __repr__(self) -> str:
        return f"<CanonicalSentence(text={self.text[:40]!r}..., version={self.version})>"


class CanonicalParagraph(Base):
    """Verified paragraph entries for serving layer.

    Promoted from staging via consensus. Versioned, audited, evidence-gated.
    """

    __tablename__ = "canonical_paragraphs"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    text: str = Column(Text, nullable=False)
    sentences: str = Column(Text, nullable=False)  # JSON: [{"sentence": "...", "tense": "past", ...}]
    style_profile: str = Column(Text, nullable=False, default="{}")  # JSON: {"narrative": 0.7, "dialogue": 0.2, ...}
    paraphrases: str = Column(Text, nullable=True)  # JSON: [{"style": "formal", "text": "..."}, ...]
    version: int = Column(Integer, nullable=False, default=1)
    source_hash: str = Column(String, nullable=False)
    verified_at: str | None = Column(String, nullable=True)
    verified_by: str | None = Column(String, nullable=True)
    evidence_ids: str = Column(Text, nullable=False, default="[]")
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_cpara_version", "version"),
        Index("ix_cpara_verified", "verified_at"),
    )

    def __repr__(self) -> str:
        return f"<CanonicalParagraph(text={self.text[:40]!r}..., version={self.version})>"


# --- Evidence / Consensus / Verification ---

class FoundationEvidence(Base):
    """Evidence records for canonical promotion.

    Tiered, provenance-tracked, confidence-scored.
    """

    __tablename__ = "foundation_evidence"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    fact_type: str = Column(String, nullable=False, index=True)  # 'word' | 'sentence' | 'paragraph' | 'grammar'
    fact_key: str = Column(String, nullable=False, index=True)  # e.g. 'word:pasian' | 'sentence:GEN 1:1'
    tier: int = Column(Integer, nullable=False)  # 1=T1(Bible), 2=T2(Dict), 3=T3(Corpus), 4=T4(Grammar), 5=T5(LLM)
    # 'bible_verses' | 'dictionary' | 'corpus' | 'grammar_patterns' | 'llm'
    source: str = Column(String, nullable=False)
    confidence: float = Column(Float, nullable=False, default=0.0)
    provenance_hash: str = Column(String, nullable=False, default="")  # SHA256 of source record(s)
    payload: str = Column(Text, nullable=False, default="{}")  # JSON: source-specific evidence detail
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fev_fact", "fact_type", "fact_key"),
        Index("ix_fev_tier", "tier"),
        Index("ix_fev_source", "source"),
        Index("ix_fev_provenance", "provenance_hash"),
    )

    def __repr__(self) -> str:
        return f"<FoundationEvidence(fact_type={self.fact_type!r}, fact_key={self.fact_key!r}, tier={self.tier})>"


class FoundationVerification(Base):
    """Verification results for candidate facts.

    Records: candidate_id, verifier, passed, score, notes.
    """

    __tablename__ = "foundation_verifications"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id: int = Column(Integer, nullable=False, index=True)
    verifier: str = Column(String, nullable=False)  # 'EvidenceThresholdVerifier' | 'NullVerifier' | 'LLMVerifier'
    passed: bool = Column(Integer, nullable=False)  # SQLite bool
    score: float = Column(Float, nullable=False, default=0.0)
    notes: str = Column(Text, nullable=True, default="")
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fver_candidate", "candidate_id"),
        Index("ix_fver_verifier", "verifier"),
    )

    def __repr__(self) -> str:
        return (
            f"<FoundationVerification(candidate_id={self.candidate_id}, "
            f"verifier={self.verifier!r}, passed={self.passed})>"
        )


class FoundationConsensus(Base):
    """Consensus decisions for canonical promotion.

    Records: fact_key, candidates[], decision, confidence, method, threshold.
    """

    __tablename__ = "foundation_consensus"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    fact_type: str = Column(String, nullable=False, index=True)
    fact_key: str = Column(String, nullable=False, index=True)
    candidates: str = Column(Text, nullable=False)  # JSON: [{"evidence_id": 123, "value": {...}, "weight": 0.9}, ...]
    decision: str = Column(Text, nullable=False)  # JSON: the promoted canonical value
    confidence: float = Column(Float, nullable=False, default=0.0)
    method: str = Column(String, nullable=False)  # 'majority_vote' | 'weighted_evidence' | 'threshold'
    threshold: float = Column(Float, nullable=False, default=0.7)
    agreeing_count: int = Column(Integer, nullable=False, default=0)
    notes: str = Column(Text, nullable=True, default="")  # JSON: array of notes
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fcon_fact", "fact_type", "fact_key"),
        Index("ix_fcon_method", "method"),
    )

    def __repr__(self) -> str:
        return (
            f"<FoundationConsensus(fact_type={self.fact_type!r}, "
            f"fact_key={self.fact_key!r}, method={self.method!r})>"
        )


# --- Meta / Operational ---

class FoundationBatch(Base):
    """Batch job runs for ETL pipeline tracking.

    Records: id, batch_type, status, stats, started_at, completed_at.
    """

    __tablename__ = "foundation_batches"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    batch_type: str = Column(String, nullable=False, index=True)  # 'ingest' | 'build_staging' | 'promote' | 'verify'
    status: str = Column(String, nullable=False, default="pending")  # 'pending' | 'running' | 'completed' | 'failed'
    stats: str = Column(Text, nullable=True)  # JSON: {"records_processed": 100, "promoted": 50, ...}
    started_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_fbatch_type_status", "batch_type", "status"),
        Index("ix_fbatch_started", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<FoundationBatch(type={self.batch_type!r}, status={self.status!r})>"


class FoundationReviewQueue(Base):
    """Human review queue for low-confidence promotions.

    Records: fact_type, fact_key, priority, assignee, status, created_at, resolved_at.
    """

    __tablename__ = "foundation_review_queue"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    fact_type: str = Column(String, nullable=False, index=True)
    fact_key: str = Column(String, nullable=False, index=True)
    priority: int = Column(Integer, nullable=False, default=0)  # Higher = more urgent
    assignee: str | None = Column(String, nullable=True)
    status: str = Column(String, nullable=False, default="pending")  # 'pending' | 'in_review' | 'approved' | 'rejected'
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str | None = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_frev_fact", "fact_type", "fact_key"),
        Index("ix_frev_status_priority", "status", "priority"),
    )

    def __repr__(self) -> str:
        return (
            f"<FoundationReviewQueue(fact_type={self.fact_type!r}, "
            f"fact_key={self.fact_key!r}, status={self.status!r})>"
        )


class FoundationMetric(Base):
    """Evaluation metrics for pipeline runs.

    Records: run_id, metric, value, baseline, delta, created_at.
    """

    __tablename__ = "foundation_metrics"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    run_id: str = Column(String, nullable=False, index=True)
    metric: str = Column(String, nullable=False, index=True)
    value: float = Column(Float, nullable=False)
    baseline: float | None = Column(Float, nullable=True)
    delta: float | None = Column(Float, nullable=True)
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fmetric_run", "run_id"),
        Index("ix_fmetric_metric", "metric"),
    )

    def __repr__(self) -> str:
        return f"<FoundationMetric(run_id={self.run_id!r}, metric={self.metric!r}, value={self.value})>"


class FoundationCostTracking(Base):
    """Cost tracking for LLM API calls.

    Records: request_id, task_type, model, input_tokens, output_tokens, cost_usd, extra_info.
    """

    __tablename__ = "foundation_cost_tracking"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    request_id: str = Column(String, nullable=False, index=True)  # UUID
    task_type: str = Column(String, nullable=False, index=True)  # 'word'|'sentence'|'paragraph'|'grammar'|'batch'
    model: str = Column(String, nullable=False)  # 'gemini-2.0-flash'|'gemini-2.0-pro'|'gemini-2.0-pro-plus'|'approx'
    input_tokens: int = Column(Integer, nullable=False, default=0)
    output_tokens: int = Column(Integer, nullable=False, default=0)
    cost_usd: float = Column(Float, nullable=False, default=0.0)
    extra_info: str | None = Column(Text, nullable=True)  # JSON: extra info
    created_at: str = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        Index("ix_fct_task_type", "task_type"),
        Index("ix_fct_created", "created_at"),
        Index("ix_fct_model", "model"),
    )

    def __repr__(self) -> str:
        return (
            f"<FoundationCostTracking(request_id={self.request_id!r}, "
            f"model={self.model!r}, cost_usd={self.cost_usd})>"
        )


# ---------------------------------------------------------------------------
# Model registry for migration/export
# ---------------------------------------------------------------------------
MODEL_REGISTRY: dict[str, type[Base]] = {
    # Existing canonical tables
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
    # Foundation Raw Layer
    "foundation_raw_corpus": FoundationRawCorpus,
    "foundation_raw_llm": FoundationRawLLM,
    # Foundation Staging Layer
    "foundation_staging_words": FoundationStagingWord,
    "foundation_staging_sentences": FoundationStagingSentence,
    "foundation_staging_paragraphs": FoundationStagingParagraph,
    "foundation_staging_evidence": FoundationStagingEvidence,
    # Foundation Canonical Layer
    "canonical_words": CanonicalWord,
    "canonical_sentences": CanonicalSentence,
    "canonical_paragraphs": CanonicalParagraph,
    # Foundation Evidence/Consensus/Verification
    "foundation_evidence": FoundationEvidence,
    "foundation_verifications": FoundationVerification,
    "foundation_consensus": FoundationConsensus,
    # Foundation Meta
    "foundation_batches": FoundationBatch,
    "foundation_review_queue": FoundationReviewQueue,
    "foundation_metrics": FoundationMetric,
    "foundation_cost_tracking": FoundationCostTracking,
}
