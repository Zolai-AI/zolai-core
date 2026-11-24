from __future__ import annotations

from pathlib import Path

from zolai.agent.domain.manifest import discover_manifest_files, load_manifest_file, load_manifest_index
from zolai.agent.domain.registry import get_domain_registry


def test_load_all_36_manifests() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    files = discover_manifest_files(repo_root / "agents")
    assert len(files) == 36
    manifests = [load_manifest_file(p) for p in files]
    ids = [m.agent_id for m in manifests]
    assert len(ids) == len(set(ids))
    assert all(m.agent_id for m in manifests)


def test_registry_reports_status_for_all_agents() -> None:
    reg = get_domain_registry(force=True)
    reports = reg.reports()
    assert len(reports) == 36
    statuses = {r.status.value for r in reports}
    assert statuses <= {"runnable", "manual", "unsupported"}
    for r in reports:
        d = r.to_dict()
        assert d["agent_id"]
        assert "tool_bindings" in d
        assert "workflow_bindings" in d


def test_teams_loaded_from_index() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    index = load_manifest_index(repo_root / "agents" / "zolai-agents.yaml")
    assert "teams" in index
    reg = get_domain_registry(force=True)
    teams = reg.teams()
    assert len(teams) >= 1
    data = reg.validate_team("data")
    assert data and data[0]["team_id"] == "data"
    assert "data-manager" in data[0]["agents"]
