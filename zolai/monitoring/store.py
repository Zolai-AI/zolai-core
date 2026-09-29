"""Persistence and health helpers behind the metrics REST API.

Everything here goes through :class:`~zolai.data.database.DatabaseManager`
(session commit/rollback semantics + ``BEGIN IMMEDIATE`` writers), never a
private connection, so annotations and integrity rows follow the same ACID
path as the rest of the toolkit.

Secrets are read from the environment only (``GRAFANA_URL``,
``GRAFANA_API_KEY``) — nothing is ever hard-coded.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import config
from ..data.database import DatabaseManager, get_manager

logger = logging.getLogger(__name__)

#: Process start (unix seconds) — backs ``uptime_s`` / ``started_at``.
PROCESS_STARTED_AT = time.time()

#: Annotation kinds accepted by the Grafana-compatible API.
ANNOTATION_KINDS = ("deploy", "eval", "manual")

#: Cached git commit (resolved once per process).
_COMMIT: str | None = None

__all__ = [
    "ANNOTATION_KINDS",
    "PROCESS_STARTED_AT",
    "annotation_payload",
    "build_info",
    "check_health",
    "create_annotation",
    "db_stats",
    "delete_annotation",
    "disk_status",
    "latest_eval_run",
    "latest_integrity",
    "list_annotations",
    "push_annotation_to_grafana",
    "update_annotation",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _manager() -> DatabaseManager:
    return get_manager()


def _db_path(mgr: DatabaseManager | None = None) -> Path | None:
    """Resolve the SQLite file for ``mgr`` (default singleton)."""
    mgr = mgr or _manager()
    path = mgr._get_db_path()
    if path is not None:
        return path
    return None


def _ensure_schema(mgr: DatabaseManager) -> None:
    """Create the monitoring tables when they are missing (idempotent)."""
    from sqlalchemy import inspect

    if not inspect(mgr.engine).has_table("monitoring_annotations"):
        from ..data.migrations import create_monitoring_tables

        create_monitoring_tables(mgr)


def _row_annotation(row: Any) -> dict[str, Any]:
    """Convert a ``monitoring_annotations`` row into the API shape."""
    raw_time = str(row.time)
    value: int | str = int(raw_time) if raw_time.lstrip("-").isdigit() else raw_time
    try:
        tags = json.loads(row.tags) if row.tags else []
    except (TypeError, ValueError):
        tags = []
    return {
        "id": row.id,
        "time": value,
        "title": row.title,
        "text": row.text or "",
        "tags": tags if isinstance(tags, list) else [],
        "kind": row.kind,
        "created_at": row.created_at,
        "dashboard_id": row.dashboard_id,
        "panel_id": row.panel_id,
    }


def annotation_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a (Grafana-compatible) annotation body into storage fields.

    Accepts both Grafana's camelCase (``dashboardId``/``panelId``) and our
    snake_case spelling; ``time`` defaults to "now".
    """
    tags = data.get("tags") or []
    if not isinstance(tags, (list, tuple)):
        tags = [str(tags)]
    raw_time = data.get("time")
    if raw_time is None:
        raw_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    kind = str(data.get("kind") or "manual")
    if kind not in ANNOTATION_KINDS:
        raise ValueError(f"kind must be one of {ANNOTATION_KINDS}")
    return {
        "time": str(raw_time),
        "title": str(data.get("title") or ""),
        "text": str(data.get("text") or ""),
        "tags": json.dumps([str(t) for t in tags]),
        "kind": kind,
        "dashboard_id": data.get("dashboardId", data.get("dashboard_id")),
        "panel_id": data.get("panelId", data.get("panel_id")),
    }


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
def list_annotations(limit: int = 100) -> list[dict[str, Any]]:
    """Return annotations, newest first."""
    from ..data.models import MonitoringAnnotation

    mgr = _manager()
    _ensure_schema(mgr)
    with mgr.session() as session:
        rows = (
            session.query(MonitoringAnnotation)
            .order_by(MonitoringAnnotation.id.desc())
            .limit(max(1, min(limit, 1000)))
            .all()
        )
        return [_row_annotation(row) for row in rows]


def create_annotation(data: dict[str, Any]) -> dict[str, Any]:
    """Insert an annotation; returns ``{id, grafana_id}``."""
    from ..data.models import MonitoringAnnotation

    payload = annotation_payload(data)
    mgr = _manager()
    _ensure_schema(mgr)
    with mgr.write_session() as session:
        row = MonitoringAnnotation(
            time=payload["time"],
            title=payload["title"],
            text=payload["text"],
            tags=payload["tags"],
            kind=payload["kind"],
            dashboard_id=payload["dashboard_id"],
            panel_id=payload["panel_id"],
            created_at=_now(),
        )
        session.add(row)
        session.flush()
        annotation_id = int(row.id)
    return {"id": annotation_id, "grafana_id": None}


def update_annotation(annotation_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Update an existing annotation; returns ``{updated}``."""
    from ..data.models import MonitoringAnnotation

    mgr = _manager()
    _ensure_schema(mgr)
    with mgr.write_session() as session:
        row = session.get(MonitoringAnnotation, annotation_id)
        if row is None:
            raise KeyError(annotation_id)
        payload = annotation_payload({**_row_annotation(row), **data})
        row.time = payload["time"]
        row.title = payload["title"]
        row.text = payload["text"]
        row.tags = payload["tags"]
        row.kind = payload["kind"]
        row.dashboard_id = payload["dashboard_id"]
        row.panel_id = payload["panel_id"]
        session.flush()
    return {"updated": annotation_id}


def delete_annotation(annotation_id: int) -> dict[str, Any]:
    """Delete an annotation; returns ``{deleted}``."""
    from ..data.models import MonitoringAnnotation

    mgr = _manager()
    _ensure_schema(mgr)
    with mgr.write_session() as session:
        row = session.get(MonitoringAnnotation, annotation_id)
        if row is None:
            raise KeyError(annotation_id)
        session.delete(row)
    return {"deleted": annotation_id}


async def push_annotation_to_grafana(data: dict[str, Any]) -> dict[str, Any] | None:
    """Forward an annotation to Grafana when ``GRAFANA_URL`` is configured.

    Returns the Grafana response body (with its ``id``) or ``None`` when
    Grafana is not configured, the call fails, or the key is missing.
    """
    base_url = os.environ.get("GRAFANA_URL", "").strip()
    if not base_url:
        return None
    api_key = os.environ.get("GRAFANA_API_KEY", "").strip()
    if not api_key:
        logger.debug("GRAFANA_URL set without GRAFANA_API_KEY; skipping push")
        return None

    body = {
        "time": data.get("time"),
        "title": data.get("title"),
        "text": data.get("text", ""),
        "tags": data.get("tags") or [],
    }
    for source, target in (("dashboardId", "dashboardId"), ("panelId", "panelId")):
        if data.get(source) is not None:
            body[target] = data[source]

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/annotations",
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            return response.json() if response.content else {}
    except Exception as exc:  # noqa: BLE001 — Grafana is best-effort
        logger.debug("Grafana annotation push failed: %s", exc)
        return None


def record_grafana_id(annotation_id: int, grafana_id: int) -> None:
    """Store the id Grafana assigned to a pushed annotation."""
    from ..data.models import MonitoringAnnotation

    try:
        mgr = _manager()
        with mgr.write_session() as session:
            row = session.get(MonitoringAnnotation, annotation_id)
            if row is not None:
                row.grafana_id = int(grafana_id)
    except Exception as exc:  # noqa: BLE001 — bookkeeping only
        logger.debug("grafana_id persistence skipped: %s", exc)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def latest_eval_run() -> dict[str, Any] | None:
    """Return the most recent ``eval_runs`` row (``None`` when unavailable)."""
    from sqlalchemy import inspect, text

    mgr = _manager()
    if not inspect(mgr.engine).has_table("eval_runs"):
        return None
    with mgr.session() as session:
        row = session.execute(
            text(
                "SELECT set_name, created_at, case_count, duration_ms, "
                "gate_passed, metrics, source FROM eval_runs "
                "ORDER BY id DESC LIMIT 1"
            )
        ).first()
    if row is None:
        return None
    try:
        metrics = json.loads(row.metrics) if row.metrics else {}
    except (TypeError, ValueError):
        metrics = {}
    return {
        "set_name": row.set_name,
        "created_at": row.created_at,
        "case_count": int(row.case_count or 0),
        "duration_ms": float(row.duration_ms or 0.0),
        "gate_passed": bool(row.gate_passed),
        "metrics": metrics if isinstance(metrics, dict) else {},
        "source": row.source or "db",
    }


# ---------------------------------------------------------------------------
# Integrity / health
# ---------------------------------------------------------------------------
def latest_integrity() -> dict[str, Any]:
    """Latest recorded integrity report, running a fast FK check if stale.

    The expensive ``PRAGMA integrity_check`` is never triggered here — this is
    a health endpoint.
    """
    from sqlalchemy import inspect, text

    from ..data.integrity import foreign_key_check
    from .metrics import DB_INTEGRITY_STATUS

    mgr = _manager()
    report: dict[str, Any] | None = None
    if inspect(mgr.engine).has_table("db_integrity_runs"):
        with mgr.session() as session:
            row = session.execute(
                text(
                    "SELECT ok, issues, checked_at FROM db_integrity_runs "
                    "ORDER BY id DESC LIMIT 1"
                )
            ).first()
        if row is not None:
            try:
                issues = json.loads(row.issues) if row.issues else []
            except (TypeError, ValueError):
                issues = []
            report = {
                "ok": bool(row.ok),
                "issues": issues if isinstance(issues, list) else [],
                "checked_at": row.checked_at,
            }
    if report is None:
        check = foreign_key_check(mgr)
        report = {
            "ok": check["ok"],
            "issues": check["issues"],
            "checked_at": check["checked_at"],
        }
    DB_INTEGRITY_STATUS.set(1.0 if report["ok"] else 0.0)
    return report


def disk_status(path: Path | str) -> dict[str, Any]:
    """Free space and writability for ``path`` (nearest existing parent)."""
    target = Path(path)
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
        free_bytes: int | None = int(usage.free)
    except OSError:
        free_bytes = None
    return {
        "path": str(target),
        "free_bytes": free_bytes,
        "writable": os.access(probe, os.W_OK) if probe.exists() else False,
    }


def db_status() -> dict[str, Any]:
    """Writable flag, journal mode and FK enforcement for the canonical DB."""
    mgr = _manager()
    path = _db_path(mgr)
    writable = True if path is None else (path.exists() and os.access(path, os.W_OK))
    wal_mode = "unknown"
    foreign_keys = False
    try:
        with mgr.engine.connect() as conn:
            mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
            wal_mode = str(mode).lower() if mode else "unknown"
            foreign_keys = bool(conn.exec_driver_sql("PRAGMA foreign_keys").scalar())
    except Exception as exc:  # noqa: BLE001 — health must not raise
        logger.debug("db_status probe failed: %s", exc)
        writable = False
    return {
        "writable": bool(writable),
        "wal_mode": wal_mode,
        "foreign_keys": foreign_keys,
        "path": str(path) if path else None,
    }


def check_health() -> dict[str, Any]:
    """Aggregate DB + disk + process health.

    ``status`` is ``ok`` or ``degraded``; the router answers 503 when degraded.
    """
    db = db_status()
    integrity = latest_integrity()
    disk = disk_status(_db_path(_manager()) or config.paths.data)
    uptime_s = round(time.time() - PROCESS_STARTED_AT, 3)

    healthy = all(
        (
            db["writable"],
            bool(integrity["ok"]),
            bool(disk.get("writable")),
            disk.get("free_bytes") is not None,
        )
    )
    return {
        "status": "ok" if healthy else "degraded",
        "db": {
            "writable": db["writable"],
            "wal_mode": db["wal_mode"],
            "foreign_keys": db["foreign_keys"],
            "integrity": {
                "ok": integrity["ok"],
                "issues": integrity["issues"],
                "checked_at": integrity["checked_at"],
            },
        },
        "disk": disk,
        "uptime_s": uptime_s,
        "version": build_info()["version"],
    }


# ---------------------------------------------------------------------------
# Stats / build info
# ---------------------------------------------------------------------------
def db_stats() -> dict[str, Any]:
    """DB size, WAL size, curated row total and p95 query latency."""
    from .background import sample_now

    sampled = sample_now()
    return {
        "size_bytes": sampled["size_bytes"],
        "wal_bytes": sampled["wal_bytes"],
        "rows_total": sampled["rows_total"],
        "query_p95_ms": sampled["query_p95_ms"],
    }


def resolve_commit() -> str:
    """Best-effort git commit for ``zolai_build_info`` (cached per process)."""
    global _COMMIT
    if _COMMIT is not None:
        return _COMMIT
    for key in ("GIT_COMMIT", "COMMIT_SHA", "GITHUB_SHA"):
        value = os.environ.get(key, "").strip()
        if value:
            _COMMIT = value[:12]
            return _COMMIT
    commit = "unknown"
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit = result.stdout.decode().strip()
    except Exception:  # noqa: BLE001 — git may be absent in containers
        commit = "unknown"
    _COMMIT = commit
    return commit


def build_info() -> dict[str, Any]:
    """Build metadata mirrored by the ``zolai_build_info`` metric."""
    import platform
    import sys

    from .. import __version__

    started_at = datetime.fromtimestamp(PROCESS_STARTED_AT, tz=timezone.utc).isoformat(
        timespec="seconds"
    )
    backend = "postgresql" if os.environ.get("ZOLAI_PG_URL") else "sqlite"
    return {
        "version": __version__,
        "python": platform.python_version(),
        "executable": sys.executable,
        "commit": resolve_commit(),
        "started_at": started_at,
        "db_backend": backend,
        "metrics": {
            "enabled": True,
            "rules_url": "/api/metrics/alerts",
        },
    }
