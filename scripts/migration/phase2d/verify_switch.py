#!/usr/bin/env python3
"""Phase 2D: Post-cutover verification.

Runs after switch_tables.sql to confirm:
1. All 23 canonical table names exist (post-rename)
2. No _v2 suffix tables remain
3. Row counts match expectations (v2 rows now in canonical tables)

Usage:
    python verify_switch.py [--db PATH]

Exit codes: 0 = PASS, 1 = FAIL
"""
import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"

# After cutover, these are the expected canonical table names.
# v2 rows are now in these tables; _old and _v2 should not exist.
EXPECTED_CANONICAL = [
    "dictionary",
    "dictionary_en_zo",
    "bible_verses",
    "grammar_patterns",
    "translations",
    "word_alignments",
    "vocabulary",           # was vocab → vocabulary_v2 → vocabulary
    "proverbs",
    "phrases",
    "word_usage",
    "syllable_data",
    "word_collocations",
    "bible_analysis",       # was bible_context → bible_analysis_v2 → bible_analysis
    "articles",
    "songs",                # was zolai_songs → songs_v2 → songs
    "wiki_content",
    "data_audit_log",
    "audit_findings",
    "provenance",
    "import_log",           # was jsonl_import_log → import_log_v2 → import_log
    "tone_sandhi",          # was zolai_tone_sandhi → tone_sandhi_v2 → tone_sandhi
    "tone_patterns",
    "training_runs",
]

# Expected row counts (from _v2 tables before cutover)
# These serve as a baseline — any row count of 0 is a red flag.
EXPECTED_MIN_ROWS = {
    "dictionary": 80000,
    "dictionary_en_zo": 60000,
    "bible_verses": 30000,
    "grammar_patterns": 5000,
    "translations": 200000,
    "word_alignments": 380000,
    "vocabulary": 100000,
    "proverbs": 7000,
    "phrases": 4000,
    "word_usage": 55000,
    "syllable_data": 180000,
    "word_collocations": 4000,
    "bible_analysis": 1000,
    "articles": 6000,
    "songs": 1000,
    "wiki_content": 1500,
    "data_audit_log": 20000,
    "audit_findings": 500,
    "provenance": 200,
    "import_log": 50,
    "tone_sandhi": 15,
    "tone_patterns": 100,
    "training_runs": 1,
}


def get_all_tables(cur: sqlite3.Cursor) -> set[str]:
    return {row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def main(db_path: Path) -> bool:
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        return False

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    print("=" * 72)
    print("Phase 2D — Post-Cutover Verification")
    print("=" * 72)
    print(f"Database: {db_path}\n")

    all_tables = get_all_tables(cur)
    errors: list[str] = []
    warnings: list[str] = []

    # --- Check 1: All expected canonical tables exist ---
    print("[Check 1] Canonical table existence")
    missing = [t for t in EXPECTED_CANONICAL if t not in all_tables]
    if missing:
        for t in missing:
            print(f"  FAIL: '{t}' does not exist")
            errors.append(f"Missing canonical table: {t}")
    else:
        print(f"  OK: All {len(EXPECTED_CANONICAL)} canonical tables exist")

    # --- Check 2: No _v2 tables remain ---
    print("\n[Check 2] No _v2 tables remaining")
    v2_tables = sorted(t for t in all_tables if t.endswith("_v2"))
    if v2_tables:
        for t in v2_tables:
            print(f"  FAIL: '{t}' still has _v2 suffix")
            errors.append(f"Leftover _v2 table: {t}")
    else:
        print("  OK: No _v2 tables remaining")

    # --- Check 3: No unexpected _old tables ---
    print("\n[Check 3] _old table check")
    old_tables = sorted(t for t in all_tables if t.endswith("_old"))
    if old_tables:
        print(f"  INFO: {len(old_tables)} _old tables present (expected — pending cleanup):")
        for t in old_tables:
            count = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            print(f"         {t}: {count:,} rows")
    else:
        print("  INFO: No _old tables (cleanup already done)")

    # --- Check 4: Row counts ---
    print("\n[Check 4] Row counts")
    total_rows = 0
    for table in EXPECTED_CANONICAL:
        if table in missing:
            print(f"  SKIP: {table} (missing)")
            continue
        try:
            count = cur.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            total_rows += count
            min_expected = EXPECTED_MIN_ROWS.get(table, 0)
            if count == 0:
                print(f"  FAIL: {table} is EMPTY")
                errors.append(f"Table '{table}' has 0 rows")
            elif count < min_expected:
                pct = count / min_expected * 100 if min_expected else 0
                print(f"  WARN: {table} has {count:,} rows (< expected {min_expected:,}, {pct:.0f}%)")
                warnings.append(f"Table '{table}' below expected minimum")
            else:
                print(f"  OK:   {table} = {count:>10,} rows")
        except Exception as e:
            print(f"  FAIL: {table} — {e}")
            errors.append(f"Cannot read '{table}': {e}")

    # --- Summary ---
    print("\n" + "=" * 72)
    print(f"  Expected canonical tables: {len(EXPECTED_CANONICAL)}")
    print(f"  Tables found:              {len(EXPECTED_CANONICAL) - len(missing)}")
    print(f"  Total rows:                {total_rows:,}")
    print(f"  _v2 remaining:             {len(v2_tables)}")
    print(f"  _old tables:               {len(old_tables)}")
    print(f"  Warnings:                  {len(warnings)}")

    if not errors:
        print("\n  ✅ POST-CUTOVER VERIFICATION PASSED")
        print("  All canonical tables are in place with expected data.")
    else:
        print(f"\n  ❌ POST-CUTOVER VERIFICATION FAILED — {len(errors)} errors")
        for e in errors:
            print(f"    - {e}")

    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")

    conn.close()
    return len(errors) == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 2D: Post-cutover verification"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to SQLite database (default: {DEFAULT_DB})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ok = main(args.db)
    sys.exit(0 if ok else 1)
