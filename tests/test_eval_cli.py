"""CLI integration tests for ``zolai.eval.cli``.

These run the real argparse entry point against the bundled smoke set. The
``--json`` output must contain all four metrics and the gate must run to
completion (exit 0) against the committed conservative floors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zolai.eval.cli import main as cli_main

_ALL_METRICS = {
    "zvs_compliance_rate",
    "translation_bleu",
    "translation_chrf",
    "qa_term_recall",
}

_FLOORS = {
    "zvs_compliance_rate": 0.95,
    "translation_bleu": 0.50,
    "translation_chrf": 0.60,
    "qa_term_recall": 0.85,
}


def test_json_output_contains_all_four_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["--set", "smoke", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["metrics"]) == _ALL_METRICS
    for name in _ALL_METRICS:
        assert 0.0 <= payload["metrics"][name] <= 1.0


def test_gate_runs_to_completion(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(_FLOORS), encoding="utf-8")
    assert (
        cli_main(["--set", "smoke", "--baseline", str(baseline), "--gate", "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["below_floor"] == []
    assert set(payload["floors"]) == _ALL_METRICS
