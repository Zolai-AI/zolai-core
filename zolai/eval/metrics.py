"""Evaluation metrics for Zolai outputs.

Two families live here, because the two CLIs score different things:

- ``compute_*_metrics`` — ZolaiBench v0.1 task metrics (tokenization, POS,
  morphology, grammar) over aligned token/tag lists.
- ``zvs_compliance_rate`` / ``translation_bleu`` / ``translation_chrf`` /
  ``qa_term_recall`` — the dependency-free lane scores that
  :func:`zolai.eval.evaluate` gates releases against.

The lane metrics are pure-Python approximations, not re-implementations of a
published reference scorer; use a proper scorer for research-grade numbers.
They are deliberately conservative: a wrong metric that looks real is worse
than a missing one, because ``below_floor()`` blocks releases on them.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..zvs import validate

_TOKEN_RE = re.compile(r"[^\s]+")


@dataclass
class TokenizationMetrics:
    """Tokenization evaluation metrics."""
    precision: float
    recall: float
    f1: float
    exact_match: float  # Sentence-level exact match

    def to_dict(self) -> dict[str, float]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "exact_match": self.exact_match,
        }


@dataclass
class POSMetrics:
    """POS tagging evaluation metrics."""
    accuracy: float  # Token-level accuracy
    macro_f1: float  # Macro-averaged F1 across POS tags
    per_tag_f1: dict[str, float]  # F1 per POS tag
    confusion_matrix: dict[str, dict[str, int]]  # gold -> pred counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "per_tag_f1": self.per_tag_f1,
            "confusion_matrix": self.confusion_matrix,
        }


@dataclass
class MorphologyMetrics:
    """Morphological segmentation evaluation metrics."""
    exact_match: float  # Word-level exact segmentation match
    boundary_precision: float
    boundary_recall: float
    boundary_f1: float
    feature_accuracy: float  # Morphological feature accuracy

    def to_dict(self) -> dict[str, float]:
        return {
            "exact_match": self.exact_match,
            "boundary_precision": self.boundary_precision,
            "boundary_recall": self.boundary_recall,
            "boundary_f1": self.boundary_f1,
            "feature_accuracy": self.feature_accuracy,
        }


@dataclass
class GrammarMetrics:
    """Grammar error detection evaluation metrics."""
    error_precision: float
    error_recall: float
    error_f1: float
    error_type_precision: dict[str, float]
    error_type_recall: dict[str, float]
    error_type_f1: dict[str, float]
    sentence_accuracy: float  # Sentence-level correct/error classification

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_precision": self.error_precision,
            "error_recall": self.error_recall,
            "error_f1": self.error_f1,
            "error_type_precision": self.error_type_precision,
            "error_type_recall": self.error_type_recall,
            "error_type_f1": self.error_type_f1,
            "sentence_accuracy": self.sentence_accuracy,
        }


def compute_tokenization_metrics(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> TokenizationMetrics:
    """Compute tokenization metrics (boundary-based)."""
    # Convert to boundary positions
    def to_boundaries(tokens: list[str]) -> set[int]:
        boundaries = set()
        pos = 0
        for tok in tokens[:-1]:  # Don't include end boundary
            pos += len(tok)
            boundaries.add(pos)
        return boundaries

    total_prec = 0.0
    total_rec = 0.0
    total_f1 = 0.0
    exact_matches = 0

    for pred_tokens, gold_tokens in zip(predicted, gold):
        pred_bounds = to_boundaries(pred_tokens)
        gold_bounds = to_boundaries(gold_tokens)

        if pred_bounds == gold_bounds:
            exact_matches += 1
            total_prec += 1.0
            total_rec += 1.0
            total_f1 += 1.0
        else:
            tp = len(pred_bounds & gold_bounds)
            fp = len(pred_bounds - gold_bounds)
            fn = len(gold_bounds - pred_bounds)

            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

            total_prec += prec
            total_rec += rec
            total_f1 += f1

    n = len(predicted)
    return TokenizationMetrics(
        precision=total_prec / n if n > 0 else 0.0,
        recall=total_rec / n if n > 0 else 0.0,
        f1=total_f1 / n if n > 0 else 0.0,
        exact_match=exact_matches / n if n > 0 else 0.0,
    )


def compute_pos_metrics(
    predicted: list[list[str]],
    gold: list[list[str]],
    tagset: list[str] | None = None,
) -> POSMetrics:
    """Compute POS tagging metrics."""
    # Flatten for token-level accuracy
    all_pred = [p for sent in predicted for p in sent]
    all_gold = [g for sent in gold for g in sent]

    correct = sum(1 for p, g in zip(all_pred, all_gold) if p == g)
    accuracy = correct / len(all_gold) if all_gold else 0.0

    # Per-tag F1
    if tagset is None:
        tagset = sorted(set(all_gold) | set(all_pred))

    per_tag_f1 = {}
    confusion: dict[str, Counter[str]] = {tag: Counter() for tag in tagset}

    for p, g in zip(all_pred, all_gold):
        confusion[g][p] += 1

    for tag in tagset:
        tp = confusion[tag].get(tag, 0)
        fp = sum(c.get(tag, 0) for t, c in confusion.items() if t != tag)
        fn = sum(confusion[tag].values()) - tp

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        per_tag_f1[tag] = f1

    macro_f1 = sum(per_tag_f1.values()) / len(per_tag_f1) if per_tag_f1 else 0.0

    # Convert confusion to regular dict
    confusion_dict = {k: dict(v) for k, v in confusion.items()}

    return POSMetrics(
        accuracy=accuracy,
        macro_f1=macro_f1,
        per_tag_f1=per_tag_f1,
        confusion_matrix=confusion_dict,
    )


def compute_morphology_metrics(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> TokenizationMetrics:
    """Compute morphological segmentation metrics (boundary-based).

    Segmentation gold carries no morphological features, so boundary numbers are
    the whole result and ``MorphologyMetrics.feature_accuracy`` stays unmeasured.
    """
    return compute_tokenization_metrics(predicted, gold)  # Same boundary logic


def compute_grammar_metrics(
    predicted_errors: list[list[dict]],
    gold_errors: list[list[dict]],
    all_error_types: list[str] | None = None,
) -> GrammarMetrics:
    """Compute grammar error detection metrics."""
    # Flatten all errors
    all_pred_errors = [e for sent in predicted_errors for e in sent]
    all_gold_errors = [e for sent in gold_errors for e in sent]

    # Error type set
    if all_error_types is None:
        all_error_types = sorted(set(
            e.get("type", "unknown") for e in all_pred_errors + all_gold_errors
        ))

    # Binary classification per sentence: has_error / no_error
    pred_has_error = [len(sent) > 0 for sent in predicted_errors]
    gold_has_error = [len(sent) > 0 for sent in gold_errors]

    tp_sent = sum(1 for p, g in zip(pred_has_error, gold_has_error) if p and g)
    fp_sent = sum(1 for p, g in zip(pred_has_error, gold_has_error) if p and not g)
    fn_sent = sum(1 for p, g in zip(pred_has_error, gold_has_error) if not p and g)

    sent_prec = tp_sent / (tp_sent + fp_sent) if (tp_sent + fp_sent) > 0 else 0.0
    sent_rec = tp_sent / (tp_sent + fn_sent) if (tp_sent + fn_sent) > 0 else 0.0
    sent_f1 = 2 * sent_prec * sent_rec / (sent_prec + sent_rec) if (sent_prec + sent_rec) > 0 else 0.0
    sent_acc = (
            sum(1 for p, g in zip(pred_has_error, gold_has_error) if p == g)
            / len(gold_has_error)
            if gold_has_error
            else 0.0
        )

    # Per error type
    type_prec = {}
    type_rec = {}
    type_f1 = {}

    for etype in all_error_types:
        pred_of_type = [e for e in all_pred_errors if e.get("type") == etype]
        gold_of_type = [e for e in all_gold_errors if e.get("type") == etype]

        # Simple span-based matching (could be improved)
        tp = min(len(pred_of_type), len(gold_of_type))
        fp = len(pred_of_type) - tp
        fn = len(gold_of_type) - tp

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

        type_prec[etype] = prec
        type_rec[etype] = rec
        type_f1[etype] = f1

    return GrammarMetrics(
        error_precision=sent_prec,
        error_recall=sent_rec,
        error_f1=sent_f1,
        error_type_precision=type_prec,
        error_type_recall=type_rec,
        error_type_f1=type_f1,
        sentence_accuracy=sent_acc,
    )


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
        An approximation of corpus BLEU, not a precise re-implementation of a
        published reference scorer: ``+1`` smoothing per n-gram order, an
        arithmetic mean of the log-precisions, then the brevity penalty. It is
        meant to be *relative* — identical candidates score ``1.0`` and drift
        scores lower, which is enough to gate gross regressions.

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

    The gold answer (``refs``) is split into lower-case terms and the metric is
    the fraction of those terms found (as whole words, any position) in the
    hypothesis (``hyps``), averaged over items — ``1.0`` when every gold term
    appears in every answer.

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
