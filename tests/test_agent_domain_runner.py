from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from zolai.agent import store
from zolai.agent.domain.registry import get_domain_registry
from zolai.agent.domain.runner import run_domain_agent, run_domain_team
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import run_all_migrations


@pytest.fixture()
def data_db(tmp_path, monkeypatch) -> Iterator[Path]:
    db_path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    run_all_migrations(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    yield db_path
    mgr.dispose()


def test_manual_mode_returns_plan(data_db: Path) -> None:
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    result = run_domain_agent(agent_id, mode="manual", input_data={"task": "smoke"}, dry_run=True, scope="admin",
        requested_by="test")
    assert result["status"] == "manual"
    assert result["output"]["mode"] == "manual"
    assert result["output"]["dry_run"] is True


def test_unknown_agent_not_found(data_db: Path) -> None:
    result = run_domain_agent("no-such-agent", mode="manual", input_data={})
    assert result["status"] == "not_found"


def test_team_run_dry_run_bounded(data_db: Path) -> None:
    result = run_domain_team("data", input_data={"member_mode": "manual"}, dry_run=True, scope="admin",
        requested_by="test")
    assert result["status"] in {"succeeded", "failed"}
    assert result["output"]["team_id"] == "data"
    assert isinstance(result["output"]["child_runs"], list)
    assert result["output"]["count"] == len(result["output"]["child_runs"])
    # bounded by MAX_TEAM_MEMBERS; data team has 8 members
    assert result["output"]["count"] <= 12


def test_domain_run_events_recorded(data_db: Path) -> None:
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    result = run_domain_agent(agent_id, mode="manual", input_data={"task": "events"}, dry_run=True)
    events = store.list_domain_runs(limit=5)
    assert events
    run = store.get_domain_run(int(result["id"]))
    assert run is not None
