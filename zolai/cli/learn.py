"""``zolai learn`` — run, inspect, and verify the local learning cycle.

Default mode is dry-run: it studies the data, reports what an apply would do,
and writes only the JSON report. ``--apply`` performs a verified backup, the
knowledge steps, the engine retrains, the incremental RAG update, and the
version snapshot, recording every step in the ledger.

Exit codes: 0 success/dry-run, 1 step or gate failure, 2 config/preflight,
3 version drift, 130 interrupt.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy.engine import Engine

from zolai.config import config
from zolai.knowledge.versioning import (
    get_knowledge_version,
    list_knowledge_versions,
    verify_knowledge_version,
)
from zolai.learning.cycle import (
    CycleInterrupted,
    LearningCycleOptions,
    PreflightError,
    VersionDriftError,
    post_apply_notice,
    render_status,
    run_learning_cycle,
)
from zolai.learning.run_ledger import (
    DEFAULT_STALE_AFTER_S,
    ensure_learning_run_tables,
    reap_stale_runs,
    sanitize_error,
    stale_running_runs,
)

app = typer.Typer(
    name="learn", help="Local learning cycle: run, status, reap, versions, verify"
)


# ── plumbing ────────────────────────────────────────────────────────────────


def _db_engine(db_path: str | None) -> Engine:
    from zolai.data.repositories import get_engine

    if not db_path:
        return get_engine()
    from zolai.data.database import DatabaseManager

    target = Path(db_path).expanduser().resolve()
    return DatabaseManager(f"sqlite:///{target}").engine


def _db_path(db_path: str | None) -> Path:
    return Path(db_path).expanduser().resolve() if db_path else Path(config.paths.db)


def _emit(payload: dict, json_out: bool, human: str | None = None) -> None:
    if json_out or not human:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(human)


def _split(values: list[str] | None) -> list[str] | None:
    """Accept repeated flags and comma-separated lists alike."""
    out: list[str] = []
    for value in values or []:
        out.extend(part.strip() for part in str(value).split(",") if part.strip())
    return out or None


def _fail(exc: BaseException, db_path: str | None) -> typer.Exit:
    error_class, message = sanitize_error(exc, db_path=_db_path(db_path))
    print(json.dumps({"status": "error", "error_class": error_class, "message": message}, indent=2))
    if isinstance(exc, PreflightError):
        code = 2
    elif isinstance(exc, VersionDriftError):
        code = 3
    elif isinstance(exc, CycleInterrupted):
        code = 130
    elif isinstance(exc, KeyboardInterrupt):
        code = 130
    else:
        code = 1
    return typer.Exit(code=code)


# ── run ─────────────────────────────────────────────────────────────────────


@app.command("run")
def run(
    apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry-run)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Explicitly dry-run (the default)"),
    db: str | None = typer.Option(None, "--db", help="Database file (default: configured DB)"),
    limit: int = typer.Option(200, "--limit", help="RAG rows to process (bounded)"),
    max_chunks: int = typer.Option(512, "--max-chunks", help="RAG chunk budget (bounded)"),
    sources: list[str] = typer.Option(None, "--sources", help="RAG tables/sources (repeatable or comma-separated)"),
    since: str | None = typer.Option(None, "--since", help="Only re-embed rows changed since this ISO time"),
    discovery_limit: int | None = typer.Option(None, "--discovery-limit", help="Cap discovery candidates"),
    steps: str | None = typer.Option(None, "--steps", help="Comma-separated step names to run"),
    skip: list[str] = typer.Option(None, "--skip", help="Step names to skip (repeatable)"),
    no_bible: bool = typer.Option(False, "--no-bible", help="Skip the Bible knowledge steps"),
    no_levels: bool = typer.Option(False, "--no-levels", help="Skip line/paragraph/document learning"),
    level_sources: list[str] = typer.Option(
        None, "--level-sources", help="Sources for layer learning (bible,zomidaily,lessons,wiki)"
    ),
    level_max_docs: int | None = typer.Option(
        None, "--level-max-docs", help="Cap documents read per source for layer learning"
    ),
    no_discovery: bool = typer.Option(False, "--no-discovery", help="Skip hypothesis discovery"),
    no_promote: bool = typer.Option(False, "--no-promote", help="Skip hypothesis promotion"),
    no_train: bool = typer.Option(False, "--no-train", help="Skip POS/syllable retraining"),
    no_rag: bool = typer.Option(False, "--no-rag", help="Skip the incremental RAG update"),
    stage_only: bool = typer.Option(False, "--stage-only", help="Train without publishing the models"),
    prune_stale: bool = typer.Option(False, "--prune-stale", help="Delete vectors whose source row is gone"),
    reconcile: bool = typer.Option(False, "--reconcile", help="Also select rows with no vector at all"),
    record_dry_run: bool = typer.Option(False, "--record-dry-run", help="Write a dry-run row to the ledger"),
    content_manifest: bool = typer.Option(
        True,
        "--content-manifest/--counts-manifest",
        help="Hash table content (slow, exact) or only row counts",
    ),
    min_free_gb: float = typer.Option(6.0, "--min-free-gb", help="Free disk required next to the DB"),
    allow_small_db: bool = typer.Option(False, "--allow-small-db", help="Allow a target under 1 GB (fixtures)"),
    backup_dir: str | None = typer.Option(None, "--backup-dir", help="Where the pre-run backup goes"),
    report_dir: str | None = typer.Option(None, "--report-dir", help="Where the JSON report goes"),
    rollback_on_failure: bool = typer.Option(False, "--rollback-on-failure", help="Restore the backup if a step fails"),
    force: bool = typer.Option(False, "--force", help="Ignore another RUNNING run"),
    max_metric_drop: float = typer.Option(0.01, "--max-metric-drop", help="Allowed drop vs the previous metric"),
    allow_metric_drop: bool = typer.Option(False, "--allow-metric-drop", help="Publish even if the metric dropped"),
    strict_regression: bool = typer.Option(False, "--strict-regression", help="Fail (not warn) on regression"),
    json_out: bool = typer.Option(True, "--json/--no-json", help="JSON output (default) or a short summary"),
) -> None:
    """Run the learning cycle (dry-run unless --apply)."""
    if apply and dry_run:
        raise _fail(PreflightError("--apply and --dry-run are mutually exclusive"), db)

    options = LearningCycleOptions(
        apply=apply,
        db_path=_db_path(db),
        limit=max(1, limit),
        max_chunks=max(1, max_chunks),
        sources=_split(sources),
        since=since,
        discovery_limit=discovery_limit,
        steps=_split([steps]) if steps else None,
        skip=_split(skip) or [],
        do_bible=not no_bible,
        do_levels=not no_levels,
        level_sources=_split(level_sources),
        level_max_docs=level_max_docs,
        do_discovery=not no_discovery,
        do_promote=not no_promote,
        do_train=not no_train,
        do_rag=not no_rag,
        stage_only=stage_only,
        prune_stale=prune_stale,
        reconcile=reconcile,
        record_dry_run=record_dry_run,
        content_manifest=content_manifest,
        min_free_gb=min_free_gb,
        allow_small_db=allow_small_db,
        backup_dir=Path(backup_dir).expanduser().resolve() if backup_dir else None,
        report_dir=Path(report_dir).expanduser().resolve() if report_dir else None,
        rollback_on_failure=rollback_on_failure,
        force=force,
        max_metric_drop=max_metric_drop,
        allow_metric_drop=allow_metric_drop,
        strict_regression=strict_regression,
    )

    try:
        result = run_learning_cycle(_db_engine(db), options)
    except BaseException as exc:  # noqa: BLE001 — mapped to exit codes
        raise _fail(exc, db) from exc

    if apply:
        result["notice"] = post_apply_notice()

    lines = [f"{result['status']}  {result['mode']}  {result['run_id']}"]
    for step in result.get("steps", []):
        lines.append(f"  {step['status']:<8} {step['name']}")
    if result.get("version_tag"):
        lines.append(f"version: {result['version_tag']}")
    lines.append(f"report: {result['report']}")
    if result.get("notice"):
        lines.append(f"note: {result['notice']}")
    _emit(result, json_out, "\n".join(lines))


# ── levels ──────────────────────────────────────────────────────────────────


@app.command("levels")
def levels(
    apply: bool = typer.Option(False, "--apply", help="Write layers/openers (default: dry-run)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Explicitly dry-run (the default)"),
    db: str | None = typer.Option(None, "--db", help="Database file (default: configured DB)"),
    sources: list[str] = typer.Option(
        None, "--sources", help="bible,zomidaily,lessons,wiki (repeatable or comma-separated)"
    ),
    levels_opt: str | None = typer.Option(
        None,
        "--levels",
        help="Comma-separated: line,paragraph,document,corpus (default: all)",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Cap Bible verses read"),
    max_docs: int | None = typer.Option(None, "--max-docs", help="Cap documents per source"),
    json_out: bool = typer.Option(True, "--json/--no-json", help="JSON output (default)"),
) -> None:
    """Learn line → paragraph → document → corpus layers from local corpora."""
    if apply and dry_run:
        raise _fail(PreflightError("--apply and --dry-run are mutually exclusive"), db)

    from zolai.learning.hierarchy import DEFAULT_LEVELS, SOURCE_SPECS, learn_layers

    active_levels = _split([levels_opt]) if levels_opt else list(DEFAULT_LEVELS)
    unknown = sorted(set(active_levels) - set(DEFAULT_LEVELS))
    if unknown:
        raise _fail(PreflightError(f"unknown level(s): {', '.join(unknown)}"), db)

    try:
        result = learn_layers(
            _db_engine(db),
            dry_run=not apply,
            sources=_split(sources),
            levels=active_levels,
            limit=limit,
            max_docs=max_docs,
        )
    except BaseException as exc:  # noqa: BLE001
        raise _fail(exc, db) from exc

    result["sources_available"] = list(SOURCE_SPECS)
    lines = [
        f"{'APPLIED' if apply else 'DRY_RUN'}  layers={result['layers_total']}  "
        f"openers={result['openers_total']}"
    ]
    for name, stats in result["per_source"].items():
        lines.append(
            f"  {name:<10} docs={stats.get('documents', 0):<6} "
            f"lines={stats.get('lines', 0):<7} paragraphs={stats.get('paragraphs', 0):<6} "
            f"non_zolai={stats.get('non_zolai_paragraphs', 0):<6} "
            f"layers={stats.get('layers', 0):<7} openers={stats.get('openers', 0)}"
        )
    _emit(result, json_out, "\n".join(lines))


# ── status ──────────────────────────────────────────────────────────────────


@app.command("status")
def status(
    run_id: str | None = typer.Option(None, "--run-id", help="A specific run (default: latest)"),
    limit: int = typer.Option(5, "--limit", help="History rows"),
    stale_after: float = typer.Option(
        DEFAULT_STALE_AFTER_S / 3600,
        "--stale-after",
        help="Hours of inactivity before a RUNNING run is reported stale",
    ),
    db: str | None = typer.Option(None, "--db", help="Database file"),
    json_out: bool = typer.Option(True, "--json/--no-json", help="JSON output (default)"),
) -> None:
    """Show the latest run, recent history, and stale RUNNING runs."""
    engine = _db_engine(db)
    try:
        ensure_learning_run_tables(engine)
        payload = render_status(engine, run_id=run_id, limit=max(1, min(limit, 100)))
        payload["stale_runs"] = stale_running_runs(
            engine, stale_after_s=max(0.0, stale_after) * 3600
        )
    except BaseException as exc:  # noqa: BLE001
        raise _fail(exc, db) from exc

    current = payload.get("run")
    if current is None:
        human = "no learning runs recorded"
    else:
        human = "\n".join(
            [f"{current['run_id']}  {current['status']}  {current['mode']}"]
            + [f"  {s['status']:<8} {s['name']}" for s in current.get("steps", [])]
        )
    for stale in payload["stale_runs"]:
        human += (
            f"\nstale: {stale['run_id']} (no activity for "
            f"{stale['idle_seconds'] / 3600:.1f}h; `zolai learn reap --apply`)"
        )
    _emit(payload, json_out, human)


# ── reap ────────────────────────────────────────────────────────────────────


@app.command("reap")
def reap(
    apply: bool = typer.Option(False, "--apply", help="Abort stale runs (default: report only)"),
    stale_after: float = typer.Option(
        DEFAULT_STALE_AFTER_S / 3600,
        "--stale-after",
        help="Hours of inactivity before a RUNNING run is stale",
    ),
    db: str | None = typer.Option(None, "--db", help="Database file"),
    json_out: bool = typer.Option(True, "--json/--no-json", help="JSON output (default)"),
) -> None:
    """Report (or, with --apply, abort) RUNNING runs whose activity stopped."""
    engine = _db_engine(db)
    try:
        ensure_learning_run_tables(engine)
        window = max(0.0, stale_after) * 3600
        stale = stale_running_runs(engine, stale_after_s=window)
        reaped = reap_stale_runs(engine, stale_after_s=window) if apply else []
    except BaseException as exc:  # noqa: BLE001
        raise _fail(exc, db) from exc

    payload = {
        "stale_after_hours": round(window / 3600, 3),
        "applied": apply,
        "stale": stale,
        "reaped": reaped,
    }
    if not stale:
        human = "no stale learning runs"
    else:
        verb = "aborted" if apply else "would abort"
        human = "\n".join(
            f"{verb} {run['run_id']} ({run['idle_seconds'] / 3600:.1f}h idle)"
            for run in stale
        )
    _emit(payload, json_out, human)


# ── versions ────────────────────────────────────────────────────────────────


def _version_view(row: dict) -> dict:
    raw = row.get("source_versions") or "{}"
    try:
        source_versions = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (TypeError, json.JSONDecodeError):
        source_versions = {}
    return {
        "version": row.get("version"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "git_commit": row.get("git_commit"),
        "manifest_hash": row.get("manifest_hash"),
        "eval_run_id": row.get("eval_run_id"),
        "learning_run": source_versions.get("learning_run"),
    }


@app.command("versions")
def versions(
    limit: int = typer.Option(10, "--limit", help="Rows to list"),
    db: str | None = typer.Option(None, "--db", help="Database file"),
    json_out: bool = typer.Option(True, "--json/--no-json", help="JSON output (default)"),
) -> None:
    """List knowledge-version snapshots, newest first."""
    try:
        rows = list_knowledge_versions(_db_engine(db), limit=max(1, limit))
    except BaseException as exc:  # noqa: BLE001
        raise _fail(exc, db) from exc

    payload = {"versions": [_version_view(dict(row)) for row in rows]}
    human = "\n".join(
        f"{v['version']:<20} {v['status'] or '':<10} {v['learning_run'] or ''}"
        for v in payload["versions"]
    )
    _emit(payload, json_out, human or "no versions recorded")


# ── verify ──────────────────────────────────────────────────────────────────


@app.command("verify")
def verify(
    version: str = typer.Argument(..., help="Version tag, e.g. v2026.10.28.1"),
    db: str | None = typer.Option(None, "--db", help="Database file"),
    json_out: bool = typer.Option(True, "--json/--no-json", help="JSON output (default)"),
) -> None:
    """Re-hash the live tables and compare them with a recorded snapshot."""
    engine = _db_engine(db)
    try:
        report = verify_knowledge_version(engine, version)
        row = get_knowledge_version(engine, version)
    except BaseException as exc:  # noqa: BLE001
        raise _fail(exc, db) from exc

    payload = {
        "version": version,
        "ok": bool(report.get("ok")),
        "mode": report.get("mode"),
        "checked": report.get("checked"),
        "drift": report.get("drift") or {},
        "error": report.get("error"),
        "status": (row or {}).get("status"),
        "created_at": (row or {}).get("created_at"),
        "manifest_hash": (row or {}).get("manifest_hash"),
    }
    if payload["error"]:
        _emit(payload, json_out, f"unknown or malformed version {version}")
        raise typer.Exit(code=2)

    human = (
        f"{version}: ok ({payload['mode']} manifest, {payload['checked']} tables)"
        if payload["ok"]
        else f"{version}: drift in {', '.join(sorted(payload['drift']))}"
    )
    _emit(payload, json_out, human)
    if not payload["ok"]:
        raise typer.Exit(code=3)


__all__ = ["app"]
