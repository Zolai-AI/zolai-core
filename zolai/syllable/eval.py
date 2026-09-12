"""Evaluation metrics for syllable segmentation.

Provides boundary-level precision/recall/F1, syllable accuracy,
word accuracy, and confusion matrix computation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

# --- Boundary extraction ----------------------------------------------------

def _extract_boundaries(syllables: list[str]) -> set[int]:
    """Extract boundary positions from syllable list.

    Boundaries are the cumulative lengths (end of each syllable
    except the last).

    Args:
        syllables: List of syllable strings.

    Returns:
        Set of boundary positions (character indices).
    """
    boundaries: set[int] = set()
    pos = 0
    for i, syl in enumerate(syllables):
        pos += len(syl)
        if i < len(syllables) - 1:
            boundaries.add(pos)
    return boundaries


# --- Metrics ----------------------------------------------------------------

def boundary_precision(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> float:
    """Compute boundary precision across multiple words.

    Precision = correct_predicted / total_predicted.

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        Precision score (0.0 to 1.0).
    """
    total_pred = 0
    correct = 0
    for pred, gld in zip(predicted, gold):
        pred_b = _extract_boundaries(pred)
        gold_b = _extract_boundaries(gld)
        total_pred += len(pred_b)
        correct += len(pred_b & gold_b)
    return correct / total_pred if total_pred > 0 else 0.0


def boundary_recall(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> float:
    """Compute boundary recall across multiple words.

    Recall = correct_predicted / total_gold.

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        Recall score (0.0 to 1.0).
    """
    total_gold = 0
    correct = 0
    for pred, gld in zip(predicted, gold):
        pred_b = _extract_boundaries(pred)
        gold_b = _extract_boundaries(gld)
        total_gold += len(gold_b)
        correct += len(pred_b & gold_b)
    return correct / total_gold if total_gold > 0 else 0.0


def boundary_f1(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> float:
    """Compute boundary F1 score.

    F1 = 2 * P * R / (P + R).

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        F1 score (0.0 to 1.0).
    """
    p = boundary_precision(predicted, gold)
    r = boundary_recall(predicted, gold)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def syllable_accuracy(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> float:
    """Compute syllable-level accuracy.

    Counts individual syllable matches by position.

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        Accuracy score (0.0 to 1.0).
    """
    correct = 0
    total = 0
    for pred, gld in zip(predicted, gold):
        max_len = max(len(pred), len(gld))
        for i in range(max_len):
            total += 1
            if i < len(pred) and i < len(gld):
                if pred[i] == gld[i]:
                    correct += 1
    return correct / total if total > 0 else 0.0


def word_accuracy(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> float:
    """Compute word-level accuracy (exact match).

    A word is correct only if ALL boundaries match exactly.

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        Accuracy score (0.0 to 1.0).
    """
    if not predicted:
        return 0.0
    correct = sum(
        1 for p, g in zip(predicted, gold) if p == g
    )
    return correct / len(predicted)


@dataclass(frozen=True, slots=True)
class ConfusionEntry:
    """A single entry in the boundary confusion matrix."""
    predicted: str
    gold: str
    count: int


def confusion_matrix(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> list[ConfusionEntry]:
    """Compute boundary confusion matrix.

    Compares boundary types (B/I) at each position.

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        List of ConfusionEntry with counts.
    """
    from .gold_dataset import word_to_bio

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for pred, gld in zip(predicted, gold):
        p_tags = word_to_bio("".join(pred), pred)
        g_tags = word_to_bio("".join(gld), gld)
        max_len = max(len(p_tags), len(g_tags))
        for i in range(max_len):
            p_tag = p_tags[i] if i < len(p_tags) else "-"
            g_tag = g_tags[i] if i < len(g_tags) else "-"
            counts[(p_tag, g_tag)] += 1

    return [
        ConfusionEntry(predicted=p, gold=g, count=c)
        for (p, g), c in sorted(counts.items())
    ]


@dataclass
class EvalReport:
    """Complete evaluation report for syllable segmentation."""
    boundary_precision: float = 0.0
    boundary_recall: float = 0.0
    boundary_f1: float = 0.0
    syllable_acc: float = 0.0
    word_acc: float = 0.0
    total_words: int = 0
    confusion: list[ConfusionEntry] = field(
        default_factory=list
    )


def evaluate(
    predicted: list[list[str]],
    gold: list[list[str]],
) -> EvalReport:
    """Run full evaluation and return a report.

    Args:
        predicted: List of syllable lists (predictions).
        gold: List of syllable lists (gold standard).

    Returns:
        EvalReport with all metrics.
    """
    return EvalReport(
        boundary_precision=boundary_precision(predicted, gold),
        boundary_recall=boundary_recall(predicted, gold),
        boundary_f1=boundary_f1(predicted, gold),
        syllable_acc=syllable_accuracy(predicted, gold),
        word_acc=word_accuracy(predicted, gold),
        total_words=len(predicted),
        confusion=confusion_matrix(predicted, gold),
    )
