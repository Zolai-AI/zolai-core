from __future__ import annotations

from pathlib import Path

import pytest

from zolai.agent import store
from zolai.agent.domain.queue import _claim_one, enqueue_domain_run, process_one
from zolai.agent.domain.registry import get_domain_registry
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import run_all_migrations


@pytest.fixture()
def data_db(tmp_path, monkeypatch):
    db_path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    run_all_migrations(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    yield db_path
    mgr.dispose()


def _make_queued_run(mode="manual"):
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    run = store.create_domain_run(
        {
            "agent_id": agent_id,
            "mode": mode,
            "status": "queued",
            "input": {"task": "smoke"},
            "output": {"status": "queued"},
            "dry_run": True,
            "autonomous": True,
            "requested_by": "test",
            "scope": "admin",
        }
    )
    enqueue_domain_run(int(run["id"]))
    return run


def test_queue_claim_prevents_duplicate(data_db: Path) -> None:
    run = _make_queued_run()
    first = _claim_one("worker-a")
    second = _claim_one("worker-b")
    assert first is not None
    assert first["domain_run_id"] == int(run["id"])
    assert second is None


def test_queue_process_executes_in_place(data_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZOLAI_AGENT_AUTONOMOUS", "1")
    run = _make_queued_run()
    result = process_one("worker-test")
    assert result["processed"] is True
    assert result["domain_run_id"] == int(run["id"])
    updated = store.get_domain_run(int(run["id"]))
    assert updated is not None
    assert updated["status"] in {"manual", "succeeded", "failed"}
    # No duplicate run created
    runs = store.list_domain_runs(limit=20)
    assert len(runs) == 1


def test_queue_dead_letters_after_max_attempts(data_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZOLAI_AGENT_AUTONOMOUS", "1")
    get_domain_registry(force=True)
    run = store.create_domain_run(
        {
            "agent_id": "no-such-agent",
            "mode": "manual",
            "status": "queued",
            "input": {},
            "output": {},
            "dry_run": True,
            "autonomous": True,
            "requested_by": "test",
            "scope": "admin",
        }
    )
    enqueue_domain_run(int(run["id"]), max_attempts=1)
    result = process_one("worker-test")
    assert result["processed"] is True
    assert result["status"] in {"dead", "failed"}
