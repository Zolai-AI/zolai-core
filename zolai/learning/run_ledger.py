"""Ledger for local learning runs.

Every ``zolai learn run`` gets one ``learning_runs`` row, one
``learning_run_steps`` row per step, one ``learning_artifacts`` row per
published engine artifact, and one ``learning_vectors_state`` row per RAG
source row touched. The ledger is the audit trail; ``knowledge_versions``
remains the release snapshot.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.config import config

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_VERSION_RE = re.compile(r"^v(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d+))?$")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+"
)

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "ABORTED"})

#: A RUNNING row with no recorded activity for this long is considered stale.
DEFAULT_STALE_AFTER_S = 6 * 3600


def utc_now() -> str:
    """UTC timestamp in the format the ledger stores."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(raw: Any) -> datetime | None:
    """Parse a stored timestamp; naive values are treated as UTC."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        moment = raw
    else:
        try:
            moment = datetime.fromisoformat(str(raw).strip())
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def new_run_id(moment: datetime | None = None) -> str:
    """A sortable, filename-safe run id."""
    moment = moment or datetime.now(timezone.utc)
    return f"lr_{moment.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"


def validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError("invalid run_id")
    return run_id


def ensure_learning_run_tables(engine: Engine) -> dict[str, Any]:
    """Create the ledger tables if they are absent (idempotent)."""
    from zolai.data.migrations import create_learning_run_tables

    return create_learning_run_tables(engine)


def sanitize_error(
    exc: BaseException | str,
    *,
    db_path: str | Path | None = None,
) -> tuple[str, str]:
    """Return ``(error_class, safe_message)`` with paths and secrets removed."""
    error_class = type(exc).__name__ if isinstance(exc, BaseException) else "Error"
    message = str(exc)

    candidates: list[tuple[str, str]] = []
    if db_path:
        candidates.append((str(db_path), "<db>"))
    candidates.append((str(config.paths.root), "<repo>"))
    candidates.append((str(Path.home()), "~"))

    for raw, replacement in candidates:
        if raw:
            message = message.replace(raw, replacement)
            message = message.replace(str(Path(raw).resolve()), replacement)

    message = _SECRET_RE.sub(r"\1=<redacted>", message)
    message = " ".join(message.split())
    if len(message) > 500:
        message = message[:497] + "..."
    return error_class, message


def display_ref(path: str | Path) -> str:
    """Repo-relative path when possible, otherwise just the basename."""
    raw = Path(path)
    try:
        resolved = raw.resolve()
        root = Path(config.paths.root).resolve()
        return resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return raw.name


def start_run(
    engine: Engine,
    *,
    mode: str,
    options: dict[str, Any],
    git_commit: str | None = None,
    db_size_bytes: int | None = None,
    run_id: str | None = None,
) -> str:
    """Insert a RUNNING run row and return its id."""
    if mode not in {"dry_run", "apply"}:
        raise ValueError("mode must be 'dry_run' or 'apply'")
    run_id = validate_run_id(run_id or new_run_id())
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO learning_runs
                    (run_id, mode, status, started_at, git_commit, db_size_bytes,
                     options_json)
                VALUES
                    (:run_id, :mode, 'RUNNING', :started_at, :git_commit,
                     :db_size_bytes, :options_json)
                """
            ),
            {
                "run_id": run_id,
                "mode": mode,
                "started_at": utc_now(),
                "git_commit": git_commit,
                "db_size_bytes": db_size_bytes,
                "options_json": json.dumps(options, sort_keys=True),
            },
        )
    return run_id


def finish_run(
    engine: Engine,
    run_id: str,
    *,
    status: str,
    elapsed_seconds: float = 0.0,
    summary: dict[str, Any] | None = None,
    version_tag: str | None = None,
    knowledge_version_id: int | None = None,
    backup_name: str | None = None,
    backup_size_bytes: int | None = None,
    backup_sha256: str | None = None,
    quick_check: str | None = None,
    error: BaseException | None = None,
    db_path: str | Path | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"not a terminal status: {status}")

    error_class = error_message = None
    if error is not None:
        error_class, error_message = sanitize_error(error, db_path=db_path)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE learning_runs SET
                    status = :status,
                    finished_at = :finished_at,
                    elapsed_seconds = :elapsed,
                    summary_json = :summary_json,
                    version_tag = COALESCE(:version_tag, version_tag),
                    knowledge_version_id = COALESCE(
                        :knowledge_version_id, knowledge_version_id
                    ),
                    backup_name = COALESCE(:backup_name, backup_name),
                    backup_size_bytes = COALESCE(
                        :backup_size_bytes, backup_size_bytes
                    ),
                    backup_sha256 = COALESCE(:backup_sha256, backup_sha256),
                    quick_check = COALESCE(:quick_check, quick_check),
                    error_class = :error_class,
                    error_message = :error_message
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "finished_at": utc_now(),
                "elapsed": round(float(elapsed_seconds), 3),
                "summary_json": json.dumps(summary or {}, sort_keys=True),
                "version_tag": version_tag,
                "knowledge_version_id": knowledge_version_id,
                "backup_name": backup_name,
                "backup_size_bytes": backup_size_bytes,
                "backup_sha256": backup_sha256,
                "quick_check": quick_check,
                "error_class": error_class,
                "error_message": error_message,
            },
        )
        _recount_steps(conn, run_id)


_STEP_COUNTS_SQL = text(
    """
    UPDATE learning_runs SET
        steps_total = (SELECT COUNT(*) FROM learning_run_steps
                       WHERE run_id = :run_id),
        steps_ok = (SELECT COUNT(*) FROM learning_run_steps
                    WHERE run_id = :run_id AND status IN ('OK','DRY_RUN')),
        steps_failed = (SELECT COUNT(*) FROM learning_run_steps
                        WHERE run_id = :run_id AND status = 'FAILED'),
        warnings = (SELECT COUNT(*) FROM learning_run_steps
                    WHERE run_id = :run_id AND status = 'WARN')
    WHERE run_id = :run_id
    """
)


def _recount_steps(conn, run_id: str) -> None:
    conn.execute(_STEP_COUNTS_SQL, {"run_id": run_id})


def record_step(
    engine: Engine,
    run_id: str,
    *,
    step_no: int,
    name: str,
    status: str,
    metrics: dict[str, Any] | None = None,
    elapsed_seconds: float = 0.0,
    started_at: str | None = None,
    finished_at: str | None = None,
    error: BaseException | None = None,
    db_path: str | Path | None = None,
) -> None:
    error_class = error_message = None
    if error is not None:
        error_class, error_message = sanitize_error(error, db_path=db_path)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO learning_run_steps
                    (run_id, step_no, name, status, started_at, finished_at,
                     elapsed_seconds, metrics_json, error_class, error_message)
                VALUES
                    (:run_id, :step_no, :name, :status, :started_at, :finished_at,
                     :elapsed, :metrics_json, :error_class, :error_message)
                ON CONFLICT(run_id, name) DO UPDATE SET
                    step_no = excluded.step_no,
                    status = excluded.status,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    elapsed_seconds = excluded.elapsed_seconds,
                    metrics_json = excluded.metrics_json,
                    error_class = excluded.error_class,
                    error_message = excluded.error_message
                """
            ),
            {
                "run_id": run_id,
                "step_no": step_no,
                "name": name,
                "status": status,
                "started_at": started_at or utc_now(),
                # A RUNNING row is a heartbeat placeholder: it has begun but
                # not finished, so its activity timestamp is its start.
                "finished_at": finished_at or (None if status == "RUNNING" else utc_now()),
                "elapsed": round(float(elapsed_seconds), 3),
                "metrics_json": json.dumps(metrics or {}, sort_keys=True),
                "error_class": error_class,
                "error_message": error_message,
            },
        )


def record_artifact(
    engine: Engine,
    run_id: str,
    *,
    engine_name: str,
    artifact_name: str,
    staging_ref: str,
    sha256: str,
    size_bytes: int,
    metrics: dict[str, Any] | None = None,
    previous_sha256: str | None = None,
    published_ref: str | None = None,
    status: str = "STAGED",
) -> None:
    now = utc_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO learning_artifacts
                    (run_id, engine, artifact_name, staging_ref, published_ref,
                     sha256, previous_sha256, size_bytes, metrics_json, status,
                     created_at, published_at)
                VALUES
                    (:run_id, :engine, :artifact_name, :staging_ref, :published_ref,
                     :sha256, :previous_sha256, :size_bytes, :metrics_json, :status,
                     :created_at, :published_at)
                ON CONFLICT(run_id, engine) DO UPDATE SET
                    artifact_name = excluded.artifact_name,
                    staging_ref = excluded.staging_ref,
                    published_ref = excluded.published_ref,
                    sha256 = excluded.sha256,
                    previous_sha256 = excluded.previous_sha256,
                    size_bytes = excluded.size_bytes,
                    metrics_json = excluded.metrics_json,
                    status = excluded.status,
                    published_at = excluded.published_at
                """
            ),
            {
                "run_id": run_id,
                "engine": engine_name,
                "artifact_name": artifact_name,
                "staging_ref": display_ref(staging_ref),
                "published_ref": display_ref(published_ref) if published_ref else None,
                "sha256": sha256,
                "previous_sha256": previous_sha256,
                "size_bytes": size_bytes,
                "metrics_json": json.dumps(metrics or {}, sort_keys=True),
                "status": status,
                "created_at": now,
                "published_at": now if status == "PUBLISHED" else None,
            },
        )


def record_vector_state(
    engine: Engine,
    run_id: str,
    *,
    source_table: str,
    source_id: str,
    action: str,
    status: str,
    vectors_before: int = 0,
    vectors_after: int = 0,
    chunks: int = 0,
    text_sha256: str | None = None,
    error_class: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO learning_vectors_state
                    (run_id, source_table, source_id, action, status,
                     vectors_before, vectors_after, chunks, text_sha256,
                     error_class, created_at)
                VALUES
                    (:run_id, :source_table, :source_id, :action, :status,
                     :vectors_before, :vectors_after, :chunks, :text_sha256,
                     :error_class, :created_at)
                ON CONFLICT(run_id, source_table, source_id) DO UPDATE SET
                    action = excluded.action,
                    status = excluded.status,
                    vectors_before = excluded.vectors_before,
                    vectors_after = excluded.vectors_after,
                    chunks = excluded.chunks,
                    text_sha256 = excluded.text_sha256,
                    error_class = excluded.error_class
                """
            ),
            {
                "run_id": run_id,
                "source_table": source_table,
                "source_id": str(source_id),
                "action": action,
                "status": status,
                "vectors_before": int(vectors_before),
                "vectors_after": int(vectors_after),
                "chunks": int(chunks),
                "text_sha256": text_sha256,
                "error_class": error_class,
                "created_at": utc_now(),
            },
        )


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _run_row(row: Any, *, include_steps: bool, engine: Engine) -> dict[str, Any]:
    data = dict(row._mapping)
    data["options"] = _parse_json(data.pop("options_json", "{}"))
    data["summary"] = _parse_json(data.pop("summary_json", "{}"))
    if include_steps:
        with engine.connect() as conn:
            steps = conn.execute(
                text(
                    "SELECT step_no, name, status, started_at, finished_at, "
                    "elapsed_seconds, metrics_json, error_class "
                    "FROM learning_run_steps WHERE run_id = :run_id "
                    "ORDER BY step_no"
                ),
                {"run_id": data["run_id"]},
            ).fetchall()
        data["steps"] = [
            {
                **{k: v for k, v in dict(step._mapping).items() if k != "metrics_json"},
                "metrics": _parse_json(step._mapping["metrics_json"]),
            }
            for step in steps
        ]
    return data


def get_run(
    engine: Engine, run_id: str | None = None, *, include_steps: bool = True
) -> dict[str, Any] | None:
    """Latest run, or one by id. Error messages are not returned."""
    query = (
        "SELECT run_id, mode, status, started_at, finished_at, elapsed_seconds, "
        "git_commit, db_size_bytes, backup_name, backup_size_bytes, backup_sha256, "
        "quick_check, version_tag, knowledge_version_id, steps_total, steps_ok, "
        "steps_failed, warnings, options_json, summary_json, error_class "
        "FROM learning_runs "
    )
    params: dict[str, Any] = {}
    if run_id:
        query += "WHERE run_id = :run_id"
        params["run_id"] = validate_run_id(run_id)
    else:
        query += "ORDER BY id DESC LIMIT 1"
    with engine.connect() as conn:
        row = conn.execute(text(query), params).first()
    return _run_row(row, include_steps=include_steps, engine=engine) if row else None


def list_runs(
    engine: Engine, *, limit: int = 10, include_steps: bool = False
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT run_id, mode, status, started_at, finished_at, "
                "elapsed_seconds, git_commit, version_tag, knowledge_version_id, "
                "steps_total, steps_ok, steps_failed, warnings, options_json, "
                "summary_json, error_class "
                "FROM learning_runs ORDER BY id DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).fetchall()
    return [_run_row(r, include_steps=include_steps, engine=engine) for r in rows]


def stale_running_runs(
    engine: Engine,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """RUNNING runs with no recorded activity for ``stale_after_s``.

    Liveness comes from the newest step start/finish (falling back to the run
    start), never from the run start alone — a legitimately long run that keeps
    recording steps stays fresh.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.run_id, r.mode, r.started_at,
                       (SELECT MAX(COALESCE(s.finished_at, s.started_at))
                          FROM learning_run_steps s
                         WHERE s.run_id = r.run_id) AS last_step_at
                  FROM learning_runs r
                 WHERE r.status = 'RUNNING'
                 ORDER BY r.id
                """
            )
        ).fetchall()

    stale: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row._mapping)
        started = _parse_ts(data["started_at"])
        step_at = _parse_ts(data["last_step_at"])
        # Activity cannot predate the run; guard against clock skew.
        last = max(started, step_at) if started and step_at else (step_at or started)
        if last is None:
            continue
        idle = (moment - last).total_seconds()
        if idle <= stale_after_s:
            continue
        stale.append(
            {
                "run_id": data["run_id"],
                "mode": data["mode"],
                "started_at": started.isoformat(timespec="seconds") if started else None,
                "last_activity_at": last.isoformat(timespec="seconds"),
                "idle_seconds": round(idle, 3),
            }
        )
    stale.sort(key=lambda item: item["idle_seconds"], reverse=True)
    return stale


def reap_stale_runs(
    engine: Engine,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    now: datetime | None = None,
) -> list[str]:
    """Abort stale RUNNING runs; return the ids actually transitioned.

    The write is guarded on ``status = 'RUNNING'``, so a run that finished
    between the scan and the abort keeps its real outcome (and reaping twice
    is a no-op).
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    reaped: list[str] = []
    for run in stale_running_runs(engine, stale_after_s=stale_after_s, now=moment):
        started = _parse_ts(run["started_at"])
        elapsed = (moment - started).total_seconds() if started else run["idle_seconds"]
        error_class, error_message = sanitize_error(
            RuntimeError(f"stale: no recorded activity for {int(run['idle_seconds'])}s")
        )
        with engine.begin() as conn:
            updated = conn.execute(
                text(
                    """
                    UPDATE learning_runs SET
                        status = 'ABORTED',
                        finished_at = :finished_at,
                        elapsed_seconds = :elapsed,
                        summary_json = :summary_json,
                        error_class = :error_class,
                        error_message = :error_message
                    WHERE run_id = :run_id AND status = 'RUNNING'
                    """
                ),
                {
                    "run_id": run["run_id"],
                    "finished_at": utc_now(),
                    "elapsed": round(max(elapsed, 0.0), 3),
                    "summary_json": json.dumps(
                        {
                            "stale": True,
                            "last_activity_at": run["last_activity_at"],
                            "idle_seconds": run["idle_seconds"],
                            "stale_after_seconds": int(stale_after_s),
                        },
                        sort_keys=True,
                    ),
                    "error_class": error_class,
                    "error_message": error_message,
                },
            )
            if not updated.rowcount:
                continue
            _recount_steps(conn, run["run_id"])
        reaped.append(run["run_id"])
    return reaped


def allocate_version_tag(engine: Engine, *, moment: datetime | None = None) -> str:
    """Next ``vYYYY.MM.DD.N`` tag for the given UTC date."""
    moment = moment or datetime.now(timezone.utc)
    prefix = f"v{moment.strftime('%Y.%m.%d')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT version FROM knowledge_versions WHERE version LIKE :p"),
            {"p": f"{prefix}%"},
        ).fetchall()

    highest = 0
    for (version,) in rows:
        match = _VERSION_RE.match(str(version))
        if not match:
            continue
        if f"v{match.group(1)}.{match.group(2)}.{match.group(3)}" != prefix:
            continue
        highest = max(highest, int(match.group(4) or 0))
    return f"{prefix}.{highest + 1}"


def latest_knowledge_version(engine: Engine) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, version, git_commit, status, eval_run_id, manifest_hash, "
                "created_at FROM knowledge_versions ORDER BY id DESC LIMIT 1"
            )
        ).first()
    return dict(row._mapping) if row else None
