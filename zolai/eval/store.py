"""DB-first evaluation store: ``eval_sets`` + ``eval_cases``.

The two tables created by :func:`ensure_schema` live in the canonical SQLite
store (``data/zolai.db``) and are the **runtime source of truth** for every
evaluation set. The bundled ``.jsonl`` files under :data:`SETS_DIR` are
demoted to import/export interchange:

- :func:`import_jsonl_to_set` / :func:`export_set_to_jsonl` — JSONL ⇄ DB.
- :func:`seed_sets` — loads the bundled fixtures (used by
  ``scripts/eval/seed_eval_sets.py`` and ``scripts/ci_prepare_db.py``).

The module is stdlib-only (``sqlite3`` + :mod:`zolai.config`) so the offline
evaluation package keeps its dependency-free guarantee.

Field mapping (payloads are stored verbatim as JSON):

===========  =========================================================
kind         payload fields
===========  =========================================================
``zvs``      ``text``
``qa``       ``question`` / ``hyp`` / ``answer``
``translation``  ``hyp`` / ``ref`` (``source`` kept when present)
===========  =========================================================
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import config

#: Evaluation case lanes (mirrors the ``eval_cases.kind`` CHECK constraint).
KINDS: tuple[str, ...] = ("zvs", "qa", "translation")

#: Sentinel ``set_name`` meaning "every active set, merged".
ALL_SETS = "*"

#: Directory holding the bundled JSONL interchange files.
SETS_DIR = Path(__file__).resolve().parent / "sets"

#: Bundled fixtures: ``(set_name, filename, kind)`` — one set per bundle.
SEED_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("smoke", "smoke_zvs.jsonl", "zvs"),
    ("smoke", "smoke_qa.jsonl", "qa"),
    ("smoke", "smoke_translation.jsonl", "translation"),
    ("eval_v1", "eval_v1_zvs.jsonl", "zvs"),
    ("eval_v1", "eval_v1_qa.jsonl", "qa"),
    ("eval_v1", "eval_v1_translation.jsonl", "translation"),
    ("benchmark_qa", "benchmark_qa.jsonl", "qa"),
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eval_sets(
  set_name TEXT PRIMARY KEY,
  version TEXT,
  description TEXT,
  case_count INTEGER,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS eval_cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  set_name TEXT NOT NULL REFERENCES eval_sets(set_name),
  kind TEXT NOT NULL CHECK(kind IN ('zvs','qa','translation')),
  payload TEXT NOT NULL,
  source_table TEXT,
  ordinal INTEGER,
  is_active INTEGER DEFAULT 1,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_cases_lookup
  ON eval_cases(set_name, is_active, ordinal);
"""


def _db_path() -> Path:
    """Resolve the eval store DB path.

    ``ZOLAI_DB_PATH`` is checked **at call time** (so tests can monkeypatch
    it); otherwise the canonical ``config.paths.zolai_db`` is used.
    """
    env = os.environ.get("ZOLAI_DB_PATH", "").strip()
    if env:
        return Path(env)
    return Path(config.paths.zolai_db)


def _now() -> str:
    """UTC timestamp (second precision) for the bookkeeping columns."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(db_path: Path | str | None = None) -> Path:
    """Create ``eval_sets`` / ``eval_cases`` if missing (idempotent).

    Additive DDL only — no existing table is touched. Called by every read
    and write in this module.

    Args:
        db_path: Explicit DB path; defaults to :func:`_db_path`.

    Returns:
        The resolved database path.
    """
    path = Path(db_path) if db_path is not None else _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return path


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with the schema guaranteed to exist."""
    path = ensure_schema(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def list_sets(*, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return every eval set row (``set_name``, ``version``, ``case_count``…)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT set_name, version, description, case_count, created_at, updated_at "
            "FROM eval_sets ORDER BY set_name"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_kinds(
    set_name: str | None = None, *, db_path: Path | str | None = None
) -> list[str]:
    """Return the distinct active case kinds (optionally within one set)."""
    sql = "SELECT DISTINCT kind FROM eval_cases WHERE is_active = 1"
    params: tuple[Any, ...] = ()
    if set_name is not None:
        sql += " AND set_name = ?"
        params = (set_name,)
    sql += " ORDER BY kind"
    conn = _connect(db_path)
    try:
        return [str(row["kind"]) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def fetch_cases(
    set_name: str,
    kind: str | None = None,
    *,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Fetch active cases grouped by lane, ordered by ``ordinal``.

    Args:
        set_name: Set to read, or :data:`ALL_SETS` (``"*"``) for every set.
        kind: Optional lane filter (``zvs`` / ``qa`` / ``translation``).
        db_path: Explicit DB path override.

    Returns:
        One dict per case: ``id``, ``set_name``, ``kind``, ``ordinal`` and
        ``payload`` (the parsed JSON object, stored verbatim). Empty when the
        set is unknown — callers decide whether that is an error.
    """
    sql = (
        "SELECT id, set_name, kind, ordinal, payload FROM eval_cases "
        "WHERE is_active = 1"
    )
    params: list[Any] = []
    if set_name != ALL_SETS:
        sql += " AND set_name = ?"
        params.append(set_name)
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY set_name, kind, ordinal, id"
    conn = _connect(db_path)
    try:
        cases: list[dict[str, Any]] = []
        for row in conn.execute(sql, params).fetchall():
            cases.append(
                {
                    "id": int(row["id"]),
                    "set_name": str(row["set_name"]),
                    "kind": str(row["kind"]),
                    "ordinal": int(row["ordinal"]) if row["ordinal"] is not None else 0,
                    "payload": json.loads(row["payload"]),
                }
            )
        return cases
    finally:
        conn.close()


def infer_kind(
    path: Path | str, records: Iterable[dict[str, Any]] | None = None
) -> str:
    """Infer the case lane for an incoming JSONL file.

    Order: file name stem tokens (``_zvs`` / ``_qa`` / ``_translation``), then
    payload fields of the first record.

    Args:
        path: JSONL file being imported (used for the name heuristic).
        records: Already-parsed records, to avoid re-reading the file.

    Returns:
        One of :data:`KINDS`.

    Raises:
        ValueError: When the lane cannot be determined.
    """
    tokens = set(Path(path).stem.replace("-", "_").split("_"))
    for kind in KINDS:
        if kind in tokens:
            return kind
    if records:
        first = next(iter(records), None) or {}
        if "text" in first:
            return "zvs"
        if "ref" in first:
            return "translation"
        if "question" in first or "answer" in first:
            return "qa"
    raise ValueError(f"cannot infer eval case kind for {path}")


def _upsert_set(
    conn: sqlite3.Connection, set_name: str, case_count: int
) -> None:
    """Insert or refresh the ``eval_sets`` bookkeeping row."""
    existing = conn.execute(
        "SELECT created_at FROM eval_sets WHERE set_name = ?", (set_name,)
    ).fetchone()
    created_at = existing["created_at"] if existing else _now()
    conn.execute(
        "INSERT INTO eval_sets (set_name, version, description, case_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(set_name) DO UPDATE SET case_count = excluded.case_count, "
        "updated_at = excluded.updated_at",
        (set_name, None, None, case_count, created_at, _now()),
    )


def import_jsonl_to_set(
    path: Path | str,
    set_name: str,
    kind: str | None = None,
    *,
    db_path: Path | str | None = None,
) -> int:
    """Import a JSONL file into an eval set (idempotent per set + lane).

    Existing rows for the same ``(set_name, kind)`` are replaced, so re-running
    an import — or the seed script — is safe.

    Args:
        path: JSONL file to read (one JSON object per line).
        set_name: Target set; created in ``eval_sets`` when missing.
        kind: Lane override; inferred from the file name / payload otherwise.
        db_path: Explicit DB path override.

    Returns:
        Number of cases imported.

    Raises:
        OSError: When the file cannot be read.
        ValueError: When the lane cannot be inferred or a record is invalid.
    """
    source = Path(path)
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    lane = kind or infer_kind(source, records)
    if lane not in KINDS:
        raise ValueError(f"unknown eval case kind {lane!r} (expected one of {KINDS})")

    path_out = ensure_schema(db_path)
    conn = _connect(path_out)
    try:
        conn.execute(
            "DELETE FROM eval_cases WHERE set_name = ? AND kind = ?", (set_name, lane)
        )
        created_at = _now()
        for ordinal, record in enumerate(records):
            conn.execute(
                "INSERT INTO eval_cases (set_name, kind, payload, source_table, ordinal, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (
                    set_name,
                    lane,
                    json.dumps(record, ensure_ascii=False),
                    source.name,
                    ordinal,
                    created_at,
                ),
            )
        total = conn.execute(
            "SELECT COUNT(*) FROM eval_cases WHERE set_name = ? AND is_active = 1",
            (set_name,),
        ).fetchone()[0]
        _upsert_set(conn, set_name, int(total))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(records)


def export_set_to_jsonl(
    set_name: str,
    path: Path | str,
    kind: str | None = None,
    *,
    db_path: Path | str | None = None,
) -> int:
    """Write an eval set back out as JSONL (one verbatim payload per line).

    Args:
        set_name: Set to export.
        path: Destination file; parent directories are created.
        kind: Optional lane filter (exports every lane when omitted).
        db_path: Explicit DB path override.

    Returns:
        Number of cases written (``0`` when the set is unknown/empty).

    Raises:
        OSError: When the destination cannot be written.
    """
    cases = fetch_cases(set_name, kind, db_path=db_path)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case["payload"], ensure_ascii=False))
            handle.write("\n")
    return len(cases)


def seed_sets(
    names: Iterable[str] | None = None, *, db_path: Path | str | None = None
) -> dict[str, int]:
    """Load the bundled fixture files into their eval sets (idempotent).

    Args:
        names: Restrict seeding to these set names (``None`` = all).
        db_path: Explicit DB path override.

    Returns:
        Mapping of set name to imported case count.

    Raises:
        ValueError: When a requested set name is not a known seed set.
    """
    known = {set_name for set_name, _, _ in SEED_SOURCES}
    if names is not None:
        unknown = sorted(set(names) - known)
        if unknown:
            raise ValueError(f"unknown eval set(s): {', '.join(unknown)}")
    wanted = set(names) if names is not None else None
    counts: dict[str, int] = {}
    for set_name, filename, kind in SEED_SOURCES:
        if wanted is not None and set_name not in wanted:
            continue
        counts[set_name] = counts.get(set_name, 0) + import_jsonl_to_set(
            SETS_DIR / filename, set_name, kind, db_path=db_path
        )
    return counts
