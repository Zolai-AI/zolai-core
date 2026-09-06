"""Tests for word attestation module."""
import subprocess
import sys
from pathlib import Path

# Ensure zolai is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.learning.word_attestation import WordAttestation, get_word_attestation

MODULE_PATH = str(
    Path(__file__).parent.parent / "zolai" / "learning" / "word_attestation.py"
)


# ── py_compile ──────────────────────────────────────────────────────────


def test_module_compiles():
    """Module compiles without syntax errors."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", MODULE_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ── ruff ────────────────────────────────────────────────────────────────


def test_ruff_lint():
    """Module passes ruff lint."""
    result = subprocess.run(
        ["ruff", "check", MODULE_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── Singleton ───────────────────────────────────────────────────────────


def test_singleton():
    """get_word_attestation returns same instance."""
    a = get_word_attestation()
    b = get_word_attestation()
    assert a is b


# ── Data loading ────────────────────────────────────────────────────────


def test_data_loaded():
    """At least some words are loaded from data files."""
    att = get_word_attestation()
    stats = att.get_stats()
    assert stats["bible_words"] > 0, "No Bible words loaded"
    assert stats["dict_words"] > 0, "No dictionary words loaded"


# ── Known Zolai words ──────────────────────────────────────────────────


def test_known_word_attested():
    """Common Zolai words are attested."""
    att = get_word_attestation()
    for word in ("pasian", "topa", "lungdam", "kum", "gam"):
        result = att.attest_word(word)
        assert result["confidence"] != "UNATTESTED", f"{word} should be attested"


def test_fake_word_unattested():
    """Made-up words are unattested."""
    att = get_word_attestation()
    for word in ("xqtvw", "bzlkm", "fwzzz", "qqqrrr"):
        result = att.attest_word(word)
        assert result["confidence"] == "UNATTESTED", f"{word} should be UNATTESTED"


# ── Sentence attestation ───────────────────────────────────────────────


def test_attest_sentence_good():
    """Sentence with known words passes."""
    att = get_word_attestation()
    result = att.attest_sentence("Pasian in topa")
    assert result["overall"] in ("PASS", "PARTIAL"), result


def test_attest_sentence_bad():
    """Sentence with fake words fails."""
    att = get_word_attestation()
    result = att.attest_sentence("xqtvw bzlkm fwzzz")
    assert result["overall"] == "FAIL", result
    assert len(result["unattested"]) >= 2


# ── Suggestion ──────────────────────────────────────────────────────────


def test_suggestion():
    """Suggestion returns something or None."""
    att = get_word_attestation()
    suggestion = att.attest_word("pasian").get("word")
    assert suggestion is not None


# ── Levenshtein ─────────────────────────────────────────────────────────


def test_levenshtein():
    """Levenshtein distance is correct."""
    assert WordAttestation._levenshtein("cat", "cat") == 0
    assert WordAttestation._levenshtein("cat", "bat") == 1
    assert WordAttestation._levenshtein("cat", "cats") == 1
    assert WordAttestation._levenshtein("", "abc") == 3
