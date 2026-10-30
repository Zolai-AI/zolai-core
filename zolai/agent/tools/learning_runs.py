"""Admin-only reads over the local learning cycle: runs, versions, RAG state.

Three questions an operator asks after ``zolai learn run`` — *what happened*,
*what did it publish*, and *what does the vector index look like now* — answered
without opening the database by hand. Every handler here issues SELECTs only:
the write path belongs to the CLI (``zolai learn run --apply``), because a
tool call must never trigger a backup, a training run or a prune.

**Nothing leaves through these tools that the wire should not carry.** The
ledger stores artifacts as paths and step metrics as the step returned them, so
:func:`_scrub` drops path-bearing keys (``staging_ref``, ``published_ref``,
``options`` …) and rewrites any absolute path that survives, and a secret-shaped
``key=value`` is redacted. ``get_run`` already withholds ``error_message``; the
classification (``error_class``) is kept because it is the operator's signal.

Scopes follow the registry's narrowing rule: ``pipeline:read`` for the run
ledger, ``eval:read`` for version snapshots (each carries an ``eval_run_id``),
``rag:read`` for the vector index. Admin tier only — the anonymous and member
sets are untouched.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .types import ToolError, ToolErrorCode, ToolSpec

_OBJ = "object"

#: Keys whose values are paths, scratch options, or whole sub-reports. The
#: ledger stores them for the operator on disk; a tool response does not need
#: them, and a path is exactly what must not leave the server.
_DROP_KEYS = frozenset(
    {
        "options",
        "report",
        "db_path",
        "backup_dir",
        "report_dir",
        "staging_ref",
        "published_ref",
        "model_path",
        "artifact_path",
    }
)

#: A secret-shaped assignment inside a string value (same class the ledger's
#: own sanitizer redacts, so the tool cannot leak what the ledger hid).
_SECRET_ALIAS = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+"
)
#: An absolute path fragment after a delimiter or at the start of a value.
_ABS_FRAGMENT = re.compile(r"(?:^|(?<=[\s\"'(=:]))(?:~?/|[A-Za-z]:[\\/])\S*")


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": _OBJ, "properties": properties, "required": required or []}


def _opt_int(inp: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(inp.get(key, default))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _opt_bool(inp: dict[str, Any], key: str, default: bool) -> bool:
    raw = inp.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _scrub(value: Any) -> Any:
    """Return ``value`` without path-bearing keys, absolute paths or secrets."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        text = _SECRET_ALIAS.sub(r"\1=<redacted>", value)
        text = _ABS_FRAGMENT.sub("<path>", text)
        return text[:500]
    if isinstance(value, float) and value != value:  # NaN — not JSON
        return None
    return value


def _engine() -> Any:
    from ...data.repositories import get_engine

    return get_engine()


def _no_such_table(exc: BaseException) -> bool:
    return "no such table" in str(exc).lower()


# ── learning_run_status ─────────────────────────────────────────────────────


def _run_status(inp: dict[str, Any]) -> Any:
    from ...learning.run_ledger import get_run, list_runs, validate_run_id

    raw_run_id = str(inp.get("run_id") or "").strip()
    run_id = raw_run_id or None
    if run_id is not None:
        try:
            validate_run_id(run_id)
        except ValueError as exc:
            raise ToolError(
                "run_id may contain only letters, digits, '_', '.' and '-' (1-80 chars)."
            ) from exc
    limit = _opt_int(inp, "limit", 5, 1, 20)
    include_steps = _opt_bool(inp, "include_steps", True)

    engine = _engine()
    try:
        current = get_run(engine, run_id, include_steps=include_steps)
        history = list_runs(engine, limit=limit, include_steps=False)
    except Exception as exc:
        if _no_such_table(exc):
            return {
                "run": None,
                "runs": [],
                "count": 0,
                "note": (
                    "No learning run has recorded a ledger row in this database yet "
                    "(the learning_runs table does not exist)."
                ),
            }
        raise ToolError(
            "The learning-run ledger is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc

    if run_id is not None and current is None:
        raise ToolError(f"unknown learning run: {run_id}", ToolErrorCode.NOT_FOUND)

    if current is None and not history:
        note = "No learning run has recorded a ledger row in this database yet."
    else:
        note = (
            "History rows never carry per-step detail; request a run_id with "
            "include_steps=true for that run's steps."
        )
    return {
        "run": _scrub(current),
        "runs": [_scrub(row) for row in history],
        "count": len(history),
        "note": note,
    }


# ── learning_version_history ────────────────────────────────────────────────


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _version_history(inp: dict[str, Any]) -> Any:
    from sqlalchemy import text

    limit = _opt_int(inp, "limit", 10, 1, 50)
    include_runs = _opt_bool(inp, "include_runs", False)
    engine = _engine()

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, version, git_commit, status, eval_run_id, "
                    "manifest_hash, created_at, source_versions "
                    "FROM knowledge_versions ORDER BY id DESC LIMIT :lim"
                ),
                {"lim": limit},
            ).fetchall()
    except Exception as exc:
        if _no_such_table(exc):
            return {
                "versions": [],
                "count": 0,
                "note": "This database has no knowledge_versions table yet (run `zolai db migrate`).",
            }
        raise ToolError(
            "The knowledge-version table is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc

    versions: list[dict[str, Any]] = []
    run_ids: list[str] = []
    for row in rows:
        sources = _parse_json(row[7])
        learning_run = str(sources.get("learning_run") or "") or None
        if learning_run:
            run_ids.append(learning_run)
        versions.append(
            {
                "id": row[0],
                "version": row[1],
                "git_commit": row[2],
                "status": row[3],
                "eval_run_id": row[4],
                "manifest_hash": row[5],
                "created_at": row[6],
                "learning_run": learning_run,
                "mode": sources.get("mode"),
            }
        )

    if include_runs and run_ids:
        runs = _runs_by_id(engine, run_ids)
        for version in versions:
            run = runs.get(version["learning_run"])
            if run is not None:
                version["run"] = run

    return {"versions": _scrub(versions), "count": len(versions)}


def _runs_by_id(engine: Any, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Ledger status for each recorded run id, in one query (read-only)."""
    from sqlalchemy import text

    placeholders = ", ".join(f":r{i}" for i in range(len(run_ids)))
    params = {f"r{i}": run_id for i, run_id in enumerate(run_ids)}
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT run_id, mode, status, started_at, finished_at, "
                    "elapsed_seconds, version_tag, knowledge_version_id, "
                    "steps_total, steps_ok, steps_failed, warnings, error_class "
                    f"FROM learning_runs WHERE run_id IN ({placeholders})"
                ),
                params,
            ).fetchall()
    except Exception:  # noqa: BLE001 — the version list is still useful
        return {}
    return {
        str(row[0]): {
            "run_id": row[0],
            "mode": row[1],
            "status": row[2],
            "started_at": row[3],
            "finished_at": row[4],
            "elapsed_seconds": row[5],
            "version_tag": row[6],
            "knowledge_version_id": row[7],
            "steps_total": row[8],
            "steps_ok": row[9],
            "steps_failed": row[10],
            "warnings": row[11],
            "error_class": row[12],
        }
        for row in rows
    }


# ── learning_rag_status ─────────────────────────────────────────────────────


def _source_tables() -> list[str]:
    from ...rag.build import SOURCES

    seen: list[str] = []
    for spec in SOURCES:
        if spec.table not in seen:
            seen.append(spec.table)
    return seen


def _id_column(table: str) -> str:
    from ...rag.build import SOURCES

    for spec in SOURCES:
        if spec.table == table:
            return spec.id_col
    return "id"


def _table_present(conn: Any, table: str) -> bool:
    from sqlalchemy import text

    return (
        conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).first()
        is not None
    )


def _stale_count(table: str, budget: int = 100_000) -> int | None:
    """Distinct source ids whose vectors outlived the row (read-only).

    ``None`` when the check was skipped: the source table is absent, or it is
    larger than ``budget`` rows so the answer would cost a full scan of both
    sides. The caller reports that as "not checked", never as zero.
    """
    from sqlalchemy import text

    engine = _engine()
    id_col = _id_column(table)
    with engine.connect() as conn:
        if not _table_present(conn, table):
            return None
        live_total = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        if int(live_total or 0) > budget:
            return None
        rows = conn.execute(
            text(
                "SELECT DISTINCT json_extract(metadata, '$.source_id') AS sid "
                "FROM knowledge_vectors WHERE source = :t"
            ),
            {"t": table},
        ).fetchall()
        attributed = {str(row[0]) for row in rows if row[0] is not None}
        if not attributed:
            return 0
        live = {
            str(row[0])
            for row in conn.execute(text(f"SELECT CAST({id_col} AS TEXT) FROM {table}"))
        }
    return len(attributed - live)


def _rag_status(inp: dict[str, Any]) -> Any:
    from sqlalchemy import text

    from ...rag.build import SOURCES

    known_tables = _source_tables()
    known_sources = set(known_tables)
    known_sources.update(spec.source_type for spec in SOURCES)

    raw = inp.get("sources")
    selected = known_tables
    if raw is not None:
        if not isinstance(raw, (list, tuple)) or not all(isinstance(s, str) for s in raw):
            raise ToolError("sources must be a list of source table names.")
        wanted = {str(s).strip() for s in raw if str(s).strip()}
        unknown = sorted(wanted - known_sources)
        if unknown:
            raise ToolError(
                "unknown source(s): " + ", ".join(unknown)
                + ". Known tables: " + ", ".join(known_tables) + "."
            )
        selected = [
            table
            for table in known_tables
            if table in wanted
            or any(
                spec.table == table and spec.source_type in wanted for spec in SOURCES
            )
        ]

    check_stale = _opt_bool(inp, "check_stale", False)
    engine = _engine()

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source, COUNT(*), "
                    "SUM(CASE WHEN json_extract(metadata, '$.source_id') IS NULL "
                    "THEN 1 ELSE 0 END) "
                    "FROM knowledge_vectors GROUP BY source"
                )
            ).fetchall()
    except Exception as exc:
        if _no_such_table(exc):
            return {
                "sources": [],
                "count": 0,
                "total_vectors": 0,
                "note": "This database has no knowledge_vectors table yet.",
            }
        raise ToolError(
            "The vector index is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc

    totals = {str(row[0]): (int(row[1] or 0), int(row[2] or 0)) for row in rows}
    from ...rag.incremental import watermark

    out: list[dict[str, Any]] = []
    for table in selected:
        vectors, unattributable = totals.get(table, (0, 0))
        entry: dict[str, Any] = {
            "source": table,
            "vectors": vectors,
            "unattributable_rows": unattributable,
            "indexed": vectors > 0,
        }
        try:
            entry["watermark"] = watermark(engine, table)
        except Exception:  # noqa: BLE001 — a missing table is reported, not raised
            entry["watermark"] = None
        if check_stale and vectors:
            stale = _stale_count(table)
            entry["stale_source_ids"] = stale
            entry["stale_checked"] = stale is not None
        out.append(entry)

    return {
        "sources": out,
        "count": len(out),
        "total_vectors": sum(entry["vectors"] for entry in out),
        "check_stale": check_stale,
        "note": (
            "Nothing is deleted here. 'unattributable_rows' are legacy vectors with no "
            "source_id; 'stale_source_ids' counts distinct source rows whose vectors "
            "outlived them. A learning run with --prune-stale removes the latter."
        ),
    }


# ── Specs + export tables (merged into registry.SPECS / executor.HANDLERS) ──

LEARNING_RUN_SPECS: dict[str, ToolSpec] = {
    "learning_run_status": ToolSpec(
        name="learning_run_status",
        description=(
            "Read the local learning-run ledger: the latest (or a named) run plus recent "
            "history — status, step counts, artifact statuses and version tags. Read-only; "
            "reports no paths, options or raw error text."
        ),
        parameters=_obj(
            {
                "run_id": {"type": "string", "minLength": 1, "maxLength": 80},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "include_steps": {"type": "boolean"},
            }
        ),
        admin_only=True,
        scope="pipeline:read",
        title="Local learning run status",
        destructive=False,
    ),
    "learning_version_history": ToolSpec(
        name="learning_version_history",
        description=(
            "List knowledge-version snapshots newest first — version tag, status, manifest "
            "hash, eval run and the learning run that produced it. Read-only."
        ),
        parameters=_obj(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "include_runs": {"type": "boolean"},
            }
        ),
        admin_only=True,
        scope="eval:read",
        title="Knowledge version history",
        destructive=False,
    ),
    "learning_rag_status": ToolSpec(
        name="learning_rag_status",
        description=(
            "Read the RAG vector index per source table: vector counts, unattributable "
            "rows and the newest indexing watermark; optionally count stale source rows. "
            "Read-only — never prunes or re-embeds."
        ),
        parameters=_obj(
            {
                "sources": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "check_stale": {"type": "boolean"},
            }
        ),
        admin_only=True,
        scope="rag:read",
        title="RAG index status",
        destructive=False,
    ),
}

LEARNING_RUN_TOOL_NAMES: tuple[str, ...] = tuple(LEARNING_RUN_SPECS)

LEARNING_RUN_HANDLERS = {
    "learning_run_status": _run_status,
    "learning_version_history": _version_history,
    "learning_rag_status": _rag_status,
}
