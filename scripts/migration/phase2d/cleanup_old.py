#!/usr/bin/env python3
"""Phase 2D: Cleanup old tables after successful cutover.

Drops all _old tables with interactive confirmation.
Optionally runs VACUUM to reclaim disk space.

Usage:
    python cleanup_old.py [--db PATH] [--dry-run] [--vacuum]

Exit codes: 0 = OK, 1 = FAIL
"""
import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"

# Tables that should NOT be dropped even if they end in _old
# (e.g., from previous migrations or manual backups)
EXCLUDED_FROM_DROP: set[str] = set()

# Minimum expected row count — if an _old table has MORE rows than
# its replacement, warn the user before dropping.
ROW_COUNT_WARNING_THRESHOLD = 1.1  # 10% more rows = suspicious


def get_old_tables(cur: sqlite3.Cursor) -> list[tuple[str, int]]:
    """Return list of (table_name, row_count) for all _old tables."""
    tables = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_old' ORDER BY name"
    ).fetchall()
    result = []
    for (name,) in tables:
        if name in EXCLUDED_FROM_DROP:
            continue
        count = cur.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
        result.append((name, count))
    return result


def get_replacement_count(cur: sqlite3.Cursor, old_name: str) -> int | None:
    """Get row count for the replacement table (without _old suffix)."""
    canonical = old_name.removesuffix("_old")
    try:
        return cur.execute(f"SELECT COUNT(*) FROM [{canonical}]").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def drop_table(cur: sqlite3.Cursor, table: str) -> bool:
    """Drop a table. Returns True on success."""
    try:
        cur.execute(f"DROP TABLE [{table}]")
        return True
    except Exception as e:
        print(f"  ERROR dropping {table}: {e}")
        return False


def main(db_path: Path, dry_run: bool, vacuum: bool) -> bool:
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        return False

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    print("=" * 72)
    print("Phase 2D — Cleanup Old Tables")
    print("=" * 72)
    print(f"Database: {db_path}\n")

    old_tables = get_old_tables(cur)

    if not old_tables:
        print("No _old tables found. Nothing to clean up.")
        conn.close()
        return True

    # --- List tables and check safety ---
    print(f"Found {len(old_tables)} _old tables:\n")
    warnings: list[str] = []

    for name, count in old_tables:
        canonical = name.removesuffix("_old")
        replacement_count = get_replacement_count(cur, name)

        if replacement_count is not None and replacement_count > 0:
            ratio = count / replacement_count if replacement_count else 0
            if ratio > ROW_COUNT_WARNING_THRESHOLD:
                warnings.append(
                    f"{name}: {count:,} rows vs replacement '{canonical}': "
                    f"{replacement_count:,} rows ({ratio:.1f}x more)"
                )

        status = ""
        if replacement_count is None:
            status = " [NO REPLACEMENT FOUND]"
        print(f"  {name:<40s} {count:>10,} rows{status}")

    if warnings:
        print(f"\n  ⚠️  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"    {w}")
        print("  These _old tables have MORE rows than their replacement.")
        print("  Verify this is expected before dropping.\n")

    # --- Confirmation ---
    if dry_run:
        print("\n  DRY RUN — no tables will be dropped.\n")
        conn.close()
        return True

    print(f"\n  This will DROP {len(old_tables)} tables permanently.")
    response = input("  Type 'DROP' to confirm: ").strip()
    if response != "DROP":
        print("  Aborted.")
        conn.close()
        return False

    # --- Drop tables ---
    print("\nDropping tables...")
    dropped = 0
    failed = 0

    for name, _ in old_tables:
        if drop_table(cur, name):
            print(f"  ✓ Dropped {name}")
            dropped += 1
        else:
            failed += 1

    conn.commit()

    # --- VACUUM ---
    if vacuum:
        print(f"\nRunning VACUUM (this may take a while)...")
        try:
            cur.execute("VACUUM")
            print("  ✓ VACUUM complete")
        except Exception as e:
            print(f"  ERROR during VACUUM: {e}")

    # --- Summary ---
    print("\n" + "=" * 72)
    print(f"  Dropped:   {dropped}")
    print(f"  Failed:    {failed}")
    print(f"  Remaining: {len(old_tables) - dropped}")

    if failed == 0:
        print("\n  ✅ CLEANUP COMPLETE")
    else:
        print(f"\n  ⚠️  CLEANUP PARTIAL — {failed} tables failed to drop")

    conn.close()
    return failed == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 2D: Cleanup old tables after cutover"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to SQLite database (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List tables without dropping",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after dropping tables to reclaim disk space",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ok = main(args.db, dry_run=args.dry_run, vacuum=args.vacuum)
    sys.exit(0 if ok else 1)
