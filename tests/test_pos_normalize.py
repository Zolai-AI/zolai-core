"""Tests for legacy POS → canonical UPOS normalization (L1.3).

Pure-function tests: no database, no I/O.

Covers:
- all 22 legacy ``pos`` values used by dictionary/vocabulary
- the abbreviation variants used by ``zolai_vocabulary``
- compound labels (``adv & a``) and their candidate output
- garbage / affix / placeholder labels (must normalize to unknown)
- the invariant that no output ever leaves the 17-tag UPOS allowlist
"""
from __future__ import annotations

import json

import pytest

from zolai.data.pos_normalize import (
    CANDIDATE_CONFIDENCE,
    EVIDENCE_VALUES,
    UPOS_ALLOWLIST,
    is_upos,
    normalize_legacy_pos,
)

# ---------------------------------------------------------------------------
# Table-driven cases: (raw, expected_canonical, expected_candidates, expected_evidence)
# ---------------------------------------------------------------------------

# The 22 non-empty ``pos`` values present in dictionary/vocabulary.
DICT_LEGACY_VALUES: list[tuple[str, str | None, tuple[str, ...], str]] = [
    ("noun", "NOUN", (), "inferred"),
    ("verb", "VERB", (), "inferred"),
    ("adjective", "ADJ", (), "inferred"),
    ("transitive verb", "VERB", (), "inferred"),
    ("adverb", "ADV", (), "inferred"),
    ("intransitive verb", "VERB", (), "inferred"),
    ("pronoun", "PRON", (), "inferred"),
    ("preposition", "ADP", (), "inferred"),
    ("particle", "PART", (), "inferred"),
    ("phrasal verb", "VERB", (), "inferred"),
    ("interjection", "INTJ", (), "inferred"),
    ("intj", "INTJ", (), "inferred"),
    ("exclamation", "INTJ", (), "inferred"),
    # ambiguous → candidates
    ("conjunction", None, ("CCONJ", "SCONJ"), "inferred"),
    ("V, adjective", None, ("VERB", "ADJ"), "inferred"),
    ("poss", None, ("PRON", "DET"), "inferred"),
    # unmapped / affix / placeholder → unknown
    ("unknown", None, (), "unknown"),
    ("vs", None, (), "unknown"),
    ("suffix", None, (), "unknown"),
    ("prefix", None, (), "unknown"),
    ("Negative imperative verbal suffix ", None, (), "unknown"),
    ("nominal prefix or verbal prefix", None, (), "unknown"),
]

# zolai_vocabulary abbreviation variants (exact casefolded tokens).
VOCAB_VARIANTS: list[tuple[str, str | None, tuple[str, ...], str]] = [
    ("n", "NOUN", (), "inferred"),
    ("Noun", "NOUN", (), "inferred"),
    ("NOUN", "NOUN", (), "inferred"),
    ("noun", "NOUN", (), "inferred"),
    ("v", "VERB", (), "inferred"),
    ("Verb", "VERB", (), "inferred"),
    ("VERB", "VERB", (), "inferred"),
    ("vt", "VERB", (), "inferred"),
    ("a", "ADJ", (), "inferred"),
    ("adj", "ADJ", (), "inferred"),
    ("Adj", "ADJ", (), "inferred"),
    ("Adjective", "ADJ", (), "inferred"),
    ("adv", "ADV", (), "inferred"),
    ("Adverb", "ADV", (), "inferred"),
    ("prep", "ADP", (), "inferred"),
    ("Prep", "ADP", (), "inferred"),
    ("pron", "PRON", (), "inferred"),
    ("pro", "PRON", (), "inferred"),
    ("Pro", "PRON", (), "inferred"),
    ("Pron", "PRON", (), "inferred"),
    ("intj", "INTJ", (), "inferred"),
    ("Interj", "INTJ", (), "inferred"),
    ("interj", "INTJ", (), "inferred"),
    ("num", "NUM", (), "inferred"),
    ("punct", "PUNCT", (), "inferred"),
    ("det", "DET", (), "inferred"),
    ("PART", "PART", (), "inferred"),
    ("part", "PART", (), "inferred"),
    ("conj", None, ("CCONJ", "SCONJ"), "inferred"),
    ("Conj", None, ("CCONJ", "SCONJ"), "inferred"),
]

# Compound labels: split on & / , / — collapse or become candidates.
COMPOUNDS: list[tuple[str, str | None, tuple[str, ...], str]] = [
    ("adv & a", None, ("ADV", "ADJ"), "inferred"),
    ("adv&a", None, ("ADV", "ADJ"), "inferred"),
    ("n & a", None, ("NOUN", "ADJ"), "inferred"),
    ("N & v", None, ("NOUN", "VERB"), "inferred"),
    ("a, adv & dem pro & a", None, (), "unknown"),  # 'dem pro' is unmapped
    ("n, adv & v", None, ("NOUN", "ADV", "VERB"), "inferred"),
    ("prep & adv", None, ("ADP", "ADV"), "inferred"),
    ("conj & adv", None, ("CCONJ", "SCONJ", "ADV"), "inferred"),
]

# Junk / affix / non-POS labels → blank (evidence unknown).
GARBAGE: list[tuple[str, str | None, tuple[str, ...], str]] = [
    ("pt par", None, (), "unknown"),
    ("pt", None, (), "unknown"),
    ("dead", None, (), "unknown"),
    ("pas. part.", None, (), "unknown"),
    ("pro. n", None, (), "unknown"),
    ("pred a", None, (), "unknown"),
    ("n.1", None, (), "unknown"),
    ("Hand", None, (), "unknown"),
    ("Down1", None, (), "unknown"),
    ("quadri", None, (), "unknown"),
]

ALL_CASES = DICT_LEGACY_VALUES + VOCAB_VARIANTS + COMPOUNDS + GARBAGE


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPosAllowlistContract:
    def test_allowlist_has_17_tags(self) -> None:
        assert len(UPOS_ALLOWLIST) == 17

    def test_evidence_values(self) -> None:
        assert EVIDENCE_VALUES == {
            "documented",
            "corpus-observed",
            "expert-validated",
            "inferred",
            "experimental",
            "unknown",
        }

    def test_is_upos(self) -> None:
        assert is_upos("NOUN")
        assert not is_upos("noun")
        assert not is_upos("")


@pytest.mark.parametrize(
    ("raw", "canonical", "candidates", "evidence"),
    ALL_CASES,
    ids=[repr(case[0]) for case in ALL_CASES],
)
class TestNormalizeLegacyPos:
    def test_expected_decision(
        self,
        raw: str,
        canonical: str | None,
        candidates: tuple[str, ...],
        evidence: str,
    ) -> None:
        decision = normalize_legacy_pos(raw)
        assert decision.canonical == canonical
        assert decision.candidates == candidates
        assert decision.evidence == evidence
        assert decision.raw == raw

    def test_output_within_upos_allowlist(
        self,
        raw: str,
        canonical: str | None,
        candidates: tuple[str, ...],
        evidence: str,
    ) -> None:
        decision = normalize_legacy_pos(raw)
        if decision.canonical is not None:
            assert decision.canonical in UPOS_ALLOWLIST
        for tag in decision.candidates:
            assert tag in UPOS_ALLOWLIST
        assert decision.evidence in EVIDENCE_VALUES

    def test_candidates_json_is_valid(
        self,
        raw: str,
        canonical: str | None,
        candidates: tuple[str, ...],
        evidence: str,
    ) -> None:
        decision = normalize_legacy_pos(raw)
        payload = json.loads(decision.candidates_json)
        assert isinstance(payload, list)
        assert len(payload) == len(decision.candidates)
        for item in payload:
            assert set(item) == {"upos", "confidence"}
            assert item["upos"] in UPOS_ALLOWLIST
            assert item["confidence"] == CANDIDATE_CONFIDENCE


class TestAmbiguityAndUnknowns:
    def test_ambiguous_rows_have_no_canonical(self) -> None:
        for raw in ("conjunction", "V, adjective", "poss", "adv & a", "conj"):
            decision = normalize_legacy_pos(raw)
            assert decision.canonical is None
            assert len(decision.candidates) >= 2

    def test_unknown_rows_emit_empty_candidates(self) -> None:
        for raw in ("pt par", "unknown", "prefix", "suffix", "vs"):
            decision = normalize_legacy_pos(raw)
            assert decision.canonical is None
            assert decision.candidates == ()
            assert decision.evidence == "unknown"
            assert decision.candidates_json == "[]"

    def test_no_output_outside_allowlist_for_case_variants(self) -> None:
        for raw in ("NOUN", "NoUn", "nOuN", "verb", "VERB", "Adj", "ADV"):
            decision = normalize_legacy_pos(raw)
            assert decision.canonical in UPOS_ALLOWLIST


class TestEmptyInput:
    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
    def test_empty_is_untouched(self, raw: str | None) -> None:
        decision = normalize_legacy_pos(raw)
        assert decision.untouched is True
        assert decision.canonical is None
        assert decision.candidates == ()
        assert decision.evidence == "unknown"

    @pytest.mark.parametrize("raw", ["noun", "pt par"])
    def test_non_empty_is_not_untouched(self, raw: str) -> None:
        assert normalize_legacy_pos(raw).untouched is False

    def test_separator_only_input_has_no_decision(self) -> None:
        decision = normalize_legacy_pos(" , & ")
        assert decision.untouched is False
        assert decision.canonical is None
        assert decision.candidates == ()
        assert decision.evidence == "unknown"


class TestDecisionValidation:
    def test_rejects_non_upos_canonical(self) -> None:
        from zolai.data.pos_normalize import PosDecision

        with pytest.raises(ValueError):
            PosDecision(raw="x", canonical="NOUNS", candidates=(), evidence="inferred")

    def test_rejects_unknown_evidence(self) -> None:
        from zolai.data.pos_normalize import PosDecision

        with pytest.raises(ValueError):
            PosDecision(raw="x", canonical="NOUN", candidates=(), evidence="made-up")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
