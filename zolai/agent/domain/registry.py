"""Manifest registry: load/cache 36 manifests and report capability status."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...shared.clock import stamp
from .binder import bind_manifest
from .manifest import (
    AgentManifest,
    ManifestStatus,
    TeamManifest,
    discover_manifest_files,
    load_manifest_file,
    load_manifest_index,
    load_team_manifests,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentCapabilityReport:
    agent_id: str
    name: str
    status: ManifestStatus
    source_path: str
    tool_bindings: list[dict[str, Any]] = field(default_factory=list)
    workflow_bindings: list[dict[str, Any]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest_hash: str = ""
    loaded_at: str = ""

    @property
    def runnable(self) -> bool:
        return self.status == ManifestStatus.RUNNABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status.value,
            "runnable": self.runnable,
            "source_path": self.source_path,
            "tool_bindings": self.tool_bindings,
            "workflow_bindings": self.workflow_bindings,
            "validation_errors": self.validation_errors,
            "warnings": self.warnings,
            "manifest_hash": self.manifest_hash,
            "loaded_at": self.loaded_at,
        }


class DomainAgentRegistry:
    def __init__(self, agents_dir: str | Path | None = None) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.agents_dir = Path(agents_dir) if agents_dir else repo_root / "agents"
        self.index_path = self.agents_dir / "zolai-agents.yaml"
        self._manifests: dict[str, AgentManifest] = {}
        self._reports: dict[str, AgentCapabilityReport] = {}
        self._teams: dict[str, TeamManifest] = {}
        self._aliases: dict[str, str] = {}  # yaml key/name -> agent_id
        self._index: dict[str, Any] = {}
        self._loaded_at = ""

    @property
    def loaded_at(self) -> str:
        return self._loaded_at

    def load(self, *, force: bool = False) -> "DomainAgentRegistry":
        if self._manifests and not force:
            return self
        self._manifests = {}
        self._reports = {}
        self._index = load_manifest_index(self.index_path)
        self._aliases = self._build_aliases(self._index)
        for team in load_team_manifests(self._index, source_path=str(self.index_path)):
            self._teams[team.team_id] = team
        for path in discover_manifest_files(self.agents_dir):
            try:
                manifest = load_manifest_file(path)
            except Exception as exc:
                agent_id = path.parent.name
                logger.warning("manifest load failed: %s: %s", path, exc)
                self._reports[agent_id] = AgentCapabilityReport(
                    agent_id=agent_id,
                    name=agent_id,
                    status=ManifestStatus.UNSUPPORTED,
                    source_path=str(path),
                    validation_errors=[f"manifest_load_error: {exc}"],
                    loaded_at=stamp(),
                )
                continue
            report = self._build_report(manifest)
            self._manifests[manifest.agent_id] = manifest
            self._reports[manifest.agent_id] = report
        self._loaded_at = stamp()
        return self

    def _build_aliases(self, index: dict[str, Any]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        agents = index.get("agents") if isinstance(index, dict) else None
        if not isinstance(agents, dict):
            return aliases
        for key, raw in agents.items():
            if not isinstance(raw, dict):
                continue
            dir_path = str(raw.get("dir") or "").strip().rstrip("/")
            if dir_path:
                agent_id = Path(dir_path).name
                aliases[str(key)] = agent_id
                aliases[str(key).lower()] = agent_id
                name = str(raw.get("name") or "").strip().lower()
                if name:
                    aliases[name] = agent_id
        return aliases

    def _resolve_agent_id(self, agent_id: str) -> str:
        if agent_id in self._manifests or agent_id in self._reports:
            return agent_id
        needle = agent_id.strip().lower()
        if needle in self._aliases:
            return self._aliases[needle]
        for m in self._manifests.values():
            if m.effective_name.lower() == needle or m.agent_id.lower() == needle:
                return m.agent_id
        for r in self._reports.values():
            if r.agent_id.lower() == needle or r.name.lower() == needle:
                return r.agent_id
        return agent_id

    def _build_report(self, manifest: AgentManifest) -> AgentCapabilityReport:
        binding = bind_manifest(manifest)
        tool_bindings = binding["tools"]
        workflow_bindings = binding["workflows"]
        errors = list(binding.get("errors") or [])
        warnings = list(binding.get("warnings") or [])

        has_bound_tool = any(t.get("bound") for t in tool_bindings)
        has_bound_workflow = any(w.get("bound") for w in workflow_bindings)
        has_any_declared = bool(tool_bindings or workflow_bindings)
        all_unsupported = has_any_declared and not has_bound_tool and not has_bound_workflow

        if errors or all_unsupported:
            status = ManifestStatus.UNSUPPORTED
        elif not has_any_declared:
            # Manifest exists but no executable declarations - manual.
            status = ManifestStatus.MANUAL
            warnings.append("no executable tools/workflows declared; manual only")
        elif has_bound_tool or has_bound_workflow:
            # Some may be manual/unbound, but at least something is runnable.
            status = ManifestStatus.RUNNABLE
            if any(not t.get("bound") for t in tool_bindings):
                warnings.append("some declared tools are unbound/manual")
            if any(not w.get("bound") for w in workflow_bindings):
                warnings.append("some declared workflows are unbound/manual")
        else:
            status = ManifestStatus.MANUAL

        return AgentCapabilityReport(
            agent_id=manifest.agent_id,
            name=manifest.effective_name,
            status=status,
            source_path=manifest.source_path,
            tool_bindings=tool_bindings,
            workflow_bindings=workflow_bindings,
            validation_errors=errors,
            warnings=warnings,
            manifest_hash=manifest.manifest_hash,
            loaded_at=stamp(),
        )

    def manifests(self) -> list[AgentManifest]:
        self.load()
        return [self._manifests[k] for k in sorted(self._manifests)]

    def reports(self) -> list[AgentCapabilityReport]:
        self.load()
        # include load-error reports for manifests that failed to parse
        ids = sorted(self._reports.keys())
        return [self._reports[i] for i in ids]

    def get_manifest(self, agent_id: str) -> AgentManifest | None:
        self.load()
        resolved = self._resolve_agent_id(agent_id)
        return self._manifests.get(resolved)

    def get_report(self, agent_id: str) -> AgentCapabilityReport | None:
        self.load()
        resolved = self._resolve_agent_id(agent_id)
        return self._reports.get(resolved)

    def validate(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        self.load()
        if agent_id:
            report = self.get_report(agent_id)
            if report is None:
                return [{"agent_id": agent_id, "status": "not_found", "validation_errors": ["manifest not found"]}]
            return [report.to_dict()]
        return [r.to_dict() for r in self.reports()]

    def teams(self) -> list[TeamManifest]:
        self.load()
        return [self._teams[k] for k in sorted(self._teams)]

    def get_team(self, team_id: str) -> TeamManifest | None:
        self.load()
        if team_id in self._teams:
            return self._teams[team_id]
        needle = team_id.strip().lower()
        for t in self._teams.values():
            if t.team_id.lower() == needle or t.name.lower() == needle:
                return t
        return None

    def validate_team(self, team_id: str | None = None) -> list[dict[str, Any]]:
        self.load()
        teams = [self.get_team(team_id)] if team_id else self.teams()
        out = []
        for t in teams:
            if t is None:
                out.append({"team_id": team_id, "status": "not_found", "validation_errors": ["team not found"]})
                continue
            errors = []
            warnings = []
            if not t.team_id:
                errors.append("missing team_id")
            if not t.agents:
                warnings.append("team has no agents")
            member_reports = []
            for a in t.agents:
                r = self.get_report(a)
                if r is None:
                    member_reports.append({"agent_id": a, "status": "not_found"})
                    warnings.append(f"member not found: {a}")
                else:
                    member_reports.append({"agent_id": r.agent_id, "status": r.status.value})
            if t.lead and t.lead not in t.agents:
                warnings.append("lead is not in agents list")
            status = "invalid" if errors else "ok"
            out.append({
                "team_id": t.team_id,
                "name": t.name,
                "description": t.description,
                "lead": t.lead,
                "agents": t.agents,
                "member_reports": member_reports,
                "status": status,
                "validation_errors": errors,
                "warnings": warnings,
                "source_path": t.source_path,
            })
        return out

    def summary(self) -> dict[str, Any]:
        reports = self.reports()
        by_status: dict[str, int] = {}
        for r in reports:
            by_status[r.status.value] = by_status.get(r.status.value, 0) + 1
        return {
            "count": len(reports),
            "by_status": by_status,
            "team_count": len(self.teams()),
            "loaded_at": self.loaded_at,
            "agents_dir": str(self.agents_dir),
        }


_REGISTRY: DomainAgentRegistry | None = None


def get_domain_registry(*, force: bool = False) -> DomainAgentRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = DomainAgentRegistry()
    _REGISTRY.load(force=force)
    return _REGISTRY
