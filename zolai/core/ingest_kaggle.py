"""Ingest missing Kaggle JSON/JSONL corpora into the canonical Zolai database.

Database-first principle: JSONL is the *import/export* format only. All runtime
reads come from ``data/zolai.db``. This module pulls cleaned Kaggle bundles that
are NOT yet in the DB and imports **only the gaps** (deduped by a natural key),
stamping consistent ``import_batch_id`` / ``source_file`` / ``version`` /
``imported_at`` metadata on every row.

Design goals
------------
* Inventory-first: report what exists vs what is missing (dry-run) before
  writing anything.
* Gap-only: never re-import records whose natural key already exists.
* Safe by default: ``--dry-run`` (the default) only prints the computed gap
  report. Pass ``--import`` to actually write rows.

Usage
-----
    python -m zolai.core.ingest_kaggle --dry-run          # computed gap report
    python -m zolai.core.ingest_kaggle --import           # import only gaps
    python -m zolai.core.ingest_kaggle --import --source simbu_jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Table, create_engine, text
from sqlalchemy import inspect as sa_inspect

from ..config import config

logger = logging.getLogger(__name__)

# Canonical DB (env-overridable via ZOLAI_DB_PATH / ZOLAI_DATA_ROOT).
DB_PATH = config.paths.zolai_db
# External Kaggle bundle root (env-overridable via ZOLAI_KAGGLE_ROOT).
KAGGLE_ROOT = Path(
    __import__("os").environ.get(
        "ZOLAI_KAGGLE_ROOT", "/home/peter/Downloads/Kaggle"
    )
)
DATA_ROOT = DB_PATH.parent

# Metadata columns every imported row carries.
_VERSION_COLS = ("import_batch_id", "source_file", "version", "imported_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _batch_id() -> str:
    return str(uuid.uuid4())


def _digest(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()[:16]


Engine = type(create_engine("sqlite:///:memory:"))


def _ensure_extra_tables(engine) -> None:
    """Create tables that don't exist yet (schema evolution — idempotent).

    Introduced here because these tables have no other creator yet:
    * ``simbu`` — simbu (song/poetry) text corpus rows
    * ``grammar_instructions`` — Kaggle AI instruction→IO pairs
    """
    inspector = sa_inspect(engine)
    existing = set(inspector.get_table_names())

    with engine.begin() as conn:
        if "simbu" not in existing:
            conn.execute(
                text(
                    """
                    CREATE TABLE simbu (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text TEXT NOT NULL,
                        source TEXT,
                        type TEXT,
                        import_batch_id TEXT,
                        source_file TEXT,
                        version INTEGER DEFAULT 1,
                        imported_at TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_simbu_batch ON simbu(import_batch_id)"
                )
            )
            logger.info("  created table: simbu")

        if "grammar_instructions" not in existing:
            conn.execute(
                text(
                    """
                    CREATE TABLE grammar_instructions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        instruction TEXT NOT NULL,
                        input TEXT,
                        output TEXT,
                        import_batch_id TEXT,
                        source_file TEXT,
                        version INTEGER DEFAULT 1,
                        imported_at TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_grammar_instr_batch "
                    "ON grammar_instructions(import_batch_id)"
                )
            )
            logger.info("  created table: grammar_instructions")

        # Ensure versioning on the build tables Kaggle feeds (articles).
        _ensure_version_cols(conn, "articles")


def _ensure_version_cols(conn, table_name: str) -> None:
    """Add unified versioning columns to a build table if missing."""
    try:
        cols = {c["name"] for c in sa_inspect(conn).get_columns(table_name)}
    except Exception:
        return
    defaults = {
        "import_batch_id": '"import_batch_id" TEXT',
        "source_file": '"source_file" TEXT',
        "version": '"version" INTEGER DEFAULT 1',
        "imported_at": '"imported_at" TEXT',
    }
    for col, ddl in defaults.items():
        if col not in cols:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))
            logger.info("  added column: %s.%s", table_name, col)


def _fetch_existing_keys(
    engine, table_name: str, key_cols: tuple[str, ...]
) -> set[tuple[Any, ...]]:
    """Return set of existing natural-key tuples for the table."""
    cols = ", ".join(key_cols)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT DISTINCT {cols} FROM {table_name} WHERE 1=1")
        ).fetchall()
    return {(r[0],) if len(key_cols) == 1 else tuple(r) for r in rows}


def _insert_rows(engine, table: Table, records: list[dict[str, Any]]) -> int:
    """Insert records (stripped of non-table columns) in one txn."""
    if not records:
        return 0
    tbl_cols = {c.name for c in table.columns}
    clean = [{k: v for k, v in r.items() if k in tbl_cols} for r in records]
    for batch_start in range(0, len(clean), 1000):
        with engine.begin() as conn:
            conn.execute(table.insert(), clean[batch_start : batch_start + 1000])
    return len(clean)


# ─────────────────────────────────────────────────────────────────────────────
# Gap-import helpers per source
# ─────────────────────────────────────────────────────────────────────────────

def _import_dict_freq(
    engine,
    path: Path,
    table_name: str,
    *,
    source_label: str,
    batch_id: str,
    version: int,
    dry_run: bool,
) -> dict[str, int]:
    """Import a ``{word: frequency}`` JSON dict into ``vocab`` (gap-only)."""
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, int] = json.load(fh)

    existing = _fetch_existing_keys(engine, table_name, ("headword",))
    missing = [(w, v) for w, v in data.items() if w not in existing]

    if dry_run:
        return {"source": source_label, "total": len(data), "gap": len(missing)}

    table = Table(table_name, _meta(engine), autoload_with=engine)
    records = [
        {
            "headword": w,
            "english": "",
            "frequency": int(v),
            "books": "[]",
            "examples": "[]",
            "import_batch_id": batch_id,
            "source_file": source_label,
            "version": version,
            "imported_at": _now(),
        }
        for w, v in missing
    ]
    inserted = _insert_rows(engine, table, records)
    return {"source": source_label, "total": len(data), "gap": len(missing),
            "inserted": inserted}


def _import_jsonl_table(
    engine,
    path: Path,
    table_name: str,
    *,
    key_cols: tuple[str, ...],
    field_map: dict[str, str] | None,
    source_label: str,
    batch_id: str,
    version: int,
    dry_run: bool,
) -> dict[str, int]:
    """Import a JSONL file into ``table_name``, deduping on ``key_cols``.

    ``field_map`` maps source record key -> DB column (unmapped source keys are
    dropped). ``source_label`` becomes the ``source_file`` value.
    """
    existing = _fetch_existing_keys(engine, table_name, key_cols)
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            cols: dict[str, Any] = {}
            for src_key, db_col in (field_map or {}).items():
                if src_key in rec:
                    cols[db_col] = rec[src_key]
            key = tuple(cols.get(k, "") for k in key_cols)
            if key in existing:
                continue
            existing.add(key)
            cols.update(
                {
                    "import_batch_id": batch_id,
                    "source_file": source_label,
                    "version": version,
                    "imported_at": _now(),
                }
            )
            records.append(cols)

    if dry_run:
        return {"source": source_label, "total": 0, "gap": len(records)}

    table = Table(table_name, _meta(engine), autoload_with=engine)
    inserted = _insert_rows(engine, table, records)
    return {"source": source_label, "total": 0, "gap": len(records),
            "inserted": inserted}


_meta_cache: dict[str, Any] = {}


def _meta(engine) -> Any:
    from sqlalchemy import MetaData
    key = id(engine)
    if key not in _meta_cache:
        md = MetaData()
        md.reflect(bind=engine)
        _meta_cache[key] = md
    return _meta_cache[key]


def _import_wordlist(
    engine,
    path: Path,
    table_name: str,
    *,
    source_label: str,
    batch_id: str,
    version: int,
    dry_run: bool,
) -> dict[str, int]:
    """Import a plain-text word list (one word per line) into ``vocab``."""
    existing = _fetch_existing_keys(engine, table_name, ("headword",))
    words: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            w = line.strip().lower()
            if w and w not in existing and w not in words:
                words.append(w)
    if dry_run:
        return {"source": source_label, "total": 0, "gap": len(words)}
    table = Table(table_name, _meta(engine), autoload_with=engine)
    records = [
        {
            "headword": w,
            "english": "",
            "frequency": 0,
            "books": "[]",
            "examples": "[]",
            "import_batch_id": batch_id,
            "source_file": source_label,
            "version": version,
            "imported_at": _now(),
        }
        for w in words
    ]
    inserted = _insert_rows(engine, table, records)
    return {"source": source_label, "total": 0, "gap": len(words),
            "inserted": inserted}


# ─────────────────────────────────────────────────────────────────────────────
# Source registry
# ─────────────────────────────────────────────────────────────────────────────

def _sources() -> list[dict[str, Any]]:
    """Declared, ordered Kaggle→canonical source mappings."""
    return [
        {
            "name": "simbu_jsonl",
            "rel": "data/processed/zolai_simbu_dataset_clean.jsonl",
            "kind": "jsonl",
            "table": "simbu",
            "key_cols": ("text",),
            "field_map": {"text": "text", "source": "source", "type": "type"},
        },
        {
            "name": "grammar_instructions",
            "rel": "data/processed/zolai_grammar_instructions.jsonl",
            "kind": "jsonl",
            "table": "grammar_instructions",
            "key_cols": ("instruction",),
            "field_map": {"instruction": "instruction", "input": "input",
                          "output": "output"},
        },
        {
            "name": "articles_tongsan",
            "rel": "data/processed/tongsan_articles_standardized.jsonl",
            "kind": "jsonl",
            "table": "articles",
            "key_cols": ("title",),
            "field_map": {"title": "title", "content": "content",
                          "excerpt": "excerpt", "categories": "categories",
                          "date": "date", "link": "link"},
        },
        {
            "name": "simbu_vocab",
            "rel": "data/zolai_simbu_vocab.json",
            "kind": "dict_freq",
            "table": "vocab",
        },
        {
            "name": "word_list",
            "rel": "data/zolai_word_list.txt",
            "kind": "wordlist",
            "table": "vocab",
        },
        {
            "name": "unified_vocabulary",
            "rel": "data/processed/zolai_unified_vocabulary_pure.json",
            "kind": "dict_freq",
            "table": "vocab",
            # NOTE: 119k words — deferred unless explicitly enabled.
            "deferred": True,
        },
    ]


def run(dry_run: bool, only: str | None = None) -> list[dict[str, int]]:
    """Ingest (or compute) gaps for all declared sources.

    Returns per-source gap/inserted summaries.
    """
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    # PRAGMA tuning for concurrent access parity with the rest of the stack.
    with engine.connect() as conn:
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))

    _ensure_extra_tables(engine)

    mode = "DRY-RUN" if dry_run else "IMPORT"
    print(f"[ingest_kaggle] mode={mode} db={DB_PATH}")
    print(f"[ingest_kaggle] kaggle_root={KAGGLE_ROOT}")

    batch_id = _batch_id()
    version = 1
    results: list[dict[str, int]] = []

    for spec in _sources():
        if only and spec["name"] != only:
            continue
        if spec.get("deferred") and dry_run is False:
            print(f"[ingest_kaggle] skipping deferred source: {spec['name']} "
                  f"(use --import --source {spec['name']} to force)")
            continue

        path = KAGGLE_ROOT / spec["rel"]
        if not path.exists():
            print(f"[ingest_kaggle] missing source: {spec['rel']}")
            continue

        rel_label = f"kaggle/{spec['rel']}"
        if spec["kind"] == "dict_freq":
            res = _import_dict_freq(
                engine, path, spec["table"], source_label=rel_label,
                batch_id=batch_id, version=version, dry_run=dry_run,
            )
        elif spec["kind"] == "jsonl":
            res = _import_jsonl_table(
                engine, path, spec["table"], key_cols=spec["key_cols"],
                field_map=spec["field_map"], source_label=rel_label,
                batch_id=batch_id, version=version, dry_run=dry_run,
            )
        elif spec["kind"] == "wordlist":
            res = _import_wordlist(
                engine, path, spec["table"], source_label=rel_label,
                batch_id=batch_id, version=version, dry_run=dry_run,
            )
        else:  # pragma: no cover
            continue
        results.append(res)
        print(f"[ingest_kaggle] {spec['name']:<22} "
              f"total={res.get('total', 0):>6} gap={res.get('gap', 0):>7} "
              f"inserted={res.get('inserted', 0):>7}")

    return results


def _report_path() -> Path:
    return DATA_ROOT / "kaggle_gap_report.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest missing Kaggle corpora")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only compute + report gaps (no writes)")
    ap.add_argument("--import", dest="do_import", action="store_true",
                    help="Import only the gaps")
    ap.add_argument("--source", default=None, help="Run a single source")
    args = ap.parse_args(argv)

    dry_run = not args.do_import
    results = run(dry_run=dry_run, only=args.source)

    # Always write the computed gap report (cheap, informative).
    report = {
        "generated_at": _now(),
        "mode": "dry-run" if dry_run else "import",
        "sources": results,
    }
    out = _report_path()
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"[ingest_kaggle] gap report -> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
