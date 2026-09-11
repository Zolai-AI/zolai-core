"""Tests for zolai.data.schemas — canonical Pydantic data schemas."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zolai.data.schemas import (
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


# ---------------------------------------------------------------------------
# 1. DictionaryEntry
# ---------------------------------------------------------------------------
class TestDictionaryEntry:
    def test_valid(self) -> None:
        entry = DictionaryEntry(
            zolai="pasian",
            english=["God"],
            source="zomidictionary",
            english_clean="God",
        )
        assert entry.zolai == "pasian"
        assert entry.english == ["God"]

    def test_multiple_translations(self) -> None:
        entry = DictionaryEntry(
            zolai="dam",
            english=["well", "healthy", "sound"],
            source="bible",
            english_clean="well/healthy",
        )
        assert len(entry.english) == 3

    def test_empty_english_rejected(self) -> None:
        with pytest.raises(Exception):
            DictionaryEntry(
                zolai="test",
                english=[],
                source="test",
                english_clean="test",
            )

    def test_empty_zolai_rejected(self) -> None:
        with pytest.raises(Exception):
            DictionaryEntry(
                zolai="",
                english=["test"],
                source="test",
                english_clean="test",
            )


# ---------------------------------------------------------------------------
# 2. BibleVerse
# ---------------------------------------------------------------------------
class TestBibleVerse:
    def test_valid(self) -> None:
        verse = BibleVerse(
            book="GEN",
            book_name="Genesis",
            chapter="1",
            verse="1",
            ref="GEN 1:1",
            zo_tdb77="Pasian in vantung leh leitung a piangsak hi.",
            zo_tedim2010="Pasian in vantung leh leitung a piangsak hi.",
            en_kJV="In the beginning God created the heaven and the earth.",
        )
        assert verse.book == "GEN"
        assert verse.ref == "GEN 1:1"

    def test_ref_must_have_space(self) -> None:
        with pytest.raises(Exception):
            BibleVerse(
                book="GEN",
                book_name="Genesis",
                chapter="1",
                verse="1",
                ref="GEN1:1",
                zo_tdb77="test",
                zo_tedim2010="test",
                en_kJV="test",
            )


# ---------------------------------------------------------------------------
# 3. GrammarPattern
# ---------------------------------------------------------------------------
class TestGrammarPattern:
    def test_valid(self) -> None:
        pattern = GrammarPattern(
            id="pat_0001",
            pattern="agreement_a",
            description="3rd person singular",
            structure="a + Verb",
            function="subject agreement",
            frequency=25477,
            confidence=0.7,
            examples=["1CH 1:4", "1CH 1:5"],
            source="v1",
            book="",
        )
        assert pattern.id == "pat_0001"
        assert pattern.frequency == 25477

    def test_negative_frequency_rejected(self) -> None:
        with pytest.raises(Exception):
            GrammarPattern(
                id="pat_0002",
                pattern="test",
                description="test",
                structure="test",
                function="test",
                frequency=-1,
                confidence=0.5,
                examples=[],
                source="v1",
            )

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(Exception):
            GrammarPattern(
                id="pat_0003",
                pattern="test",
                description="test",
                structure="test",
                function="test",
                frequency=0,
                confidence=1.5,
                examples=[],
                source="v1",
            )


# ---------------------------------------------------------------------------
# 4. PhraseEntry
# ---------------------------------------------------------------------------
class TestPhraseEntry:
    def test_valid(self) -> None:
        entry = PhraseEntry(
            zo="uh hi",
            frequency=10131,
            examples=[
                {
                    "ref": "1CH 1:4",
                    "zo": "Lamek' ta Noah ahi hi.",
                    "en": "Noah, Shem, Ham, and Japheth.",
                }
            ],
            confidence="high",
        )
        assert entry.zo == "uh hi"
        assert len(entry.examples) == 1

    def test_with_pydantic_model(self) -> None:
        from zolai.data.schemas import PhraseExample

        ex = PhraseExample(
            ref="GEN 1:1", zo="Pasian om hi.", en="God exists."
        )
        assert ex.ref == "GEN 1:1"


# ---------------------------------------------------------------------------
# 5. VocabEntry
# ---------------------------------------------------------------------------
class TestVocabEntry:
    def test_valid(self) -> None:
        entry = VocabEntry(
            headword="pasian",
            english="God",
            frequency=68,
            books=["GEN", "EXO", "PSA"],
            book_count=3,
            examples=["GEN 1:1"],
            pos="noun",
            notes="",
            source="bible",
        )
        assert entry.headword == "pasian"
        assert entry.book_count == 3

    def test_defaults(self) -> None:
        entry = VocabEntry(
            headword="test",
            english="test",
            frequency=0,
            books=[],
            book_count=0,
            examples=[],
        )
        assert entry.pos == ""
        assert entry.notes == ""


# ---------------------------------------------------------------------------
# 6. TranslationPair
# ---------------------------------------------------------------------------
class TestTranslationPair:
    def test_valid(self) -> None:
        pair = TranslationPair(
            source="Adam' ta Seth, Seth' ta Enosh,",
            target="Adam, Seth, Enosh,",
            direction="zo_to_en",
            reference="1CH 1:1",
            confidence=0.95,
        )
        assert pair.direction == "zo_to_en"
        assert pair.confidence == 0.95

    def test_invalid_direction(self) -> None:
        # direction is just a string, no enum constraint — should accept any
        pair = TranslationPair(
            source="test",
            target="test",
            direction="en_to_zo",
            reference="GEN 1:1",
            confidence=0.5,
        )
        assert pair.direction == "en_to_zo"


# ---------------------------------------------------------------------------
# 7. WordUsageProfile
# ---------------------------------------------------------------------------
class TestWordUsageProfile:
    def test_valid(self) -> None:
        profile = WordUsageProfile(
            word="pasian",
            total_freq=68,
            books_found=17,
            per_book_distribution=[
                {
                    "book": "GEN",
                    "book_name": "Genesis",
                    "frequency": 225,
                    "top_translations": ["God"],
                    "co_occurring_words": [],
                }
            ],
            meaning_shifts=[],
            all_translations=["God"],
        )
        assert profile.word == "pasian"
        assert len(profile.per_book_distribution) == 1

    def test_defaults(self) -> None:
        profile = WordUsageProfile(
            word="test",
            total_freq=0,
            books_found=0,
            per_book_distribution=[],
        )
        assert profile.meaning_shifts == []
        assert profile.all_translations == []


# ---------------------------------------------------------------------------
# 8. ProvenanceEntry
# ---------------------------------------------------------------------------
class TestProvenanceEntry:
    def test_valid(self) -> None:
        prov = ProvenanceEntry(
            version="1.0",
            generated_at="2026-09-10T16:14:22Z",
            workspace_root="/home/peter/Documents/Projects/zolai-ai",
            total_files=227,
            total_size_bytes=2687449749,
            files=[
                {
                    "filename": "data/bible/parallel_corpus_v1.jsonl",
                    "size_bytes": 1600000,
                    "sha256": "abc123",
                    "row_count": 31102,
                    "last_modified": "2026-09-09T15:18:40Z",
                }
            ],
        )
        assert prov.total_files == 227
        assert len(prov.files) == 1


# ---------------------------------------------------------------------------
# 9. validate_file — temp JSONL files
# ---------------------------------------------------------------------------
class TestValidateFile:
    def _write_jsonl(self, tmp: Path, rows: list[dict]) -> Path:
        p = tmp / "test.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return p

    def test_valid_file(self, tmp_path: Path) -> None:
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "zolai": "pasian",
                    "english": ["God"],
                    "source": "test",
                    "english_clean": "God",
                },
            ],
        )
        result = validate_file(path, DictionaryEntry)
        assert result.is_valid
        assert result.total_rows == 1
        assert result.valid_rows == 1
        assert result.invalid_rows == 0
        assert result.pass_rate == 1.0

    def test_invalid_file(self, tmp_path: Path) -> None:
        path = self._write_jsonl(
            tmp_path,
            [
                {"zolai": "pasian", "english": ["God"], "source": "test", "english_clean": "God"},
                {"zolai": "", "english": [], "source": "test", "english_clean": ""},  # bad
            ],
        )
        result = validate_file(path, DictionaryEntry)
        assert not result.is_valid
        assert result.valid_rows == 1
        assert result.invalid_rows == 1

    def test_missing_file(self, tmp_path: Path) -> None:
        result = validate_file(tmp_path / "nope.jsonl", DictionaryEntry)
        assert not result.is_valid
        assert "File not found" in result.errors[0]

    def test_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n{broken\n")
        result = validate_file(path, DictionaryEntry)
        assert not result.is_valid
        assert result.invalid_rows == 2

    def test_summary_output(self, tmp_path: Path) -> None:
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "zolai": "pasian",
                    "english": ["God"],
                    "source": "test",
                    "english_clean": "God",
                },
            ],
        )
        result = validate_file(path, DictionaryEntry)
        summary = result.summary()
        assert "[PASS]" in summary
        assert "DictionaryEntry" in summary


# ---------------------------------------------------------------------------
# 10. Validate real data file (if present)
# ---------------------------------------------------------------------------
class TestValidateRealFile:
    def test_parallel_corpus(self) -> None:
        """Validate first 100 rows of the real parallel corpus."""
        real_path = (
            Path("/home/peter/Documents/Projects/zolai-ai")
            / "data/bible/parallel_corpus_v1.jsonl"
        )
        if not real_path.exists():
            pytest.skip("Real data file not present")

        # Validate a sample (first 100 rows) for speed
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as tmp:
            with open(real_path, encoding="utf-8") as src:
                for i, line in enumerate(src):
                    if i >= 100:
                        break
                    tmp.write(line)
            tmp_path = Path(tmp.name)

        result = validate_file(tmp_path, BibleVerse)
        tmp_path.unlink()

        assert result.total_rows == 100, result.summary()
        # Allow up to 5 failures for edge cases in real data
        assert result.valid_rows >= 95, result.summary()
        assert result.pass_rate >= 0.95, result.summary()

    def test_grammar_patterns(self) -> None:
        """Validate first 50 rows of real grammar patterns."""
        real_path = (
            Path("/home/peter/Documents/Projects/zolai-ai")
            / "data/bible/grammar_patterns_v2.jsonl"
        )
        if not real_path.exists():
            pytest.skip("Real data file not present")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as tmp:
            with open(real_path, encoding="utf-8") as src:
                for i, line in enumerate(src):
                    if i >= 50:
                        break
                    tmp.write(line)
            tmp_path = Path(tmp.name)

        result = validate_file(tmp_path, GrammarPattern)
        tmp_path.unlink()

        assert result.total_rows == 50, result.summary()
        assert result.valid_rows >= 45, result.summary()
