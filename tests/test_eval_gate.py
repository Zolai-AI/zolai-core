"""Tests for the regression baseline gate (``zolai.eval.baseline``).

Covers the floor-checking helper and the CLI gate behaviour directly against
the bundled smoke set: the committed smoke floors must pass, an injected floor
above a measured score must fail (exit 1), a missing baseline under ``--gate``
must be a hard error, and baseline JSON must round-trip through
:func:`zolai.eval.load_baseline`.
"""

from __future__ import annotations

import json
from pathlib import Path

from zolai.eval import load_baseline
from zolai.eval.baseline import below_floor
from zolai.eval.cli import main as cli_main

#: Committed conservative floors (match ``report/eval-baseline.json``).
_FLOORS: dict[str, float] = {
    "zvs_compliance_rate": 0.95,
    "translation_bleu": 0.50,
    "translation_chrf": 0.60,
    "qa_term_recall": 0.85,
}


def _scores_above_floors() -> dict[str, float]:
    return {name: floor + 0.1 for name, floor in _FLOORS.items()}


def _write_baseline(path: Path, floors: dict[str, float]) -> Path:
    path.write_text(json.dumps(floors), encoding="utf-8")
    return path


def test_below_floor_none_when_above() -> None:
    assert below_floor(_scores_above_floors(), _FLOORS) == []


def test_below_floor_reports_bad_metric() -> None:
    scores = _scores_above_floors()
    scores["translation_bleu"] = _FLOORS["translation_bleu"] - 0.1
    assert below_floor(scores, _FLOORS) == ["translation_bleu"]


def test_missing_score_counts_as_regression() -> None:
    scores = {name: fl + 0.1 for name, fl in _FLOORS.items() if name != "qa_term_recall"}
    assert below_floor(scores, _FLOORS) == ["qa_term_recall"]


def test_baseline_json_round_trip(tmp_path: Path) -> None:
    baseline = _write_baseline(tmp_path / "baseline.json", _FLOORS)
    loaded = load_baseline(baseline)
    assert loaded == _FLOORS


def test_gate_passes_when_scores_meet_floors(tmp_path: Path) -> None:
    baseline = _write_baseline(tmp_path / "baseline.json", _FLOORS)
    # Smoke set measures: zvs=1.0, bleu=1.0, chrf=1.0, qa~0.96 — all above
    # the committed conservative floors, so the gate must exit 0.
    assert cli_main(["--set", "smoke", "--baseline", str(baseline), "--gate"]) == 0


def test_gate_fails_when_metric_injected_below_floor(tmp_path: Path) -> None:
    floors = dict(_FLOORS)
    floors["translation_bleu"] = 1.01  # above measured 1.0 -> forced regression
    baseline = _write_baseline(tmp_path / "baseline.json", floors)
    assert cli_main(["--set", "smoke", "--baseline", str(baseline), "--gate"]) == 1


def test_gate_requires_baseline() -> None:
    assert cli_main(["--set", "smoke", "--gate"]) == 2
