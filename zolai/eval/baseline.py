"""Regression baseline handling for evaluation gates.

A baseline is a small JSON file mapping metric name to a minimum acceptable
score::

    {
        "zvs_compliance_rate": 0.95,
        "translation_bleu": 0.50,
        "translation_chrf": 0.60,
        "qa_term_recall": 0.85
    }

The gate logic lowers this to: a metric is a *regression* when its live score
on the same set is strictly below its recorded floor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_baseline(path: str | Path) -> dict[str, float]:
    """Load a baseline JSON mapping metric name -> minimum floor score."""
    with open(path, "r", encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return {str(key): float(value) for key, value in data.items()}


def below_floor(
    scores: dict[str, float], floors: dict[str, float]
) -> list[str]:
    """Return the sorted metric names whose live score is below the floor.

    A metric missing from ``scores`` counts as ``-1.0``, so a newly-introduced
    floor with no score trips the gate rather than silently passing.

    Args:
        scores: Live metric scores.
        floors: Baseline minimum scores.

    Returns:
        Sorted list of metric names that regressed below their floor.
    """
    return sorted(
        name
        for name, floor in floors.items()
        if scores.get(name, -1.0) < floor
    )
