"""``zolai agent`` CLI (P3 + domain runtime, additive).

Keeps existing commands: ``run``, ``list``, ``show``.
Adds domain commands: ``agents list|validate|run``, ``teams list|show|run``,
``runs status|logs``, ``worker``.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

agent_app = typer.Typer(help="Agent runs: research → build → review → shipped (rule-mode offline).")
agents_app = typer.Typer(help="Domain agent manifests.")
teams_app = typer.Typer(help="Domain agent teams.")
runs_app = typer.Typer(help="Domain agent runs.")
agent_app.add_typer(agents_app, name="agents")
agent_app.add_typer(teams_app, name="teams")
agent_app.add_typer(runs_app, name="runs")
console = Console()


def _fmt_run(run: dict[str, Any]) -> None:
    table = Table(title=f"agent run #{run.get('id')}")
    table.add_column("field")
    table.add_column("value")
    for key in (
        "status",
        "goal",
        "outcome",
        "provider",
        "model",
        "turns",
        "latency_ms",
        "feedback_score",
        "error",
        "created_by",
        "created_at",
    ):
        table.add_row(key, str(run.get(key, "")))
    console.print(table)
    phases = run.get("phases") or {}
    if phases:
        pt = Table(title="phases")
        pt.add_column("phase")
        pt.add_column("status")
        pt.add_column("detail")
        for name, info in phases.items():
            if isinstance(info, dict):
                detail = ", ".join(f"{k}={v}" for k, v in info.items() if k != "status")
            else:
                detail = str(info)
            pt.add_row(name, str((info or {}).get("status", "")) if isinstance(info, dict) else "", detail)
        console.print(pt)
    if run.get("answer"):
        console.print("[bold]answer[/bold]")
        console.print(str(run["answer"]))


# ── Existing P3 commands (backward compatible) ───────────────────────────────

@agent_app.command("run")
def run_command(
    goal: str = typer.Argument(..., help="What the agent should accomplish"),
    created_by: str = typer.Option("cli", "--as", help="Label stored on the run"),
    scope: str = typer.Option("admin", "--scope", help="Tool scope: public|member|admin"),
) -> None:
    """Execute one agent run end-to-end (offline in rule mode)."""
    from .orchestrator import run_agent_goal

    try:
        run = run_agent_goal(goal, created_by=created_by, scope=scope)
    except ValueError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _fmt_run(run)
    if run.get("status") != "succeeded":
        raise typer.Exit(code=1)


@agent_app.command("list")
def list_command(limit: int = typer.Option(20, "--limit", "-n")) -> None:
    """List recent agent runs."""
    from . import store

    runs = store.list_runs(limit=limit)
    if not runs:
        console.print("no agent runs yet")
        return
    table = Table(title="agent runs")
    for col in ("id", "status", "goal", "outcome", "provider", "model", "created_at"):
        table.add_column(col)
    for run in runs:
        table.add_row(
            str(run.get("id")),
            str(run.get("status")),
            str(run.get("goal"))[:60],
            str(run.get("outcome")),
            str(run.get("provider")),
            str(run.get("model")),
            str(run.get("created_at")),
        )
    console.print(table)


@agent_app.command("show")
def show_command(run_id: int = typer.Argument(..., help="Run id")) -> None:
    """Show one run in full (phases, trace, answer)."""
    from . import store

    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]error:[/red] run {run_id} not found")
        raise typer.Exit(code=1)
    _fmt_run(run)
    trace = run.get("tool_calls") or []
    if trace:
        tt = Table(title="tool trace")
        for col in ("turn", "name", "ok", "latency_ms"):
            tt.add_column(col)
        for call in trace:
            if isinstance(call, dict):
                tt.add_row(
                    str(call.get("turn", "")),
                    str(call.get("name", "")),
                    str(call.get("ok", "")),
                    str(call.get("latency_ms", "")),
                )
        console.print(tt)


# ── Domain agents ────────────────────────────────────────────────────────────

@agents_app.command("list")
def agents_list(json_output: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """List domain agent manifests with binding status."""
    from .domain.registry import get_domain_registry

    reg = get_domain_registry(force=True)
    reports = [r.to_dict() for r in reg.reports()]
    if json_output:
        console.print_json(json.dumps({"items": reports, "summary": reg.summary()}, ensure_ascii=False))
        return
    table = Table(title="domain agents")
    for col in ("agent_id", "name", "status", "tools", "workflows"):
        table.add_column(col)
    for r in reports:
        tools = sum(1 for t in r.get("tool_bindings", []) if t.get("bound"))
        wfs = sum(1 for w in r.get("workflow_bindings", []) if w.get("bound"))
        table.add_row(r["agent_id"], r.get("name", "")[:32], r.get("status", ""), f"{tools}/{len(r.get('tool_bindings',
            []))}", f"{wfs}/{len(r.get('workflow_bindings', []))}")
    console.print(table)
    console.print(reg.summary())


@agents_app.command("validate")
def agents_validate(
    agent_id: str = typer.Argument(None, help="Agent id (omit with --all)"),
    all_agents: bool = typer.Option(False, "--all", help="Validate all manifests"),
) -> None:
    """Validate manifest(s) and report binding status."""
    from .domain.registry import get_domain_registry

    if not agent_id and not all_agents:
        console.print("[red]error:[/red] provide AGENT_ID or --all")
        raise typer.Exit(code=2)
    reg = get_domain_registry(force=True)
    reports = reg.validate(None if all_agents else agent_id)
    console.print_json(json.dumps({"items": reports}, ensure_ascii=False))
    bad = [r for r in reports if r.get("status") in {"not_found", "invalid"}]
    if bad:
        raise typer.Exit(code=1)


@agents_app.command("run")
def agents_run(
    agent_id: str = typer.Argument(..., help="Agent id"),
    mode: str = typer.Option("manual", "--mode", help="goal|workflow|batch|manual"),
    input_json: str = typer.Option("{}", "--input", help="JSON input"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Block side effects"),
    autonomous: bool = typer.Option(False, "--autonomous", help="Request autonomous/queued execution"),
    scope: str = typer.Option("admin", "--scope", help="public|member|admin"),
) -> None:
    """Run a domain agent manifest."""
    from .domain.runner import run_domain_agent

    try:
        input_data = json.loads(input_json)
    except Exception as exc:
        console.print(f"[red]error:[/red] invalid --input JSON: {exc}")
        raise typer.Exit(code=2) from exc
    result = run_domain_agent(agent_id, mode=mode, input_data=input_data, dry_run=dry_run, autonomous=autonomous,
        scope=scope, requested_by="cli")
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


# ── Teams ────────────────────────────────────────────────────────────────────

@teams_app.command("list")
def teams_list(json_output: bool = typer.Option(False, "--json")) -> None:
    from .domain.registry import get_domain_registry

    reg = get_domain_registry(force=True)
    items = reg.validate_team(None)
    if json_output:
        console.print_json(json.dumps({"items": items}, ensure_ascii=False))
        return
    table = Table(title="domain teams")
    for col in ("team_id", "name", "lead", "agents", "status"):
        table.add_column(col)
    for t in items:
        table.add_row(t["team_id"], t.get("name", "")[:32], t.get("lead", ""), str(len(t.get("agents", []))),
            t.get("status", ""))
    console.print(table)


@teams_app.command("show")
def teams_show(team_id: str = typer.Argument(...)) -> None:
    from .domain.registry import get_domain_registry

    reg = get_domain_registry(force=True)
    items = reg.validate_team(team_id)
    if not items or items[0].get("status") == "not_found":
        console.print(f"[red]error:[/red] team {team_id} not found")
        raise typer.Exit(code=1)
    console.print_json(json.dumps(items[0], ensure_ascii=False))


@teams_app.command("run")
def teams_run(
    team_id: str = typer.Argument(...),
    input_json: str = typer.Option("{}", "--input"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    member_mode: str = typer.Option("manual", "--member-mode", help="goal|workflow|manual"),
    scope: str = typer.Option("admin", "--scope"),
) -> None:
    from .domain.runner import run_domain_team

    try:
        input_data = json.loads(input_json)
    except Exception as exc:
        console.print(f"[red]error:[/red] invalid --input JSON: {exc}")
        raise typer.Exit(code=2) from exc
    input_data.setdefault("member_mode", member_mode)
    result = run_domain_team(team_id, input_data=input_data, dry_run=dry_run, scope=scope, requested_by="cli",
        member_mode=member_mode)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


# ── Domain runs ──────────────────────────────────────────────────────────────

@runs_app.command("status")
def runs_status(run_id: int = typer.Argument(...)) -> None:
    from . import store

    run = store.get_domain_run(run_id)
    if run is None:
        console.print(f"[red]error:[/red] domain run {run_id} not found")
        raise typer.Exit(code=1)
    console.print_json(json.dumps(run, ensure_ascii=False, default=str))


@runs_app.command("logs")
def runs_logs(run_id: int = typer.Argument(...), tail: int = typer.Option(200, "--tail", "-n"),
    json_output: bool = typer.Option(False, "--json")) -> None:
    from ..data.repositories import get_engine
    from .domain.audit import list_events

    events = list_events(get_engine(), run_id, limit=tail)
    if json_output:
        console.print_json(json.dumps({"items": events}, ensure_ascii=False))
        return
    for e in events:
        console.print(f"[{e.get('seq')}] {e.get('event_type')} phase={e.get('phase')} tool={e.get('tool_name')} {
            json.dumps(e.get('payload', {}), ensure_ascii=False)[:200]}")


@agent_app.command("worker")
def worker_command(
    once: bool = typer.Option(False, "--once", help="Process at most one queued job"),
    poll: int = typer.Option(5, "--poll", help="Poll interval seconds when not --once"),
) -> None:
    """Bounded domain queue worker (disabled unless ZOLAI_AGENT_AUTONOMOUS=1)."""
    from .domain.queue import run_worker

    result = run_worker(once=once, poll_seconds=poll)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
