"""Canonical Pydantic schemas for all Zolai data files.

Defines the expected structure of each JSONL data file in the Zolai ecosystem.
Includes validation functions for individual files and bulk validation.

Usage:
    from zolai.data.schemas import DictionaryEntry, validate_file
    result = validate_file("data/dict_zo_en_master_v1.jsonl", DictionaryEntry)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# 1. Dictionary — dict_zo_en_master_v1.jsonl
# ---------------------------------------------------------------------------
class DictionaryEntry(BaseModel):
    """Schema for Zolai→English dictionary entries (dict_zo_en_master_v1.jsonl)."""

    zolai: str = Field(description="Zolai headword")
    english: list[str] = Field(description="English translations")
    source: str = Field(description="Source dictionary identifier")
    english_clean: str = Field(description="Short clean English translation")

    @field_validator("english")
    @classmethod
    def english_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("english translations list must not be empty")
        return v

    @field_validator("zolai")
    @classmethod
    def zolai_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("zolai headword must not be empty")
        return v


# ---------------------------------------------------------------------------
# 2. Bible Verse — parallel_corpus_v1.jsonl
# ---------------------------------------------------------------------------
class BibleVerse(BaseModel):
    """Schema for parallel Bible verse data (parallel_corpus_v1.jsonl)."""

    book: str = Field(description="Book code (e.g. GEN, PSA)")
    book_name: str = Field(description="Full book name")
    chapter: str = Field(description="Chapter number as string")
    verse: str = Field(description="Verse number as string")
    ref: str = Field(description="Canonical reference (e.g. GEN 1:1)")
    zo_tdb77: str = Field(description="Zolai TDB77 version text")
    zo_tedim2010: str = Field(description="Zolai Tedim 2010 version text")
    en_kJV: str = Field(description="English KJV text")

    @field_validator("ref")
    @classmethod
    def ref_format(cls, v: str) -> str:
        if " " not in v:
            raise ValueError(
                f"ref should contain space (e.g. GEN 1:1), got: {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# 3. Grammar Pattern — grammar_patterns_v2.jsonl
# ---------------------------------------------------------------------------
class GrammarPattern(BaseModel):
    """Schema for grammar patterns (grammar_patterns_v2.jsonl)."""

    id: str = Field(description="Unique pattern identifier (e.g. pat_0001)")
    pattern: str = Field(description="Pattern category name")
    description: str = Field(description="Human-readable description")
    structure: str = Field(description="Syntactic structure formula")
    function: str = Field(description="Grammatical function")
    frequency: int = Field(description="Occurrence count in corpus", ge=0)
    confidence: float = Field(
        description="Confidence score 0.0–1.0", ge=0.0, le=1.0
    )
    examples: list[str] = Field(description="Example verse references")
    source: str = Field(description="Data source version")
    book: str = Field(default="", description="Applicable book (empty = all)")


# ---------------------------------------------------------------------------
# 4. Phrase Entry — phrases_v1.jsonl
# ---------------------------------------------------------------------------
class PhraseExample(BaseModel):
    """A single example occurrence of a phrase."""

    ref: str = Field(description="Bible verse reference")
    zo: str = Field(description="Zolai context sentence")
    en: str = Field(description="English translation")


class PhraseEntry(BaseModel):
    """Schema for multi-word phrase entries (phrases_v1.jsonl)."""

    zo: str = Field(description="Zolai phrase text")
    frequency: int = Field(description="Occurrence frequency", ge=0)
    examples: list[PhraseExample] = Field(
        description="Example occurrences from Bible"
    )
    confidence: str = Field(
        description="Confidence level (high/medium/low)"
    )


# ---------------------------------------------------------------------------
# 5. Vocab Entry — vocab_index_full.jsonl
# ---------------------------------------------------------------------------
class VocabEntry(BaseModel):
    """Schema for vocabulary index entries (vocab_index_full.jsonl)."""

    headword: str = Field(description="Zolai word form")
    english: str = Field(description="Primary English translation")
    frequency: int = Field(description="Total frequency in Bible corpus", ge=0)
    books: list[str] = Field(description="Book codes where word appears")
    book_count: int = Field(description="Number of books", ge=0)
    examples: list[str] = Field(description="Example verse references")
    pos: str = Field(default="", description="Part of speech")
    notes: str = Field(default="", description="Usage notes")
    source: str = Field(default="", description="Data source")


# ---------------------------------------------------------------------------
# 6. Translation Pair — translation_pairs_v1.jsonl
# ---------------------------------------------------------------------------
class TranslationPair(BaseModel):
    """Schema for translation sentence pairs (translation_pairs_v1.jsonl)."""

    source: str = Field(description="Source language sentence")
    target: str = Field(description="Target language sentence")
    direction: str = Field(description="Translation direction (e.g. zo_to_en)")
    reference: str = Field(description="Bible verse reference")
    confidence: float = Field(
        description="Alignment confidence 0.0–1.0", ge=0.0, le=1.0
    )


# ---------------------------------------------------------------------------
# 7. Word Usage Profile — word_usage_profiles.jsonl
# ---------------------------------------------------------------------------
class BookDistribution(BaseModel):
    """Per-book distribution for a word."""

    book: str = Field(description="Book code")
    book_name: str = Field(description="Full book name")
    frequency: int = Field(description="Frequency in this book", ge=0)
    top_translations: list[str] = Field(
        default_factory=list, description="Top English translations"
    )
    co_occurring_words: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Words that co-occur with count",
    )


class WordUsageProfile(BaseModel):
    """Schema for word usage profiles (word_usage_profiles.jsonl)."""

    word: str = Field(description="Zolai word form")
    total_freq: int = Field(description="Total frequency across Bible", ge=0)
    books_found: int = Field(description="Number of books found in", ge=0)
    per_book_distribution: list[BookDistribution] = Field(
        description="Per-book frequency distribution"
    )
    meaning_shifts: list[str] = Field(
        default_factory=list,
        description="Detected meaning shifts across books",
    )
    all_translations: list[str] = Field(
        default_factory=list,
        description="All known English translations",
    )


# ---------------------------------------------------------------------------
# 8. Provenance Entry — provenance.json (top-level structure)
# ---------------------------------------------------------------------------
class ProvenanceFile(BaseModel):
    """Single file record inside provenance.json."""

    filename: str = Field(description="Relative path from workspace root")
    size_bytes: int = Field(description="File size in bytes", ge=0)
    sha256: str = Field(description="SHA-256 hash of file contents")
    row_count: int = Field(description="Number of data rows", ge=0)
    last_modified: str = Field(description="ISO-8601 last modified timestamp")
    source: str = Field(default="", description="Processing source")
    generator_script: str = Field(default="", description="Script that generated the file")


class ProvenanceEntry(BaseModel):
    """Schema for the top-level provenance.json manifest."""

    version: str = Field(description="Provenance schema version")
    generated_at: str = Field(description="ISO-8601 generation timestamp")
    workspace_root: str = Field(description="Absolute workspace root path")
    total_files: int = Field(description="Total number of tracked files", ge=0)
    total_size_bytes: int = Field(description="Total size in bytes", ge=0)
    files: list[ProvenanceFile] = Field(description="File records")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    """Result of validating a JSONL file against a schema."""

    file_path: str
    schema_name: str
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.invalid_rows == 0 and not self.errors

    @property
    def pass_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return self.valid_rows / self.total_rows

    def summary(self) -> str:
        status = "PASS" if self.is_valid else "FAIL"
        rate = f"{self.pass_rate:.1%}"
        lines = [
            f"[{status}] {self.schema_name} — {self.file_path}",
            f"  rows: {self.total_rows} | valid: {self.valid_rows} | "
            f"invalid: {self.invalid_rows} | rate: {rate}",
        ]
        for err in self.errors[:5]:
            lines.append(f"  ERROR: {err}")
        if len(self.errors) > 5:
            lines.append(f"  ... and {len(self.errors) - 5} more errors")
        return "\n".join(lines)


def validate_file(
    path: str | Path,
    schema_class: Type[BaseModel],
    max_errors: int = 10,
) -> ValidationResult:
    """Validate a JSONL file line-by-line against a Pydantic model.

    Args:
        path: Path to a JSONL file.
        schema_class: Pydantic model class to validate each line against.
        max_errors: Stop collecting errors after this many.

    Returns:
        ValidationResult with counts and error details.
    """
    path = Path(path)
    schema_name = schema_class.__name__
    result = ValidationResult(file_path=str(path), schema_name=schema_name)

    if not path.exists():
        result.errors.append(f"File not found: {path}")
        return result

    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            result.total_rows += 1
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                result.invalid_rows += 1
                if len(result.errors) < max_errors:
                    result.errors.append(
                        f"Line {line_no}: JSON decode error — {exc}"
                    )
                continue

            try:
                schema_class.model_validate(data)
                result.valid_rows += 1
            except Exception as exc:
                result.invalid_rows += 1
                if len(result.errors) < max_errors:
                    result.errors.append(
                        f"Line {line_no}: {type(exc).__name__} — {exc}"
                    )

    return result


# Canonical file → schema mapping (for bulk validation)
CANONICAL_SCHEMAS: dict[str, Type[BaseModel]] = {
    "dict_zo_en_master_v1.jsonl": DictionaryEntry,
    "parallel_corpus_v1.jsonl": BibleVerse,
    "grammar_patterns_v2.jsonl": GrammarPattern,
    "phrases_v1.jsonl": PhraseEntry,
    "vocab_index_full.jsonl": VocabEntry,
    "translation_pairs_v1.jsonl": TranslationPair,
    "word_usage_profiles.jsonl": WordUsageProfile,
    "provenance.json": ProvenanceEntry,
}
