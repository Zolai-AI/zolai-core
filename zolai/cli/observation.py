"""``zolai observation`` — Phase 2 observation-engine CLI (§36).

Three subcommands over the observation layer:

- ``build``         — run the capability 1-7 pipeline into the three rebuildable
  derived tables (``observations``, ``word_observation_stats``,
  ``attestation_index`` untouched here).  Writes by default (the layer is a
  derived, idempotent cache — ``ux_obs_source_ref`` makes re-runs no-ops) and
  skips per-row ``data_audit_log`` rows by design (plan deviation 5).  Use
  ``--dry-run`` for a read-only report.
- ``refresh-index`` — materialize ``attestation_index`` from the loader
  queries (§27 parity, ``prefer_index=False``).
- ``bloom``         — write the optional negative-only Bloom artifact.

Rule mode only: offline, deterministic, no LLM (§25).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

__all__ = ["observation_app"]

observation_app = typer.Typer(
    name="observation",
    help="🔭 Observation engine — build observations/stats, refresh the attestation index, write the Bloom artifact.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


def _emit(payload: dict, json_out: bool) -> None:
    if json_out:
        typer.echo(json.dumps(payload, indent=2, default=str))
        raise typer.Exit(code=0)


def _split_sources(sources: Optional[str]) -> list[str] | None:
    if not sources:
        return None
    return [part.strip() for part in sources.split(",") if part.strip()]


def _dry_run_report(engine, sources: list[str] | None, limit: int | None) -> dict:
    """Read-only preview: which sources exist and how many sentences each yields."""
    from sqlalchemy import text as sa_text

    from zolai.foundation.observation.sentences import (
        _require_safe_ident,
        available_sources,
        select_sources,
    )

    requested = select_sources(sources)
    available = {s.name for s in available_sources(engine)}
    selected = [s for s in requested if s.name in available]
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for source in selected:
            _require_safe_ident(source.table)
            sql = (
                "SELECT COUNT(*) FROM (SELECT 1 FROM "
                f"{source.table} WHERE ({source.where})"
                + (" LIMIT :n)" if limit is not None else ")")
            )
            params = {"n": limit} if limit is not None else {}
            counts[source.name] = int(conn.execute(sa_text(sql), params).scalar() or 0)
    return {
        "dry_run": True,
        "sources_requested": [s.name for s in requested],
        "sources_available": sorted(available),
        "sources_selected": [s.name for s in selected],
        "sources_skipped": [s.name for s in requested if s.name not in available],
        "sentences_by_source": counts,
        "sentences": sum(counts.values()),
        "limit": limit,
    }


@observation_app.command("build")
def observation_build(
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", help="Max sentences per source (default: all rows)"
    ),
    sources: Optional[str] = typer.Option(
        None,
        "--sources",
        help="Comma-separated source names (default: all available). "
        "Known: bible_tdb77, bible_tedim2010, translations_zo, phrases",
    ),
    db: Optional[Path] = typer.Option(
        None, "--db", help="SQLite store (default: canonical data/zolai.db)"
    ),
    no_attest: bool = typer.Option(
        False,
        "--no-attest",
        help="Skip wiring word attestation into the stats rows (faster; column stays empty)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be built — writes nothing"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the raw summary as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🏗 Build the observations + word stats layer (rebuildable, idempotent)."""
    _setup_logging(verbose)
    from zolai.data.repositories import get_engine

    engine = get_engine(db)
    try:
        if dry_run:
            report = _dry_run_report(engine, _split_sources(sources), limit)
            _emit(report, json_out)
            rprint(
                "[bold yellow]DRY-RUN[/bold yellow] · observation build · "
                f"{report['sentences']} sentences across "
                f"{len(report['sources_selected'])} source(s)"
            )
            for name, count in report["sentences_by_source"].items():
                rprint(f"[cyan]{name}:[/cyan] {count} sentences")
            if report["sources_skipped"]:
                rprint(f"[yellow]skipped (missing table):[/yellow] {report['sources_skipped']}")
            raise typer.Exit(code=0)

        attestor = None
        if not no_attest:
            from zolai.learning.word_attestation import WordAttestation

            attestor = WordAttestation(db_path=db).attest_word

        from zolai.foundation.observation import build_observations

        try:
            summary = build_observations(
                engine,
                sources=_split_sources(sources),
                limit=limit,
                attestor=attestor,
            )
        except ValueError as exc:
            rprint(f"[red]✗ {exc}[/red]")
            raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    _emit(summary, json_out)
    rprint(
        "[bold green]BUILD[/bold green] · observation build · "
        f"{summary['observations_inserted']} observations inserted "
        f"({summary['observations']} seen) · {summary['stats_rows']} stats rows · "
        f"{summary['tokens']} tokens · {summary['elapsed_seconds']}s"
    )
    rprint(f"[cyan]sources:[/cyan] {', '.join(summary['sources'])} (limit={summary['limit']})")
    if summary["sources_skipped"]:
        rprint(f"[yellow]skipped:[/yellow] {summary['sources_skipped']}")


@observation_app.command("refresh-index")
def observation_refresh_index(
    db: Optional[Path] = typer.Option(
        None, "--db", help="SQLite store (default: canonical data/zolai.db)"
    ),
    keep_existing: bool = typer.Option(
        False, "--keep-existing", help="Do not rebuild a populated index"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the raw summary as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔁 Rebuild ``attestation_index`` from the loader queries (§27 parity)."""
    _setup_logging(verbose)
    from zolai.learning.word_attestation import build_attestation_index

    stats = build_attestation_index(db_path=db, force=not keep_existing)
    _emit(stats, json_out)
    if stats.get("rebuilt"):
        rprint(
            "[bold green]INDEX[/bold green] · attestation_index rebuilt · "
            f"{stats['pairs_written']} pairs · {stats['elapsed_seconds']}s"
        )
        rprint(f"[cyan]sources:[/cyan] {stats['sources']}")
    else:
        rprint(f"[yellow]kept[/yellow] · {stats.get('reason', 'index untouched')}")


@observation_app.command("bloom")
def observation_bloom(
    db: Optional[Path] = typer.Option(
        None, "--db", help="SQLite store (default: canonical data/zolai.db)"
    ),
    out_dir: Optional[Path] = typer.Option(
        None, "--out-dir", help="Artifact directory (default: data/attestation)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the raw summary as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🌸 Write the optional negative-only Bloom artifact (never changes verdicts)."""
    _setup_logging(verbose)
    from zolai.learning.word_attestation import build_attestation_bloom

    stats = build_attestation_bloom(db_path=db, out_dir=out_dir)
    _emit(stats, json_out)
    rprint(
        "[bold green]BLOOM[/bold green] · "
        f"{stats['count']} words · {stats['bits']} bits · "
        f"origin={stats['origin']} · {stats['elapsed_seconds']}s"
    )
    rprint(f"[cyan]json:[/cyan] {stats['json']}")
    rprint(f"[cyan]bin:[/cyan] {stats['bin']}")
