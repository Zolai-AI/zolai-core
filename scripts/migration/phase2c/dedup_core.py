#!/usr/bin/env python3
"""Phase 2C: Deduplicate 6 core _v2 tables.

Strategy: keep the most-complete row per business key.
Non-destructive: DELETE only duplicates, no schema changes here.
Run add_unique_constraints.sql AFTER this to enforce uniqueness.
"""
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"

# (table, key_col, strategy)
TABLES = [
    ("dictionary_v2", "zolai", "most_complete"),
    ("dictionary_en_zo_v2", "headword", "most_complete"),
    ("bible_verses_v2", "ref", "most_complete"),
    ("translations_v2", "source", "first"),
    ("vocabulary_v2", "headword", "most_complete"),
    ("proverbs_v2", "zolai", "first"),
]


def _build_completeness_expr(cur: sqlite3.Cursor, table: str) -> str:
    """Build an expression counting non-NULL/non-empty TEXT columns (no SUM wrapper)."""
    cols = cur.execute(f"PRAGMA table_info([{table}])").fetchall()
    # Skip id, version, created_at, updated_at, content_hash — not "data" columns
    skip = {"id", "version", "created_at", "updated_at", "content_hash"}
    data_cols = [row[1] for row in cols if row[1] not in skip and row[2] == "TEXT"]
    if not data_cols:
        return "0"
    return " + ".join(
        f"CASE WHEN [{c}] IS NOT NULL AND [{c}] != '' THEN 1 ELSE 0 END"
        for c in data_cols
    )


def dedup_table(
    conn: sqlite3.Connection, table: str, key_col: str, strategy: str
) -> tuple[int, int]:
    """Deduplicate table by business key. Returns (before, after)."""
    cur = conn.cursor()
    before = cur.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]

    if strategy == "most_complete":
        # Use a single CTE with window function to rank rows per key.
        # Pick row with most non-NULL TEXT columns; tie-break by lowest id.
        comp = _build_completeness_expr(cur, table)
        cur.execute(f"""
            DELETE FROM [{table}] WHERE id IN (
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY [{key_col}]
                               ORDER BY {comp} DESC, id ASC
                           ) AS rn
                    FROM [{table}]
                )
                SELECT id FROM ranked WHERE rn > 1
            )
        """)
    elif strategy == "first":
        # Keep first occurrence (lowest id)
        cur.execute(f"""
            DELETE FROM [{table}] WHERE id NOT IN (
                SELECT MIN(id) FROM [{table}] GROUP BY [{key_col}]
            )
        """)

    after = cur.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    return before, after


def main() -> int:
    if not DB.exists():
        print(f"Database not found: {DB}")
        return 1

    conn = sqlite3.connect(str(DB))
    print("Phase 2C: Deduplication")
    print("=" * 50)
    print(f"Database: {DB}\n")

    total_before = 0
    total_after = 0
    ok = True

    for table, key, strategy in TABLES:
        try:
            before, after = dedup_table(conn, table, key, strategy)
            removed = before - after
            total_before += before
            total_after += after
            pct = (removed / before * 100) if before else 0
            status = "OK" if removed >= 0 else "ERROR"
            print(f"  {table:<30} {before:>10,} -> {after:>10,}  (-{removed:,}, {pct:.1f}%)  [{status}]")
            if removed < 0:
                ok = False
        except Exception as e:
            print(f"  {table:<30} FAILED: {e}")
            ok = False

    conn.commit()

    print()
    print("-" * 50)
    print(f"  {'TOTAL':<30} {total_before:>10,} -> {total_after:>10,}  (-{total_before - total_after:,})")
    print()

    if ok:
        print("Deduplication complete.")
    else:
        print("Deduplication completed with errors — check output above.")

    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
