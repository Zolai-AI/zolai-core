#!/usr/bin/env python3
"""Phase 2C: Validate deduplication results.

Checks:
- 0 duplicates per business key
- UNIQUE constraints exist
- Row counts > 0
- NULL/empty key counts
"""
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"

# (table, key_col, unique_index_name, description)
VALIDATIONS = [
    ("dictionary_v2", "zolai", "uniq_dict_v2_zolai", "ZO->EN dictionary"),
    ("dictionary_en_zo_v2", "headword", "uniq_dict_en_zo_v2_headword", "EN->ZO dictionary"),
    ("bible_verses_v2", "ref", "uniq_bible_v2_ref", "Bible verses"),
    ("translations_v2", "source", "uniq_trans_v2_source_target", "Translation pairs"),
    ("vocabulary_v2", "headword", "uniq_vocab_v2_headword", "Vocabulary index"),
    ("proverbs_v2", "zolai", "uniq_proverb_v2_zolai", "Proverbs"),
]


def validate_table(
    cur: sqlite3.Cursor,
    table: str,
    key_col: str,
    index_name: str,
    desc: str,
) -> dict:
    """Validate one table. Returns dict with status."""
    result: dict = {
        "desc": desc,
        "row_count": 0,
        "duplicates": 0,
        "null_keys": 0,
        "index_exists": False,
        "status": "ok",
        "errors": [],
        "warnings": [],
    }

    # Row count
    try:
        result["row_count"] = cur.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    except Exception as e:
        result["errors"].append(f"Cannot read {table}: {e}")
        result["status"] = "error"
        return result

    if result["row_count"] == 0:
        result["errors"].append(f"{table} is empty")
        result["status"] = "error"
        return result

    # Duplicate check
    try:
        result["duplicates"] = cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT [{key_col}], COUNT(*) as cnt
                FROM [{table}]
                GROUP BY [{key_col}]
                HAVING cnt > 1
            )
        """).fetchone()[0]
        if result["duplicates"] > 0:
            result["errors"].append(
                f"{result['duplicates']:,} duplicate [{key_col}] groups remain"
            )
    except Exception as e:
        result["warnings"].append(f"Could not check duplicates: {e}")

    # NULL/empty key check
    try:
        result["null_keys"] = cur.execute(f"""
            SELECT COUNT(*) FROM [{table}]
            WHERE [{key_col}] IS NULL OR [{key_col}] = ''
        """).fetchone()[0]
        if result["null_keys"] > 0:
            result["warnings"].append(
                f"{result['null_keys']:,} NULL/empty [{key_col}] values"
            )
    except Exception as e:
        result["warnings"].append(f"Could not check NULL keys: {e}")

    # UNIQUE index check
    try:
        idx = cur.execute("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'index' AND name = ?
        """, (index_name,)).fetchone()[0]
        result["index_exists"] = idx > 0
        if not result["index_exists"]:
            result["errors"].append(f"UNIQUE index {index_name} not found")
    except Exception as e:
        result["warnings"].append(f"Could not check index: {e}")

    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"

    return result


def main() -> bool:
    if not DB.exists():
        print(f"Database not found: {DB}")
        return False

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    print("Phase 2C: Deduplication Validation")
    print("=" * 60)
    print(f"Database: {DB}\n")

    results = []
    all_errors: list[str] = []

    for table, key_col, index_name, desc in VALIDATIONS:
        r = validate_table(cur, table, key_col, index_name, desc)
        results.append(r)
        all_errors.extend(r["errors"])

        icon = {"ok": "OK", "warning": "WARN", "error": "FAIL"}[r["status"]]
        print(f"  [{icon}] {desc}")
        print(f"        Rows: {r['row_count']:>10,}  |  Dups: {r['duplicates']:>8,}  |  NULLs: {r['null_keys']:>8,}  |  Index: {'Y' if r['index_exists'] else 'N'}")
        for w in r["warnings"]:
            print(f"        WARN: {w}")
        for e in r["errors"]:
            print(f"        FAIL: {e}")

    # Summary
    total_rows = sum(r["row_count"] for r in results)
    total_dups = sum(r["duplicates"] for r in results)
    total_nulls = sum(r["null_keys"] for r in results)
    all_ok = all(r["status"] == "ok" for r in results)

    print()
    print("-" * 60)
    print(f"  Total rows:    {total_rows:>10,}")
    print(f"  Total dups:    {total_dups:>10,}")
    print(f"  Total NULLs:   {total_nulls:>10,}")
    print(f"  Indexes OK:    {sum(1 for r in results if r['index_exists']):>10} / {len(results)}")

    if all_ok:
        print("\n  VALIDATION PASSED")
    else:
        print(f"\n  VALIDATION FAILED — {len(all_errors)} errors")
        for e in all_errors:
            print(f"    - {e}")

    conn.close()
    return all_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
