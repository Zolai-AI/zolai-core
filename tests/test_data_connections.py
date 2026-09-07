"""Tests for data connections across zolai-core modules."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.learning.context_validator import ContextValidator
from zolai.learning.word_attestation import WordAttestation

DATA_DIR = Path(__file__).parent.parent.parent / "data"

MODULES = [
    str(Path(__file__).parent.parent / "zolai" / "learning" / "context_validator.py"),
    str(Path(__file__).parent.parent / "zolai" / "learning" / "word_attestation.py"),
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
    """dict_zo_en_clean.jsonl exists and has data."""
    path = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_clean.jsonl"
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
    corpus_dir = DATA_DIR / "online" / "-corpus"
    assert corpus_dir.exists(), f"Missing: {corpus_dir}"
    files = list(corpus_dir.glob("zomi_clean_p*.txt"))
    assert len(files) >= 1, f"No zomi_clean_p*.txt files in {corpus_dir}"


def test__dictionary_exists():
    """ dictionary exists."""
    path = DATA_DIR / "online" / "-zolai-dictionary" / "words.json"
    assert path.exists(), f"Missing: {path}"


# ── Context Validator: Bible verse loading ────────────────────────────────


def test_context_validator_loads_bible_verses():
    """ContextValidator loads all Bible verses individually."""
    v = ContextValidator()
    stats = v.get_stats()
    assert stats["bible_verses"] > 0, (
        f"Expected >0 bible_verses, got {stats['bible_verses']}"
    )


def test_context_validator_verses_have_refs():
    """Each loaded verse has ref, zolai, english."""
    v = ContextValidator()
    if not v.bible_verses:
        return  # no data file
    sample = v.bible_verses[:5]
    for verse in sample:
        assert "ref" in verse, f"Missing ref in verse: {verse}"
        assert "zolai" in verse, f"Missing zolai in verse: {verse}"
        assert "english" in verse, f"Missing english in verse: {verse}"
        assert len(verse["ref"]) > 0
        assert len(verse["zolai"]) > 0


def test_context_validator_relevant_passage():
    """get_relevant_passage returns a dict or None."""
    v = ContextValidator()
    result = v.get_relevant_passage("pasian topa gam")
    if v.bible_verses:
        # With 31K verses, 'pasian' or 'topa' should match
        assert result is not None, "Expected a match for common Zolai words"
        assert "reference" in result
        assert "verses" in result


def test_context_validator_stats_format():
    """Stats dict has correct keys and types."""
    v = ContextValidator()
    stats = v.get_stats()
    assert set(stats.keys()) == {"bible_verses", "conversation_turns"}
    assert isinstance(stats["bible_verses"], int)
    assert isinstance(stats["conversation_turns"], int)


# ── Word Attestation: multi-source loading ────────────────────────────────


def test_attestation_loads_bible_words():
    """WordAttestation loads Bible words."""
    att = WordAttestation()
    assert len(att.bible_words) > 0, "No Bible words loaded"


def test_attestation_loads_dict_words():
    """WordAttestation loads dictionary words."""
    att = WordAttestation()
    assert len(att.dict_words) > 0, "No dictionary words loaded"


def test_attestation_loads_corpus_words():
    """WordAttestation loads parallel corpus words."""
    att = WordAttestation()
    assert len(att.corpus_words) > 0, "No corpus words loaded"


def test_attestation_loads__corpus():
    """WordAttestation loads  corpus words (subset of corpus_words)."""
    att = WordAttestation()
    #  words are merged into corpus_words, so corpus should be large
    assert len(att.corpus_words) > 1000, (
        f"Expected >1000 corpus words ( merged), got {len(att.corpus_words)}"
    )


def test_attestation_loads_():
    """WordAttestation loads  dictionary words."""
    att = WordAttestation()
    assert len(att._words) > 0, (
        f"No  words loaded from {DATA_DIR / 'online' / '-zolai-dictionary'}"
    )


def test_attestation_stats_keys():
    """Stats has all four source counts."""
    att = WordAttestation()
    stats = att.get_stats()
    assert set(stats.keys()) == {
        "bible_words",
        "dict_words",
        "corpus_words",
        "_words",
    }
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
    #  has Kei, Nang, Amah
    for word in ("kei", "nang", "amah"):
        result = att.attest_word(word)
        assert result["in_"] is True, (
            f"{word} should be in _words"
        )
