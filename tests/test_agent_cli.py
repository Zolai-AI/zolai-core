"""P3 — ``zolai agent`` CLI (run / list / show), offline under the socket guard.

The CLI is the non-API surface for the same rule-mode pipeline: one run
completes with zero network, ``list`` and ``show`` read back the persisted
row, unknown ids exit 1.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_agent_runs import _SEED_SQL
from test_engine_contract import network_blocked
from typer.testing import CliRunner

from zolai.cli.main import app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_ai_provider_tables

runner = CliRunner()


@pytest.fixture()
def cli_db(tmp_path, monkeypatch) -> Iterator[Path]:
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_ai_provider_tables(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
    yield db_path
    mgr.dispose()


class TestAgentCli:
    def test_run_completes_offline_and_persists(self, cli_db) -> None:
        with network_blocked() as attempts:
            result = runner.invoke(app, ["agent", "run", "summarize what pasian means"])
        assert attempts == [], f"CLI run attempted network: {attempts}"
        assert result.exit_code == 0, result.output
        assert "succeeded" in result.output
        conn = sqlite3.connect(cli_db)
        row = conn.execute("SELECT status, mode, goal FROM agent_runs").fetchone()
        conn.close()
        assert row == ("succeeded", "rule", "summarize what pasian means")

    def test_list_and_show_read_back_the_run(self, cli_db) -> None:
        created = runner.invoke(app, ["agent", "run", "define gam"])
        assert created.exit_code == 0, created.output

        listed = runner.invoke(app, ["agent", "list"])
        assert listed.exit_code == 0
        assert "define gam" in listed.output

        shown = runner.invoke(app, ["agent", "show", "1"])
        assert shown.exit_code == 0, shown.output
        assert "phases" in shown.output
        assert "define gam" in shown.output

    def test_show_unknown_run_exits_1(self, cli_db) -> None:
        result = runner.invoke(app, ["agent", "show", "999999"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_list_empty_store_is_friendly(self, cli_db) -> None:
        result = runner.invoke(app, ["agent", "list"])
        assert result.exit_code == 0
        assert "no agent runs yet" in result.output
