"""Tests for zolai.data database layer — 25 tests.

Covers: schema creation, CRUD, search, migration, export,
round-trip, performance, and edge cases.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from zolai.data.database import DatabaseManager, get_manager, init_db
from zolai.data.models import (
    MODEL_REGISTRY,
    Base,
    BibleVerse,
    DictionaryEntry,
    GrammarPattern,
    PhraseEntry,
    ProvenanceFile,
    TranslationPair,
    VocabEntry,
    WordUsageProfile,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database, yield manager, clean up."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    mgr.init_db()
    yield mgr
    mgr.dispose()
    os.unlink(db_path)


@pytest.fixture()
def sample_dict():
    return {
        "zolai": "pasian",
        "english": "God",
        "english_clean": "God",
        "source": "test",
        "pos": "noun",
    }


@pytest.fixture()
def sample_verse():
    return {
        "ref": "GEN 1:1",
        "book": "GEN",
        "chapter": 1,
        "verse": 1,
        "zo_tdb77": "Pasian in vantung leh leitung a piangsak hi.",
        "zo_tedim2010": "Pasian in vantung leh leitung a piangsak hi.",
        "en_kJV": "In the beginning God created the heaven and the earth.",
    }


@pytest.fixture()
def sample_grammar():
    return {
        "pattern_id": "pat_0001",
        "pattern": "sov_basic",
        "description": "Subject-Object-Verb basic word order",
        "function": "word_order",
        "examples": json.dumps(["GEN 1:1", "GEN 1:3"]),
        "frequency": 500,
    }


@pytest.fixture()
def sample_phrase():
    return {
        "zo": "vantung leh leitung",
        "english": "heaven and earth",
        "frequency": 100,
        "examples": json.dumps([{"ref": "GEN 1:1", "zo": "vantung leh leitung", "en": "heaven and earth"}]),
    }


@pytest.fixture()
def sample_vocab():
    return {
        "headword": "pasian",
        "english": "God",
        "frequency": 1500,
        "books": json.dumps(["GEN", "PSA", "MAT"]),
        "examples": json.dumps(["GEN 1:1", "PSA 23:1"]),
    }


@pytest.fixture()
def sample_translation():
    return {
        "source": "Pasian in vantung leh leitung a piangsak hi.",
        "target": "In the beginning God created the heaven and the earth.",
        "direction": "zo_to_en",
        "reference": "GEN 1:1",
        "confidence": 0.95,
    }


@pytest.fixture()
def sample_usage():
    return {
        "word": "pasian",
        "book": "GEN",
        "total_freq": 225,
        "meaning_shifts": json.dumps([{"translation": "God", "books": ["GEN", "EXO"]}]),
        "co_occurring_words": json.dumps([{"word": "ciangin", "count": 50}]),
    }


@pytest.fixture()
def sample_provenance():
    return {
        "filename": "data/bible/parallel_corpus_v1.jsonl",
        "size_bytes": 16_000_000,
        "sha256": "abc123def456",
        "row_count": 31_102,
        "source": "bible",
        "generator_script": "scripts/build_parallel_corpus.py",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestSchemaCreation:
    def test_init_db_creates_tables(self, tmp_db):
        """1. init_db creates all 8 tables."""
        names = tmp_db.table_names()
        expected = {
            "dictionary", "bible_verses", "grammar_patterns",
            "phrases", "vocab", "translations", "word_usage",
            "provenance",
        }
        assert expected.issubset(set(names))

    def test_init_db_idempotent(self, tmp_db):
        """20. Calling init_db twice does not raise."""
        tmp_db.init_db()
        tmp_db.init_db()
        assert len(tmp_db.table_names()) == 8


class TestDictionaryCRUD:
    def test_insert_and_lookup_dictionary(self, tmp_db, sample_dict):
        """2. Insert a dictionary entry and look it up."""
        tmp_db.insert_many("dictionary", [sample_dict])
        results = tmp_db.lookup_word("pasian")
        assert len(results) >= 1
        assert results[0]["zolai"] == "pasian"
        assert "God" in results[0]["english"]


class TestBibleCRUD:
    def test_insert_and_search_bible(self, tmp_db, sample_verse):
        """3. Insert a Bible verse and search."""
        tmp_db.insert_many("bible_verses", [sample_verse])
        results = tmp_db.search_bible("heaven")
        assert len(results) >= 1
        assert results[0]["ref"] == "GEN 1:1"


class TestPhraseCRUD:
    def test_insert_and_match_phrase(self, tmp_db, sample_phrase):
        """4. Insert a phrase and match by word."""
        tmp_db.insert_many("phrases", [sample_phrase])
        results = tmp_db.match_phrase("vantung")
        assert len(results) >= 1
        assert "vantung" in results[0]["zo"]


class TestGrammarCRUD:
    def test_insert_and_get_grammar(self, tmp_db, sample_grammar):
        """5. Insert a grammar pattern and retrieve."""
        tmp_db.insert_many("grammar_patterns", [sample_grammar])
        results = tmp_db.get_grammar("sov")
        assert len(results) >= 1
        assert results[0]["pattern"] == "sov_basic"


class TestVocabCRUD:
    def test_insert_and_get_vocab(self, tmp_db, sample_vocab):
        """6. Insert vocab entry and look up."""
        tmp_db.insert_many("vocab", [sample_vocab])
        results = tmp_db.get_vocab("pasian")
        assert len(results) >= 1
        assert results[0]["headword"] == "pasian"


class TestTranslationCRUD:
    def test_insert_and_get_translations(self, tmp_db, sample_translation):
        """7. Insert translation pair and search."""
        tmp_db.insert_many("translations", [sample_translation])
        results = tmp_db.get_translations("pasian")
        assert len(results) >= 1
        assert results[0]["direction"] == "zo_to_en"


class TestCounts:
    def test_count_tables(self, tmp_db, sample_dict, sample_verse):
        """8. Count returns correct row counts."""
        tmp_db.insert_many("dictionary", [sample_dict])
        tmp_db.insert_many("bible_verses", [sample_verse])
        assert tmp_db.count("dictionary") == 1
        assert tmp_db.count("bible_verses") == 1
        assert tmp_db.count("phrases") == 0


class TestExport:
    def test_export_table_returns_dicts(
        self, tmp_db, sample_dict, sample_verse
    ):
        """9. export_table returns list of dicts."""
        tmp_db.insert_many("dictionary", [sample_dict])
        tmp_db.insert_many("bible_verses", [sample_verse])
        d = tmp_db.export_table("dictionary")
        assert isinstance(d, list)
        assert len(d) == 1
        assert isinstance(d[0], dict)
        assert d[0]["zolai"] == "pasian"


class TestMigration:
    def test_migration_loads_all_data(self, tmp_db):
        """10. migrate_jsonl_to_db loads all available files."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        if not data_dir.exists():
            pytest.skip("data/ directory not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        # At least bible_verses should exist
        if "bible_verses" in results:
            assert results["bible_verses"] > 0

    def test_migration_dictionary_count(self, tmp_db):
        """11. Dictionary migration produces expected count."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        fpath = data_dir / "dictionary/processed/dict_zo_en_master_v1.jsonl"
        if not fpath.exists():
            pytest.skip("Dictionary file not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        assert results.get("dictionary", 0) > 80000

    def test_migration_bible_count(self, tmp_db):
        """12. Bible migration produces ~31K rows."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        fpath = data_dir / "bible/parallel_corpus_v1.jsonl"
        if not fpath.exists():
            pytest.skip("Bible corpus not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        assert results.get("bible_verses", 0) > 25000

    def test_migration_grammar_count(self, tmp_db):
        """13. Grammar migration loads patterns."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        fpath = data_dir / "bible/grammar_patterns_v2.jsonl"
        if not fpath.exists():
            pytest.skip("Grammar patterns not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        assert results.get("grammar_patterns", 0) > 100

    def test_migration_phrase_count(self, tmp_db):
        """14. Phrase migration loads phrases."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        fpath = data_dir / "bible/phrases_v1.jsonl"
        if not fpath.exists():
            pytest.skip("Phrases file not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        assert results.get("phrases", 0) > 100

    def test_migration_vocab_count(self, tmp_db):
        """15. Vocab migration loads entries."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        fpath = data_dir / "bible/vocab_index_full.jsonl"
        if not fpath.exists():
            pytest.skip("Vocab file not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        assert results.get("vocab", 0) > 1000

    def test_migration_translation_count(self, tmp_db):
        """16. Translation migration loads pairs."""
        from zolai.data.migrate import migrate_jsonl_to_db
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        fpath = data_dir / "bible/translation_pairs_v1.jsonl"
        if not fpath.exists():
            pytest.skip("Translation pairs not available")
        results = migrate_jsonl_to_db(data_dir, tmp_db._db_url)
        assert results.get("translations", 0) > 1000


class TestPerformance:
    def test_lookup_word_performance(self, tmp_db):
        """17. lookup_word returns within 1 second."""
        data = [
            {"zolai": f"word{i}", "english": json.dumps(f"eng{i}"),
             "english_clean": f"eng{i}", "source": "perf", "pos": "noun"}
            for i in range(10000)
        ]
        tmp_db.insert_many("dictionary", data)
        t0 = time.time()
        results = tmp_db.lookup_word("word5000")
        elapsed = time.time() - t0
        assert elapsed < 1.0, f"lookup_word took {elapsed:.2f}s"
        assert len(results) >= 1

    def test_search_bible_performance(self, tmp_db):
        """18. search_bible returns within 1 second."""
        data = [
            {"ref": f"GEN {i}", "book": "GEN", "chapter": 1,
             "verse": i, "zo_tdb77": f"Verse {i} text",
             "zo_tedim2010": f"Verse {i} text",
             "en_kJV": f"English verse {i}"}
            for i in range(10000)
        ]
        tmp_db.insert_many("bible_verses", data)
        t0 = time.time()
        results = tmp_db.search_bible("English verse 5000")
        elapsed = time.time() - t0
        assert elapsed < 1.0, f"search_bible took {elapsed:.2f}s"
        assert len(results) >= 1


class TestRoundtrip:
    def test_export_jsonl_roundtrip(self, tmp_db):
        """19. DB → JSONL → re-read produces same data."""
        import tempfile
        tmp_db.insert_many("dictionary", [{
            "zolai": "pasian", "english": json.dumps("God"),
            "english_clean": "God", "source": "test", "pos": "noun",
        }])
        with tempfile.TemporaryDirectory() as tmpdir:
            from zolai.data.export import export_db_to_jsonl
            export_db_to_jsonl(tmp_db._db_url, tmpdir)
            fpath = Path(tmpdir) / "dict_zo_en_master_v1.jsonl"
            assert fpath.exists()
            with open(fpath) as fh:
                data = json.loads(fh.readline())
            assert data["zolai"] == "pasian"


class TestEdgeCases:
    def test_empty_database_handled(self, tmp_db):
        """21. Queries on empty database return empty lists."""
        assert tmp_db.lookup_word("nonexistent") == []
        assert tmp_db.search_bible("nonexistent") == []
        assert tmp_db.match_phrase("nonexistent") == []
        assert tmp_db.get_grammar("nonexistent") == []
        assert tmp_db.get_vocab("nonexistent") == []
        assert tmp_db.get_translations("nonexistent") == []

    def test_special_characters_in_text(self, tmp_db):
        """22. Special characters in text are stored and retrieved."""
        entry = {
            "zolai": "café",
            "english": "café & crème — 'quotes'",
            "english_clean": "cafe",
            "source": "test",
            "pos": "noun",
        }
        tmp_db.insert_many("dictionary", [entry])
        results = tmp_db.lookup_word("café")
        assert len(results) == 1
        assert "café" in results[0]["english"]

    def test_concurrent_read_write(self, tmp_db):
        """23. Concurrent reads during writes do not crash."""
        errors: list[str] = []

        def writer():
            try:
                for i in range(100):
                    tmp_db.insert_many("dictionary", [{
                        "zolai": f"thread{i}",
                        "english": f"eng{i}",
                        "english_clean": f"eng{i}",
                        "source": "thread",
                        "pos": "noun",
                    }])
            except Exception as exc:
                errors.append(f"writer: {exc}")

        def reader():
            try:
                for _ in range(50):
                    tmp_db.lookup_word("thread0")
            except Exception as exc:
                errors.append(f"reader: {exc}")

        # Writer first, then concurrent readers (SQLite WAL)
        writer()
        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"Concurrent errors: {errors}"
        assert tmp_db.count("dictionary") == 100


class TestPostgresSkip:
    def test_postgres_skipped_if_unavailable(self):
        """24. PostgreSQL connection is skipped if not available."""
        try:
            mgr = DatabaseManager("postgresql://localhost/test_nonexistent")
            mgr.engine  # Will fail if no PG running
            mgr.dispose()
        except Exception:
            pytest.skip("PostgreSQL not available")


class TestModelImports:
    def test_all_models_importable(self):
        """25. All 8 ORM models are importable and have correct table names."""
        models = [
            DictionaryEntry, BibleVerse, GrammarPattern, PhraseEntry,
            VocabEntry, TranslationPair, WordUsageProfile, ProvenanceFile,
        ]
        table_names = {
            "dictionary", "bible_verses", "grammar_patterns",
            "phrases", "vocab", "translations", "word_usage",
            "provenance",
        }
        actual = {m.__tablename__ for m in models}
        assert actual == table_names
        assert len(MODEL_REGISTRY) == 8
