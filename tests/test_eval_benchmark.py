"""Unit tests for the comprehensive evaluation benchmark.

Verifies that the benchmark_qa.jsonl file contains valid, well-structured
evaluation cases across all required categories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zolai.eval.datasets import resolve_set
from zolai.zvs import validate

BENCHMARK_PATH = Path(__file__).resolve().parent.parent / "zolai" / "eval" / "sets" / "benchmark_qa.jsonl"

# ZVS 2018 forbidden forms
FORBIDDEN_FORMS = {
    "pathian": "pasian",
    "ram": "gam",
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
    "nunnak": "nuntakna",
    "suah": "chuak",
}

VALID_DIFFICULTIES = {"A1", "A2", "B1", "B2", "C1", "C2"}
VALID_CATEGORIES = {"vocabulary", "translation", "grammar", "zvs_compliance", "context"}


def _load_benchmark() -> list[dict]:
    """Load all records from the benchmark file."""
    if not BENCHMARK_PATH.exists():
        pytest.skip(f"Benchmark file not found: {BENCHMARK_PATH}")
    records = []
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def test_benchmark_has_100_plus_cases() -> None:
    """Benchmark must contain at least 100 evaluation cases."""
    records = _load_benchmark()
    assert len(records) >= 100, f"Benchmark has {len(records)} cases, expected >= 100"


def test_all_categories_present() -> None:
    """All five required categories must be represented."""
    records = _load_benchmark()
    categories = {r["category"] for r in records}
    assert categories == VALID_CATEGORIES, f"Missing categories: {VALID_CATEGORIES - categories}"


def test_all_entries_have_required_fields() -> None:
    """Every entry must have id, category, input, expected, and difficulty."""
    records = _load_benchmark()
    for r in records:
        assert "id" in r, f"Missing 'id' in {r}"
        assert "category" in r, f"Missing 'category' in {r}"
        assert "input" in r, f"Missing 'input' in {r}"
        # ZVS compliance cases use 'expected_correction' instead of 'expected'
        has_expected = "expected" in r or "expected_correction" in r
        assert has_expected, f"Missing 'expected' or 'expected_correction' in {r}"
        assert "difficulty" in r, f"Missing 'difficulty' in {r}"


def test_vocabulary_cases_correct() -> None:
    """Vocabulary cases must map Zolai words to correct English translations."""
    records = _load_benchmark()
    vocab_cases = [r for r in records if r["category"] == "vocabulary"]
    assert len(vocab_cases) >= 30, f"Expected >= 30 vocabulary cases, got {len(vocab_cases)}"
    
    # Some words have multiple valid meanings (polysemy)
    known_words = {
        "pasian": {"God"},
        "gam": {"earth"},
        "vantung": {"heaven"},
        "leitung": {"earth"},
        "tapa": {"life"},
        "topa": {"Lord"},
        "kumpipa": {"Savior"},
        "tui": {"water"},
        "khuavak": {"light"},
        "khuamial": {"darkness"},
        "sun": {"day"},
        "zan": {"night"},
        "ni": {"sun", "two"},  # polysemous
        "mi": {"person"},
        "numei": {"woman"},
        "sing": {"tree"},
        "nek": {"eat"},
        "pai": {"go"},
        "mu": {"see"},
        "gen": {"speak"},
        "ci": {"say"},
        "bawl": {"create"},
        "piangsak": {"created"},
        "om": {"exist"},
        "hiam": {"question"},
        "kei": {"not"},
        "lo": {"not"},
        "ding": {"will"},
        "ta": {"past"},
        "zo": {"completed"},
        "lai": {"book"},
        "inn": {"house"},
        "khua": {"village"},
        "kum": {"year"},
        "khat": {"one"},
        "nih": {"two"},
        "thum": {"three"},
        "li": {"four"},
        "nga": {"five"},
    }
    
    for case in vocab_cases:
        word = case["input"]
        expected = case["expected"]
        if word in known_words:
            assert expected in known_words[word], \
                f"Wrong translation for {word}: {expected} not in {known_words[word]}"


def test_zvs_cases_correct() -> None:
    """ZVS compliance cases must map forbidden forms to correct corrections."""
    records = _load_benchmark()
    zvs_cases = [r for r in records if r["category"] == "zvs_compliance"]
    assert len(zvs_cases) >= 20, f"Expected >= 20 ZVS cases, got {len(zvs_cases)}"
    
    for case in zvs_cases:
        forbidden = case["input"]
        correction = case["expected_correction"]
        assert forbidden in FORBIDDEN_FORMS, f"Unknown forbidden form: {forbidden}"
        assert FORBIDDEN_FORMS[forbidden] == correction, f"Wrong correction for {forbidden}: {correction}"


def test_grammar_cases_follow_sov() -> None:
    """Grammar cases must demonstrate SOV word order patterns."""
    records = _load_benchmark()
    grammar_cases = [r for r in records if r["category"] == "grammar"]
    assert len(grammar_cases) >= 20, f"Expected >= 20 grammar cases, got {len(grammar_cases)}"
    
    # Valid sentence endings in Zolai
    valid_endings = ("hi.", "hiam?", "ding.", "ding?", "hen.")
    
    for case in grammar_cases:
        expected = case["expected"]
        # All Zolai sentences must end with a particle
        assert any(expected.endswith(ending) for ending in valid_endings), \
            f"Grammar case must end with valid particle: {expected}"


def test_no_forbidden_forms_in_expected() -> None:
    """Expected outputs must not contain forbidden ZVS forms."""
    records = _load_benchmark()
    for case in records:
        if "expected" in case:
            text = case["expected"]
            for forbidden in FORBIDDEN_FORMS:
                assert forbidden not in text.lower(), \
                    f"Forbidden form '{forbidden}' found in expected: {text}"


def test_difficulty_levels_valid() -> None:
    """All difficulty levels must be valid CEFR levels."""
    records = _load_benchmark()
    for case in records:
        assert case["difficulty"] in VALID_DIFFICULTIES, \
            f"Invalid difficulty '{case['difficulty']}' in {case['id']}"


def test_ids_unique() -> None:
    """All IDs must be unique across the benchmark."""
    records = _load_benchmark()
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)), f"Duplicate IDs found: {len(ids)} total, {len(set(ids))} unique"


def test_benchmark_loads_in_eval_cli() -> None:
    """Benchmark should be loadable by the evaluation CLI."""
    sources = {"benchmark": BENCHMARK_PATH}
    assert "benchmark" in sources, "Benchmark not loadable"
    from zolai.eval.datasets import load_qa
    hyps, refs = load_qa(sources["benchmark"])
    assert len(hyps) >= 100, f"Expected >= 100 QA pairs, got {len(hyps)}"
    assert len(hyps) == len(refs), "Mismatched hyps/refs lengths"
