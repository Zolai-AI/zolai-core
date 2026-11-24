"""Bind manifest-declared tools/workflows to runtime capabilities.

Binding is explicit: every declared tool/workflow gets a status and reason.
No silent drops. Execution safety is enforced again at runtime by allow-lists
and dry-run/guardrails.
"""

from __future__ import annotations

from typing import Any

from ..tools.registry import SPECS as TOOL_SPECS
from .manifest import AgentManifest

# Manifest tool names often use legacy/CLI/API names. Map known aliases to
# canonical registry tools so agents can run without fake execution.
_TOOL_ALIASES: dict[str, str] = {
    "search_dictionary": "dictionary_lookup",
    "get /api/dictionary/search": "dictionary_lookup",
    "post /api/grammar/validate": "grammar_check",
    "validate_grammar": "grammar_check",
    "grammar_validate": "grammar_check",
    "validate_sentence": "grammar_check",
    "search_wiki": "rag_search",
    "search_resources": "rag_search",
    "retrieve_knowledge": "rag_search",
    "retrieve": "rag_search",
    "search_parallel": "rag_search",
    "get_grammar": "rag_search",
    "knowledge_lookup": "rag_search",
    "classify_dialect": "analyze_text",
    "analyze_sentence": "analyze_text",
    "predict_next": "predict_word_completion",
    "predict_word": "predict_word_completion",
    "word_predict": "predict_word_completion",
    "check_status": "provider_status",
    "status": "provider_status",
    "provider_status_check": "provider_status",
    "kb_stats": "knowledge_stats",
    "knowledge_base_stats": "knowledge_stats",
}


def _canonical_tool_name(name: str) -> tuple[str, str | None]:
    raw = str(name or "").strip()
    if raw in TOOL_SPECS:
        return raw, None
    alias = _TOOL_ALIASES.get(raw.lower())
    if alias and alias in TOOL_SPECS:
        return alias, raw
    return raw, None


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
    canonical, aliased_from = _canonical_tool_name(tool_name)
    # Direct registry tool binding.
    if canonical in TOOL_SPECS:
        spec = TOOL_SPECS[canonical]
        out = {
            "name": canonical,
            "declared_name": tool_name,
            "kind": "registry",
            "bound": True,
            "status": "bound",
            "reason": "bound to agent tool registry",
            "scope": spec.scope,
            "writes": bool(spec.writes),
            "destructive": bool(spec.is_destructive),
            "raw": raw,
        }
        if aliased_from:
            out["alias_of"] = aliased_from
            out["reason"] = f"aliased to registry tool {canonical}"
        return out
    # File/path references: bind as safe read-only manifest reference adapter.
    is_path_ref = "/" in tool_name or tool_name.endswith(
        (".md", ".jsonl", ".json", ".db", ".yaml", ".yml", ".txt", ".py", ".sh", ".ipynb", ".csv")
    )
    if kind in {"file", "path"} or is_path_ref:
        return {
            "name": tool_name,
            "declared_name": tool_name,
            "kind": "manifest_ref",
            "bound": True,
            "status": "bound",
            "reason": "bound to safe manifest reference adapter (read/plan only)",
            "scope": "member",
            "writes": False,
            "destructive": False,
            "adapter": "manifest_ref",
            "raw": raw,
        }
    # Command/script/API/env references: bind as explicit manual adapter. The
    # runtime can fully run the agent in plan/manual mode and returns a
    # structured manual_action_required result; it never executes arbitrary
    # shell/remote side effects.
    return {
        "name": tool_name,
        "declared_name": tool_name,
        "kind": kind or "manual_adapter",
        "bound": True,
        "status": "bound",
        "reason": "bound to manual action adapter (explicit operator action required; no arbitrary execution)",
        "scope": "member",
        "writes": False,
        "destructive": False,
        "adapter": "manual_action",
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
            step_name = str(step.get("tool") or step.get("name") or step.get("action") or step.get("step") or
                "").strip()
        else:
            step_name = str(step)
        # Step may reference a tool name; bind if known.
        bound = False
        reason = "no executable binding; manual step"
        status = "manual"
        tb = tool_bindings.get(step_name)
        if tb and tb.get("bound"):
            bound = True
            status = "bound"
            adapter = tb.get("adapter")
            if adapter == "manifest_ref":
                reason = "bound to manifest reference adapter"
            elif adapter == "manual_action":
                reason = "bound to manual action adapter"
            elif tb.get("kind") == "registry":
                reason = "bound to registry tool"
            else:
                reason = "bound to adapter"
        elif step_name in TOOL_SPECS:
            bound = True
            status = "bound"
            reason = "bound to registry tool"
        elif any(ch.isspace() for ch in step_name) or "/" in step_name or step_name.endswith(('.py','.sh')):
            # Treat as manual_action-adaptable step even if not predeclared in tools.
            bound = True
            status = "bound"
            reason = "bound to manual action adapter"
        else:
            # Unknown symbolic step - bind to manual action adapter so workflows remain runnable.
            bound = True
            status = "bound"
            reason = "bound to manual action adapter (symbolic step)"
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


def _synthetic_tools_from_raw(manifest: AgentManifest) -> list[dict[str, Any]]:
    """Create safe adapter bindings from common manifest keys when tools/workflows absent.

    This makes legacy/heterogeneous manifests runnable in explicit plan/manual-action mode
    without executing arbitrary commands.
    """
    raw = manifest.raw if isinstance(manifest.raw, dict) else {}
    candidates: list[tuple[str, str, Any]] = []
    for key in (
        "scripts",
        "script",
        "commands",
        "command",
        "outputs",
        "output",
        "input",
        "inputs",
        "files",
        "paths",
    ):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            kind = "script" if key.startswith("script") or key.startswith("command") else "file"
            candidates.append((val.strip(), kind, val))
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    kind = "script" if key.startswith("script") or key.startswith("command") else "file"
                    candidates.append((item.strip(), kind, item))
                elif isinstance(item, dict):
                    name = str(
                        item.get("name")
                        or item.get("path")
                        or item.get("command")
                        or item.get("id")
                        or ""
                    ).strip()
                    if name:
                        kind = str(
                            item.get("kind")
                            or ("script" if key.startswith("script") or key.startswith("command") else "file")
                        )
                        candidates.append((name, kind, item))
        elif isinstance(val, dict):
            for k, v in val.items():
                name = str(v if isinstance(v, str) else k).strip()
                if name:
                    kind = "file" if isinstance(v, str) and ("/" in v or "." in v) else "manual"
                    candidates.append((name, kind, {k: v}))
    # also include plan/pipeline/steps as symbolic workflow-ish refs
    for key in ("plan", "pipeline", "steps", "recommended_pipeline", "watch_list"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    candidates.append((item.strip(), "manual", item))
                elif isinstance(item, dict):
                    name = str(
                        item.get("name") or item.get("step") or item.get("action") or item.get("id") or ""
                    ).strip()
                    if name:
                        candidates.append((name, "manual", item))
    out = []
    seen = set()
    for name, kind, raw_item in candidates:
        key = (name, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(_bind_tool(name, kind, raw_item))
    # always provide a safe introspection tool so agent can run goal/manual with no external side effects
    if "knowledge_stats" in TOOL_SPECS and not any(t.get("name") == "knowledge_stats" for t in out):
        out.insert(0, _bind_tool("knowledge_stats", "registry", "synthetic:knowledge_stats"))
    return out


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
    # Synthesize safe adapters for legacy manifests with no executable declarations.
    if not tool_bindings and not workflow_bindings:
        synthetic = _synthetic_tools_from_raw(manifest)
        if synthetic:
            tool_bindings = synthetic
            tools_by_name = {t["name"]: t for t in tool_bindings if t.get("name")}
            workflow_bindings = [
                _bind_workflow(
                    "default",
                    [t.get("declared_name") or t.get("name") for t in tool_bindings],
                    tools_by_name,
                )
            ]
            warnings.append("synthetic safe adapter bindings generated from manifest metadata")
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
