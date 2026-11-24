"""Bind manifest-declared tools/workflows to runtime capabilities.

Binding is explicit: every declared tool/workflow gets a status and reason.
No silent drops. Execution safety is enforced again at runtime by allow-lists
and dry-run/guardrails.
"""

from __future__ import annotations

from typing import Any

from ..tools.registry import SPECS as TOOL_SPECS
from .manifest import AgentManifest


def _bind_tool(name: str, kind: str, raw: Any) -> dict[str, Any]:
    tool_name = str(name or "").strip()
    if not tool_name:
        return {
            "name": tool_name,
            "kind": kind,
            "bound": False,
            "status": "unsupported",
            "reason": "empty tool name",
            "raw": raw,
        }
    # Direct registry tool binding.
    if tool_name in TOOL_SPECS:
        spec = TOOL_SPECS[tool_name]
        return {
            "name": tool_name,
            "kind": "registry",
            "bound": True,
            "status": "bound",
            "reason": "bound to agent tool registry",
            "scope": spec.scope,
            "writes": bool(spec.writes),
            "destructive": bool(spec.is_destructive),
            "raw": raw,
        }
    # Known command/script references are not executable by the domain runtime
    # without an explicit safe adapter; mark manual.
    if kind in {"command", "script"} or "/" in tool_name or tool_name.endswith((".py", ".sh")) or " " in tool_name:
        return {
            "name": tool_name,
            "kind": kind or "command",
            "bound": False,
            "status": "manual",
            "reason": "command/script reference requires manual execution or dedicated adapter",
            "raw": raw,
        }
    return {
        "name": tool_name,
        "kind": kind or "unknown",
        "bound": False,
        "status": "unsupported",
        "reason": "no registry tool/adapter binding found",
        "raw": raw,
    }


def _bind_workflow(name: str, steps: list[Any], tool_bindings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bound_steps: list[dict[str, Any]] = []
    any_bound = False
    all_bound = True
    for idx, step in enumerate(steps):
        step_name = ""
        if isinstance(step, str):
            step_name = step.strip()
        elif isinstance(step, dict):
            step_name = str(step.get("tool") or step.get("name") or step.get("action") or step.get("step") or "").strip()
        else:
            step_name = str(step)
        # Step may reference a tool name; bind if known.
        bound = False
        reason = "no executable binding; manual step"
        status = "manual"
        if step_name in tool_bindings and tool_bindings[step_name]["bound"]:
            bound = True
            status = "bound"
            reason = "bound to registry tool"
        elif step_name in TOOL_SPECS:
            bound = True
            status = "bound"
            reason = "bound to registry tool"
        elif any(ch.isspace() for ch in step_name) or "/" in step_name:
            status = "manual"
            reason = "command/script step requires manual execution"
        else:
            status = "unsupported" if step_name else "manual"
            reason = "unknown workflow step" if step_name else "empty step"
        any_bound = any_bound or bound
        all_bound = all_bound and bound
        bound_steps.append(
            {
                "index": idx,
                "step": step,
                "name": step_name,
                "bound": bound,
                "status": status,
                "reason": reason,
            }
        )
    if not steps:
        return {"name": name, "bound": False, "status": "manual", "reason": "empty workflow", "steps": []}
    if all_bound:
        status = "bound"
        reason = "all steps bound"
    elif any_bound:
        status = "partial"
        reason = "some steps bound; unbound steps require manual handling"
    else:
        # If steps look like commands, manual; else unsupported.
        if any(s.get("status") == "manual" for s in bound_steps):
            status = "manual"
            reason = "workflow steps require manual execution"
        else:
            status = "unsupported"
            reason = "no workflow steps could be bound"
    return {"name": name, "bound": status == "bound", "status": status, "reason": reason, "steps": bound_steps}


def bind_manifest(manifest: AgentManifest) -> dict[str, Any]:
    tool_bindings = [_bind_tool(t.name, t.kind, t.raw) for t in manifest.tools]
    tools_by_name = {t["name"]: t for t in tool_bindings if t.get("name")}
    workflow_bindings = [
        _bind_workflow(name, steps, tools_by_name) for name, steps in manifest.workflows.items()
    ]
    errors: list[str] = []
    warnings: list[str] = []
    if not manifest.agent_id:
        errors.append("missing agent_id")
    # Unknown top-level keys are tolerated, but we note them as warnings for visibility.
    known = {
        "id", "agent_id", "slug", "name", "version", "description", "role", "goal",
        "department", "team", "trigger", "rules", "tools", "workflows", "input_schema",
        "output", "output_schema", "config", "steps", "pipeline", "plan", "seed_sources",
        "master_source", "do_not_use",
    }
    unknown = sorted(k for k in manifest.raw.keys() if k not in known)
    if unknown:
        warnings.append(f"unknown manifest keys preserved: {', '.join(unknown[:12])}")
    return {
        "tools": tool_bindings,
        "workflows": workflow_bindings,
        "errors": errors,
        "warnings": warnings,
    }
