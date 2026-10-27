"""Evaluation store: ``eval_sets`` + ``eval_cases`` in the canonical database.

Both tables live in the single source of truth (``config.paths.db``, which
honours ``ZOLAI_DB_PATH``) and hold every evaluation and gold set. The bundled
``.jsonl`` files under :data:`SETS_DIR` and the per-task gold files are
import/export interchange, not the source of truth:

- :func:`import_jsonl_to_set` / :func:`export_set_to_jsonl` — JSONL ⇄ DB.
- :func:`seed_sets` — loads the bundled fixtures (used by
  ``scripts/eval/seed_eval_sets.py`` and ``scripts/ci_prepare_db.py``).
- :func:`migrate_legacy_store` — one-time copy out of the retired side file
  ``data/zolai_eval.db``, which this module no longer reads or writes.

The legacy ZolaiBench API (:func:`init_eval_db`, :func:`create_eval_set`,
:func:`add_eval_item`, :func:`get_eval_items`, …) is a thin adapter over the
same two tables, so the POS trainer and the bench runner read the same rows the
lane metrics do.

The module is stdlib-only (``sqlite3`` + :mod:`zolai.config`) so the offline
evaluation package keeps its dependency-free guarantee.

Field mapping (payloads are stored verbatim as JSON):

===========  =========================================================
kind         payload fields
===========  =========================================================
``zvs``      ``text``
``qa``       ``question`` / ``hyp`` / ``answer``
``translation``  ``hyp`` / ``ref`` (``source`` kept when present)
``pos`` / ``morph`` / ``grammar`` / ``tokenization``
             the gold annotation object (``text`` + ``tokens`` /
             ``errors`` / ``expected_segmentation``)
===========  =========================================================
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import config

#: Lanes scored by :func:`zolai.eval.evaluate`.
METRIC_LANES: tuple[str, ...] = ("zvs", "qa", "translation")

#: Gold annotation lanes consumed by ZolaiBench v0.1 and the POS trainer.
GOLD_LANES: tuple[str, ...] = ("tokenization", "pos", "morph", "grammar")

#: Every evaluation case lane (mirrors the ``eval_cases.kind`` CHECK).
KINDS: tuple[str, ...] = METRIC_LANES + GOLD_LANES

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

#: Pre-defined gold sets: ``task -> (eval set name, description)``.
GOLD_SETS: dict[str, tuple[str, str]] = {
    "tokenization": ("tokenization_gold_v0", "Tokenization gold set v0"),
    "pos": ("pos_gold_v0", "POS tagging gold set v0"),
    "morph": ("morph_gold_v0", "Morphology gold set v0"),
    "grammar": ("grammar_gold_v0", "Grammar error detection gold set v0"),
    "pos_gold_v0": ("pos_gold_v0", "POS tagging gold set v0"),
    "morph_gold_v0": ("morph_gold_v0", "Morphology gold set v0"),
    "grammar_gold_v0": ("grammar_gold_v0", "Grammar error detection gold set v0"),
    "tokenization_gold_v0": ("tokenization_gold_v0", "Tokenization gold set v0"),
}

#: ``eval set name -> gold lane``, so a legacy call that carries no lane still
#: lands on a valid one.
_GOLD_LANES_BY_SET: dict[str, str] = {
    name: task for task, (name, _desc) in sorted(GOLD_SETS.items()) if task in GOLD_LANES
}

_CASE_COLUMNS = (
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "set_name TEXT NOT NULL REFERENCES eval_sets(set_name),"
    "kind TEXT NOT NULL,"
    "payload TEXT NOT NULL,"
    "source_table TEXT,"
    "ordinal INTEGER,"
    "is_active INTEGER DEFAULT 1,"
    "created_at TEXT"
)

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS eval_sets(
  set_name TEXT PRIMARY KEY,
  version TEXT,
  description TEXT,
  case_count INTEGER,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS eval_cases(
  {_CASE_COLUMNS}
  CHECK(kind IN ({", ".join(repr(k) for k in KINDS)}))
);
CREATE INDEX IF NOT EXISTS idx_eval_cases_lookup
  ON eval_cases(set_name, is_active, ordinal);
"""

_NO_SUCH_TABLE = "no such table"


@dataclass
class EvalSet:
    """Evaluation set metadata (legacy ZolaiBench view of ``eval_sets``)."""

    name: str
    task: str
    description: str
    version: str
    item_count: int
    created_at: str


@dataclass
class EvalItem:
    """Single evaluation item (legacy view of one ``eval_cases`` row)."""

    id: int
    eval_set: str
    input_text: str
    gold_annotation: str  # JSON string
    metadata: str  # JSON string (source, difficulty, etc.)


def _db_path() -> Path:
    """Resolve the database the eval store reads and writes.

    :attr:`zolai.config.paths.db` is the only answer to "which database?" —
    this used to re-read ``ZOLAI_DB_PATH`` itself, so a test that moved
    ``config.paths.db`` still wrote the corpus, and the CLI's run history
    landed in a different file than the cases it scored.
    """
    return Path(config.paths.db)


def legacy_store_path() -> Path:
    """The retired side store ``data/zolai_eval.db``.

    Kept only so :func:`migrate_legacy_store` can find it; nothing in this
    module reads or writes it during normal operation.
    """
    override = os.environ.get("ZOLAI_EVAL_DB_PATH")
    if override:
        return Path(override).expanduser()
    return Path(config.paths.db).parent / "zolai_eval.db"


def _now() -> str:
    """UTC timestamp (second precision) for the bookkeeping columns."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_sql(conn: sqlite3.Connection, table: str) -> str | None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _widen_lane_check(conn: sqlite3.Connection) -> None:
    """Rebuild ``eval_cases`` when its CHECK predates the gold lanes.

    The first stores constrained ``kind`` to the three metric lanes; gold
    annotation lanes would be rejected by that constraint. The rebuild keeps
    every column, row and id — only the CHECK widens — and runs in one
    transaction so a failure leaves the original table in place.
    """
    existing = _table_sql(conn, "eval_cases")
    if existing is None or all(f"'{kind}'" in existing for kind in KINDS):
        return
    counts_before = int(conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0])
    # Foreign keys are per-connection and off by default here; renaming a table
    # that nothing else references must not be rewritten by the rename.
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE eval_cases RENAME TO eval_cases_prefill")
        conn.execute(
            f"CREATE TABLE eval_cases({_CASE_COLUMNS} "
            f"CHECK(kind IN ({', '.join(repr(kind) for kind in KINDS)})))"
        )
        conn.execute(
            "INSERT INTO eval_cases(id, set_name, kind, payload, source_table, ordinal, is_active, created_at) "
            "SELECT id, set_name, kind, payload, source_table, ordinal, is_active, created_at "
            "FROM eval_cases_prefill"
        )
        counts_after = int(conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0])
        if counts_after != counts_before:
            raise RuntimeError(f"eval_cases rebuild kept {counts_after}/{counts_before} rows")
        conn.execute("DROP TABLE eval_cases_prefill")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_cases_lookup "
            "ON eval_cases(set_name, is_active, ordinal)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_schema(db_path: Path | str | None = None) -> Path:
    """Create ``eval_sets`` / ``eval_cases`` if missing (idempotent).

    Additive: an existing table is only touched when its ``kind`` CHECK predates
    :data:`KINDS`. The DDL is skipped entirely when the schema already matches,
    so pointing a read path at the canonical store does not write to it.

    Args:
        db_path: Explicit DB path; defaults to :func:`_db_path`.

    Returns:
        The resolved database path.
    """
    path = Path(db_path) if db_path is not None else _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        if _table_sql(conn, "eval_sets") is None or _table_sql(conn, "eval_cases") is None:
            conn.executescript(_SCHEMA_SQL)
        else:
            _widen_lane_check(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return path


def _connect(db_path: Path | str | None = None, *, create: bool = False) -> sqlite3.Connection:
    """Open a connection, creating the schema only when the caller writes.

    Read paths pass ``create=False``: a missing table is a legitimate state
    ("no sets seeded yet"), and issuing DDL to answer a SELECT would make every
    read a write against the canonical store.
    """
    path = ensure_schema(db_path) if create else (Path(db_path) if db_path is not None else _db_path())
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _read(sql: str, params: Iterable[Any] = (), *, db_path: Path | str | None = None) -> list[sqlite3.Row]:
    """Run a SELECT, treating a missing eval table as zero rows."""
    conn = _connect(db_path)
    try:
        return conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.OperationalError as exc:
        if _NO_SUCH_TABLE in str(exc):
            return []
        raise
    finally:
        conn.close()


def list_sets(*, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return every eval set row (``set_name``, ``version``, ``case_count``…)."""
    rows = _read(
        "SELECT set_name, version, description, case_count, created_at, updated_at "
        "FROM eval_sets ORDER BY set_name",
        db_path=db_path,
    )
    return [dict(row) for row in rows]


def list_kinds(
    set_name: str | None = None, *, db_path: Path | str | None = None
) -> list[str]:
    """Return the distinct active case kinds (optionally within one set)."""
    sql = "SELECT DISTINCT kind FROM eval_cases WHERE is_active = 1"
    params: list[Any] = []
    if set_name is not None:
        sql += " AND set_name = ?"
        params.append(set_name)
    sql += " ORDER BY kind"
    return [str(row["kind"]) for row in _read(sql, params, db_path=db_path)]


def fetch_cases(
    set_name: str,
    kind: str | None = None,
    *,
    limit: int | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Fetch active cases, ordered by ``set_name``, ``kind``, ``ordinal``.

    Args:
        set_name: Set to read, or :data:`ALL_SETS` (``"*"``) for every set.
        kind: Optional lane filter.
        limit: Optional row cap (applied after ordering).
        db_path: Explicit DB path override.

    Returns:
        One dict per case: ``id``, ``set_name``, ``kind``, ``ordinal`` and
        ``payload`` (the parsed JSON object, stored verbatim). Empty when the
        set is unknown or nothing has been seeded — callers decide whether
        that is an error.
    """
    sql = "SELECT id, set_name, kind, ordinal, payload FROM eval_cases WHERE is_active = 1"
    params: list[Any] = []
    if set_name != ALL_SETS:
        sql += " AND set_name = ?"
        params.append(set_name)
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY set_name, kind, ordinal, id"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    cases: list[dict[str, Any]] = []
    for row in _read(sql, params, db_path=db_path):
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


def infer_kind(
    path: Path | str, records: Iterable[dict[str, Any]] | None = None
) -> str:
    """Infer the case lane for an incoming JSONL file.

    Order: file name stem tokens (any of :data:`KINDS`), then payload fields of
    the first record.

    Args:
        path: JSONL file being imported (used for the name heuristic).
        records: Already-parsed records, to avoid re-reading the file.

    Returns:
        One of :data:`KINDS`.

    Raises:
        ValueError: When the lane cannot be determined.
    """
    tokens = set(Path(path).stem.replace("-", "_").lower().split("_"))
    for kind in KINDS:
        if kind in tokens:
            return kind
    if records:
        first = next(iter(records), None) or {}
        lane = _lane_for_payload(first) or ("zvs" if "text" in first else None)
        if lane:
            return lane
        if "ref" in first:
            return "translation"
        if "question" in first or "answer" in first:
            return "qa"
    raise ValueError(f"cannot infer eval case kind for {path}")


def _lane_for_payload(record: dict[str, Any]) -> str | None:
    """Best-effort gold-annotation lane from the shape of one record.

    The legacy store's ``task`` column is unreliable (several sets record the
    set name in it), so the payload itself decides: annotated tokens are POS,
    bare tokens are tokenization, ``errors`` is grammar, a segmentation is
    morphology.
    """
    if not isinstance(record, dict):
        return None
    if "errors" in record:
        return "grammar"
    if "expected_segmentation" in record:
        return "morph"
    tokens = record.get("tokens")
    if isinstance(tokens, list) and tokens and isinstance(tokens[0], dict):
        return "pos" if "pos" in tokens[0] else "tokenization"
    return None


def _resolve_lane(set_name: str, kind: str | None) -> str:
    """Validate a lane, falling back to the lane registered for ``set_name``."""
    lane = kind or _GOLD_LANES_BY_SET.get(set_name) or ""
    if lane not in KINDS:
        raise ValueError(f"unknown eval case kind {lane!r} (expected one of {KINDS})")
    return lane


def _upsert_set(
    conn: sqlite3.Connection,
    set_name: str,
    case_count: int,
    *,
    version: str | None = None,
    description: str | None = None,
) -> None:
    """Insert or refresh the ``eval_sets`` bookkeeping row."""
    existing = conn.execute("SELECT * FROM eval_sets WHERE set_name = ?", (set_name,)).fetchone()
    created_at = existing["created_at"] if existing and existing["created_at"] else _now()
    conn.execute(
        "INSERT INTO eval_sets (set_name, version, description, case_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(set_name) DO UPDATE SET case_count = excluded.case_count, "
        "version = COALESCE(excluded.version, eval_sets.version), "
        "description = COALESCE(excluded.description, eval_sets.description), "
        "updated_at = excluded.updated_at",
        (
            set_name,
            version or (existing["version"] if existing else None),
            description or (existing["description"] if existing else None),
            case_count,
            created_at,
            _now(),
        ),
    )


def _replace_lane(
    conn: sqlite3.Connection,
    set_name: str,
    lane: str,
    records: list[dict[str, Any]],
    source_table: str,
) -> None:
    """Rewrite one lane of one set from ``records`` (replace, not append)."""
    conn.execute("DELETE FROM eval_cases WHERE set_name = ? AND kind = ?", (set_name, lane))
    created_at = _now()
    for ordinal, record in enumerate(records):
        conn.execute(
            "INSERT INTO eval_cases "
            "(set_name, kind, payload, source_table, ordinal, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (set_name, lane, json.dumps(record, ensure_ascii=False), source_table, ordinal, created_at),
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
    records = [
        json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    lane = _resolve_lane(set_name, kind or infer_kind(source, records))

    conn = _connect(db_path, create=True)
    try:
        _replace_lane(conn, set_name, lane, records, source.name)
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


# ---------------------------------------------------------------------------
# Legacy ZolaiBench v0.1 API — the same two tables, dataclass-shaped.
# ---------------------------------------------------------------------------


def init_eval_db() -> None:
    """Ensure the evaluation tables exist in the canonical store.

    Historic name, kept for ZolaiBench callers. It no longer creates a second
    database: ``eval_sets`` / ``eval_cases`` *are* the evaluation store.
    """
    ensure_schema()


def _lane_counts(set_name: str, *, db_path: Path | str | None = None) -> list[str]:
    return list_kinds(set_name, db_path=db_path)


def create_eval_set(
    name: str,
    task: str = "",
    description: str = "",
    version: str = "v0",
    *,
    db_path: Path | str | None = None,
) -> EvalSet:
    """Register (or refresh) an eval set; ``task`` names its gold lane."""
    lane = _resolve_lane(name, task or None)
    conn = _connect(db_path, create=True)
    try:
        _upsert_set(conn, name, 0, version=version, description=description or None)
        conn.commit()
    finally:
        conn.close()
    return EvalSet(
        name=name,
        task=lane,
        description=description,
        version=version,
        item_count=0,
        created_at=_now(),
    )


def add_eval_item(
    eval_set: str,
    input_text: str,
    gold_annotation: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    *,
    kind: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Append one case to ``eval_set`` and return its row id.

    ``gold_annotation`` is stored verbatim as the payload, with ``metadata``
    folded in under ``"metadata"`` so nothing is dropped. Appending does not
    replace the lane the way :func:`import_jsonl_to_set` does, so building a set
    item by item stays possible.
    """
    lane = _resolve_lane(eval_set, kind)
    payload = dict(gold_annotation) if isinstance(gold_annotation, dict) else {"value": gold_annotation}
    if metadata:
        payload["metadata"] = metadata
    if input_text and "text" not in payload:
        payload["text"] = input_text
    conn = _connect(db_path, create=True)
    try:
        ordinal_row = conn.execute(
            "SELECT COALESCE(MAX(ordinal) + 1, 0) FROM eval_cases WHERE set_name = ? AND kind = ?",
            (eval_set, lane),
        ).fetchone()
        cur = conn.execute(
            "INSERT INTO eval_cases "
            "(set_name, kind, payload, source_table, ordinal, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (eval_set, lane, json.dumps(payload, ensure_ascii=False), "api", int(ordinal_row[0]), _now()),
        )
        total = conn.execute(
            "SELECT COUNT(*) FROM eval_cases WHERE set_name = ? AND is_active = 1",
            (eval_set,),
        ).fetchone()[0]
        _upsert_set(conn, eval_set, int(total))
        conn.commit()
        return int(cur.lastrowid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_eval_set(name: str, *, db_path: Path | str | None = None) -> EvalSet | None:
    """Metadata for one eval set, or ``None`` when it does not exist."""
    rows = _read("SELECT * FROM eval_sets WHERE set_name = ?", (name,), db_path=db_path)
    if not rows:
        return None
    return _to_eval_set(dict(rows[0]), db_path=db_path)


def list_eval_sets(*, db_path: Path | str | None = None) -> list[EvalSet]:
    """Every eval set, newest first."""
    rows = _read("SELECT * FROM eval_sets ORDER BY created_at DESC", db_path=db_path)
    return [_to_eval_set(dict(row), db_path=db_path) for row in rows]


def _to_eval_set(row: dict[str, Any], *, db_path: Path | str | None = None) -> EvalSet:
    """Build the legacy view of an ``eval_sets`` row.

    ``task`` is derived from the lanes actually present rather than from a
    column that used to drift from the data.
    """
    name = str(row["set_name"])
    lanes = _lane_counts(name, db_path=db_path)
    return EvalSet(
        name=name,
        task=",".join(lanes),
        description=row["description"] or "",
        version=row["version"] or "v0",
        item_count=int(row["case_count"] or 0),
        created_at=row["created_at"] or "",
    )


def get_eval_items(
    eval_set: str, limit: int | None = None, *, db_path: Path | str | None = None
) -> list[EvalItem]:
    """Cases of one set as legacy items (``gold_annotation`` is a JSON string)."""
    items: list[EvalItem] = []
    for case in fetch_cases(eval_set, limit=limit, db_path=db_path):
        payload = case["payload"]
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        annotation = {k: v for k, v in payload.items() if k != "metadata"} if isinstance(payload, dict) else payload
        items.append(
            EvalItem(
                id=case["id"],
                eval_set=case["set_name"],
                input_text=str(payload.get("text", "")) if isinstance(payload, dict) else "",
                gold_annotation=json.dumps(annotation, ensure_ascii=False),
                metadata=json.dumps(metadata or {}, ensure_ascii=False),
            )
        )
    return items


def load_gold_from_jsonl(
    filepath: Path,
    eval_set: str,
    task: str,
    description: str = "",
    *,
    db_path: Path | str | None = None,
) -> int:
    """Import a gold JSONL file as one eval set lane.

    Re-running replaces that lane instead of appending a second copy of every
    annotation, which is what made the old side store grow without bound.
    """
    path = Path(filepath)
    if not path.exists():
        return 0
    if description:
        create_eval_set(eval_set, task, description, db_path=db_path)
    return import_jsonl_to_set(path, eval_set, task or None, db_path=db_path)


def export_eval_set_to_jsonl(
    eval_set: str, filepath: Path, *, db_path: Path | str | None = None
) -> int:
    """Export an eval set to JSONL for backup or portability."""
    return export_set_to_jsonl(eval_set, filepath, db_path=db_path)


def migrate_legacy_store(
    source: Path | str | None = None,
    *,
    db_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Copy the retired ``zolai_eval.db`` gold items into ``eval_cases``.

    The side store held ``eval_items(input_text, gold_annotation, metadata)``
    per task; each row becomes one case whose payload is the annotation object,
    so a migrated set re-imports identically through :func:`get_eval_items`.

    Args:
        source: Legacy DB path; defaults to :func:`legacy_store_path`.
        db_path: Target (canonical) DB path.
        dry_run: Count rows without writing.

    Returns:
        Mapping of ``"set_name/kind"`` to the number of cases in that lane.

    Raises:
        FileNotFoundError: When the legacy store is not there.
        sqlite3.OperationalError: When it lacks the expected tables.
    """
    legacy = Path(source) if source is not None else legacy_store_path()
    if not legacy.exists():
        raise FileNotFoundError(f"legacy eval store not found: {legacy}")

    src = sqlite3.connect(str(legacy))
    src.row_factory = sqlite3.Row
    try:
        sets = src.execute("SELECT name, task FROM eval_sets").fetchall()
        by_lane: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in sets:
            name = str(row["name"])
            for item in src.execute(
                "SELECT input_text, gold_annotation, metadata FROM eval_items "
                "WHERE eval_set = ? ORDER BY id",
                (name,),
            ).fetchall():
                annotation = json.loads(item["gold_annotation"])
                payload = dict(annotation) if isinstance(annotation, dict) else {"value": annotation}
                if item["metadata"]:
                    payload["metadata"] = json.loads(item["metadata"])
                if item["input_text"] and "text" not in payload:
                    payload["text"] = item["input_text"]
                lane = _GOLD_LANES_BY_SET.get(name)
                if lane is None:
                    task = str(row["task"] or "")
                    lane = task if task in KINDS else _lane_for_payload(payload)
                if lane not in KINDS:
                    raise ValueError(f"cannot resolve an eval lane for legacy set {name!r}")
                by_lane.setdefault((name, str(lane)), []).append(payload)

        counts = {f"{name}/{lane}": len(records) for (name, lane), records in sorted(by_lane.items())}
        if dry_run:
            return counts

        conn = _connect(db_path, create=True)
        try:
            for (name, lane), records in sorted(by_lane.items()):
                _replace_lane(conn, name, lane, records, legacy.name)
                total = conn.execute(
                    "SELECT COUNT(*) FROM eval_cases WHERE set_name = ? AND is_active = 1",
                    (name,),
                ).fetchone()[0]
                _upsert_set(conn, name, int(total))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return counts
    finally:
        src.close()
