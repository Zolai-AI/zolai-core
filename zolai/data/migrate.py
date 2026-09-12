"""JSONL → Database migration scripts.

Migrates all 8 canonical JSONL files into the SQLAlchemy database.
Target: <60s for full migration on SSD.

Usage:
    python -m zolai.data.migrate --data-dir ../data --db sqlite:///zolai.db
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .database import DatabaseManager, get_manager
from .models import MODEL_REGISTRY

from ..config import config

# Map of table_name → (jsonl_relative_path_under_data_dir, json_columns)
迁移_MAP: dict[str, tuple[str, set[str]]] = {
    "dictionary": ("dictionary/processed/dict_zo_en_master_v1.jsonl", {"english"}),
    "bible_verses": ("bible/parallel_corpus_v1.jsonl", set()),
    "grammar_patterns": ("bible/grammar_patterns_v2.jsonl", {"examples"}),
    "phrases": ("bible/phrases_v1.jsonl", {"examples"}),
    "vocab": ("bible/vocab_index_full.jsonl", {"books", "examples"}),
    "translations": ("bible/translation_pairs_v1.jsonl", set()),
    "word_usage": ("bible/context/word_usage_profiles.jsonl", {"meaning_shifts", "co_occurring_words"}),
    "provenance": ("provenance.json", set()),
}

# Special handling: provenance.json is a JSON object with a "files" array
PROVENANCE_KEY = "provenance"

BATCH_SIZE = 5000
REPORT_EVERY = 10000


def _transform_record(table_name: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Transform a JSONL record to match ORM column names and types.

    Handles field renames, type casts, and extra-field stripping.
    """
    if table_name == "grammar_patterns":
        # JSONL 'id' (string) → ORM 'pattern_id'
        return {
            "pattern_id": rec.get("id", ""),
            "pattern": rec.get("pattern", ""),
            "description": rec.get("description"),
            "function": rec.get("function", ""),
            "examples": json.dumps(rec.get("examples", []), ensure_ascii=False),
            "frequency": int(rec.get("frequency", 0)),
        }

    if table_name == "bible_verses":
        # chapter/verse are strings in JSONL, ints in ORM
        ch = rec.get("chapter", 0)
        vs = rec.get("verse", 0)
        try:
            ch = int(ch)
        except (TypeError, ValueError):
            ch = 0
        try:
            vs = int(vs)
        except (TypeError, ValueError):
            vs = 0
        return {
            "ref": rec.get("ref", ""),
            "book": rec.get("book", ""),
            "chapter": ch,
            "verse": vs,
            "zo_tdb77": rec.get("zo_tdb77"),
            "zo_tedim2010": rec.get("zo_tedim2010"),
            "en_kJV": rec.get("en_kJV"),
        }

    if table_name == "phrases":
        # examples is a list of dicts in JSONL, store as JSON text
        return {
            "zo": rec.get("zo", ""),
            "english": rec.get("english", ""),
            "frequency": int(rec.get("frequency", 0)),
            "examples": json.dumps(rec.get("examples", []), ensure_ascii=False),
        }

    if table_name == "vocab":
        # books/examples are lists in JSONL, store as JSON text
        return {
            "headword": rec.get("headword", ""),
            "english": rec.get("english", ""),
            "frequency": int(rec.get("frequency", 0)),
            "books": json.dumps(rec.get("books", []), ensure_ascii=False),
            "examples": json.dumps(rec.get("examples", []), ensure_ascii=False),
        }

    # dictionary, translations, word_usage, provenance: pass through
    # (insert_many handles JSON cols for dict/english and word_usage)
    return rec


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts."""
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _read_provenance(path: Path) -> list[dict[str, Any]]:
    """Read provenance.json and extract individual file records."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    files = data.get("files", [])
    # Ensure every record has the required columns
    result = []
    for f in files:
        rec = {
            "filename": f.get("filename", ""),
            "size_bytes": f.get("size_bytes", 0),
            "sha256": f.get("sha256", ""),
            "row_count": f.get("row_count", 0),
            "source": f.get("source", ""),
            "generator_script": f.get("generator_script", ""),
        }
        result.append(rec)
    return result


def migrate_table(
    mgr: DatabaseManager,
    table_name: str,
    records: list[dict[str, Any]],
) -> int:
    """Migrate records into a single table with progress reporting."""
    total = len(records)
    inserted = 0
    t0 = time.time()
    for i in range(0, total, BATCH_SIZE):
        batch = [_transform_record(table_name, r) for r in records[i : i + BATCH_SIZE]]
        count = mgr.insert_many(table_name, batch)
        inserted += count
        if inserted % REPORT_EVERY < BATCH_SIZE:
            elapsed = time.time() - t0
            print(
                f"  {table_name}: {inserted}/{total} "
                f"({elapsed:.1f}s)",
                flush=True,
            )
    elapsed = time.time() - t0
    print(f"  {table_name}: DONE — {inserted} rows in {elapsed:.1f}s")
    return inserted


def migrate_jsonl_to_db(
    data_dir: str | Path,
    db_url: str | None = None,
) -> dict[str, int]:
    """Migrate all JSONL files into the database.

    Args:
        data_dir: Path to the workspace data/ directory.
        db_url: Database URL (default: sqlite:///zolai.db).

    Returns:
        Dict mapping table_name → row_count.
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        raise FileNotFoundError(f"Data directory not found: {data_path}")

    mgr = get_manager(db_url)
    mgr.init_db()
    results: dict[str, int] = {}

    t0 = time.time()
    for table_name, (rel_path, _json_cols) in 迁移_MAP.items():
        fpath = data_path / rel_path
        if not fpath.exists():
            print(f"  SKIP {table_name}: {fpath} not found")
            continue

        print(f"Migrating {table_name} from {rel_path}...")
        if table_name == PROVENANCE_KEY:
            records = _read_provenance(fpath)
        else:
            records = _read_jsonl(fpath)

        count = migrate_table(mgr, table_name, records)
        results[table_name] = count

    elapsed = time.time() - t0
    print(f"\nMigration complete in {elapsed:.1f}s")
    print("Table counts:")
    for tname, cnt in results.items():
        print(f"  {tname}: {cnt:,}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point for migration."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate Zolai JSONL files to database"
    )
    parser.add_argument(
        "--data-dir",
        default="../data",
        help="Path to data directory (default: ../data)",
    )
    parser.add_argument(
        "--db",
        default=f"sqlite:///{config.paths.data / 'zolai.db'}",
        help="Database URL (default: <data_dir>/zolai.db)",
    )
    args = parser.parse_args()
    migrate_jsonl_to_db(args.data_dir, args.db)


if __name__ == "__main__":
    main()
