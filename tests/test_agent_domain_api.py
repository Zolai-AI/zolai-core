from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.agent_router import _reset_run_bucket
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import run_all_migrations


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    _reset_run_bucket()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    _reset_run_bucket()


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


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    from zolai.data.migrations import create_api_keys_table

    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def key() -> dict:
    return auth.create_api_key(name="domain", scopes=["agent:read", "agent:run"], created_by="test")


def _h(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def test_manifests_list_and_get(data_db: Path, auth_db: DatabaseManager, client: TestClient, key: dict) -> None:
    r = client.get("/api/v1/agent/manifests", headers=_h(key))
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["count"] == 36
    agent_id = body["items"][0]["agent_id"]
    r2 = client.get(f"/api/v1/agent/manifests/{agent_id}", headers=_h(key))
    assert r2.status_code == 200
    assert r2.json()["agent_id"] == agent_id


def test_teams_list_show_run(data_db: Path, auth_db: DatabaseManager, client: TestClient, key: dict) -> None:
    r = client.get("/api/v1/agent/teams", headers=_h(key))
    assert r.status_code == 200
    teams = r.json()["items"]
    assert any(t["team_id"] == "data" for t in teams)
    r2 = client.get("/api/v1/agent/teams/data", headers=_h(key))
    assert r2.status_code == 200
    r3 = client.post("/api/v1/agent/teams/data/run", headers=_h(key), json={"dry_run": True, "member_mode": "manual",
        "input": {}})
    assert r3.status_code == 200
    body = r3.json()
    assert body["output"]["team_id"] == "data"
    assert "child_runs" in body["output"]


def test_domain_run_create_and_events(data_db: Path, auth_db: DatabaseManager, client: TestClient, key: dict) -> None:
    manifests = client.get("/api/v1/agent/manifests", headers=_h(key)).json()["items"]
    agent_id = manifests[0]["agent_id"]
    r = client.post("/api/v1/agent/domain-runs", headers=_h(key), json={"agent_id": agent_id, "mode": "manual",
        "input": {"task": "smoke"}, "dry_run": True})
    assert r.status_code == 200
    run = r.json()
    run_id = run["id"]
    r2 = client.get(f"/api/v1/agent/domain-runs/{run_id}", headers=_h(key))
    assert r2.status_code == 200
    r3 = client.get(f"/api/v1/agent/domain-runs/{run_id}/events", headers=_h(key))
    assert r3.status_code == 200
    assert r3.json()["count"] >= 1


def test_existing_p3_runs_contract_still_works(data_db: Path, auth_db: DatabaseManager, client: TestClient, key: dict,
    monkeypatch) -> None:
    monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
    r = client.post(
        "/api/v1/agent/runs", headers=_h(key), json={"goal": "What is pasian?"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"succeeded", "failed"}
    r2 = client.get("/api/v1/agent/runs", headers=_h(key))
    assert r2.status_code == 200
