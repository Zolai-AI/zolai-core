"""``zolai observation`` CLI smoke (Phase 2 §36, commit 5).

Covers the three registered subcommands and the ``build`` write contract:
``--limit``/``--sources`` selection writes ``observations`` +
``word_observation_stats`` rows, while ``--dry-run`` writes nothing.  The
heavy full-corpus build and the §27 index/Bloom builders are exercised by
``test_observation_pipeline`` / ``test_attestation_index`` instead — never in
CI (plan risk: full build = ~130k rows).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zolai.cli.main import app as cli_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_observation_tables
from zolai.data.models import BibleVerse

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """Throwaway ORM DB with two Zolai verses and the Phase 2 tables."""
    db_path = tmp_path / "cli_observations.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_observation_tables(mgr)
    with mgr.engine.begin() as conn:
        conn.execute(
            BibleVerse.__table__.insert(),
            [
                {
                    "id": 1,
                    "ref": "GEN 1:1",
                    "book": "GEN",
                    "chapter": 1,
                    "verse": 1,
                    "zo_tdb77": "Pasian in vantung leh leitung a piangsak hi.",
                    "zo_tedim2010": None,
                },
                {
                    "id": 2,
                    "ref": "GEN 1:2",
                    "book": "GEN",
                    "chapter": 1,
                    "verse": 2,
                    "zo_tdb77": "Gam ka lak hi.",
                    "zo_tedim2010": None,
                },
            ],
        )
    mgr.dispose()
    return db_path


def _json_payload(result) -> dict:
    text = _ANSI.sub("", result.stdout or "")
    return json.loads(text[text.index("{") :])


def _counts(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_group_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli_app, ["observation", "--help"])
    assert result.exit_code == 0, result.output
    for name in ("build", "refresh-index", "bloom"):
        assert name in result.output


def test_build_limit_writes_derived_rows(
    runner: CliRunner, seeded_db: Path
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "observation",
            "build",
            "--db",
            str(seeded_db),
            "--sources",
            "bible_tdb77",
            "--limit",
            "1",
            "--no-attest",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = _json_payload(result)
    assert summary["sources"] == ["bible_tdb77"]
    assert summary["limit"] == 1
    assert summary["observations"] == 1
    assert summary["observations_inserted"] == 1
    assert summary["stats_rows"] >= 1
    assert summary["migration"]["errors"] == []

    assert _counts(seeded_db, "observations") == 1
    assert _counts(seeded_db, "word_observation_stats") == summary["stats_rows"]


def test_build_is_idempotent_across_runs(runner: CliRunner, seeded_db: Path) -> None:
    args = [
        "observation",
        "build",
        "--db",
        str(seeded_db),
        "--sources",
        "bible_tdb77",
        "--limit",
        "2",
        "--no-attest",
        "--json",
    ]
    first = _json_payload(runner.invoke(cli_app, args))
    second = _json_payload(runner.invoke(cli_app, args))
    assert first["observations_inserted"] == 2
    assert second["observations_inserted"] == 0, "ux_obs_source_ref must dedupe"
    assert _counts(seeded_db, "observations") == 2


def test_build_dry_run_writes_nothing(runner: CliRunner, seeded_db: Path) -> None:
    result = runner.invoke(
        cli_app,
        [
            "observation",
            "build",
            "--db",
            str(seeded_db),
            "--sources",
            "bible_tdb77",
            "--limit",
            "10",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in _ANSI.sub("", result.output)
    assert _counts(seeded_db, "observations") == 0
    assert _counts(seeded_db, "word_observation_stats") == 0


def test_build_unknown_source_exits_1(runner: CliRunner, seeded_db: Path) -> None:
    result = runner.invoke(
        cli_app,
        [
            "observation",
            "build",
            "--db",
            str(seeded_db),
            "--sources",
            "not_a_source",
            "--no-attest",
        ],
    )
    assert result.exit_code == 1
    assert "unknown source" in _ANSI.sub("", result.output)
    assert _counts(seeded_db, "observations") == 0
