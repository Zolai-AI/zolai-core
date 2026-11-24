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
from pathlib import Path
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


def _autonomous_env_enabled() -> bool:
    return os.environ.get("ZOLAI_AGENT_AUTONOMOUS", "").strip().lower() in {"1", "true", "yes", "on"}


def _autonomous_allowed(autonomous: bool, scope: str) -> tuple[bool, str]:
    if not autonomous:
        return True, ""
    if not _autonomous_env_enabled():
        return False, "Autonomous execution is disabled; set ZOLAI_AGENT_AUTONOMOUS=1."
    if str(scope or "").lower() != "admin":
        return False, "Autonomous execution requires admin scope."
    return True, ""


def _effective_allow(
    manifest_report: dict[str, Any], scope: str, granted: Any = None
) -> tuple[frozenset[str], list[dict[str, Any]]]:
    """manifest bound tools ∩ registry tier allow-list ∩ scope/grants.

    Admin-scope domain runs may execute bound registry tools that are admin-only
    in the tool catalog, while still honoring explicit spec scopes when granted
    scopes are present. This keeps domain agent manifests runnable without
    widening public/member access.
    """
    tier = tier_allow_list(scope, granted=granted)
    granted_set = set(granted) if granted is not None else None
    bound = []
    allowed: set[str] = set()
    for tb in manifest_report.get("tool_bindings") or []:
        name = str(tb.get("name") or "")
        spec = get_tool(name)
        is_bound = bool(tb.get("bound")) and name in tier
        # Admin domain scope: allow bound admin-only registry tools unless an
        # explicit spec scope is denied by provided grants.
        if not is_bound and bool(tb.get("bound")) and spec is not None and str(scope) == "admin":
            # Domain admin runs are already admin-authorized; allow admin-only tools.
            # Honor explicit spec scopes only when they are granted; if caller is admin
            # but spec scope missing, allow for admin runtime operability.
            spec_scope_ok = (
                spec.scope is None
                or granted_set is None
                or spec.scope in granted_set
                or "admin" in (granted_set or set())
            )
            if spec.admin_only and spec_scope_ok:
                is_bound = True
        # Adapter-bound tools (manifest_ref/manual_action) are runtime adapters,
        # not registry tools; they are allowed in manual/workflow plan execution
        # for any authenticated domain scope because they have no side effects.
        if (
            not is_bound
            and bool(tb.get("bound"))
            and spec is None
            and tb.get("adapter") in {"manifest_ref", "manual_action"}
        ):
            is_bound = True
        entry = {**tb, "effective_allowed": is_bound, "scope_tier": scope}
        if tb.get("bound") and not is_bound:
            entry["reason"] = f"bound tool not allowed for scope {scope}"
        if is_bound and name:
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _execute_adapter_step(
    name: str,
    *,
    binding: dict[str, Any],
    input_data: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    """Execute safe non-registry adapters.

    - manifest_ref: read-only metadata/snippet for files/paths in repo.
    - manual_action: explicit structured operator action; never executes shell.
    """
    adapter = str(binding.get("adapter") or "manual_action")
    declared = str(binding.get("declared_name") or name)
    if adapter == "manifest_ref":
        raw_path = declared
        p = ( _repo_root() / raw_path ).resolve()
        root = _repo_root().resolve()
        exists = p.exists() and root in p.parents or p == root
        info: dict[str, Any] = {
            "status": "completed",
            "adapter": "manifest_ref",
            "path": raw_path,
            "exists": bool(exists),
            "dry_run": bool(dry_run),
        }
        if exists and p.is_file():
            try:
                data = p.read_text(encoding="utf-8", errors="replace")
                info["size_bytes"] = p.stat().st_size
                info["preview"] = data[:1200]
                info["truncated"] = len(data) > 1200
            except Exception as exc:
                info["status"] = "failed"
                info["error"] = str(exc)
        elif exists and p.is_dir():
            try:
                children = sorted(x.name for x in p.iterdir())[:100]
                info["children"] = children
                info["child_count"] = len(children)
            except Exception as exc:
                info["status"] = "failed"
                info["error"] = str(exc)
        else:
            info["status"] = "manual_action_required"
            info["reason"] = "path not found in repo; operator should create/provide it"
        return info
    # manual_action
    return {
        "status": "manual_action_required",
        "adapter": "manual_action",
        "tool": declared,
        "dry_run": bool(dry_run),
        "reason": "explicit operator action required; runtime does not execute arbitrary commands/remote side effects",
        "input": redact(input_data),
        "instruction": f"Perform manual action for `{declared}` and provide outputs/artifacts if needed.",
    }


def _run_manual(
    manifest, report, *, input_data: dict[str, Any], dry_run: bool, scope: str, granted: Any = None
) -> dict[str, Any]:
    allow, tool_bindings = _effective_allow(report, scope, granted=granted)
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
    domain_run_id: int, seq: int, granted: Any = None) -> tuple[dict[str, Any], int]:
    allow, _ = _effective_allow(report, scope, granted=granted)
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
    tool_bindings = {t.get("name"): t for t in (report.get("tool_bindings") or []) if isinstance(t, dict)}
    for s in (wf.get("steps") or [])[:MAX_STEPS]:
        step_name = str(s.get("name") or "")
        bound = bool(s.get("bound"))
        binding = tool_bindings.get(step_name, {})
        adapter = binding.get("adapter")
        spec = get_tool(step_name)
        if not bound:
            results.append({"step": s, "status": "unsupported", "reason": s.get("reason") or "not bound"})
            status = "manual" if status == "succeeded" else status
            seq = _audit(
                eng, domain_run_id, seq, "workflow.step.skipped", phase="workflow", tool_name=step_name,
                payload={"reason": s.get("reason"), "bound": bound},
            )
            continue
        call_input = (
            input_data.get("step_input")
            if isinstance(input_data.get("step_input"), dict)
            else {"query": str(input_data.get("goal") or input_data.get("task") or ""), "input": redact(input_data)}
        )
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
            payload={"input": redact(call_input), "adapter": adapter or "registry"},
        )
        # Adapter-handled steps (manifest refs / explicit manual actions)
        if adapter in {"manifest_ref", "manual_action"} or spec is None:
            action = _execute_adapter_step(
                step_name, binding=binding, input_data=call_input, dry_run=dry_run
            )
            row_status = action.get("status", "manual_action_required")
            results.append(
                {
                    "step": s,
                    "status": row_status,
                    "tool": step_name,
                    "adapter": adapter or "manual_action",
                    "result": action,
                }
            )
            seq = _audit(
                eng,
                domain_run_id,
                seq,
                "workflow.step.end",
                phase="workflow",
                tool_name=step_name,
                payload={"status": row_status, "adapter": adapter or "manual_action"},
            )
            # manual_action_required is explicit and not a runtime failure
            if row_status == "failed":
                status = "failed"
                break
            continue
        if step_name not in allow:
            results.append(
                {
                    "step": s,
                    "status": "blocked",
                    "reason": "tool not in effective allow-list",
                    "tool": step_name,
                }
            )
            status = "failed"
            seq = _audit(
                eng,
                domain_run_id,
                seq,
                "workflow.step.blocked",
                phase="workflow",
                tool_name=step_name,
                payload={"reason": "not in allow-list"},
            )
            break
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
                payload={"input": redact(call_input)},
            )
            continue
        res = execute_tool(ToolCall(name=step_name, input=call_input, source="workflow"), allow, turn=s.get("index", 0))
        row = res.to_dict()
        row["status"] = "completed" if res.ok else "failed"
        row["data"] = redact(res.data) if res.ok else None
        row["error"] = res.error
        results.append({"step": s, "status": row["status"], "tool": step_name, "result": row})
        seq = _audit(
            eng, domain_run_id, seq, "workflow.step.end", phase="workflow", tool_name=step_name,
            payload={"ok": res.ok, "error_code": getattr(res.error_code, "value", res.error_code)},
        )
        if not res.ok:
            status = "failed"
            break
    return {"workflow": workflow_name, "status": status, "steps": results}, seq


def _run_goal(manifest, report, *, input_data: dict[str, Any], dry_run: bool, autonomous: bool, scope: str,
    requested_by: str, eng: Engine, domain_run_id: int, seq: int, granted: Any = None) -> tuple[dict[str, Any], int]:
    from ..orchestrator import run_agent_goal

    goal = str(input_data.get("goal") or input_data.get("task") or input_data.get("query") or "").strip()
    if not goal:
        return {"status": "failed", "error": "goal is required for goal mode"}, seq
    allow, _ = _effective_allow(report, scope, granted=granted)
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
    run = run_agent_goal(goal, created_by=requested_by, scope=scope, granted=granted, tool_allowlist=allow)
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
    granted: Any = None,
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
    auto_ok, auto_reason = _autonomous_allowed(autonomous, scope)
    if autonomous and not auto_ok:
        created = store.create_domain_run(
            {
                "agent_id": manifest.agent_id,
                "mode": mode,
                "status": "blocked",
                "input": redact(input_data),
                "output": {"status": "blocked", "reason": auto_reason},
                "error": auto_reason,
                "dry_run": bool(dry_run),
                "autonomous": False,
                "requested_by": requested_by,
                "scope": scope,
                "started_at": stamp(),
                "finished_at": stamp(),
            },
            engine=eng,
        )
        _audit(eng, int(created["id"]), 0, "run.blocked", phase="start", payload={"reason": auto_reason})
        return created

    if autonomous and auto_ok:
        created = store.create_domain_run(
            {
                "agent_id": manifest.agent_id,
                "mode": mode,
                "status": "queued",
                "input": redact(input_data),
                "output": {"status": "queued", "mode": mode},
                "dry_run": bool(dry_run),
                "autonomous": True,
                "requested_by": requested_by,
                "scope": scope,
                "started_at": stamp(),
            },
            engine=eng,
        )
        domain_run_id = int(created["id"])
        _audit(eng, domain_run_id, 0, "run.queued", phase="start", payload={"mode": mode, "scope": scope})
        from .queue import enqueue_domain_run

        enqueue_domain_run(domain_run_id, priority=0)
        return store.get_domain_run(domain_run_id, engine=eng) or created

    created = store.create_domain_run(
        {
            "agent_id": manifest.agent_id,
            "mode": mode,
            "status": "running",
            "input": redact(input_data),
            "output": {},
            "dry_run": bool(dry_run),
            "autonomous": False,
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
            "autonomous": False,
            "scope": scope,
        },
    )
    try:
        if mode == "manual":
            output = _run_manual(manifest, report, input_data=input_data, dry_run=dry_run, scope=scope, granted=granted)
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
                granted=granted,
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
                granted=granted,
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
            # MVP deviation: synchronous bounded fan-out (no per-item queue enqueue).
            # Bounded by MAX_BATCH_ITEMS to prevent uncontrolled fan-out.
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
                    granted=granted,
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


def _execute_queued_team_run(
    domain_run_id: int, run: dict[str, Any], *, eng: Engine, granted: Any = None
) -> dict[str, Any]:
    registry = get_domain_registry()
    team_id = str(run.get("agent_id") or "").removeprefix("team:")
    team = registry.get_team(team_id)
    if team is None:
        return _finish(eng, run, status="failed", output={"error": "team not found"}, error="team not found")
    input_data = run.get("input") if isinstance(run.get("input"), dict) else {}
    member_mode = str(input_data.get("member_mode") or "manual").lower().strip()
    if member_mode not in {"goal", "workflow", "manual"}:
        member_mode = "manual"
    dry_run = bool(run.get("dry_run"))
    scope = str(run.get("scope") or "admin")
    requested_by = str(run.get("requested_by") or "worker")
    members = list(team.agents)[:MAX_TEAM_MEMBERS]
    store.update_domain_run(domain_run_id, engine=eng, status="running", started_at=stamp(), error="")
    created = store.get_domain_run(domain_run_id, engine=eng) or run
    seq = 0
    seq = _audit(eng, domain_run_id, seq, "team.start", phase="team", payload={
        "team_id": team.team_id, "members": members, "member_mode": member_mode, "dry_run": dry_run, "queued": True,
    })
    child_runs = []
    status = "succeeded"
    for idx, agent_id in enumerate(members):
        report = registry.get_report(agent_id)
        if report is None:
            child_runs.append({"index": idx, "agent_id": agent_id, "status": "not_found"})
            status = "failed"
            seq = _audit(eng, domain_run_id, seq, "team.member.not_found", phase="team", payload={"agent_id": agent_id})
            continue
        member_input = dict(input_data.get("member_input") or {})
        if isinstance(input_data.get("members"), dict) and isinstance(input_data["members"].get(agent_id), dict):
            member_input = {**member_input, **input_data["members"][agent_id]}
        seq = _audit(eng, domain_run_id, seq, "team.member.start", phase="team",
            payload={"agent_id": agent_id, "index": idx, "mode": member_mode})
        child = run_domain_agent(
            agent_id, mode=member_mode, input_data=member_input, dry_run=dry_run, autonomous=False,
            scope=scope, requested_by=requested_by, granted=granted, engine=eng,
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
        seq = _audit(eng, domain_run_id, seq, "team.member.end", phase="team",
            payload={"agent_id": agent_id, "child_run_id": child.get("id"), "status": child.get("status")})
        if child.get("status") == "failed":
            status = "failed"
    output = {
        "team_id": team.team_id, "team_name": team.name, "lead": team.lead, "member_mode": member_mode,
        "dry_run": dry_run, "members": members, "child_runs": child_runs, "count": len(child_runs),
    }
    err = "" if status == "succeeded" else "one or more team members failed/not_found"
    out = _finish(eng, created, status=status, output=output, error=err)
    _audit(
        eng, domain_run_id, seq, "team.end", phase="team",
        payload={"status": out.get("status"), "count": len(child_runs)},
    )
    return out


def execute_queued_domain_run(
    domain_run_id: int,
    *,
    engine: Engine | None = None,
    granted: Any = None,
) -> dict[str, Any]:
    """Execute an already-queued domain run in-place (no duplicate row)."""
    eng = engine or _engine()
    store.ensure_domain_agent_tables(eng)
    run = store.get_domain_run(domain_run_id, engine=eng)
    if run is None:
        return {"status": "not_found", "domain_run_id": domain_run_id, "error": "domain run not found"}
    if run.get("status") not in {"queued", "running"}:
        return run
    agent_id = str(run.get("agent_id") or "")
    if agent_id.startswith("team:"):
        return _execute_queued_team_run(domain_run_id, run, eng=eng, granted=granted)
    registry = get_domain_registry()
    report_obj = registry.get_report(agent_id)
    manifest = registry.get_manifest(agent_id)
    if report_obj is None or manifest is None:
        return _finish(eng, run, status="failed", output={"error": "manifest not found"}, error="manifest not found")
    report = report_obj.to_dict()
    mode = str(run.get("mode") or "manual").lower().strip()
    input_data = run.get("input") if isinstance(run.get("input"), dict) else {}
    dry_run = bool(run.get("dry_run"))
    scope = str(run.get("scope") or "admin")
    requested_by = str(run.get("requested_by") or "worker")
    store.update_domain_run(
        domain_run_id,
        engine=eng,
        status="running",
        started_at=stamp(),
        error="",
    )
    created = store.get_domain_run(domain_run_id, engine=eng) or run
    seq = 0
    seq = _audit(eng, domain_run_id, seq, "run.start", phase="start", payload={
        "agent_id": agent_id, "mode": mode, "dry_run": dry_run, "autonomous": True, "scope": scope, "queued": True,
    })
    try:
        if mode == "manual":
            output = _run_manual(manifest, report, input_data=input_data, dry_run=dry_run, scope=scope, granted=granted)
            seq = _audit(eng, domain_run_id, seq, "run.plan", phase="manual", payload={"status": output.get("status")})
            out = _finish(eng, created, status="manual", output=output)
        elif mode == "workflow":
            output, seq = _run_workflow(
                manifest, report, input_data=input_data, dry_run=dry_run, scope=scope, eng=eng,
                domain_run_id=domain_run_id, seq=seq, granted=granted,
            )
            status = str(output.get("status") or "manual")
            final_status = "succeeded" if status == "succeeded" else ("failed" if status == "failed" else "manual")
            out = _finish(eng, created, status=final_status, output=output, error=str(output.get("error") or ""))
        elif mode == "goal":
            output, seq = _run_goal(
                manifest, report, input_data=input_data, dry_run=dry_run, autonomous=True, scope=scope,
                requested_by=requested_by, eng=eng, domain_run_id=domain_run_id, seq=seq, granted=granted,
            )
            final_status = str(output.get("status") or "failed")
            if final_status not in {"succeeded", "failed", "dry_run", "manual"}:
                final_status = "failed"
            final = (
                "succeeded"
                if final_status == "succeeded"
                else ("manual" if final_status in {"dry_run", "manual"} else "failed")
            )
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
                seq = _audit(eng, domain_run_id, seq, "batch.item.start", phase="batch",
                    payload={"index": i, "child_mode": child_mode})
                child = run_domain_agent(
                    manifest.agent_id, mode=child_mode, input_data=item_input, dry_run=dry_run,
                    autonomous=False, scope=scope, requested_by=requested_by, granted=granted, engine=eng,
                )
                results.append(
                    {
                        "index": i,
                        "child_run_id": child.get("id"),
                        "status": child.get("status"),
                        "error": child.get("error"),
                    }
                )
                seq = _audit(eng, domain_run_id, seq, "batch.item.end", phase="batch",
                    payload={"index": i, "status": child.get("status")})
                if child.get("status") == "failed":
                    batch_status = "failed"
            out = _finish(eng, created, status=batch_status, output={
                "mode": "batch", "child_mode": child_mode, "items": results, "count": len(results),
            })
        else:
            out = _finish(eng, created, status="failed", output={}, error=f"unsupported mode: {mode}")
        _audit(eng, domain_run_id, seq, "run.end", phase="end", payload={"status": out.get("status")})
        return out
    except Exception as exc:
        logger.exception("queued domain run failed: %s", exc)
        append_event(
            eng, domain_run_id=domain_run_id, seq=seq, event_type="run.error", phase="error",
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
    granted: Any = None,
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
    auto_ok, auto_reason = _autonomous_allowed(autonomous, scope)
    if autonomous and not auto_ok:
        created = store.create_domain_run(
            {
                "agent_id": f"team:{team.team_id}",
                "mode": "team",
                "status": "blocked",
                "input": redact({**input_data, "team_id": team.team_id, "member_mode": member_mode}),
                "output": {"status": "blocked", "reason": auto_reason},
                "error": auto_reason,
                "dry_run": bool(dry_run),
                "autonomous": False,
                "requested_by": requested_by,
                "scope": scope,
                "started_at": stamp(),
                "finished_at": stamp(),
            },
            engine=eng,
        )
        _audit(eng, int(created["id"]), 0, "run.blocked", phase="start", payload={"reason": auto_reason})
        return created
    if autonomous and auto_ok:
        created = store.create_domain_run(
            {
                "agent_id": f"team:{team.team_id}",
                "mode": "team",
                "status": "queued",
                "input": redact({**input_data, "team_id": team.team_id, "member_mode": member_mode}),
                "output": {"status": "queued", "mode": "team"},
                "dry_run": bool(dry_run),
                "autonomous": True,
                "requested_by": requested_by,
                "scope": scope,
                "started_at": stamp(),
            },
            engine=eng,
        )
        _audit(eng, int(created["id"]), 0, "run.queued", phase="start", payload={"team_id": team.team_id})
        from .queue import enqueue_domain_run

        enqueue_domain_run(int(created["id"]), priority=0)
        return store.get_domain_run(int(created["id"]), engine=eng) or created
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
            "autonomous": False,
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
            granted=granted,
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
