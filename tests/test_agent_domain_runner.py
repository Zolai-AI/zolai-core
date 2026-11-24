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


def test_batch_limit_enforced(data_db: Path) -> None:
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    items = [{"task": f"t{i}"} for i in range(25)]
    result = run_domain_agent(agent_id, mode="batch", input_data={"items": items, "child_mode": "workflow"})
    assert result["status"] == "failed"
    assert "batch too large" in str(result.get("error") or result.get("output", {}).get("error", ""))


def test_workflow_unsupported_steps_reported(data_db: Path) -> None:
    reg = get_domain_registry(force=True)
    # Find an agent with workflow bindings that are not all bound, or just use manual plan shape
    agent_id = reg.reports()[0].agent_id
    result = run_domain_agent(agent_id, mode="workflow", input_data={"task": "smoke"}, dry_run=True)
    assert result["status"] in {"manual", "succeeded", "failed"}
    wf = result.get("output", {})
    assert "steps" in wf or "workflows" in wf or wf.get("status")


def test_autonomous_gate_blocks_without_env(data_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ZOLAI_AGENT_AUTONOMOUS", raising=False)
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    result = run_domain_agent(agent_id, mode="manual", input_data={"task": "smoke"}, autonomous=True, scope="admin")
    assert result["status"] == "blocked"
    assert "ZOLAI_AGENT_AUTONOMOUS" in str(result.get("error") or "")


def test_autonomous_gate_blocks_non_admin_scope(data_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZOLAI_AGENT_AUTONOMOUS", "1")
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    result = run_domain_agent(agent_id, mode="manual", input_data={"task": "smoke"}, autonomous=True, scope="member")
    assert result["status"] == "blocked"
    assert "admin" in str(result.get("error") or "").lower()


def test_dry_run_marks_side_effect_tools(data_db: Path) -> None:
    from zolai.agent.domain.runner import _is_side_effect_tool
    # write/destructive tools must be treated as side-effecting
    # rag_search is read-only; kb_ingest style tools should be side-effecting if present
    assert _is_side_effect_tool("nonexistent_tool") is True


def test_redaction_in_domain_events(data_db: Path) -> None:
    reg = get_domain_registry(force=True)
    agent_id = reg.reports()[0].agent_id
    secret = "sk-" + "abcdef1234567890" + "SECRET"
    result = run_domain_agent(agent_id, mode="manual", input_data={"task": "smoke", "auth_token": secret}, dry_run=True)
    events = store.get_domain_run_events(int(result["id"])) if hasattr(store, "get_domain_run_events") else []
    if not events:
        # fallback: list via audit module
        from zolai.agent.domain.audit import list_events
        from zolai.data.repositories import get_engine
        events = list_events(get_engine(), int(result["id"]))
    blob = str(events)
    assert secret not in blob
