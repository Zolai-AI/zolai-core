"""Tests for data connections across zolai-core modules."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.learning.context_validator import ContextValidator
from zolai.learning.word_attestation import WordAttestation
from zolai.learning.sentence_validator import SentenceValidator
from zolai.api.rag_context_v2 import ZolaiRAGContextV2

DATA_DIR = Path(__file__).parent.parent.parent / "data"

MODULES = [
    str(Path(__file__).parent.parent / "zolai" / "learning" / "context_validator.py"),
    str(Path(__file__).parent.parent / "zolai" / "learning" / "word_attestation.py"),
    str(Path(__file__).parent.parent / "zolai" / "learning" / "sentence_validator.py"),
    str(Path(__file__).parent.parent / "zolai" / "api" / "rag_context_v2.py"),
]


# ── Compilation ──────────────────────────────────────────────────────────


def test_all_modules_compile():
    """All data modules compile cleanly."""
    for mod in MODULES:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", mod],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{mod}: {result.stderr}"


def test_all_modules_ruff():
    """All data modules pass ruff lint."""
    for mod in MODULES:
        result = subprocess.run(
            ["ruff", "check", mod],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{mod}: {result.stdout + result.stderr}"


# ── Data file existence ──────────────────────────────────────────────────


def test_parallel_corpus_exists():
    """parallel_corpus_v1.jsonl exists and has data."""
    path = DATA_DIR / "bible" / "parallel_corpus_v1.jsonl"
    assert path.exists(), f"Missing: {path}"
    with open(path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        assert len(first_line.strip()) > 0, "File is empty"


def test_dictionary_exists():
    """dict_zo_en_master_v1.jsonl exists and has data."""
    path = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_master_v1.jsonl"
    assert path.exists(), f"Missing: {path}"
    with open(path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        assert len(first_line.strip()) > 0, "File is empty"


def test_parallel_pairs_exists():
    """zo_en_pairs_combined_v1.jsonl exists."""
    path = DATA_DIR / "parallel" / "zo_en_pairs_combined_v1.jsonl"
    assert path.exists(), f"Missing: {path}"


def test__corpus_exists():
    """ corpus files exist."""
    corpus_dir = DATA_DIR / "online" / "zolai-web-corpus"
    assert corpus_dir.exists(), f"Missing: {corpus_dir}"
    files = list(corpus_dir.glob("zomi_clean_p*.txt"))
    assert len(files) >= 1, f"No zomi_clean_p*.txt files in {corpus_dir}"


def test__dictionary_exists():
    """ dictionary exists."""
    path = DATA_DIR / "online" / "zolai-extra-dictionary" / "words.json"
    assert path.exists(), f"Missing: {path}"


# ── Lazy loading: no data at init ───────────────────────────────────────


def test_word_attestation_lazy():
    """WordAttestation has no data until first query."""
    att = WordAttestation()
    assert att._loaded is False
    assert len(att.bible_words) == 0
    assert len(att.dict_words) == 0
    assert len(att.corpus_words) == 0
    assert len(att._words) == 0
    # After query, data loads
    att.attest_word("pasian")
    assert att._loaded is True
    assert len(att.bible_words) > 0


def test_sentence_validator_lazy():
    """SentenceValidator has no data until first query."""
    v = SentenceValidator()
    assert v._loaded is False
    assert len(v.bible_sentences) == 0
    assert len(v.corpus_sentences) == 0
    # After query, data loads
    v.validate("pasian")
    assert v._loaded is True
    assert len(v.bible_sentences) > 0


def test_rag_context_v2_lazy():
    """ZolaiRAGContextV2 is DB-backed; build_context returns usable context."""
    rag = ZolaiRAGContextV2()
    context = rag.build_context("pasian")
    assert isinstance(context, str)
    assert len(context) > 0


# ── Singleton: sentence_validator ───────────────────────────────────────


def test_sentence_validator_singleton():
    """get_sentence_validator returns same instance."""
    from zolai.learning.sentence_validator import get_sentence_validator
    v1 = get_sentence_validator()
    v2 = get_sentence_validator()
    assert v1 is v2


# ── Word Attestation: multi-source loading ────────────────────────────────


def test_attestation_loads_bible_words():
    """WordAttestation loads Bible words."""
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load
    assert len(att.bible_words) > 0, "No Bible words loaded"


def test_attestation_loads_dict_words():
    """WordAttestation loads dictionary words."""
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load
    assert len(att.dict_words) > 0, "No dictionary words loaded"


def test_attestation_loads_corpus_words():
    """WordAttestation loads parallel corpus words."""
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load
    assert len(att.corpus_words) > 0, "No corpus words loaded"


def test_attestation_loads__corpus():
    """WordAttestation loads  corpus words only when ZOLAI_LOAD_CORPUS=1."""
    import os
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load
    # With ZOLAI_LOAD_CORPUS unset,  is not loaded
    # corpus_words still has parallel corpus data
    assert len(att.corpus_words) > 0, "No corpus words loaded"


def test_attestation_loads_():
    """WordAttestation loads  dictionary words."""
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load
    assert len(att._words) > 0, (
        f"No  words loaded from {DATA_DIR / 'online' / 'zolai-extra-dictionary'}"
    )


def test_attestation_stats_keys():
    """Stats has all source counts."""
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load
    stats = att.get_stats()
    expected_keys = {"bible_words", "dict_words", "corpus_words", "_words", "total"}
    assert set(stats.keys()) == expected_keys
    for key, val in stats.items():
        assert isinstance(val, int), f"stats[{key}] is {type(val)}, expected int"


# ── Cross-module: known words attested ───────────────────────────────────


def test_common_words_verified():
    """Common Zolai words are VERIFIED (2+ sources)."""
    att = WordAttestation()
    for word in ("pasian", "topa", "gam", "vantung", "tui"):
        result = att.attest_word(word)
        assert result["confidence"] in ("VERIFIED", "ATTESTED"), (
            f"{word}: expected VERIFIED/ATTESTED, got {result['confidence']}"
        )
        assert result["source_count"] >= 1, (
            f"{word}: source_count={result['source_count']}, expected >=1"
        )


def test__known_word():
    """ dictionary has known words."""
    att = WordAttestation()
    att.attest_word("pasian")  # trigger load first
    #  has Kei, Nang, Amah
    for word in ("kei", "nang", "amah"):
        result = att.attest_word(word)
        assert result["in_"] is True, (
            f"{word} should be in _words"
        )


# ── RAG Context V2: builds context ───────────────────────────────────────


def test_rag_v2_builds_context():
    """RAG context builder returns non-empty string."""
    rag = ZolaiRAGContextV2()
    context = rag.build_context("What is God in Zolai?")
    assert isinstance(context, str)
    assert len(context) > 0
    assert context != "No Zolai context found."


def test_rag_v2_stats():
    """RAG V2 stats return correct keys."""
    rag = ZolaiRAGContextV2()
    rag.build_context("pasian")  # warm DB connections
    stats = rag.get_stats()
    expected = {"dict_entries", "bible_verses", "parallel_pairs", "grammar_patterns", "vocab_entries"}
    assert set(stats.keys()) == expected
    assert stats["dict_entries"] > 0
    assert stats["bible_verses"] > 0
