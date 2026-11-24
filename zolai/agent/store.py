"""``agent_runs`` persistence (P3 §C) — additive DDL, JSON columns, CRUD.

The table is created by ``zolai.data.migrations.create_agent_runs_table`` on
app boot; :func:`ensure_agent_runs_table` re-asserts ``CREATE TABLE IF NOT
EXISTS`` so the CLI and tests can run against a fresh store without booting
the API (still purely additive — no DROP/RENAME/ALTER).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from ..shared.clock import stamp

logger = logging.getLogger(__name__)

#: Column order used by INSERT (kept explicit so the shape is greppable).
COLUMNS: tuple[str, ...] = (
    "goal",
    "status",
    "phases",
    "tool_calls",
    "evidence",
    "answer",
    "provider",
    "model",
    "turns",
    "latency_ms",
    "outcome",
    "feedback_score",
    "error",
    "mode",
    "created_by",
    "created_at",
    "finished_at",
)

_JSON_COLUMNS = {"phases", "tool_calls", "evidence"}
_INT_COLUMNS = {"turns"}
_FLOAT_COLUMNS = {"latency_ms", "feedback_score"}

_JSON_LOADS = ("phases", "tool_calls", "evidence")


def _engine() -> Engine:
    from ..data.repositories import get_engine

    return get_engine()


def ensure_agent_runs_table(engine: Engine | None = None) -> None:
    """Idempotent ``CREATE TABLE IF NOT EXISTS`` for ``agent_runs`` + indexes."""
    eng = engine or _engine()
    with eng.begin() as conn:
        conn.execute(
            sa_text(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    phases TEXT NOT NULL DEFAULT '{}',
                    tool_calls TEXT NOT NULL DEFAULT '[]',
                    evidence TEXT NOT NULL DEFAULT '[]',
                    answer TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    turns INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL DEFAULT '',
                    feedback_score REAL,
                    error TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'rule',
                    created_by TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    finished_at TEXT
                )
                """
            )
        )
        conn.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS ix_agent_runs_status ON agent_runs(status)"
            )
        )
        conn.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS ix_agent_runs_created_at ON agent_runs(created_at)"
            )
        )


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_mapping"):
        data = dict(row._mapping)
    else:  # pragma: no cover - non-SQLAlchemy row
        data = dict(row)
    for key in _JSON_LOADS:
        raw = data.get(key)
        if isinstance(raw, str) and raw:
            try:
                data[key] = json.loads(raw)
            except ValueError:
                data[key] = {} if key == "phases" else []
        elif raw is None:
            data[key] = {} if key == "phases" else []
    data["turns"] = int(data.get("turns") or 0)
    data["latency_ms"] = float(data.get("latency_ms") or 0.0)
    return data


def create_run(
    goal: str,
    *,
    created_by: str = "system",
    mode: str = "rule",
    status: str = "running",
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Insert a new run row (``status='running'``) and return it."""
    eng = engine or _engine()
    ensure_agent_runs_table(eng)
    with eng.begin() as conn:
        cursor = conn.execute(
            sa_text(
                "INSERT INTO agent_runs (goal, status, mode, created_by) "
                "VALUES (:goal, :status, :mode, :created_by)"
            ),
            {"goal": str(goal), "status": status, "mode": mode, "created_by": created_by},
        )
        run_id = cursor.lastrowid
    created = get_run(int(run_id), engine=eng)
    assert created is not None
    return created


def update_run(run_id: int, *, engine: Engine | None = None, **fields: Any) -> dict[str, Any] | None:
    """Update a subset of run columns (JSON columns are serialised here)."""
    if not fields:
        return get_run(run_id, engine=engine)
    eng = engine or _engine()
    sets: list[str] = []
    params: dict[str, Any] = {"id": int(run_id)}
    for key, value in fields.items():
        if key not in COLUMNS or key == "id":
            continue
        if key in _JSON_COLUMNS:
            value = json.dumps(value, ensure_ascii=False)
        elif key in _INT_COLUMNS:
            value = int(value or 0)
        elif key in _FLOAT_COLUMNS:
            value = float(value) if value is not None else None
        sets.append(f"{key} = :{key}")
        params[key] = value
    if not sets:
        return get_run(run_id, engine=engine)
    with eng.begin() as conn:
        conn.execute(sa_text(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id = :id"), params)
    return get_run(run_id, engine=eng)


def get_run(run_id: int, *, engine: Engine | None = None) -> dict[str, Any] | None:
    """One run by id (``None`` when unknown → the API answers 404)."""
    eng = engine or _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM agent_runs WHERE id = :id"), {"id": int(run_id)}
            ).fetchone()
    except Exception:
        logger.exception("agent_runs read failed (id=%s)", run_id)
        return None
    return _row_to_dict(row) if row is not None else None


def list_runs(*, limit: int = 20, engine: Engine | None = None) -> list[dict[str, Any]]:
    """Most recent runs first."""
    eng = engine or _engine()
    limit = max(1, min(int(limit or 20), 100))
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                sa_text("SELECT * FROM agent_runs ORDER BY id DESC LIMIT :lim"),
                {"lim": limit},
            ).fetchall()
    except Exception:
        logger.exception("agent_runs list failed")
        return []
    return [_row_to_dict(r) for r in rows]


def set_feedback(run_id: int, score: float, *, engine: Engine | None = None) -> dict[str, Any] | None:
    """Record ``feedback_score`` on a finished run."""
    return update_run(run_id, feedback_score=float(score), engine=engine)


# ---------------------------------------------------------------------------
# Domain agent runtime (additive) — manifests, domain runs, events, queue
# ---------------------------------------------------------------------------

DOMAIN_RUN_COLUMNS: tuple[str, ...] = (
    "run_id",
    "agent_id",
    "mode",
    "status",
    "input_json",
    "output_json",
    "error",
    "dry_run",
    "autonomous",
    "requested_by",
    "scope",
    "started_at",
    "finished_at",
    "created_at",
)
_DOMAIN_JSON = {"input_json", "output_json"}
_DOMAIN_BOOL = {"dry_run", "autonomous"}


def ensure_domain_agent_tables(engine: Engine | None = None) -> None:
    eng = engine or _engine()
    from ..data.migrations import (
        create_agent_domain_runs_table,
        create_agent_manifests_table,
        create_agent_queue_table,
        create_agent_run_events_table,
    )

    class _Mgr:
        def __init__(self, engine: Engine):
            self.engine = engine

    mgr = _Mgr(eng)
    create_agent_manifests_table(mgr)
    create_agent_domain_runs_table(mgr)
    create_agent_run_events_table(mgr)
    create_agent_queue_table(mgr)


def _domain_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    for key in ("input_json", "output_json", "manifest_json", "capabilities_json", "validation_errors_json", "payload_json"):
        if key in data and isinstance(data[key], str) and data[key]:
            try:
                data[key] = json.loads(data[key])
            except ValueError:
                pass
    for key in ("dry_run", "autonomous"):
        if key in data:
            data[key] = bool(data[key])
    # Friendly aliases
    if "input_json" in data and "input" not in data:
        data["input"] = data["input_json"]
    if "output_json" in data and "output" not in data:
        data["output"] = data["output_json"]
    return data


def upsert_manifest_record(record: dict[str, Any], *, engine: Engine | None = None) -> dict[str, Any]:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    payload = {
        "agent_id": str(record.get("agent_id") or ""),
        "name": str(record.get("name") or ""),
        "version": str(record.get("version") or ""),
        "source_path": str(record.get("source_path") or ""),
        "manifest_json": json.dumps(record.get("manifest_json") or {}, ensure_ascii=False),
        "status": str(record.get("status") or "unknown"),
        "capabilities_json": json.dumps(record.get("capabilities_json") or {}, ensure_ascii=False),
        "validation_errors_json": json.dumps(record.get("validation_errors") or [], ensure_ascii=False),
        "registered_at": str(record.get("registered_at") or stamp()),
        "updated_at": str(record.get("updated_at") or stamp()),
    }
    with eng.begin() as conn:
        existing = conn.execute(
            sa_text("SELECT id FROM agent_manifests WHERE agent_id = :agent_id"),
            {"agent_id": payload["agent_id"]},
        ).fetchone()
        if existing:
            conn.execute(
                sa_text(
                    "UPDATE agent_manifests SET name=:name, version=:version, source_path=:source_path, "
                    "manifest_json=:manifest_json, status=:status, capabilities_json=:capabilities_json, "
                    "validation_errors_json=:validation_errors_json, updated_at=:updated_at WHERE agent_id=:agent_id"
                ),
                payload,
            )
            row_id = int(existing[0])
        else:
            cursor = conn.execute(
                sa_text(
                    "INSERT INTO agent_manifests (agent_id, name, version, source_path, manifest_json, status, capabilities_json, validation_errors_json, registered_at, updated_at) "
                    "VALUES (:agent_id, :name, :version, :source_path, :manifest_json, :status, :capabilities_json, :validation_errors_json, :registered_at, :updated_at)"
                ),
                payload,
            )
            row_id = int(cursor.lastrowid)
    return get_manifest_record(payload["agent_id"], engine=eng) or {"id": row_id, **payload}


def get_manifest_record(agent_id: str, *, engine: Engine | None = None) -> dict[str, Any] | None:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    with eng.connect() as conn:
        row = conn.execute(
            sa_text("SELECT * FROM agent_manifests WHERE agent_id = :agent_id"),
            {"agent_id": str(agent_id)},
        ).fetchone()
    return _domain_row_to_dict(row) if row else None


def list_manifest_records(*, limit: int = 200, engine: Engine | None = None) -> list[dict[str, Any]]:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    limit = max(1, min(int(limit or 200), 500))
    with eng.connect() as conn:
        rows = conn.execute(
            sa_text("SELECT * FROM agent_manifests ORDER BY agent_id ASC LIMIT :lim"),
            {"lim": limit},
        ).fetchall()
    return [_domain_row_to_dict(r) for r in rows]


def create_domain_run(record: dict[str, Any], *, engine: Engine | None = None) -> dict[str, Any]:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    payload = {
        "run_id": record.get("run_id"),
        "agent_id": str(record.get("agent_id") or ""),
        "mode": str(record.get("mode") or "manual"),
        "status": str(record.get("status") or "queued"),
        "input_json": json.dumps(record.get("input") or {}, ensure_ascii=False),
        "output_json": json.dumps(record.get("output") or {}, ensure_ascii=False),
        "error": str(record.get("error") or ""),
        "dry_run": 1 if record.get("dry_run") else 0,
        "autonomous": 1 if record.get("autonomous") else 0,
        "requested_by": str(record.get("requested_by") or "system"),
        "scope": str(record.get("scope") or "admin"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "created_at": str(record.get("created_at") or stamp()),
    }
    with eng.begin() as conn:
        cursor = conn.execute(
            sa_text(
                "INSERT INTO agent_domain_runs (run_id, agent_id, mode, status, input_json, output_json, error, dry_run, autonomous, requested_by, scope, started_at, finished_at, created_at) "
                "VALUES (:run_id, :agent_id, :mode, :status, :input_json, :output_json, :error, :dry_run, :autonomous, :requested_by, :scope, :started_at, :finished_at, :created_at)"
            ),
            payload,
        )
        row_id = int(cursor.lastrowid)
    return get_domain_run(row_id, engine=eng) or {"id": row_id, **record}


def update_domain_run(domain_run_id: int, *, engine: Engine | None = None, **fields: Any) -> dict[str, Any] | None:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    if not fields:
        return get_domain_run(domain_run_id, engine=eng)
    sets = []
    params: dict[str, Any] = {"id": int(domain_run_id)}
    for k, v in fields.items():
        if k not in DOMAIN_RUN_COLUMNS:
            continue
        if k in _DOMAIN_JSON:
            v = json.dumps(v or {}, ensure_ascii=False)
        elif k in _DOMAIN_BOOL:
            v = 1 if v else 0
        sets.append(f"{k} = :{k}")
        params[k] = v
    if not sets:
        return get_domain_run(domain_run_id, engine=eng)
    with eng.begin() as conn:
        conn.execute(sa_text(f"UPDATE agent_domain_runs SET {', '.join(sets)} WHERE id = :id"), params)
    return get_domain_run(domain_run_id, engine=eng)


def get_domain_run(domain_run_id: int, *, engine: Engine | None = None) -> dict[str, Any] | None:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    with eng.connect() as conn:
        row = conn.execute(
            sa_text("SELECT * FROM agent_domain_runs WHERE id = :id"),
            {"id": int(domain_run_id)},
        ).fetchone()
    return _domain_row_to_dict(row) if row else None


def list_domain_runs(*, limit: int = 20, agent_id: str | None = None, engine: Engine | None = None) -> list[dict[str, Any]]:
    eng = engine or _engine()
    ensure_domain_agent_tables(eng)
    limit = max(1, min(int(limit or 20), 100))
    params: dict[str, Any] = {"lim": limit}
    where = ""
    if agent_id:
        where = "WHERE agent_id = :agent_id"
        params["agent_id"] = str(agent_id)
    with eng.connect() as conn:
        rows = conn.execute(
            sa_text(f"SELECT * FROM agent_domain_runs {where} ORDER BY id DESC LIMIT :lim"),
            params,
        ).fetchall()
    return [_domain_row_to_dict(r) for r in rows]
