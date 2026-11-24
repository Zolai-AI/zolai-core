"""Tolerant manifest models for ``agents/*.json`` and ``agents/zolai-agents.yaml``.

The 36 agent.json files are heterogeneous; the model accepts known fields,
preserves unknown keys (`extra="allow"`), and normalizes to a stable view
used by registry/binder/runner. All timestamps come from
:func:`zolai.shared.clock.stamp`.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...shared.clock import stamp


class ManifestStatus(str, Enum):
    RUNNABLE = "runnable"
    MANUAL = "manual"
    UNSUPPORTED = "unsupported"


class ManifestToolRef(BaseModel):
    """A tool reference declared by a manifest.

    Manifests encode tools as strings (``"zolai crawl"`` or ``"rag_search"``)
    or objects; we normalize both.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    kind: str = "unknown"  # registry|command|script|unknown
    raw: Any = None


class ManifestWorkflow(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    steps: list[Any] = Field(default_factory=list)


class AgentManifest(BaseModel):
    """Normalized agent manifest (tolerant)."""

    model_config = ConfigDict(extra="allow")

    agent_id: str
    name: str = ""
    version: str = ""
    description: str = ""
    role: str = ""
    goal: str = ""
    department: str = ""
    team: str = ""
    trigger: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    tools: list[ManifestToolRef] = Field(default_factory=list)
    workflows: dict[str, list[Any]] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    source_path: str = ""
    manifest_hash: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    loaded_at: str = ""

    @property
    def effective_name(self) -> str:
        return self.name or self.agent_id

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def workflow_names(self) -> list[str]:
        return list(self.workflows.keys())


class TeamManifest(BaseModel):
    """First-class team from ``agents/zolai-agents.yaml``."""

    model_config = ConfigDict(extra="allow")

    team_id: str
    name: str = ""
    description: str = ""
    agents: list[str] = Field(default_factory=list)
    lead: str = ""
    source_path: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


def _slug_from_path(path: Path) -> str:
    # agents/zomi-data/agent.json -> zomi-data
    return path.parent.name


def _normalize_tool(raw: Any) -> ManifestToolRef:
    if isinstance(raw, str):
        name = raw.strip()
        kind = "command"
        # Heuristic: plain identifier is likely a registry tool name.
        if name and all(ch.isalnum() or ch in "_-" for ch in name):
            kind = "registry"
        elif "/" in name or name.endswith(".py") or name.endswith(".sh"):
            kind = "script"
        return ManifestToolRef(name=name, kind=kind, raw=raw)
    if isinstance(raw, dict):
        name = str(raw.get("name") or raw.get("tool") or raw.get("id") or "").strip()
        kind = str(raw.get("kind") or raw.get("type") or "unknown")
        return ManifestToolRef(name=name or json.dumps(raw, ensure_ascii=False)[:80], kind=kind, raw=raw)
    return ManifestToolRef(name=str(raw), kind="unknown", raw=raw)


def _normalize_workflows(raw: Any) -> dict[str, list[Any]]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, list[Any]] = {}
        for k, v in raw.items():
            if isinstance(v, list):
                out[str(k)] = v
            elif isinstance(v, dict) and isinstance(v.get("steps"), list):
                out[str(k)] = list(v["steps"])
            else:
                out[str(k)] = [v]
        return out
    if isinstance(raw, list):
        # Anonymous workflow list
        return {"default": raw}
    return {}


def load_manifest_file(path: str | Path) -> AgentManifest:
    """Load one agent.json manifest tolerantly."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object: {p}")
    agent_id = str(data.get("id") or data.get("agent_id") or data.get("slug") or _slug_from_path(p)).strip()
    name = str(data.get("name") or agent_id)
    tools_raw = data.get("tools") or []
    if not isinstance(tools_raw, list):
        tools_raw = [tools_raw]
    tools = [_normalize_tool(t) for t in tools_raw]
    workflows = _normalize_workflows(data.get("workflows"))
    # Some manifests encode a single workflow/pipeline as steps.
    for key in ("steps", "pipeline", "plan"):
        if key in data and key not in workflows and isinstance(data[key], list):
            workflows[key] = data[key]
    manifest = AgentManifest(
        agent_id=agent_id,
        name=name,
        version=str(data.get("version") or ""),
        description=str(data.get("description") or data.get("goal") or ""),
        role=str(data.get("role") or ""),
        goal=str(data.get("goal") or ""),
        department=str(data.get("department") or ""),
        team=str(data.get("team") or ""),
        trigger=[str(x) for x in (data.get("trigger") or []) if x is not None] if isinstance(data.get("trigger"), list) else [],
        rules=[str(x) for x in (data.get("rules") or []) if x is not None] if isinstance(data.get("rules"), list) else [],
        tools=tools,
        workflows=workflows,
        input_schema=data.get("input_schema") if isinstance(data.get("input_schema"), dict) else {},
        output=str(data.get("output") or ""),
        output_schema=data.get("output_schema") if isinstance(data.get("output_schema"), dict) else {},
        config=data.get("config") if isinstance(data.get("config"), dict) else {},
        source_path=str(p),
        manifest_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        raw=data,
        loaded_at=stamp(),
    )
    return manifest


def load_manifest_index(path: str | Path) -> dict[str, Any]:
    """Load ``agents/zolai-agents.yaml`` as a raw dict (tolerant)."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        # PyYAML may not be installed; return empty with marker.
        return {"_error": "pyyaml_not_installed", "_path": str(p)}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"_value": data, "_path": str(p)}


def discover_manifest_files(agents_dir: str | Path) -> list[Path]:
    """Find all ``*/agent.json`` manifests under agents dir."""
    root = Path(agents_dir)
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*/agent.json") if p.is_file())


def load_team_manifests(index: dict[str, Any], *, source_path: str = "") -> list[TeamManifest]:
    teams = index.get("teams") if isinstance(index, dict) else None
    if not isinstance(teams, dict):
        return []
    out: list[TeamManifest] = []
    for team_id, raw in teams.items():
        if not isinstance(raw, dict):
            raw = {"name": str(raw)}
        agents = raw.get("agents") or []
        if not isinstance(agents, list):
            agents = [agents]
        out.append(
            TeamManifest(
                team_id=str(team_id),
                name=str(raw.get("name") or team_id),
                description=str(raw.get("description") or ""),
                agents=[str(a) for a in agents if a is not None],
                lead=str(raw.get("lead") or ""),
                source_path=source_path,
                raw=raw,
            )
        )
    return sorted(out, key=lambda t: t.team_id)
