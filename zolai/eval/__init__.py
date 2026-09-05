"""Offline evaluation utilities for Zolai outputs.

This package measures output quality with dependency-free, pure-Python metrics
so it can run anywhere (including CI) with no model weights and no extra
dependencies::

    from zolai.eval import evaluate
    scores = evaluate(sets="smoke")

Model training is intentionally out of scope; this package only measures.
"""

from __future__ import annotations

from .baseline import below_floor, load_baseline
from .datasets import SMOKE, load_dataset, resolve_set
from .metrics import (
    qa_term_recall,
    translation_bleu,
    translation_chrf,
    zvs_compliance_rate,
)

__all__ = [
    "SMOKE",
    "below_floor",
    "evaluate",
    "load_baseline",
    "load_dataset",
    "qa_term_recall",
    "resolve_set",
    "translation_bleu",
    "translation_chrf",
    "zvs_compliance_rate",
]


def evaluate(sets: str = SMOKE, *, base_dir: str | None = None) -> dict[str, float]:
    """Evaluate a dataset set and return every available metric.

    Args:
        sets: ``"smoke"`` or a path/base prefix for loaders to resolve.
        base_dir: Optional override directory used for the ``smoke`` fixtures.

    Returns:
        A mapping of metric name to score. Only metrics for which a source file
        exists are computed (the smoke set produces all four).
    """
    data = load_dataset(sets, base_dir=base_dir)
    result: dict[str, float] = {}
    if "zvs" in data:
        result["zvs_compliance_rate"] = zvs_compliance_rate(data["zvs"])  # type: ignore[arg-type]
    if "translation" in data:
        hyps, refs = data["translation"]  # type: ignore[misc]
        result["translation_bleu"] = translation_bleu(hyps, refs)
        result["translation_chrf"] = translation_chrf(hyps, refs)
    if "qa" in data:
        hyps, refs = data["qa"]  # type: ignore[misc]
        result["qa_term_recall"] = qa_term_recall(hyps, refs)
    return result
