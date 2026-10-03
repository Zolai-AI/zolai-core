"""``zolai discovery`` — Phase 3 discovery-engine CLI (§36).

Subcommands:
- ``build`` — run the 5-capability discovery pipeline (POS, morphology, collocations,
  sentence patterns, grammar phenomena). Writes to ``hypotheses`` and ``grammar_patterns``.
  Idempotent, capped, offline rule-mode. Use ``--dry-run`` for read-only report.

Rule mode only: offline, deterministic, no LLM (§34/§36).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

__all__ = ["discovery_app"]

discovery_app = typer.Typer(
    name="discovery",
    help="🔬 Discovery engine — build POS/morphology/collocation/grammar hypotheses from corpus evidence.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Valid capabilities
VALID_CAPABILITIES = ["pos", "morphology", "collocation", "sentence_patterns", "grammar"]


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


def _emit(payload: dict, json_out: bool) -> None:
    if json_out:
        typer.echo(json.dumps(payload, indent=2, default=str))
        raise typer.Exit(code=0)


def _split_capabilities(caps: Optional[str]) -> list[str] | None:
    if not caps:
        return None
    parts = [part.strip() for part in caps.split(",") if part.strip()]
    invalid = [p for p in parts if p not in VALID_CAPABILITIES]
    if invalid:
        rprint(f"[red]✗ Invalid capabilities:[/red] {invalid} (valid: {VALID_CAPABILITIES})")
        raise typer.Exit(code=1)
    return parts


@discovery_app.command("build")
def discovery_build(
    cap: Optional[str] = typer.Option(
        None,
        "--cap",
        "-c",
        help="Comma-separated capabilities to run (default: all). "
        "Valid: pos,morphology,collocation,sentence_patterns,grammar",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", help="Max source rows per capability (default: all)"
    ),
    db: Optional[Path] = typer.Option(
        None, "--db", help="SQLite store (default: canonical data/zolai.db)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be built — writes nothing"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the raw summary as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🏗 Build discovery hypotheses (rebuildable, idempotent, capped)."""
    _setup_logging(verbose)

    from zolai.data.repositories import get_engine

    engine = get_engine(db)
    capabilities = _split_capabilities(cap)

    try:
        if dry_run:
            rprint("[bold yellow]DRY-RUN[/bold yellow] · discovery build")
            rprint(f"Capabilities: {capabilities or 'all'}")
            rprint(f"Limit per capability: {limit or 'unlimited'}")
            rprint("Caps: POS/morph 5k, colloc 1k, patterns few hundred")
            raise typer.Exit(code=0)

        from zolai.learning.discovery import build_discovery

        summary = build_discovery(
            engine,
            capabilities=capabilities,
            limit=limit,
            dry_run=False,
        )
    except Exception as exc:
        rprint(f"[red]✗ {exc}[/red]")
        import traceback
        traceback.print_exc()
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    _emit(summary, json_out)

    caps_run = ", ".join(summary.get("capabilities_run", []))
    rprint(
        f"[bold green]BUILD[/bold green] · discovery · "
        f"capabilities: {caps_run} · {summary.get('elapsed_seconds', 0):.1f}s"
    )

    for cap_name in summary.get("capabilities_run", []):
        cap_summary = summary.get(cap_name, {})
        written = cap_summary.get("hypotheses_written") or cap_summary.get(
            "relations_written"
        ) or cap_summary.get("collocations_written") or cap_summary.get(
            "patterns_written"
        ) or 0
        rprint(f"  [cyan]{cap_name}:[/cyan] {written} written")

    if summary.get("status_counts"):
        rprint(f"  [dim]Status:[/dim] {summary['status_counts']}")
