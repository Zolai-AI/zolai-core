"""Unit tests for the offline evaluation metrics in ``zolai.eval.metrics``.

The metrics are dependency-free approximations used for regression gating, so
these tests verify *relative* behaviour on tiny known inputs rather than exact
reference-scorer parity: identical candidates must score highest, clean ZVS
input must score ``1.0``, and missing gold terms must lower QA recall.
"""

from __future__ import annotations

from zolai.eval.metrics import (
    qa_term_recall,
    translation_bleu,
    translation_chrf,
    zvs_compliance_rate,
)

#: A ZVS-clean sentence (``gam``, not the forbidden ``ram``).
_CLEAN = "Mihingte in gam a nual tuan hi."
#: A sentence carrying a ZVS violation (``ram`` -> ``gam``, unseeded).
_VIOLATION = "Mihingte in ram a nual tuan hi."


def test_zvs_compliance_is_one_on_clean_input() -> None:
    texts = [_CLEAN, "Pasian in van leh lei a phuak hi.", "Ni a chuak hi."]
    assert zvs_compliance_rate(texts) == 1.0


def test_zvs_compliance_drops_when_violation_present() -> None:
    texts = [_CLEAN, _VIOLATION]
    assert zvs_compliance_rate(texts) < 1.0


def test_zvs_compliance_zero_on_empty() -> None:
    assert zvs_compliance_rate([]) == 0.0


def test_bleu_identical_higher_than_different() -> None:
    identical = translation_bleu(["Ni a chuak hi."], ["Ni a chuak hi."])
    different = translation_bleu(["Ni a chuak hi."], ["Mi khat a uk hi."])
    assert identical > different
    assert identical == 1.0


def test_chrf_identical_higher_than_different() -> None:
    identical = translation_chrf(["Ni a chuak hi."], ["Ni a chuak hi."])
    different = translation_chrf(["Ni a chuak hi."], ["Mi khat a uk hi."])
    assert identical > different
    assert identical == 1.0


def test_qa_recall_one_when_all_gold_terms_present() -> None:
    score = qa_term_recall(["Pasian in phuak hi."], ["Pasian"])
    assert score == 1.0


def test_qa_recall_lower_when_gold_term_missing() -> None:
    score = qa_term_recall(["Skul ah a pai hi."], ["Pasian"])
    assert score < 1.0
