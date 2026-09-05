"""Pure-Python offline evaluation metrics for Zolai outputs.

All four metrics are dependency-free approximations, suitable for regression
gates in CI (exit non-zero when quality drops). They intentionally do not
replicate reference implementations taken from published work (e.g. sacrebleu);
use a proper reference scorer for research-grade numbers.

Each metric operates on ``(hyps, refs)``: ``hyps`` is the list of candidate /
model-generated strings and ``refs`` the corresponding gold reference strings
(zis ``(hyps, refs)`` pairs are aligned by index).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ..zvs import validate

_TOKEN_RE = re.compile(r"[^\s]+")


def _tokens(text: str) -> list[str]:
    """Lower-case whitespace tokens of a string."""
    return _TOKEN_RE.findall(text.lower())


def _ngrams(tokens: list[str], n: int) -> Counter:
    """Count word n-grams of the given order."""
    size = max(0, len(tokens) - n + 1)
    return Counter(tuple(tokens[i : i + n]) for i in range(size))


def zvs_compliance_rate(hyps: list[str]) -> float:
    """Fraction of outputs that pass the ZVS 2018 validator.

    Uses the real :func:`zolai.zvs.validate` engine, so regressions in
    orthography (dialect, compound and stem rules) are caught automatically.

    Args:
        hyps: Candidate output strings to validate.

    Returns:
        A fraction in ``[0.0, 1.0]`` (``1.0`` when every output is valid).
    """
    if not hyps:
        return 0.0
    valid = sum(1 for text in hyps if validate(text).is_valid)
    return valid / len(hyps)


def translation_bleu(hyps: list[str], refs: list[str]) -> float:
    """Smoothed word-overlap translation score (1-4 gram, brevity penalty).

    .. note::
        This is an approximation of corpus BLEU, not a precise re-implementation
        of a published reference scorer. It adds +1 smoothing to each n-gram
        order's precision and combines order scores with an arithmetic mean of
        log-precisions before applying the brevity penalty. It is designed to
        be *relative*: identical candidates score ``1.0``, and drift scores
        lower, which is enough to gate gross regressions.

    Args:
        hyps: Candidate translations.
        refs: Reference translations, aligned by index with ``hyps``.

    Returns:
        A smoothed BLEU-ish score in ``[0.0, 1.0]``.
    """
    if not hyps:
        return 0.0
    scores: list[float] = []
    for hyp, ref in zip(hyps, refs):
        hyp_tokens = _tokens(hyp)
        ref_tokens = _tokens(ref)
        if not hyp_tokens:
            scores.append(0.0)
            continue
        log_precisions: list[float] = []
        for n in range(1, 5):
            hyp_counts = _ngrams(hyp_tokens, n)
            ref_counts = _ngrams(ref_tokens, n)
            total = len(hyp_tokens) - n + 1
            if total <= 0:
                log_precisions.append(math.log(1.0))
                continue
            clipped = sum(min(hyp_counts[g], ref_counts[g]) for g in hyp_counts)
            match_p = (clipped + 1.0) / (total + 1.0)
            log_precisions.append(math.log(match_p))
        precision = math.exp(sum(log_precisions) / 4.0)
        if len(hyp_tokens) > len(ref_tokens):
            brevity = 1.0
        else:
            brevity = math.exp(1.0 - len(ref_tokens) / len(hyp_tokens))
        scores.append(precision * brevity)
    return sum(scores) / len(scores)


def _char_ngrams(text: str, n: int) -> Counter:
    """Count character n-grams of the given order (spaces preserved)."""
    lowered = text.lower()
    size = max(0, len(lowered) - n + 1)
    return Counter(lowered[i : i + n] for i in range(size))


def translation_chrf(hyps: list[str], refs: list[str]) -> float:
    """Character n-gram F-score translation metric.

    .. note::
        An approximation of the character n-gram F metric (orders 1-6, recall
        weighted with ``beta = 3`` to match the classic preference), not a
        faithful re-implementation of a published reference scorer. Use for
        regression gating only.

    Args:
        hyps: Candidate translations.
        refs: Reference translations, aligned by index with ``hyps``.

    Returns:
        A character-level F-score in ``[0.0, 1.0]``.
    """
    if not hyps:
        return 0.0
    beta = 3.0
    beta2 = beta * beta
    f_scores: list[float] = []
    for hyp, ref in zip(hyps, refs):
        precisions: list[float] = []
        recalls: list[float] = []
        for n in range(1, 7):
            hyp_counts = _char_ngrams(hyp, n)
            ref_counts = _char_ngrams(ref, n)
            hyp_total = sum(hyp_counts.values())
            if hyp_total == 0:
                precisions.append(0.0)
                recalls.append(0.0)
                continue
            matched = sum(min(hyp_counts[g], ref_counts[g]) for g in hyp_counts)
            precisions.append(matched / hyp_total)
            ref_total = sum(ref_counts.values())
            recalls.append(matched / ref_total if ref_total else 0.0)
        if not precisions:
            f_scores.append(0.0)
            continue
        precision = sum(precisions) / len(precisions)
        recall = sum(recalls) / len(recalls)
        if precision + recall == 0.0:
            f_scores.append(0.0)
            continue
        f_scores.append((1.0 + beta2) * precision * recall / (beta2 * precision + recall))
    return sum(f_scores) / len(f_scores)


def qa_term_recall(hyps: list[str], refs: list[str]) -> float:
    """Proportion of gold answer terms present in the model's answer.

    Rater: the gold answer (``refs``) is split into lower-case terms and the
    metric is the fraction of those terms found (as whole words, any position)
    in the hypothesis (``hyps``). Returns the mean over all items, so ``1.0``
    when every gold term appears in every answer.

    Args:
        hyps: Model question-answering outputs.
        refs: Gold answer strings, aligned by index with ``hyps``.

    Returns:
        A recall fraction in ``[0.0, 1.0]``.
    """
    if not hyps:
        return 0.0
    recalls: list[float] = []
    for hyp, ref in zip(hyps, refs):
        hyp_words = set(_tokens(hyp))
        ref_words = _tokens(ref)
        if not ref_words:
            recalls.append(0.0)
            continue
        matched = sum(1 for word in ref_words if word in hyp_words)
        recalls.append(matched / len(ref_words))
    return sum(recalls) / len(recalls)
