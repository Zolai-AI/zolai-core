from __future__ import annotations

import json

from typer.testing import CliRunner

from zolai.agent.cli import agent_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import run_all_migrations

runner = CliRunner()


def test_agents_list_json(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    run_all_migrations(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    res = runner.invoke(agent_app, ["agents", "list", "--json"])
    assert res.exit_code == 0
    body = json.loads(res.stdout)
    assert body["summary"]["count"] == 36
    mgr.dispose()


def test_teams_list_and_run_dry(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    run_all_migrations(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    res = runner.invoke(agent_app, ["teams", "list", "--json"])
    assert res.exit_code == 0
    body = json.loads(res.stdout)
    assert any(t["team_id"] == "data" for t in body["items"])
    res2 = runner.invoke(agent_app, ["teams", "run", "data", "--dry-run", "--member-mode", "manual", "--input", "{}"])
    assert res2.exit_code == 0
    mgr.dispose()
