"""Unit tests for the observation primitives (stats, contexts, normalize, PMI).

Every numeric expectation is hand-computed from the fixture stream — no
golden file, no re-execution of the pipeline under test (plan §Done when).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

import zolai.foundation.observation.normalize as normalize_module
from zolai.foundation.observation import (
    ContextCollector,
    StatsAggregator,
    normalize_token,
    normalize_tokens,
    tokenize_sentence,
)
from zolai.foundation.observation.cooccurrence import PairCollector
from zolai.zvs.rules_data import (
    COMPOUND_SPLIT_TO_PREFERRED,
    DIALECT_FORBIDDEN_TO_PREFERRED,
    STEM_FORBIDDEN_TO_PREFERRED,
)

# ---------------------------------------------------------------------------
# Capability 2 — normalization
# ---------------------------------------------------------------------------


def test_normalize_imports_rules_data_maps() -> None:
    """The token map *is* the union of the three owned ZVS tables (no copies)."""
    expected = {
        **DIALECT_FORBIDDEN_TO_PREFERRED,
        **COMPOUND_SPLIT_TO_PREFERRED,
        **STEM_FORBIDDEN_TO_PREFERRED,
    }
    assert dict(normalize_module.ZVS_TOKEN_MAP) == expected
    assert normalize_token("pathian") == DIALECT_FORBIDDEN_TO_PREFERRED["pathian"]


def test_normalize_module_has_no_forbidden_literals() -> None:
    """ZVS ownership stays in ``zolai/zvs`` — the source must not re-type forms."""
    source = Path(normalize_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "pathian",
        "bawipa",
        "siangpahrang",
        "fapa",
    ):
        assert forbidden not in source


def test_normalize_casefold_and_correction_flag() -> None:
    assert normalize_token("Pasian") == "pasian"  # casefold only
    assert normalize_token("PATHIAN") == "pasian"  # ZVS correction
    forms, corrected = normalize_tokens(["Pathian", "in", "vantung"])
    assert forms == ["pasian", "in", "vantung"]
    assert corrected is True
    forms, corrected = normalize_tokens(["pasian", "in"])
    assert corrected is False


# ---------------------------------------------------------------------------
# Capability 1 — tokenization policy
# ---------------------------------------------------------------------------


def test_tokenize_sentence_keeps_one_letter_agreement_particles() -> None:
    # Observation layer keeps every word (attestation keeps only len >= 2).
    assert tokenize_sentence("A pai hi.") == ["a", "pai", "hi"]
    assert tokenize_sentence("???!") == []
    assert tokenize_sentence("") == []


# ---------------------------------------------------------------------------
# Capability 3 — streaming frequency aggregation
# ---------------------------------------------------------------------------


def test_stats_aggregator_hand_computed() -> None:
    agg = StatsAggregator()
    agg.observe(["a", "b"], ["A", "b"], document_id="GEN", source_id="s1")
    agg.observe(["a", "c"], ["a", "c"], document_id="GEN", source_id="s1")
    agg.observe(["a", "b"], ["a", "b"], document_id="EXO", source_id="s2")

    assert agg.total_sentences == 3
    assert agg.total_tokens == 6
    assert agg.total_documents == 2
    assert agg.distinct_words == 3

    assert agg.scalars("a") == {
        "frequency": 3,
        "doc_freq": 2,
        "sent_freq": 3,
        "source_count": 2,
        "diversity": 1.0,
    }
    assert agg.scalars("b") == {
        "frequency": 2,
        "doc_freq": 2,
        "sent_freq": 2,
        "source_count": 2,
        "diversity": 1.0,
    }
    assert agg.scalars("c") == {
        "frequency": 1,
        "doc_freq": 1,
        "sent_freq": 1,
        "source_count": 1,
        "diversity": 0.5,
    }


def test_stats_diversity_rounding_and_surface_ranking() -> None:
    agg = StatsAggregator()
    agg.observe(["a", "b"], ["a", "b"], document_id="D1", source_id="s")
    agg.observe(["a", "b"], ["A", "b"], document_id="D2", source_id="s")
    agg.observe(["b"], ["b"], document_id="D3", source_id="s")

    # ``a`` appears in 2 of 3 documents → round(2/3, 4) = 0.6667.
    assert agg.scalars("a")["diversity"] == 0.6667
    # Surfaces rank by count desc, then token asc (ties: 'A' < 'a').
    assert agg.surface_forms("a") == [
        {"surface": "A", "count": 1},
        {"surface": "a", "count": 1},
    ]
    assert agg.surface_forms("a", top_k=1) == [{"surface": "A", "count": 1}]


def test_freq_map_feeds_pmi_inputs() -> None:
    agg = StatsAggregator()
    agg.observe(["x", "y"], ["x", "y"], document_id="D", source_id="s")
    agg.observe(["x"], ["x"], document_id="D", source_id="s")
    assert agg.frequencies() == {"x": 2, "y": 1}


# ---------------------------------------------------------------------------
# Capability 4 — contexts
# ---------------------------------------------------------------------------


def test_context_window_counts_and_snippet() -> None:
    collector = ContextCollector(top_k=3, window=1)
    collector.observe(["ka", "mu", "hi"], "Gam ka mu hi.")
    collector.observe(["a", "piangsak", "hi"], "Pasian a piangsak hi.")

    hi = collector.as_dict("hi")
    assert hi["right"] == []
    # Window ±1 keeps only the immediately preceding token of each sentence.
    assert sorted(hi["left"], key=lambda c: c["token"]) == [
        {"token": "mu", "count": 1},
        {"token": "piangsak", "count": 1},
    ]
    # First-seen sentence wins as the snippet.
    assert hi["snippet"] == "Gam ka mu hi."
    assert collector.as_dict("unknown") == {"left": [], "right": [], "snippet": ""}


def test_context_top_k_cut_is_deterministic() -> None:
    collector = ContextCollector(top_k=2, window=1)
    for sentence in (["x", "hi"], ["y", "hi"], ["z", "hi"]):
        collector.observe(sentence, "snippet")

    left = collector.as_dict("hi")["left"]
    assert left == [{"token": "x", "count": 1}, {"token": "y", "count": 1}]
    assert len(left) == 2  # top-K respected; ties break on token asc


def test_context_space_saving_eviction() -> None:
    collector = ContextCollector(top_k=1, window=1, capacity=2)
    collector.observe(["w", "a"], "first")
    collector.observe(["w", "b"], "second")
    collector.observe(["w", "c"], "third")

    # ``w`` sits first in each sentence → its contexts are *right*-hand.
    right = collector.as_dict("w")["right"]
    # ``a`` (lowest count, then lowest token) evicted; ``c`` inherits its count.
    assert right == [{"token": "c", "count": 2}]
    assert collector.as_dict("w")["snippet"] == "first"


def test_context_snippet_truncation() -> None:
    collector = ContextCollector(top_k=1, snippet_len=5)
    collector.observe(["hi"], "abcdefghij")
    assert collector.as_dict("hi")["snippet"] == "abcd…"


# ---------------------------------------------------------------------------
# Capability 5 — co-occurrence pairs + PMI
# ---------------------------------------------------------------------------

PAIR_CORPUS = (
    ["a", "b", "c"],
    ["a", "b", "d"],
    ["a", "b", "c"],
    ["e", "f", "g"],
)


@pytest.fixture()
def pair_engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'pairs.db'}")
    yield engine
    engine.dispose()


@pytest.fixture()
def pair_conn(pair_engine: Engine) -> Connection:
    with pair_engine.begin() as conn:
        yield conn


def _collect(conn: Connection, **kwargs) -> PairCollector:
    collector = PairCollector(conn, **kwargs)
    for sentence in PAIR_CORPUS:
        collector.observe(sentence)
    return collector


def test_pair_occurrences_and_self_pairs_skipped(pair_conn: Connection) -> None:
    collector = _collect(pair_conn)
    # 3-token sentence, window 2 → 2 + 1 pairs each × 4 sentences = 12
    # (no self-pairs — every fixture sentence has distinct tokens).
    assert collector.occurrences == 12
    # Distinct canonical (w1 <= w2) rows: (a,b) (a,c) (a,d) (b,c) (b,d)
    # (e,f) (e,g) (f,g) = 8.
    assert collector.flush() == 8
    solo = PairCollector(pair_conn)
    solo.observe(["a", "a", "a"])
    assert solo.occurrences == 0  # self-pairs carry no information
    count = pair_conn.execute(
        text("SELECT COUNT(*) FROM obs_pair_counts")
    ).scalar_one()
    assert count == 8


def test_neighbors_ranking_and_tie_break(pair_conn: Connection) -> None:
    collector = _collect(pair_conn, min_freq=1)
    neighbors = collector.neighbors()
    # ``a`` partners: b×3 (all three a-sentences), c×2, d×1 → count desc.
    assert neighbors["a"] == [
        {"token": "b", "count": 3},
        {"token": "c", "count": 2},
        {"token": "d", "count": 1},
    ]
    # ``c`` partners: a×2, b×2 → equal counts → token asc.
    assert neighbors["c"] == [
        {"token": "a", "count": 2},
        {"token": "b", "count": 2},
    ]


def test_neighbor_top_k_cut(pair_conn: Connection) -> None:
    collector = _collect(pair_conn, min_freq=1, neighbor_top_k=2)
    assert len(collector.neighbors()["a"]) == 2


def test_pmi_hand_computed(pair_conn: Connection) -> None:
    collector = _collect(pair_conn, min_freq=1)
    frequencies = {"a": 3, "b": 3, "c": 2, "d": 1, "e": 1, "f": 1, "g": 1}
    total_tokens = 12  # four 3-token sentences

    collocations = collector.collocations(frequencies, total_tokens=total_tokens)

    # PMI(e, f) = log2(1 × 12 / (1 × 1)) = log2(12)
    expected_pmi = round(math.log2(12), 4)
    assert collocations["e"] == [
        {"token": "f", "pmi": expected_pmi, "count": 1},
        {"token": "g", "pmi": expected_pmi, "count": 1},
    ]
    # PMI(a, b) = log2(3 × 12 / (3 × 3)) = log2(4) = 2.0 — and c/d tie at
    # 2.0 as well, so the tie-break (token asc) pins the full ranking.
    assert collocations["a"] == [
        {"token": "b", "pmi": 2.0, "count": 3},
        {"token": "c", "pmi": 2.0, "count": 2},
        {"token": "d", "pmi": 2.0, "count": 1},
    ]


def test_pmi_nonpositive_pairs_are_filtered(pair_conn: Connection) -> None:
    collector = PairCollector(pair_conn, min_freq=1)
    collector.observe(["a"] * 50 + ["b"] * 50)
    # f1 = f2 = 50, count = 3, N = 100 → log2(300/2500) < 0 → dropped.
    result = collector.collocations(
        {"a": 50, "b": 50}, total_tokens=100
    )
    assert result == {}


def test_pmi_min_freq_filters_rare_pairs(pair_conn: Connection) -> None:
    collector = PairCollector(pair_conn, min_freq=5)
    collector.observe(["x", "y"])
    # Pair count 1 < min_freq 5 → nothing survives the SQL pre-filter even
    # though the caller claims generous unigram frequencies.
    result = collector.collocations({"x": 10, "y": 10}, total_tokens=20)
    assert result == {}


def test_collocations_empty_without_tokens(pair_conn: Connection) -> None:
    collector = PairCollector(pair_conn, min_freq=1)
    assert collector.collocations({}, total_tokens=0) == {}
