"""Phase 2 §27 — attestation index / Bloom artifact / membership cache.

Hand-computed fixture corpus — every expected set below is derived by hand
from these rows, never re-executed through the loaders under test:

====================  ==================================================
bible_verses:1  GEN   ``Pasian in vantung leh leitung a piangsak hi.`` (7 tok ≥2)
bible_verses:2  GEN   ``Gam ka lak hi.`` (4 tok)
bible_verses:3  EXO   tedim only: ``Pathian a pai ta hi.`` (3 tok, ``a`` excl)
dictionary:           pasian / vantung / gam (whole-field, 3)
translations:1  zo→en target ``Gam ka mu hi.`` (4 tok)
translations:2  en→zo target ``Vanlai a piang hi.`` (3 tok, ``a`` excl)
translations:3  en→my Myanmar target — regex yields no tokens
extra words.json      Kei / Nang / amah (lowercased, 3)
====================  ==================================================

bible 13 · dict 3 · corpus 6 · extra 3 → index pairs 25,
union over all four sources (Bloom universe) 19, stats total 25.

Both load paths (``prefer_index=True`` vs ``False``) must agree word-for-word
— parity by construction, since the index is materialized from the very same
loader queries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_observation_tables
from zolai.data.models import BibleVerse, DictionaryEntry, TranslationPair
from zolai.learning import word_attestation
from zolai.learning.word_attestation import (
    _CACHE_MAX,
    WordAttestation,
    _BloomFilter,
    build_attestation_bloom,
    build_attestation_index,
)

# Hand-computed expectations (see module docstring) ------------------------

BIBLE_WORDS = {
    "pasian", "in", "vantung", "leh", "leitung", "piangsak", "hi",
    "gam", "ka", "lak",
    "pathian", "pai", "ta",
}  # 13
DICT_WORDS = {"pasian", "vantung", "gam"}  # 3
CORPUS_WORDS = {"gam", "ka", "mu", "hi", "vanlai", "piang"}  # 6
EXTRA_WORDS = {"kei", "nang", "amah"}  # 3
UNION_WORDS = BIBLE_WORDS | DICT_WORDS | CORPUS_WORDS | EXTRA_WORDS  # 19
PAIRS_TOTAL = len(BIBLE_WORDS) + len(DICT_WORDS) + len(CORPUS_WORDS) + len(EXTRA_WORDS)  # 25

RESULT_KEYS = {
    "word", "confidence", "in_bible", "in_dict", "in_corpus", "in_", "source_count",
}
STATS_KEYS = {"bible_words", "dict_words", "corpus_words", "_words", "total"}


def _seed_sources(engine: Engine) -> None:
    """Load the fixture corpus (identical key sets per table for executemany)."""
    with engine.begin() as conn:
        conn.execute(
            BibleVerse.__table__.insert(),
            [
                {
                    "id": 1, "ref": "GEN 1:1", "book": "GEN", "chapter": 1, "verse": 1,
                    "zo_tdb77": "Pasian in vantung leh leitung a piangsak hi.",
                    "zo_tedim2010": None,
                },
                {
                    "id": 2, "ref": "GEN 1:2", "book": "GEN", "chapter": 1, "verse": 2,
                    "zo_tdb77": "Gam ka lak hi.",
                    "zo_tedim2010": None,
                },
                {
                    "id": 3, "ref": "EXO 1:1", "book": "EXO", "chapter": 1, "verse": 1,
                    "zo_tdb77": None,
                    "zo_tedim2010": "Pathian a pai ta hi.",
                },
            ],
        )
        conn.execute(
            DictionaryEntry.__table__.insert(),
            [
                {"zolai": "pasian", "english": "God", "source": "test", "pos": "n"},
                {"zolai": "vantung", "english": "heaven", "source": "test", "pos": "n"},
                {"zolai": "gam", "english": "earth", "source": "test", "pos": "n"},
            ],
        )
        conn.execute(
            TranslationPair.__table__.insert(),
            [
                {
                    "id": 1, "direction": "zo_to_en", "source": "Gam ka mu hi.",
                    "target": "Gam ka mu hi.", "reference": "gen:1", "confidence": 1.0,
                },
                {
                    "id": 2, "direction": "en_to_zo", "source": "I see.",
                    "target": "Vanlai a piang hi.", "reference": "gen:2", "confidence": 1.0,
                },
                {
                    # Myanmar target — the word regex must yield nothing.
                    "id": 3, "direction": "en_to_my", "source": "I see the land.",
                    "target": "မြင်သည်။", "reference": "gen:3", "confidence": 1.0,
                },
            ],
        )


@pytest.fixture()
def att_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DatabaseManager:
    """Temporary DB + patched DATA_DIR (extra words.json, no real data/ IO)."""
    data_dir = tmp_path / "data"
    extra_dir = data_dir / "online" / "zolai-extra-dictionary"
    extra_dir.mkdir(parents=True)
    (extra_dir / "words.json").write_text(
        json.dumps(
            {"words": [{"word": "Kei"}, {"word": "Nang"}, {"word": "amah"}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(word_attestation, "DATA_DIR", data_dir)

    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'attestation.db'}")
    mgr.init_db()
    create_observation_tables(mgr)
    _seed_sources(mgr.engine)
    yield mgr
    mgr.dispose()


def _db_path(mgr: DatabaseManager) -> str:
    path = mgr.engine.url.database
    assert path, "fixture engine must point at a file"
    return str(path)


def _queries_instance(mgr: DatabaseManager) -> WordAttestation:
    """Loader path — the byte-identical pre-§27 behavior."""
    att = WordAttestation(db_path=_db_path(mgr), prefer_index=False)
    att.attest_word("warmup")  # force the load now
    return att


def _index_instance(mgr: DatabaseManager) -> WordAttestation:
    att = WordAttestation(db_path=_db_path(mgr))
    att.attest_word("warmup")
    assert att.loaded_via == "index", "index must be populated by the test"
    return att


# ---------------------------------------------------------------------------
# Capability A/B — loader queries vs index content
# ---------------------------------------------------------------------------


def test_loader_queries_produce_hand_sets(att_db: DatabaseManager) -> None:
    att = _queries_instance(att_db)
    assert att.loaded_via == "queries"
    assert att.bible_words == BIBLE_WORDS
    assert att.dict_words == DICT_WORDS
    assert att.corpus_words == CORPUS_WORDS
    assert att._words == EXTRA_WORDS


def test_index_matches_queries_sets(att_db: DatabaseManager) -> None:
    summary = build_attestation_index(_db_path(att_db))
    assert summary["rebuilt"] is True
    assert summary["pairs_written"] == PAIRS_TOTAL
    assert summary["sources"] == {
        "bible": len(BIBLE_WORDS),
        "dict": len(DICT_WORDS),
        "corpus": len(CORPUS_WORDS),
        "extra": len(EXTRA_WORDS),
    }

    queries = _queries_instance(att_db)
    indexed = _index_instance(att_db)
    assert indexed.bible_words == queries.bible_words == BIBLE_WORDS
    assert indexed.dict_words == queries.dict_words == DICT_WORDS
    assert indexed.corpus_words == queries.corpus_words == CORPUS_WORDS
    assert indexed._words == queries._words == EXTRA_WORDS


def test_empty_index_falls_back_to_queries(att_db: DatabaseManager) -> None:
    att = WordAttestation(db_path=_db_path(att_db))
    att.attest_word("pasian")
    assert att.loaded_via == "queries"


def test_force_false_keeps_populated_index(att_db: DatabaseManager) -> None:
    path = _db_path(att_db)
    first = build_attestation_index(path)
    assert first["rebuilt"] is True
    second = build_attestation_index(path, force=False)
    assert second["rebuilt"] is False
    assert second["pairs_written"] == 0
    assert "populated" in second["reason"]


# ---------------------------------------------------------------------------
# Capability C — attest_word verdicts + exact dict shape
# ---------------------------------------------------------------------------


def test_attest_word_shape_and_casing_parity(att_db: DatabaseManager) -> None:
    build_attestation_index(_db_path(att_db))
    queries = _queries_instance(att_db)
    indexed = _index_instance(att_db)

    for probe in ("PATHIAN", "  Pasian ", "MU", "Kei", "zzznope"):
        from_queries = queries.attest_word(probe)
        from_index = indexed.attest_word(probe)
        assert from_index == from_queries, probe
        assert set(from_queries) == RESULT_KEYS
        assert from_queries["word"] == probe, "original casing/space must survive"


def test_verdicts_hand_computed_both_paths(att_db: DatabaseManager) -> None:
    build_attestation_index(_db_path(att_db))
    for att in (_queries_instance(att_db), _index_instance(att_db)):
        r = att.attest_word("pasian")
        assert (r["confidence"], r["in_bible"], r["in_dict"], r["source_count"]) == (
            "VERIFIED", True, True, 2,
        )
        assert r["in_corpus"] is False and r["in_"] is False

        r = att.attest_word("gam")
        assert r["confidence"] == "VERIFIED" and r["source_count"] == 3
        assert r["in_bible"] and r["in_dict"] and r["in_corpus"]

        r = att.attest_word("mu")
        assert (r["confidence"], r["in_corpus"], r["source_count"]) == (
            "ATTESTED", True, 1,
        )

        r = att.attest_word("kei")
        assert (r["confidence"], r["in_"], r["source_count"]) == ("ATTESTED", True, 1)

        # Surface form exists in the corpus text (attestation ≠ ZVS validation).
        r = att.attest_word("pathian")
        assert (r["confidence"], r["in_bible"]) == ("ATTESTED", True)

        r = att.attest_word("zzznotaword")
        assert r["confidence"] == "UNATTESTED" and r["source_count"] == 0
        assert not any((r["in_bible"], r["in_dict"], r["in_corpus"], r["in_"]))


def test_index_is_source_of_truth_for_later_rows(att_db: DatabaseManager) -> None:
    """Rows added *after* a rebuild are visible on the index path only.

    The index is a materialized snapshot; refresh (force=True) re-syncs it
    with the loader queries.  Pinning this makes the rebuild responsibility
    explicit instead of accidental.
    """
    path = _db_path(att_db)
    build_attestation_index(path)
    with att_db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO attestation_index (word, source) VALUES ('ghostword', 'bible')")
        )
    indexed = WordAttestation(db_path=path)
    assert indexed.attest_word("ghostword")["in_bible"] is True
    queries = _queries_instance(att_db)
    assert queries.attest_word("ghostword")["in_bible"] is False


# ---------------------------------------------------------------------------
# Capability D/E — stats cache + bounded LRU membership cache
# ---------------------------------------------------------------------------


def test_stats_five_keys_and_cached(att_db: DatabaseManager) -> None:
    build_attestation_index(_db_path(att_db))
    att = _index_instance(att_db)
    first = att.get_stats()
    second = att.get_stats()
    assert set(first) == STATS_KEYS
    assert first == {
        "bible_words": len(BIBLE_WORDS),
        "dict_words": len(DICT_WORDS),
        "corpus_words": len(CORPUS_WORDS),
        "_words": len(EXTRA_WORDS),
        "total": PAIRS_TOTAL,
    }
    assert first == second
    assert second is not first, "callers must not mutate the internal cache"
    assert all(isinstance(v, int) for v in first.values())


def test_membership_cache_bounded_and_mru(att_db: DatabaseManager) -> None:
    att = WordAttestation(db_path=_db_path(att_db), prefer_index=False)
    att.attest_word("warm")
    overflow = _CACHE_MAX + 100
    for i in range(overflow):
        att.attest_word(f"zzword{i}")
    assert len(att._membership_cache) == _CACHE_MAX
    assert "warm" not in att._membership_cache, "oldest entry must be evicted"
    assert f"zzword{overflow - 1}" in att._membership_cache, "MRU entry retained"
    # Evicted or not, verdicts stay correct — the cache is only a speed path.
    assert att.attest_word("pasian")["confidence"] == "VERIFIED"


# ---------------------------------------------------------------------------
# Capability F — optional negative-only Bloom artifact
# ---------------------------------------------------------------------------


def test_build_bloom_roundtrip(att_db: DatabaseManager) -> None:
    build_attestation_index(_db_path(att_db))
    summary = build_attestation_bloom(_db_path(att_db))
    assert summary["origin"] == "index"
    assert summary["count"] == len(UNION_WORDS)

    json_path = Path(summary["json"])
    bin_path = Path(summary["bin"])
    assert json_path.exists() and bin_path.exists()

    bloom = _BloomFilter.load(json_path)
    assert bloom is not None
    assert bloom.bits & (bloom.bits - 1) == 0, "bits must be a power of two"
    assert bloom.hashes >= 1
    for member in ("pasian", "kei", "vanlai"):
        assert bloom.maybe(member) is True, "members are never negative"
    assert bloom.maybe("zzzdefinitelyabsent") is False


def test_build_bloom_falls_back_to_queries(att_db: DatabaseManager) -> None:
    summary = build_attestation_bloom(_db_path(att_db))
    assert summary["origin"] == "queries"
    assert summary["count"] == len(UNION_WORDS)


def test_bloom_load_rejects_corrupt_or_stale_files(tmp_path: Path) -> None:
    bad_version = tmp_path / "bloom-v1.json"
    bad_version.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert _BloomFilter.load(bad_version) is None

    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert _BloomFilter.load(garbage) is None

    missing = tmp_path / "nope.json"
    assert _BloomFilter.load(missing) is None

    # bit-length/bin-size mismatch must refuse to load, not crash
    bloom = _BloomFilter.build({"alpha", "beta"})
    json_path, bin_path = bloom.save(tmp_path / "mismatch.json")
    bin_path.write_bytes(b"\x00" * 3)
    assert _BloomFilter.load(json_path) is None


def test_bloom_negative_short_circuits_without_loading_sets(
    att_db: DatabaseManager,
) -> None:
    build_attestation_index(_db_path(att_db))
    build_attestation_bloom(_db_path(att_db))  # default path → patched DATA_DIR

    att = WordAttestation(db_path=_db_path(att_db))
    result = att.attest_word("zzzdefinitelyabsent")
    assert result["confidence"] == "UNATTESTED"
    assert att._loaded is False, "negative must be final without loading sets"
    assert att.loaded_via == ""

    # A member falls through the filter to the real load.
    member = att.attest_word("pasian")
    assert member["confidence"] == "VERIFIED"
    assert att._loaded is True and att.loaded_via == "index"


def test_bloom_never_changes_verdicts(
    att_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _db_path(att_db)
    build_attestation_index(path)
    build_attestation_bloom(path)
    probes = ("pasian", "mu", "kei", "pathian", "zzznope")

    with_bloom = WordAttestation(db_path=path)
    baseline = {p: with_bloom.attest_word(p) for p in probes}

    monkeypatch.setattr(
        word_attestation,
        "_bloom_json_path",
        lambda out_dir=None: Path(path).parent / "missing-bloom.json",
    )
    without_bloom = WordAttestation(db_path=path)
    assert {p: without_bloom.attest_word(p) for p in probes} == baseline


# ---------------------------------------------------------------------------
# Capability G — corpus flag semantics
# ---------------------------------------------------------------------------


def test_corpus_flag_bypasses_index(
    att_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_attestation_index(_db_path(att_db))
    monkeypatch.setenv("ZOLAI_LOAD_CORPUS", "1")
    att = WordAttestation(db_path=_db_path(att_db))
    assert att.attest_word("pasian")["confidence"] == "VERIFIED"
    assert att.loaded_via == "queries", "flag-on loads must ignore the index"


def test_build_functions_restore_corpus_env(
    att_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _db_path(att_db)
    monkeypatch.setenv("ZOLAI_LOAD_CORPUS", "1")
    index_summary = build_attestation_index(path)
    assert os.environ.get("ZOLAI_LOAD_CORPUS") == "1"
    assert index_summary["rebuilt"] is True
    bloom_summary = build_attestation_bloom(path)
    assert os.environ.get("ZOLAI_LOAD_CORPUS") == "1"
    # Index/Bloom are built from the flag-off universe by design.
    assert bloom_summary["origin"] == "index"
    assert bloom_summary["count"] == len(UNION_WORDS)
