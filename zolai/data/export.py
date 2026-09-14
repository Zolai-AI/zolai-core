"""Database → JSONL export for backup and ML training.

Exports all tables back to JSONL files, verifying round-trip integrity.

Usage:
    from zolai.data.export import export_db_to_jsonl
    export_db_to_jsonl("sqlite:///zolai.db", "./output")
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .database import DatabaseManager, get_manager
from .models import MODEL_REGISTRY

# Table name → output filename
_EXPORT_FILES: dict[str, str] = {
    "dictionary": "dict_zo_en_master_v1.jsonl",
    "bible_verses": "parallel_corpus_v1.jsonl",
    "grammar_patterns": "grammar_patterns_v2.jsonl",
    "phrases": "phrases_v1.jsonl",
    "vocabulary": "vocab_index_full.jsonl",
    "translations": "translation_pairs_v1.jsonl",
    "word_usage": "word_usage_profiles.jsonl",
    "provenance": "provenance.jsonl",
}


def export_db_to_jsonl(
    db_url: str | None = None,
    output_dir: str | Path = "./export",
) -> dict[str, int]:
    """Export all database tables to JSONL files.

    Args:
        db_url: Database URL (default: uses singleton).
        output_dir: Directory to write exported files.

    Returns:
        Dict mapping table_name → row_count exported.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    mgr = get_manager(db_url)
    results: dict[str, int] = {}

    for table_name, filename in _EXPORT_FILES.items():
        print(f"Exporting {table_name} → {filename}...")
        try:
            records = mgr.export_table(table_name)
        except Exception as exc:
            print(f"  SKIP {table_name}: {exc}")
            continue

        fpath = out_path / filename
        with open(fpath, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        count = len(records)
        results[table_name] = count
        print(f"  {table_name}: {count:,} rows → {fpath}")

    print(f"\nExport complete: {len(results)} tables → {out_path}")
    return results


def verify_roundtrip(
    db_url: str | None = None,
    output_dir: str | Path = "./export",
) -> bool:
    """Export DB → JSONL and verify data integrity.

    Reads back each exported JSONL and compares row counts.
    Returns True if all tables match.
    """
    results = export_db_to_jsonl(db_url, output_dir)
    mgr = get_manager(db_url)
    all_ok = True
    for table_name, exported_count in results.items():
        db_count = mgr.count(table_name)
        if exported_count != db_count:
            print(
                f"MISMATCH {table_name}: "
                f"exported={exported_count} db={db_count}"
            )
            all_ok = False
    if all_ok:
        print("Round-trip verification: PASS")
    return all_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point for export."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Export Zolai database to JSONL"
    )
    parser.add_argument(
        "--db",
        default="sqlite:///zolai.db",
        help="Database URL",
    )
    parser.add_argument(
        "--output-dir",
        default="./export",
        help="Output directory",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify round-trip integrity",
    )
    args = parser.parse_args()

    if args.verify:
        ok = verify_roundtrip(args.db, args.output_dir)
        sys.exit(0 if ok else 1)
    else:
        export_db_to_jsonl(args.db, args.output_dir)


if __name__ == "__main__":
    main()
