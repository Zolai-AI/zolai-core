"""``zolai learn``: JSON output, path reduction, and documented exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from zolai.cli.main import app
from zolai.data.migrations import (
    EVAL_CASES_DDL,
    EVAL_RUNS_DDL,
    EVAL_SETS_DDL,
    KNOWLEDGE_VERSIONS_DDL,
    create_learning_run_tables,
)
from zolai.data.models import DataAuditLog

DDL = """
CREATE TABLE knowledge_vectors (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    metadata TEXT,
    embedding TEXT,
    source_type TEXT,
    source TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);
CREATE TABLE phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT,
    english TEXT,
    frequency INTEGER DEFAULT 1,
    examples TEXT DEFAULT '[]'
);
"""

runner = CliRunner()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "cli.db"
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:
        for ddl in (KNOWLEDGE_VERSIONS_DDL, EVAL_SETS_DDL, EVAL_CASES_DDL, EVAL_RUNS_DDL):
            conn.execute(text(ddl))
        for statement in DDL.split(";"):
            if statement.strip():
                conn.execute(text(statement))
    DataAuditLog.__table__.create(eng, checkfirst=True)
    create_learning_run_tables(eng)
    eng.dispose()
    return path


def _invoke(*args: str):
    return runner.invoke(app, ["learn", *args])


def _json(result) -> dict:
    return json.loads(result.output)


def test_run_dry_run_prints_json_and_writes_report(tmp_path: Path, db: Path) -> None:
    result = _invoke(
        "run",
        "--db",
        str(db),
        "--allow-small-db",
        "--min-free-gb",
        "0",
        "--report-dir",
        str(tmp_path / "reports"),
        "--steps",
        "preflight,study",
        "--json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["status"] == "SUCCEEDED"
    assert payload["mode"] == "dry_run"
    assert payload["version_tag"] is None
    assert str(tmp_path) not in result.output
    assert (tmp_path / "reports" / f"learning_run_{payload['run_id']}.json").exists()


def test_apply_and_dry_run_together_is_a_config_error(db: Path) -> None:
    result = _invoke("run", "--db", str(db), "--apply", "--dry-run", "--allow-small-db")
    assert result.exit_code == 2
    assert _json(result)["status"] == "error"


def test_missing_database_is_a_config_error(tmp_path: Path) -> None:
    result = _invoke(
        "run",
        "--db",
        str(tmp_path / "absent.db"),
        "--allow-small-db",
        "--min-free-gb",
        "0",
        "--report-dir",
        str(tmp_path / "reports"),
        "--steps",
        "preflight",
    )
    assert result.exit_code == 2
    body = _json(result)
    assert body["error_class"] == "PreflightError"
    assert str(tmp_path) not in result.output


def test_apply_snapshot_then_versions_and_verify(tmp_path: Path, db: Path) -> None:
    run = _invoke(
        "run",
        "--db",
        str(db),
        "--apply",
        "--allow-small-db",
        "--min-free-gb",
        "0",
        "--counts-manifest",
        "--report-dir",
        str(tmp_path / "reports"),
        "--backup-dir",
        str(tmp_path / "backups"),
        "--steps",
        "preflight,backup,version_snapshot",
    )
    assert run.exit_code == 0, run.output
    tag = _json(run)["version_tag"]
    assert tag
    assert _json(run)["notice"]

    listed = _invoke("versions", "--db", str(db), "--limit", "5")
    assert listed.exit_code == 0, listed.output
    versions = _json(listed)["versions"]
    assert versions[0]["version"] == tag
    assert versions[0]["learning_run"]
    assert str(tmp_path) not in listed.output

    ok = _invoke("verify", tag, "--db", str(db))
    assert ok.exit_code == 0, ok.output
    assert _json(ok)["ok"] is True

    # A row written after the snapshot is drift, not success.
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO phrases (zolai, english) VALUES ('lungdam', 'thanks')"))
    eng.dispose()

    drift = _invoke("verify", tag, "--db", str(db))
    assert drift.exit_code == 3
    assert _json(drift)["ok"] is False
    assert "phrases" in _json(drift)["drift"]


def test_verify_unknown_version_is_a_config_error(db: Path) -> None:
    result = _invoke("verify", "v2026.01.01.9", "--db", str(db))
    assert result.exit_code == 2
    assert _json(result)["error"]


def test_status_reports_history_and_accepts_human_output(tmp_path: Path, db: Path) -> None:
    _invoke(
        "run",
        "--db",
        str(db),
        "--allow-small-db",
        "--min-free-gb",
        "0",
        "--record-dry-run",
        "--report-dir",
        str(tmp_path / "reports"),
        "--steps",
        "preflight,study",
    )
    result = _invoke("status", "--db", str(db), "--limit", "5")
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["run"]["mode"] == "dry_run"
    assert payload["run"]["status"] == "SUCCEEDED"
    assert len(payload["history"]) == 1

    human = _invoke("status", "--db", str(db), "--no-json")
    assert human.exit_code == 0
    assert "SUCCEEDED" in human.output
