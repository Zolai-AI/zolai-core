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
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
