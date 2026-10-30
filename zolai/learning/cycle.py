"""One local learning cycle: study → learn → train → index → version.

The cycle is the only supported way to run the learning steps against the live
database, because each piece alone was previously safe but the *sequence* was
not: no shared backup, no shared ledger, and no single place that records what
happened. Ordering, stop-gates and reporting live here; the work itself stays
in the modules that already own it.

Modes:

* **dry-run** (default) — every read-only step runs, every writing step only
  computes, and the only file produced is the JSON report. Nothing is written
  to the database unless ``record_dry_run`` is set.
* **apply** — verified backup first, then the steps in order; a failure stops
  the run and leaves the backup as the restore path.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.config import config
from zolai.learning import core_training
from zolai.learning.run_ledger import (
    allocate_version_tag,
    display_ref,
    ensure_learning_run_tables,
    finish_run,
    latest_knowledge_version,
    list_runs,
    new_run_id,
    record_artifact,
    record_step,
    sanitize_error,
    stale_running_runs,
    start_run,
    utc_now,
)
from zolai.learning.safety import (
    MIN_DISK_FREE_BYTES,
    StepFailure,
    checkpoint,
    connect,
    make_backup,
    quick_check,
    restore_backup,
    sha256_file,
    verify_backup,
)

REPORTS_DIR = config.paths.root / "data" / "reports"
DEFAULT_BACKUP_SUBDIR = "backups"

log = logging.getLogger(__name__)


class PreflightError(RuntimeError):
    """Configuration or environment is not fit for a run (CLI exit 2)."""


class VersionDriftError(RuntimeError):
    """A version snapshot failed verification (CLI exit 3)."""


class CycleInterrupted(RuntimeError):
    """Ctrl-C during a run (CLI exit 130)."""


@dataclass
class LearningCycleOptions:
    """Everything the cycle can be told to do — defaults are the safe ones."""

    apply: bool = False
    db_path: Path | None = None
    limit: int = 200
    max_chunks: int = 512
    sources: list[str] | None = None
    since: str | None = None
    discovery_limit: int | None = None
    steps: list[str] | None = None
    skip: list[str] = field(default_factory=list)
    do_bible: bool = True
    do_levels: bool = True
    level_sources: list[str] | None = None
    level_max_docs: int | None = None
    do_discovery: bool = True
    do_promote: bool = True
    do_train: bool = True
    do_rag: bool = True
    stage_only: bool = False
    prune_stale: bool = False
    reconcile: bool = False
    record_dry_run: bool = False
    content_manifest: bool = True
    min_free_gb: float = MIN_DISK_FREE_BYTES / 1e9
    allow_small_db: bool = False
    backup_dir: Path | None = None
    report_dir: Path | None = None
    rollback_on_failure: bool = False
    force: bool = False
    max_metric_drop: float = core_training.MAX_METRIC_DROP_DEFAULT
    allow_metric_drop: bool = False
    strict_regression: bool = False
    promote_min_evidence: int = 1
    promote_min_confidence: float = 0.0

    @property
    def mode(self) -> str:
        return "apply" if self.apply else "dry_run"

    def ledger_options(self) -> dict[str, Any]:
        """Options as recorded in the ledger — no paths, no environment."""
        payload = asdict(self)
        for key in ("db_path", "backup_dir", "report_dir"):
            payload.pop(key, None)
        return payload


@dataclass
class CycleContext:
    """Mutable state shared by the steps of one run."""

    engine: Engine
    db_path: Path
    run_id: str
    options: LearningCycleOptions
    started_at: str = field(default_factory=utc_now)
    backup_path: Path | None = None
    backup_sha256: str | None = None
    backup_size_bytes: int | None = None
    quick_check: str | None = None
    version_tag: str | None = None
    knowledge_version_id: int | None = None
    pos: dict[str, Any] = field(default_factory=dict)
    syllable: dict[str, Any] = field(default_factory=dict)
    rag: dict[str, Any] = field(default_factory=dict)
    promotion: dict[str, Any] = field(default_factory=dict)
    regression: dict[str, Any] = field(default_factory=dict)
    learning_eval_run_id: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def dry_run(self) -> bool:
        return not self.options.apply

    @property
    def write_ledger(self) -> bool:
        return self.options.apply or self.options.record_dry_run


# ── helpers ─────────────────────────────────────────────────────────────────


def _scalar(conn, sql: str, params: dict[str, Any] | None = None) -> Any:
    try:
        return conn.execute(text(sql), params or {}).scalar()
    except Exception:  # noqa: BLE001 — study tolerates absent tables
        return None


def _group_counts(conn, table: str, column: str) -> dict[str, int]:
    try:
        rows = conn.execute(
            text(f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}")
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {str(row[0]): int(row[1]) for row in rows}


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.paths.root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _table_exists(engine: Engine, table: str) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).first()
            is not None
        )


def _table_count(engine: Engine, table: str) -> int | None:
    with engine.connect() as conn:
        value = _scalar(conn, f"SELECT COUNT(*) FROM {table}")
    return int(value) if value is not None else None


# ── steps ───────────────────────────────────────────────────────────────────


StepResult = tuple[str, dict[str, Any]]


def _step_preflight(ctx: CycleContext) -> StepResult:
    opts = ctx.options
    metrics: dict[str, Any] = {}

    if not ctx.db_path.exists():
        raise PreflightError(f"target database not found: {display_ref(ctx.db_path)}")

    size = ctx.db_path.stat().st_size
    metrics["db_size_bytes"] = size
    if size < 1_000_000_000 and not opts.allow_small_db:
        raise PreflightError(
            f"target is {size:,} bytes — a real target must be ≥1 GB "
            "(pass --allow-small-db only for fixtures)"
        )

    import shutil

    free = shutil.disk_usage(ctx.db_path.parent).free
    metrics["disk_free_bytes"] = free
    if free < opts.min_free_gb * 1e9:
        raise PreflightError(
            f"only {free / 1e9:.2f} GB free next to the database — "
            f"need ≥{opts.min_free_gb:.1f} GB for the backup and rebuilds"
        )

    if shutil.which("sqlite3") is None:
        raise PreflightError("sqlite3 CLI not found on PATH (backup/restore unavailable)")

    missing = [
        table
        for table in ("knowledge_versions", "eval_sets", "eval_runs")
        if not _table_exists(ctx.engine, table)
    ]
    if missing:
        raise PreflightError(
            "required tables missing: " + ", ".join(missing)
            + " — run `zolai db migrate` first"
        )

    conn = connect(ctx.db_path)
    try:
        ctx.quick_check = quick_check(conn)
    finally:
        conn.close()
    metrics["quick_check"] = ctx.quick_check
    if ctx.quick_check != "ok":
        raise PreflightError(f"PRAGMA quick_check failed: {ctx.quick_check}")

    if not opts.force and _table_exists(ctx.engine, "learning_runs"):
        with ctx.engine.connect() as db_conn:
            running = db_conn.execute(
                text(
                    "SELECT run_id FROM learning_runs "
                    "WHERE status = 'RUNNING' AND run_id != :this "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"this": ctx.run_id},
            ).first()
        if running is not None:
            blocker = running[0]
            stale = {r["run_id"]: r for r in stale_running_runs(ctx.engine)}
            if blocker in stale:
                hours = stale[blocker]["idle_seconds"] / 3600
                raise PreflightError(
                    f"run {blocker} is still marked RUNNING but idle for "
                    f"{hours:.1f}h — `zolai learn reap --apply`, or pass --force"
                )
            raise PreflightError(
                f"run {blocker} is still marked RUNNING — finish it or pass --force"
            )

    deps: dict[str, bool] = {}
    for module in ("sklearn_crfsuite", "sentence_transformers"):
        # Presence, not import: importing torch inside every preflight costs
        # ~10 s and cannot change the outcome — the step that needs the
        # dependency imports it for real and fails there.
        try:
            deps[module] = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            deps[module] = False
    metrics["dependencies"] = deps

    syll_train = core_training.DEFAULT_SPLITS_DIR / "gold_train.jsonl"
    syll_test = core_training.DEFAULT_SPLITS_DIR / "gold_test.jsonl"
    metrics["syllable_splits_present"] = syll_train.exists() and syll_test.exists()
    with ctx.engine.connect() as db_conn:
        pos_items = _scalar(
            db_conn,
            "SELECT COUNT(*) FROM eval_cases WHERE set_name = :s AND is_active = 1",
            {"s": core_training.POS_SET},
        )
    metrics["pos_gold_items"] = int(pos_items or 0)

    warnings = []
    if not deps.get("sklearn_crfsuite"):
        warnings.append("sklearn-crfsuite missing: training steps will fail")
    if not deps.get("sentence_transformers"):
        warnings.append("sentence-transformers missing: RAG step will be skipped")
    if not metrics["syllable_splits_present"]:
        warnings.append("syllable gold splits missing: train_syllable will skip")
    if metrics["pos_gold_items"] == 0:
        warnings.append("pos_gold_v0 is empty: train_pos will skip")
    metrics["warnings"] = warnings
    return ("WARN" if warnings else "OK"), metrics


def _step_backup(ctx: CycleContext) -> StepResult:
    if ctx.dry_run:
        return "SKIPPED", {"reason": "dry-run"}
    backup_dir = ctx.options.backup_dir or ctx.db_path.parent / DEFAULT_BACKUP_SUBDIR
    path = make_backup(ctx.db_path, backup_dir, tag=ctx.run_id)
    size = verify_backup(ctx.db_path, path)
    ctx.backup_path = path
    ctx.backup_size_bytes = size
    ctx.backup_sha256 = sha256_file(path)
    return "OK", {
        "backup_name": path.name,
        "size_bytes": size,
        "sha256": ctx.backup_sha256,
    }


def _step_study(ctx: CycleContext) -> StepResult:
    with ctx.engine.connect() as conn:
        counts = {
            table: _scalar(conn, f"SELECT COUNT(*) FROM {table}")
            for table in (
                "bible_verses",
                "vocabulary",
                "word_usage",
                "grammar_patterns",
                "knowledge_claims",
                "hypotheses",
                "attestation_index",
                "knowledge_vectors",
                "corpus_learning_layers",
            )
        }
        metrics: dict[str, Any] = {
            "counts": {k: int(v) for k, v in counts.items() if v is not None},
            "distinct_books": int(_scalar(conn, "SELECT COUNT(DISTINCT book) FROM bible_verses") or 0),
            "vocabulary_review_status": _group_counts(conn, "vocabulary", "review_status"),
            "claims_by_status": _group_counts(conn, "knowledge_claims", "status"),
            "hypotheses_by_status": _group_counts(conn, "hypotheses", "status"),
            "eval_cases_by_set": _group_counts(conn, "eval_cases", "set_name"),
            "vectors_by_source": _group_counts(conn, "knowledge_vectors", "source"),
        }
    latest = latest_knowledge_version(ctx.engine)
    metrics["latest_version"] = (
        {"version": latest["version"], "id": latest["id"]} if latest else None
    )
    ctx.summary["study"] = metrics
    return "OK", metrics


def _step_knowledge_bible(ctx: CycleContext) -> StepResult:
    from scripts.learning.bible_learning_run import run_attestation
    from scripts.learning.learn_grammar_from_bible import learn as learn_grammar_patterns
    from scripts.learning.repair_and_learn_bible_word_usage import learn_bible
    from scripts.learning.wire_vocabulary_evidence import wire
    from scripts.maintenance.dedupe_bible_verses_import import dedupe

    db_path = str(ctx.db_path)
    report_dir = ctx.options.report_dir or REPORTS_DIR
    metrics: dict[str, Any] = {}
    steps: dict[str, Any] = {}

    steps["repair_word_usage"] = learn_bible(db_path, dry_run=ctx.dry_run)
    if ctx.dry_run:
        steps["attestation"] = {"skipped": "dry-run"}
    else:
        steps["attestation"] = run_attestation(db_path, force=True)
    steps["wire_vocabulary"] = wire(
        db_path,
        dry_run=ctx.dry_run,
        report_path=None if ctx.dry_run else report_dir / f"vocab_gloss_disagreements_{ctx.run_id}.jsonl",
    )
    # The owner-review jsonl path belongs to the caller, not the report: the
    # summary is copied verbatim into the JSON report and the ledger.
    if isinstance(steps["wire_vocabulary"], dict) and steps["wire_vocabulary"].get("report_path"):
        steps["wire_vocabulary"]["report_path"] = display_ref(
            steps["wire_vocabulary"]["report_path"]
        )
    steps["dedupe_import"] = dedupe(db_path, dry_run=ctx.dry_run)
    steps["grammar_patterns"] = learn_grammar_patterns(db_path, dry_run=ctx.dry_run)
    metrics["steps"] = steps
    ctx.summary["knowledge_bible"] = metrics
    return ("DRY_RUN" if ctx.dry_run else "OK"), metrics


def _step_bible_levels(ctx: CycleContext) -> StepResult:
    """Learn line → paragraph → document → corpus layers from every source."""
    from zolai.learning.hierarchy import learn_layers

    result = learn_layers(
        ctx.engine,
        dry_run=ctx.dry_run,
        sources=ctx.options.level_sources,
        max_docs=ctx.options.level_max_docs,
    )
    # Sample text is for inspection, not for the ledger.
    metrics = {k: v for k, v in result.items() if k != "sample"}
    ctx.summary["bible_levels"] = metrics
    return ("DRY_RUN" if ctx.dry_run else "OK"), metrics


def _step_knowledge_discovery(ctx: CycleContext) -> StepResult:
    from zolai.learning.discovery.pipeline import build_discovery

    result = build_discovery(
        ctx.engine, limit=ctx.options.discovery_limit, dry_run=ctx.dry_run
    )
    ctx.summary["knowledge_discovery"] = result
    if result.get("failed_capabilities"):
        # A swallowed capability error is partial success, never OK.
        return "WARN", result
    return ("DRY_RUN" if ctx.dry_run else "OK"), result


def _step_knowledge_promote(ctx: CycleContext) -> StepResult:
    from zolai.knowledge.promotion import promote_hypotheses_to_claims

    summary = promote_hypotheses_to_claims(
        ctx.engine,
        min_evidence=ctx.options.promote_min_evidence,
        min_confidence=ctx.options.promote_min_confidence,
        dry_run=ctx.dry_run,
    )
    metrics = summary.to_dict() if hasattr(summary, "to_dict") else vars(summary)
    ctx.promotion = metrics
    ctx.summary["knowledge_promote"] = metrics
    return ("DRY_RUN" if ctx.dry_run else "OK"), metrics


def _step_knowledge_regression(ctx: CycleContext) -> StepResult:
    from zolai.learning.incremental.regression import run_regression_checks

    latest = latest_knowledge_version(ctx.engine)
    baseline_id = latest["id"] if latest else None
    report = run_regression_checks(ctx.engine, baseline_version_id=baseline_id)
    metrics = {
        "passed": report.passed,
        "baseline_version_id": baseline_id,
        "checks": report.checks,
        "errors": report.errors,
    }
    ctx.regression = metrics
    ctx.summary["knowledge_regression"] = metrics
    if report.passed:
        return "OK", metrics
    return ("FAILED" if ctx.options.strict_regression else "WARN"), metrics


def _step_train_pos(ctx: CycleContext) -> StepResult:
    result = core_training.train_and_publish_pos(
        ctx.engine,
        run_id=ctx.run_id,
        staging_dir=_run_dir(ctx) / "pos",
        max_metric_drop=ctx.options.max_metric_drop,
        allow_metric_drop=ctx.options.allow_metric_drop,
        stage_only=ctx.options.stage_only,
        dry_run=ctx.dry_run,
    )
    ctx.pos = result
    ctx.summary["train_pos"] = result
    if not ctx.dry_run and result.get("staging_ref") and ctx.write_ledger:
        _record_artifact(ctx, result)
    return _artifact_status(ctx, result)


def _step_train_syllable(ctx: CycleContext) -> StepResult:
    result = core_training.train_and_publish_syllable(
        ctx.engine,
        run_id=ctx.run_id,
        staging_dir=_run_dir(ctx) / "syllable",
        max_metric_drop=ctx.options.max_metric_drop,
        allow_metric_drop=ctx.options.allow_metric_drop,
        stage_only=ctx.options.stage_only,
        dry_run=ctx.dry_run,
    )
    ctx.syllable = result
    ctx.summary["train_syllable"] = result
    if not ctx.dry_run and result.get("staging_ref") and ctx.write_ledger:
        _record_artifact(ctx, result)
    return _artifact_status(ctx, result)


def _artifact_status(ctx: CycleContext, result: dict[str, Any]) -> StepResult:
    if ctx.dry_run:
        return "DRY_RUN", result
    if result.get("artifact_status") == "PUBLISHED":
        return "OK", result
    if result.get("status") == "SKIPPED":
        return "SKIPPED", result
    return "WARN", result


def _run_dir(ctx: CycleContext) -> Path:
    return config.paths.root / "models" / "runs" / ctx.run_id


def _record_artifact(ctx: CycleContext, result: dict[str, Any]) -> None:
    record_artifact(
        ctx.engine,
        ctx.run_id,
        engine_name=result["engine"],
        artifact_name=result.get("artifact_name") or "",
        staging_ref=result.get("staging_ref") or "",
        sha256=result.get("sha256") or "",
        size_bytes=int(result.get("size_bytes") or 0),
        metrics=result.get("metrics") or {},
        previous_sha256=result.get("previous_sha256"),
        published_ref=result.get("published_ref"),
        status=result.get("artifact_status") or "STAGED",
    )


def _step_rag_incremental(ctx: CycleContext) -> StepResult:
    from zolai.rag.incremental import update_knowledge_vectors_incremental

    result = update_knowledge_vectors_incremental(
        ctx.engine,
        limit=ctx.options.limit,
        max_chunks=ctx.options.max_chunks,
        sources=ctx.options.sources,
        since=ctx.options.since,
        prune_stale=ctx.options.prune_stale,
        reconcile=ctx.options.reconcile,
        dry_run=ctx.dry_run,
        run_id=ctx.run_id if ctx.write_ledger and not ctx.dry_run else None,
    )
    ctx.rag = result
    ctx.summary["rag_incremental"] = result
    if result.get("errors"):
        if result.get("reembedded") or ctx.dry_run:
            return ("DRY_RUN" if ctx.dry_run else "WARN"), result
        return "WARN", result
    return ("DRY_RUN" if ctx.dry_run else "OK"), result


def _step_version_snapshot(ctx: CycleContext) -> StepResult:
    if ctx.dry_run:
        return "SKIPPED", {"reason": "dry-run creates no version"}

    from zolai.knowledge.versioning import create_knowledge_version, verify_knowledge_version

    pos_ok = bool((ctx.pos.get("gate") or {}).get("passed"))
    syl_ok = bool((ctx.syllable.get("gate") or {}).get("passed"))
    regression_ok = bool(ctx.regression.get("passed", True))
    status = "VERIFIED" if (pos_ok and syl_ok and regression_ok) else "OBSERVED"

    agg = core_training.record_aggregate_eval(
        ctx.engine,
        pos=ctx.pos,
        syllable=ctx.syllable,
        duration_ms=0.0,
        gate_passed=pos_ok and syl_ok,
    )
    ctx.learning_eval_run_id = agg

    source_versions = {
        "learning_run": ctx.run_id,
        "mode": ctx.options.mode,
        "git_commit": _git_commit() or "",
        "pos_eval_run_id": str(ctx.pos.get("eval_run_id") or ""),
        "syllable_eval_run_id": str(ctx.syllable.get("eval_run_id") or ""),
        "learning_eval_run_id": str(agg or ""),
        "pos_artifact_sha256": str(ctx.pos.get("sha256") or ""),
        "syllable_artifact_sha256": str(ctx.syllable.get("sha256") or ""),
        "rag_reembedded": str(ctx.rag.get("reembedded", 0)),
        "rag_chunks": str(ctx.rag.get("chunks", 0)),
        "rag_vectors_before": str(ctx.rag.get("vectors_before", 0)),
        "rag_vectors_after": str(ctx.rag.get("vectors_after", 0)),
        "data_since": ctx.options.since or "",
    }

    last_error: Exception | None = None
    for attempt in range(1, 4):
        tag = allocate_version_tag(ctx.engine)
        try:
            version_id = create_knowledge_version(
                ctx.engine,
                tag,
                source_versions=source_versions,
                eval_run_id=agg,
                status=status,
                user="zolai-learn",
                content_manifest=ctx.options.content_manifest,
            )
        except Exception as exc:  # noqa: BLE001 — unique tag collision is retryable
            last_error = exc
            continue
        ctx.version_tag = tag
        ctx.knowledge_version_id = version_id

        verification = verify_knowledge_version(ctx.engine, tag)
        metrics = {
            "version": tag,
            "knowledge_version_id": version_id,
            "status": status,
            "manifest_mode": "content" if ctx.options.content_manifest else "counts",
            "verify": {
                "ok": verification.get("ok"),
                "drift": verification.get("drift") or verification.get("mismatches") or [],
            },
            "source_versions": source_versions,
        }
        if verification.get("ok", True):
            return "OK", metrics
        # The DB moved between hashing and verification — another writer's
        # commit landed in that window. The row is an honest snapshot of that
        # instant, but a snapshot that cannot verify is a bad baseline, so take
        # a fresh one instead of failing a run whose earlier steps committed.
        last_error = VersionDriftError(
            f"version {tag} failed verification: {metrics['verify']['drift']}"
        )
        log.warning(
            "knowledge snapshot %s drifted (attempt %d/3): %s",
            tag, attempt, metrics["verify"]["drift"],
        )
        time.sleep(3)

    raise StepFailure(f"could not record a verifiable version snapshot: {last_error}")


def _step_report(ctx: CycleContext) -> StepResult:
    path = write_report(ctx, status="SUCCEEDED")
    return "OK", {"report": display_ref(path)}


STEPS: tuple[tuple[str, Callable[[CycleContext], StepResult]], ...] = (
    ("preflight", _step_preflight),
    ("backup", _step_backup),
    ("study", _step_study),
    ("knowledge_bible", _step_knowledge_bible),
    ("bible_levels", _step_bible_levels),
    ("knowledge_discovery", _step_knowledge_discovery),
    ("knowledge_promote", _step_knowledge_promote),
    ("knowledge_regression", _step_knowledge_regression),
    ("train_pos", _step_train_pos),
    ("train_syllable", _step_train_syllable),
    ("rag_incremental", _step_rag_incremental),
    ("version_snapshot", _step_version_snapshot),
    ("report", _step_report),
)

_STEP_FLAGS = {
    "knowledge_bible": "do_bible",
    "bible_levels": "do_levels",
    "knowledge_discovery": "do_discovery",
    "knowledge_promote": "do_promote",
    "train_pos": "do_train",
    "train_syllable": "do_train",
    "rag_incremental": "do_rag",
}


def _skip_reason(name: str, opts: LearningCycleOptions) -> str | None:
    if name in opts.skip:
        return "skipped by --skip"
    if opts.steps and name not in opts.steps and name != "report":
        return "not in --steps"
    flag = _STEP_FLAGS.get(name)
    if flag and not getattr(opts, flag):
        return f"disabled by --no-{flag[3:]}"
    return None


# ── report ──────────────────────────────────────────────────────────────────


def _step_records(ctx: CycleContext) -> list[dict[str, Any]]:
    """Step rows for the report — from the context, not the (maybe absent) ledger."""
    return ctx.summary.get("_steps", [])


def write_report(ctx: CycleContext, *, status: str, error: BaseException | None = None) -> Path:
    report_dir = ctx.options.report_dir or REPORTS_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    dest = Path(report_dir) / f"learning_run_{ctx.run_id}.json"

    error_class = error_message = None
    if error is not None:
        error_class, error_message = sanitize_error(error, db_path=ctx.db_path)

    payload: dict[str, Any] = {
        "run_id": ctx.run_id,
        "mode": ctx.options.mode,
        "status": status,
        "started_at": ctx.started_at,
        "finished_at": utc_now(),
        "db": {
            "name": ctx.db_path.name,
            "size_bytes": ctx.db_path.stat().st_size if ctx.db_path.exists() else None,
        },
        "git_commit": _git_commit(),
        "quick_check": ctx.quick_check,
        "backup": (
            {
                "name": ctx.backup_path.name,
                "size_bytes": ctx.backup_size_bytes,
                "sha256": ctx.backup_sha256,
            }
            if ctx.backup_path
            else None
        ),
        "version_tag": ctx.version_tag,
        "knowledge_version_id": ctx.knowledge_version_id,
        "learning_eval_run_id": ctx.learning_eval_run_id,
        "steps": _step_records(ctx),
        "summary": {k: v for k, v in ctx.summary.items() if not k.startswith("_")},
        "error_class": error_class,
        "error_message": error_message,
    }
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, dest)
    return dest


# ── the run ─────────────────────────────────────────────────────────────────


def run_learning_cycle(engine: Engine, options: LearningCycleOptions | None = None) -> dict[str, Any]:
    """Run the cycle and return the report payload.

    Raises:
        PreflightError: environment/config refusal (nothing written).
        StepFailure: a step failed; the run is marked FAILED, no version exists.
        CycleInterrupted: Ctrl-C (run marked ABORTED).
    """
    opts = options or LearningCycleOptions()
    db_path = Path(opts.db_path or engine.url.database or config.paths.db)
    run_id = new_run_id()

    ctx = CycleContext(
        engine=engine,
        db_path=db_path,
        run_id=run_id,
        options=opts,
        summary={"_steps": []},
    )

    if ctx.write_ledger:
        ensure_learning_run_tables(engine)
        start_run(
            engine,
            mode=opts.mode,
            options=opts.ledger_options(),
            git_commit=_git_commit(),
            db_size_bytes=db_path.stat().st_size if db_path.exists() else None,
            run_id=run_id,
        )

    started = time.perf_counter()
    failures = 0
    warnings = 0

    def _record(
        step_no: int,
        name: str,
        status: str,
        elapsed: float,
        metrics: dict,
        error=None,
        started_at: str | None = None,
    ):
        nonlocal failures, warnings
        if status == "FAILED":
            failures += 1
        if status == "WARN":
            warnings += 1
        entry = {
            "step_no": step_no,
            "name": name,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "metrics": metrics,
        }
        ctx.summary["_steps"].append(entry)
        if ctx.write_ledger:
            record_step(
                engine,
                run_id,
                step_no=step_no,
                name=name,
                status=status,
                metrics=metrics,
                elapsed_seconds=elapsed,
                started_at=started_at,
                error=error,
                db_path=db_path,
            )

    try:
        for step_no, (name, func) in enumerate(STEPS, start=1):
            reason = _skip_reason(name, opts)
            if reason:
                _record(step_no, name, "SKIPPED", 0.0, {"reason": reason})
                continue
            step_started = time.perf_counter()
            step_started_at = utc_now()
            if ctx.write_ledger:
                # Heartbeat: an in-flight step is visible (and keeps the run
                # out of stale detection) while it runs.
                record_step(
                    engine,
                    run_id,
                    step_no=step_no,
                    name=name,
                    status="RUNNING",
                    started_at=step_started_at,
                    db_path=db_path,
                )
            try:
                status, metrics = func(ctx)
            except BaseException as exc:  # noqa: BLE001 — recorded then re-raised
                _record(
                    step_no,
                    name,
                    "FAILED",
                    time.perf_counter() - step_started,
                    {"error_class": type(exc).__name__},
                    error=exc,
                    started_at=step_started_at,
                )
                raise
            _record(
                step_no,
                name,
                status,
                time.perf_counter() - step_started,
                metrics,
                started_at=step_started_at,
            )
            if status == "FAILED":
                raise StepFailure(f"{name} reported failure")

        elapsed = time.perf_counter() - started
        report_path = Path(opts.report_dir or REPORTS_DIR) / f"learning_run_{run_id}.json"
        if ctx.write_ledger:
            finish_run(
                engine,
                run_id,
                status="SUCCEEDED",
                elapsed_seconds=elapsed,
                summary=_safe_summary(ctx),
                version_tag=ctx.version_tag,
                knowledge_version_id=ctx.knowledge_version_id,
                backup_name=ctx.backup_path.name if ctx.backup_path else None,
                backup_size_bytes=ctx.backup_size_bytes,
                backup_sha256=ctx.backup_sha256,
                quick_check=ctx.quick_check,
                db_path=db_path,
            )
        _checkpoint(ctx)
        return {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "mode": opts.mode,
            "version_tag": ctx.version_tag,
            "report": display_ref(report_path),
            "warnings": warnings,
            "steps": _step_records(ctx),
        }

    except KeyboardInterrupt as exc:
        if ctx.write_ledger:
            finish_run(
                engine,
                run_id,
                status="ABORTED",
                elapsed_seconds=time.perf_counter() - started,
                summary=_safe_summary(ctx),
                quick_check=ctx.quick_check,
                error=exc,
                db_path=db_path,
            )
        write_report(ctx, status="ABORTED", error=exc)
        raise CycleInterrupted("interrupted") from exc

    except BaseException as exc:  # noqa: BLE001 — every failure is recorded
        rollback_note = None
        if opts.rollback_on_failure and ctx.backup_path and not ctx.dry_run:
            try:
                restore_backup(db_path, ctx.backup_path)
                rollback_note = "restored from the pre-run backup"
            except SystemExit as restore_exc:  # noqa: BLE001
                rollback_note = f"restore failed: {restore_exc}"
        if ctx.write_ledger:
            finish_run(
                engine,
                run_id,
                status="FAILED",
                elapsed_seconds=time.perf_counter() - started,
                summary=_safe_summary(ctx),
                quick_check=ctx.quick_check,
                error=exc,
                db_path=db_path,
            )
        write_report(ctx, status="FAILED", error=exc)
        if rollback_note:
            ctx.summary["rollback"] = rollback_note
        raise


def _safe_summary(ctx: CycleContext) -> dict[str, Any]:
    """Ledger summary: counts and tags only, no paths or error text."""
    return {
        "version_tag": ctx.version_tag,
        "pos_artifact": (ctx.pos or {}).get("artifact_status"),
        "syllable_artifact": (ctx.syllable or {}).get("artifact_status"),
        "rag_reembedded": (ctx.rag or {}).get("reembedded"),
        "rag_failed": (ctx.rag or {}).get("failed"),
        "regression_passed": (ctx.regression or {}).get("passed"),
        "warnings": sum(1 for s in _step_records(ctx) if s["status"] == "WARN"),
    }


def _checkpoint(ctx: CycleContext) -> None:
    if ctx.dry_run:
        return
    try:
        conn = connect(ctx.db_path)
        try:
            checkpoint(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — a checkpoint is best-effort
        pass


# ── status helpers used by the CLI and the tools ────────────────────────────


def render_status(engine: Engine, *, run_id: str | None = None, limit: int = 5) -> dict[str, Any]:
    from zolai.learning.run_ledger import get_run

    current = get_run(engine, run_id, include_steps=True)
    history = list_runs(engine, limit=limit, include_steps=False)
    return {"run": current, "history": history}


def post_apply_notice() -> str:
    """The one operational caveat every apply run must report."""
    return "restart long-lived API processes to load the newly published models"


__all__ = [
    "CycleContext",
    "CycleInterrupted",
    "LearningCycleOptions",
    "PreflightError",
    "STEPS",
    "VersionDriftError",
    "post_apply_notice",
    "render_status",
    "run_learning_cycle",
    "write_report",
]
