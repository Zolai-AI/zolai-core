"""``zolai agent`` CLI (P3) — run / list / show against the local store.

Registered from ``zolai.cli.main`` (``app.add_typer(agent_app, name="agent")``).
Distinct from the orphaned ``zolai/cli/agent.py`` (never registered).
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

agent_app = typer.Typer(help="Agent runs: research → build → review → shipped (rule-mode offline).")
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
