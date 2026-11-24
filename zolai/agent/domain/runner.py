"""Unified domain agent runner (additive).

Modes: goal | workflow | batch | manual.
- goal: constrained P3 research/build/review via run_agent_goal, linked to domain run.
- workflow: execute declared manifest workflow steps bound to registry tools.
- batch: bounded fan-out over items, each item executed through goal/workflow.
- manual: validation/binding/plan only, no side effects.

Dry-run blocks side-effecting tools and queue dispatch; emits audit events.
All timestamps via zolai.shared.clock.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.engine import Engine

from ...shared.clock import stamp
from .. import store
from ..tools.executor import execute_tool
from ..tools.registry import allow_list as tier_allow_list
from ..tools.registry import get_tool
from ..tools.types import ToolCall
from .audit import append_event, redact
from .registry import get_domain_registry

logger = logging.getLogger(__name__)

MAX_BATCH_ITEMS = max(1, int(os.environ.get("AGENT_MAX_BATCH_ITEMS", "20")))
MAX_TEAM_MEMBERS = max(1, int(os.environ.get("AGENT_MAX_TEAM_MEMBERS", "12")))
MAX_STEPS = max(1, int(os.environ.get("AGENT_MAX_STEPS", "16")))
QUEUE_MAX_ATTEMPTS = max(1, int(os.environ.get("AGENT_QUEUE_MAX_ATTEMPTS", "3")))


def _engine() -> Engine:
    from ...data.repositories import get_engine

    return get_engine()


def _is_side_effect_tool(name: str) -> bool:
    spec = get_tool(name)
    if spec is None:
        return True
    return bool(spec.writes or spec.is_destructive or spec.requires_network)


def _effective_allow(manifest_report: dict[str, Any], scope: str) -> tuple[frozenset[str], list[dict[str, Any]]]:
    """manifest bound tools ∩ registry tier allow-list ∩ scope."""
    tier = tier_allow_list(scope)
    bound = []
    allowed: set[str] = set()
    for tb in manifest_report.get("tool_bindings") or []:
        name = str(tb.get("name") or "")
        is_bound = bool(tb.get("bound")) and name in tier
        entry = {**tb, "effective_allowed": is_bound, "scope_tier": scope}
        if tb.get("bound") and name and name not in tier:
            entry["effective_allowed"] = False
            entry["reason"] = f"bound tool not allowed for scope {scope}"
        if is_bound:
            allowed.add(name)
        bound.append(entry)
    return frozenset(allowed), bound


def _audit(eng: Engine, domain_run_id: int, seq: int, event_type: str, *, phase: str = "", tool_name: str = "",
    payload: dict[str, Any] | None = None) -> int:
    append_event(eng, domain_run_id=domain_run_id, seq=seq, event_type=event_type, phase=phase, tool_name=tool_name,
        payload=payload)
    return seq + 1


def _finish(eng: Engine, domain_run: dict[str, Any], *, status: str, output: dict[str, Any],
    error: str = "") -> dict[str, Any]:
    finished = stamp()
    updated = store.update_domain_run(
        int(domain_run["id"]),
        engine=eng,
        status=status,
        output_json=output,
        error=error,
        finished_at=finished,
    )
    return updated or domain_run


def _run_manual(manifest, report, *, input_data: dict[str, Any], dry_run: bool, scope: str) -> dict[str, Any]:
    allow, tool_bindings = _effective_allow(report, scope)
    plan = {
        "agent_id": manifest.agent_id,
        "name": manifest.effective_name,
        "mode": "manual",
        "dry_run": bool(dry_run),
        "scope": scope,
        "status": report.get("status"),
        "effective_allow": sorted(allow),
        "tool_bindings": tool_bindings,
        "workflow_bindings": report.get("workflow_bindings") or [],
        "validation_errors": report.get("validation_errors") or [],
        "warnings": report.get("warnings") or [],
        "input": redact(input_data),
        "execution": "not_executed_manual_mode",
    }
    return plan


def _run_workflow(manifest, report, *, input_data: dict[str, Any], dry_run: bool, scope: str, eng: Engine,
    domain_run_id: int, seq: int) -> tuple[dict[str, Any], int]:
    allow, _ = _effective_allow(report, scope)
    workflow_name = str(input_data.get("workflow") or (manifest.workflow_names()[0] if manifest.workflow_names() else
        "default"))
    workflows = {w["name"]: w for w in (report.get("workflow_bindings") or [])}
    wf = workflows.get(workflow_name)
    if wf is None:
        # try raw manifest workflows
        steps = manifest.workflows.get(workflow_name)
        if steps is None and manifest.workflows:
            workflow_name, steps = next(iter(manifest.workflows.items()))
        wf = {
            "name": workflow_name,
            "status": "manual",
            "bound": False,
            "reason": "workflow not found in binding report",
            "steps": [
                {
                    "index": i,
                    "step": s,
                    "name": str(s),
                    "bound": False,
                    "status": "unsupported",
                    "reason": "workflow not bound",
                }
                for i, s in enumerate(steps or [])
            ],
        }
    results = []
    status = "succeeded"
    for s in (wf.get("steps") or [])[:MAX_STEPS]:
        step_name = str(s.get("name") or "")
        bound = bool(s.get("bound"))
        spec = get_tool(step_name)
        if not bound or spec is None or step_name not in allow:
            results.append({"step": s, "status": "unsupported", "reason": s.get("reason") or "not bound/allowed"})
            status = "manual" if status == "succeeded" else status
            seq = _audit(
        eng,
        domain_run_id,
        seq,
        "workflow.step.skipped",
        phase="workflow",
        tool_name=step_name,
        payload={"reason": s.get("reason"), "bound": bound},
    )
            continue
        if dry_run and _is_side_effect_tool(step_name):
            results.append(
                {
                    "step": s,
                    "status": "dry_run_blocked",
                    "tool": step_name,
                    "reason": "side-effecting tool blocked in dry-run",
                }
            )
            seq = _audit(
                eng,
                domain_run_id,
                seq,
                "workflow.step.dry_run_blocked",
                phase="workflow",
                tool_name=step_name,
                payload={"input": redact(input_data.get("step_input") or input_data)},
            )
            continue
        call_input = (
            input_data.get("step_input")
            if isinstance(input_data.get("step_input"), dict)
            else {"query": str(input_data.get("goal") or input_data.get("task") or "")}
        )
        # allow per-step input map
        step_inputs = input_data.get("steps") if isinstance(input_data.get("steps"), dict) else {}
        if step_name in step_inputs and isinstance(step_inputs[step_name], dict):
            call_input = step_inputs[step_name]
        seq = _audit(
            eng,
            domain_run_id,
            seq,
            "workflow.step.start",
            phase="workflow",
            tool_name=step_name,
            payload={"input": redact(call_input)},
        )
        res = execute_tool(
            ToolCall(name=step_name, input=call_input, source="workflow"),
            allow,
            turn=s.get("index", 0),
        )
        row = res.to_dict()
        row["status"] = "completed" if res.ok else "failed"
        row["data"] = redact(res.data) if res.ok else None
        row["error"] = res.error
        results.append({"step": s, "status": row["status"], "tool": step_name, "result": row})
        seq = _audit(
            eng,
            domain_run_id,
            seq,
            "workflow.step.end",
            phase="workflow",
            tool_name=step_name,
            payload={
                "ok": res.ok,
                "error_code": getattr(res.error_code, "value", res.error_code),
            },
        )
        if not res.ok:
            status = "failed"
            break
    return {"workflow": workflow_name, "status": status, "steps": results}, seq


def _run_goal(manifest, report, *, input_data: dict[str, Any], dry_run: bool, autonomous: bool, scope: str,
    requested_by: str, eng: Engine, domain_run_id: int, seq: int) -> tuple[dict[str, Any], int]:
    from ..orchestrator import run_agent_goal

    goal = str(input_data.get("goal") or input_data.get("task") or input_data.get("query") or "").strip()
    if not goal:
        return {"status": "failed", "error": "goal is required for goal mode"}, seq
    allow, _ = _effective_allow(report, scope)
    if not allow:
        # Still run but with no tools; mark manual-ish.
        seq = _audit(
        eng,
        domain_run_id,
        seq,
        "goal.no_bound_tools",
        phase="goal",
        payload={"agent_id": manifest.agent_id},
    )
    if dry_run:
        # Dry-run goal: produce plan only, no P3 execution.
        plan = {
            "status": "dry_run",
            "mode": "goal",
            "agent_id": manifest.agent_id,
            "goal": goal,
            "effective_allow": sorted(allow),
            "would_execute": "run_agent_goal with manifest-constrained allow-list",
            "autonomous": autonomous,
            "scope": scope,
        }
        seq = _audit(eng, domain_run_id, seq, "goal.dry_run", phase="goal", payload=plan)
        return plan, seq

    seq = _audit(
        eng,
        domain_run_id,
        seq,
        "goal.start",
        phase="goal",
        payload={"goal": redact(goal), "allow": sorted(allow)},
    )
    # Reuse existing P3 runtime. It creates its own agent_runs row.
    run = run_agent_goal(goal, created_by=requested_by, scope=scope, tool_allowlist=allow)
    seq = _audit(
        eng,
        domain_run_id,
        seq,
        "goal.end",
        phase="goal",
        payload={"run_id": run.get("id"), "status": run.get("status")},
    )
    return {
        "status": run.get("status"),
        "agent_run_id": run.get("id"),
        "answer": run.get("answer"),
        "phases": run.get("phases"),
        "tool_calls": redact(run.get("tool_calls") or []),
        "evidence": redact(run.get("evidence") or []),
        "provider": run.get("provider"),
        "model": run.get("model"),
        "turns": run.get("turns"),
        "latency_ms": run.get("latency_ms"),
        "error": run.get("error"),
    }, seq


def run_domain_agent(
    agent_id: str,
    *,
    mode: str = "manual",
    input_data: dict[str, Any] | None = None,
    dry_run: bool = False,
    autonomous: bool = False,
    scope: str = "admin",
    requested_by: str = "system",
    engine: Engine | None = None,
) -> dict[str, Any]:
    eng = engine or _engine()
    store.ensure_domain_agent_tables(eng)
    registry = get_domain_registry()
    report_obj = registry.get_report(agent_id)
    manifest = registry.get_manifest(agent_id)
    if report_obj is None or manifest is None:
        return {"status": "not_found", "agent_id": agent_id, "error": "manifest not found"}
    report = report_obj.to_dict()
    mode = str(mode or "manual").lower().strip()
    if mode not in {"goal", "workflow", "batch", "manual"}:
        return {"status": "invalid", "error": f"unsupported mode: {mode}"}
    input_data = input_data or {}
    created = store.create_domain_run(
        {
            "agent_id": manifest.agent_id,
            "mode": mode,
            "status": "running",
            "input": redact(input_data),
            "output": {},
            "dry_run": bool(dry_run),
            "autonomous": bool(autonomous),
            "requested_by": requested_by,
            "scope": scope,
            "started_at": stamp(),
        },
        engine=eng,
    )
    domain_run_id = int(created["id"])
    seq = 0
    seq = _audit(
        eng,
        domain_run_id,
        seq,
        "run.start",
        phase="start",
        payload={
            "agent_id": manifest.agent_id,
            "mode": mode,
            "dry_run": dry_run,
            "autonomous": autonomous,
            "scope": scope,
        },
    )
    try:
        if mode == "manual":
            output = _run_manual(manifest, report, input_data=input_data, dry_run=dry_run, scope=scope)
            seq = _audit(eng, domain_run_id, seq, "run.plan", phase="manual", payload={"status": output.get("status")})
            out = _finish(eng, created, status="manual", output=output)
        elif mode == "workflow":
            output, seq = _run_workflow(
                manifest,
                report,
                input_data=input_data,
                dry_run=dry_run,
                scope=scope,
                eng=eng,
                domain_run_id=domain_run_id,
                seq=seq,
            )
            status = str(output.get("status") or "manual")
            if status == "succeeded":
                final_status = "succeeded"
            elif status == "failed":
                final_status = "failed"
            else:
                final_status = "manual"
            out = _finish(eng, created, status=final_status, output=output, error=str(output.get("error") or ""))
        elif mode == "goal":
            output, seq = _run_goal(
                manifest,
                report,
                input_data=input_data,
                dry_run=dry_run,
                autonomous=autonomous,
                scope=scope,
                requested_by=requested_by,
                eng=eng,
                domain_run_id=domain_run_id,
                seq=seq,
            )
            final_status = str(output.get("status") or "failed")
            if final_status not in {"succeeded", "failed", "dry_run", "manual"}:
                final_status = "failed"
            final = "succeeded" if final_status == "succeeded" else ("manual" if final_status in {"dry_run",
                "manual"} else "failed")
            out = _finish(eng, created, status=final, output=output, error=str(output.get("error") or ""))
            if output.get("agent_run_id"):
                store.update_domain_run(domain_run_id, engine=eng, run_id=int(output["agent_run_id"]))
                out = store.get_domain_run(domain_run_id, engine=eng) or out
        elif mode == "batch":
            items = input_data.get("items")
            if not isinstance(items, list) or not items:
                raise ValueError("batch mode requires input.items list")
            if len(items) > MAX_BATCH_ITEMS:
                raise ValueError(f"batch too large: {len(items)} > {MAX_BATCH_ITEMS}")
            child_mode = str(input_data.get("child_mode") or "goal")
            if child_mode not in {"goal", "workflow"}:
                child_mode = "goal"
            results = []
            batch_status = "succeeded"
            for i, item in enumerate(items):
                item_input = item if isinstance(item, dict) else {"goal": str(item)}
                seq = _audit(
                    eng,
                    domain_run_id,
                    seq,
                    "batch.item.start",
                    phase="batch",
                    payload={"index": i, "child_mode": child_mode},
                )
                child = run_domain_agent(
                    manifest.agent_id,
                    mode=child_mode,
                    input_data=item_input,
                    dry_run=dry_run,
                    autonomous=False,
                    scope=scope,
                    requested_by=requested_by,
                    engine=eng,
                )
                results.append(
                    {
                        "index": i,
                        "child_run_id": child.get("id"),
                        "status": child.get("status"),
                        "error": child.get("error"),
                    }
                )
                seq = _audit(
                    eng,
                    domain_run_id,
                    seq,
                    "batch.item.end",
                    phase="batch",
                    payload={"index": i, "status": child.get("status")},
                )
                if child.get("status") == "failed":
                    batch_status = "failed"
            out = _finish(
                eng,
                created,
                status=batch_status,
                output={
                    "mode": "batch",
                    "child_mode": child_mode,
                    "items": results,
                    "count": len(results),
                },
            )
        else:
            out = _finish(eng, created, status="failed", output={}, error="unreachable")
        seq = _audit(eng, domain_run_id, seq, "run.end", phase="end", payload={"status": out.get("status")})
        return out
    except Exception as exc:
        logger.exception("domain run failed: %s", exc)
        append_event(
            eng,
            domain_run_id=domain_run_id,
            seq=seq,
            event_type="run.error",
            phase="error",
            payload={"error": str(exc)},
        )
        return _finish(eng, created, status="failed", output={"error": str(exc)}, error=str(exc))


def run_domain_team(
    team_id: str,
    *,
    input_data: dict[str, Any] | None = None,
    dry_run: bool = False,
    autonomous: bool = False,
    scope: str = "admin",
    requested_by: str = "system",
    engine: Engine | None = None,
    member_mode: str = "manual",
) -> dict[str, Any]:
    """Run a first-class team boundedly (sequential)."""
    eng = engine or _engine()
    store.ensure_domain_agent_tables(eng)
    registry = get_domain_registry()
    team = registry.get_team(team_id)
    if team is None:
        return {"status": "not_found", "team_id": team_id, "error": "team not found"}
    input_data = input_data or {}
    member_mode = str(input_data.get("member_mode") or member_mode or "manual").lower().strip()
    if member_mode not in {"goal", "workflow", "manual"}:
        member_mode = "manual"
    members = list(team.agents)
    if len(members) > MAX_TEAM_MEMBERS:
        members = members[:MAX_TEAM_MEMBERS]
    created = store.create_domain_run(
        {
            "agent_id": f"team:{team.team_id}",
            "mode": "team",
            "status": "running",
            "input": redact({**input_data, "team_id": team.team_id, "member_mode": member_mode}),
            "output": {},
            "dry_run": bool(dry_run),
            "autonomous": bool(autonomous),
            "requested_by": requested_by,
            "scope": scope,
            "started_at": stamp(),
        },
        engine=eng,
    )
    domain_run_id = int(created["id"])
    seq = 0
    seq = _audit(
        eng,
        domain_run_id,
        seq,
        "team.start",
        phase="team",
        payload={
            "team_id": team.team_id,
            "members": members,
            "member_mode": member_mode,
            "dry_run": dry_run,
        },
    )
    child_runs = []
    status = "succeeded"
    for idx, agent_id in enumerate(members):
        report = registry.get_report(agent_id)
        if report is None:
            child_runs.append({"index": idx, "agent_id": agent_id, "status": "not_found"})
            status = "failed"
            seq = _audit(eng, domain_run_id, seq, "team.member.not_found", phase="team", payload={"agent_id": agent_id})
            continue
        # Bounded sequential execution, no uncontrolled fan-out.
        member_input = dict(input_data.get("member_input") or {})
        if isinstance(input_data.get("members"), dict) and isinstance(input_data["members"].get(agent_id), dict):
            member_input = {**member_input, **input_data["members"][agent_id]}
        seq = _audit(
            eng,
            domain_run_id,
            seq,
            "team.member.start",
            phase="team",
            payload={"agent_id": agent_id, "index": idx, "mode": member_mode},
        )
        child = run_domain_agent(
            agent_id,
            mode=member_mode,
            input_data=member_input,
            dry_run=dry_run,
            autonomous=False,
            scope=scope,
            requested_by=requested_by,
            engine=eng,
        )
        child_runs.append(
            {
                "index": idx,
                "agent_id": agent_id,
                "child_run_id": child.get("id"),
                "status": child.get("status"),
                "error": child.get("error"),
            }
        )
        seq = _audit(
            eng,
            domain_run_id,
            seq,
            "team.member.end",
            phase="team",
            payload={
                "agent_id": agent_id,
                "child_run_id": child.get("id"),
                "status": child.get("status"),
            },
        )
        if child.get("status") == "failed":
            status = "failed"
    output = {
        "team_id": team.team_id,
        "team_name": team.name,
        "lead": team.lead,
        "member_mode": member_mode,
        "dry_run": dry_run,
        "members": members,
        "child_runs": child_runs,
        "count": len(child_runs),
    }
    err = "" if status == "succeeded" else "one or more team members failed/not_found"
    out = _finish(eng, created, status=status, output=output, error=err)
    seq = _audit(
        eng,
        domain_run_id,
        seq,
        "team.end",
        phase="team",
        payload={"status": out.get("status"), "count": len(child_runs)},
    )
    return out
