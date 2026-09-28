"""Tests for the DB-first evaluation store (``zolai.eval.store``).

Covers schema creation, JSONL import/export round-trips, kind inference,
DB parity with the bundled JSONL fixtures, and the CLI ``--import`` /
``--export`` short-circuits. Every test runs against a throwaway SQLite file
via the ``ZOLAI_DB_PATH`` environment variable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from zolai.eval import evaluate, load_dataset, resolve_set
from zolai.eval.cli import main as cli_main
from zolai.eval.datasets import DbRef
from zolai.eval.store import (
    SETS_DIR,
    ensure_schema,
    export_set_to_jsonl,
    fetch_cases,
    import_jsonl_to_set,
    list_kinds,
    list_sets,
    seed_sets,
)

#: Every bundled JSONL interchange file (set name == file stem for round-trip).
FIXTURES = (
    "smoke_zvs",
    "smoke_qa",
    "smoke_translation",
    "eval_v1_zvs",
    "eval_v1_qa",
    "eval_v1_translation",
    "benchmark_qa",
)

#: Bundled smoke fixtures are 12 records per lane.
_SMOKE_PER_LANE = 12

#: Committed conservative floors (match ``report/eval-baseline.json``).
_FLOORS = {
    "zvs_compliance_rate": 0.95,
    "translation_bleu": 0.50,
    "translation_chrf": 0.60,
    "qa_term_recall": 0.85,
}


@pytest.fixture(autouse=True)
def _eval_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the store at a throwaway SQLite file for every test."""
    db = tmp_path / "eval.db"
    monkeypatch.setenv("ZOLAI_DB_PATH", str(db))
    return db


def _table_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}
    finally:
        conn.close()


def test_ensure_schema_creates_tables_idempotently(_eval_db: Path) -> None:
    assert ensure_schema() == _eval_db
    ensure_schema()  # second run must be a no-op
    assert {"eval_sets", "eval_cases"} <= _table_names(_eval_db)
    conn = sqlite3.connect(str(_eval_db))
    try:
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        conn.close()
    assert "idx_eval_cases_lookup" in indexes


def test_import_replaces_lane_and_tracks_counts(_eval_db: Path) -> None:
    assert import_jsonl_to_set(SETS_DIR / "smoke_zvs.jsonl", "smoke", "zvs") == _SMOKE_PER_LANE
    # Re-importing the same lane is idempotent (replace, not append).
    import_jsonl_to_set(SETS_DIR / "smoke_zvs.jsonl", "smoke", "zvs")
    assert len(fetch_cases("smoke", "zvs")) == _SMOKE_PER_LANE
    rows = list_sets()
    assert len(rows) == 1
    assert rows[0]["set_name"] == "smoke"
    assert rows[0]["case_count"] == _SMOKE_PER_LANE
    assert rows[0]["created_at"] and rows[0]["updated_at"]

    # A second lane joins the same set without disturbing the first.
    import_jsonl_to_set(SETS_DIR / "smoke_qa.jsonl", "smoke", "qa")
    assert list_kinds("smoke") == ["qa", "zvs"]
    assert len(fetch_cases("smoke")) == 2 * _SMOKE_PER_LANE
    assert list_sets()[0]["case_count"] == 2 * _SMOKE_PER_LANE


def test_payloads_stored_verbatim(_eval_db: Path) -> None:
    import_jsonl_to_set(SETS_DIR / "smoke_translation.jsonl", "smoke", "translation")
    first = json.loads((SETS_DIR / "smoke_translation.jsonl").read_text(encoding="utf-8").splitlines()[0])
    cases = fetch_cases("smoke", "translation")
    assert cases[0]["payload"] == first
    assert cases[0]["ordinal"] == 0
    assert [case["ordinal"] for case in cases] == list(range(len(cases)))


def test_kind_inferred_from_file_name(_eval_db: Path) -> None:
    assert import_jsonl_to_set(SETS_DIR / "eval_v1_zvs.jsonl", "inferred") == 40
    assert list_kinds("inferred") == ["zvs"]
    assert import_jsonl_to_set(SETS_DIR / "benchmark_qa.jsonl", "bench") == 127
    assert list_kinds("bench") == ["qa"]


@pytest.mark.parametrize("stem", FIXTURES)
def test_import_export_round_trip_is_byte_equivalent(_eval_db: Path, tmp_path: Path, stem: str) -> None:
    source = SETS_DIR / f"{stem}.jsonl"
    count = import_jsonl_to_set(source, stem)
    assert count > 0
    destination = tmp_path / f"{stem}.export.jsonl"
    assert export_set_to_jsonl(stem, destination) == count
    original = source.read_bytes()
    exported = destination.read_bytes()
    # Payloads round-trip byte-for-byte; only a missing final newline is normalised.
    expected = original if original.endswith(b"\n") else original + b"\n"
    assert exported == expected


def test_db_and_jsonl_loaders_produce_identical_metrics(_eval_db: Path) -> None:
    seed_sets(("smoke",))
    assert load_dataset("db:smoke") == load_dataset("smoke")
    assert evaluate(sets="db:smoke") == evaluate(sets="smoke")
    # Only smoke is seeded, so the merged DB spec resolves to the same metrics.
    assert evaluate(sets="db") == evaluate(sets="smoke")


def test_resolve_set_returns_db_refs(_eval_db: Path) -> None:
    seed_sets(("smoke", "eval_v1"))
    merged = resolve_set("db")
    assert set(merged) == {"zvs", "qa", "translation"}
    assert all(isinstance(source, DbRef) for source in merged.values())
    assert {source.set_name for source in merged.values()} == {"*"}
    named = resolve_set("db:eval_v1")
    assert set(named) == {"zvs", "qa", "translation"}
    assert named["qa"] == DbRef("eval_v1", "qa")
    # Unknown sets resolve to no source rather than raising.
    assert resolve_set("db:nope") == {}


def test_merged_db_skips_records_without_lane_fields(_eval_db: Path) -> None:
    seed_sets()
    data = load_dataset("db")
    # zvs: 40 (eval_v1) + 12 (smoke); benchmark_qa payloads carry no lane fields.
    assert len(data["zvs"]) == 52  # type: ignore[arg-type]
    hyps, refs = data["qa"]  # type: ignore[misc]
    assert len(hyps) == len(refs) == 52  # eval_v1 + smoke only
    assert len(data["translation"][0]) == 42  # type: ignore[index]


def test_cli_import_and_export(_eval_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["--import", str(SETS_DIR / "smoke_zvs.jsonl"), "--as", "smoke"]) == 0
    assert "imported 12 cases into set 'smoke'" in capsys.readouterr().out
    assert cli_main(["--import", str(SETS_DIR / "smoke_qa.jsonl"), "--as", "smoke"]) == 0
    capsys.readouterr()

    destination = _eval_db.parent / "smoke.jsonl"
    assert cli_main(["--set", "db:smoke", "--export", str(destination)]) == 0
    assert "exported 24 cases from set 'smoke'" in capsys.readouterr().out
    assert destination.read_bytes().endswith(b"\n")

    # Import/export short-circuit: no metrics were computed.
    assert cli_main(["--set", "smoke", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["metrics"]) >= {"zvs_compliance_rate", "qa_term_recall"}


def test_cli_import_requires_as_and_valid_file(_eval_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["--import", str(SETS_DIR / "smoke_zvs.jsonl")]) == 2
    assert "--as" in capsys.readouterr().err
    assert cli_main(["--import", str(_eval_db.parent / "missing.jsonl"), "--as", "smoke"]) == 2
    assert "error" in capsys.readouterr().err


def test_cli_export_requires_db_set(_eval_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["--set", "smoke", "--export", str(_eval_db.parent / "out.jsonl")]) == 2
    assert "db:" in capsys.readouterr().err
    assert cli_main(["--set", "db:missing", "--export", str(_eval_db.parent / "out.jsonl")]) == 2
    assert "no cases" in capsys.readouterr().err


def test_cli_gate_passes_on_db_set(_eval_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_sets(("smoke",))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(_FLOORS), encoding="utf-8")
    assert cli_main(["--set", "db:smoke", "--baseline", str(baseline), "--gate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["below_floor"] == []
    assert set(payload["metrics"]) == set(_FLOORS)
