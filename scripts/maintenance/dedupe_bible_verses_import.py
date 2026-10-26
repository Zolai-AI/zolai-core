#!/usr/bin/env python3
"""G2 — dedupe ``bible_verses_import`` to one best row per ``ref``.

Root cause (see plan): ``bible/parallel_corpus_v1.jsonl`` was imported 7–8 times
as separate batch ids and the table has no unique key on ``ref`` — 226,626 rows
for 30,569 distinct references.

Selection rule, per ``ref``:

* score = number of non-empty columns among the ZO translations + ``en_kJV``
  (only the columns that actually exist on the table are scored);
* highest score wins, ties go to the earliest ``imported_at`` (a NULL
  ``imported_at`` counts as earliest/unbounded), final tie-break = lowest rowid.

Then, in one transaction: delete the losers and
``CREATE UNIQUE INDEX IF NOT EXISTS ux_bible_verses_import_ref`` so a future
re-import fails loudly instead of duplicating (upsert follow-up is recorded in
the tracker).  The index is created here (live DB only) — no code migration, so
the test/pipeline blast radius stays zero.

Verse text itself lives in ``bible_verses`` (SoT) and is not touched.  The
target must be ≥1 GB before any write (0-byte decoy refused); backup first.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

MIN_TARGET_BYTES = 1_000_000_000
BUSY_TIMEOUT_MS = 60_000

#: Candidate score columns, in preference order; only existing ones are used.
SCORE_COLUMNS = (
    "zo_tdb77",
    "zo_tedim2010",
    "zo_tedim1932",
    "zo_hcl06",
    "zo_fcl",
    "en_kJV",
)

UNIQUE_INDEX_NAME = "ux_bible_verses_import_ref"
UNIQUE_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_bible_verses_import_ref"
    " ON bible_verses_import(ref)"
)


# ── preflight / connection ──────────────────────────────────────────────────


def preflight(db_path: str | Path, *, min_bytes: int = MIN_TARGET_BYTES) -> Path:
    """Refuse missing / sub-1 GB targets (the 0-byte decoy can never be written)."""
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"[G2] target database not found: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise SystemExit(
            f"[G2] target database {path} is {size:,} bytes — refusing: "
            f"a real target must be ≥1 GB ({min_bytes:,} bytes)."
        )
    return path


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # isolation_level=None → we own BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def score_columns(conn: sqlite3.Connection) -> list[str]:
    """Plan score columns that exist on this table (intersection, stable order)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(bible_verses_import)")}
    return [col for col in SCORE_COLUMNS if col in existing]


# ── planning ────────────────────────────────────────────────────────────────


def plan_dedupe(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compute keep/delete rowids without writing anything."""
    cols = score_columns(conn)
    if not cols:
        raise RuntimeError(
            "bible_verses_import has none of the score columns "
            f"{SCORE_COLUMNS} — refusing to dedupe blind"
        )
    empty_refs = int(
        conn.execute(
            "SELECT COUNT(*) FROM bible_verses_import WHERE ref IS NULL OR TRIM(ref) = ''"
        ).fetchone()[0]
    )
    if empty_refs > 1:
        # The unique index cannot be created with more than one empty/NULL ref.
        raise RuntimeError(
            f"{empty_refs} bible_verses_import row(s) have an empty ref — "
            "the unique index would be ambiguous; aborting"
        )

    select = ", ".join(f'"{col}"' for col in cols)
    rows = conn.execute(
        f'SELECT rowid AS "_rowid", ref, imported_at, {select}'
        " FROM bible_verses_import"
    ).fetchall()

    keep: dict[str, int] = {}
    best_key: dict[str, tuple] = {}
    for row in rows:
        ref = row["ref"]
        score = sum(1 for col in cols if (row[col] or "").strip())
        key = (-score, row["imported_at"] or "", int(row["_rowid"]))
        if ref not in best_key or key < best_key[ref]:
            best_key[ref] = key
            keep[ref] = int(row["_rowid"])

    kept_rowids = set(keep.values())
    delete_ids = [int(r["_rowid"]) for r in rows if int(r["_rowid"]) not in kept_rowids]
    scores = {ref: -key[0] for ref, key in best_key.items()}
    return {
        "score_columns": cols,
        "before": len(rows),
        "distinct_refs": len(keep),
        "keep_rowids": sorted(kept_rowids),
        "delete_ids": sorted(delete_ids),
        "best_scores": scores,
        "max_score": max(scores.values()) if scores else 0,
    }


# ── main step ───────────────────────────────────────────────────────────────


def dedupe(db_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Delete duplicate rows and install the unique ``ref`` guard.

    Args:
        db_path: Target database.
        dry_run: Plan only — no row deleted, no index created.

    Returns:
        Summary dict (row counts only).

    Raises:
        RuntimeError: the table cannot be deduped safely (nothing written).
        sqlite3.Error: any write failure rolls the whole transaction back.
    """
    conn = _connect(db_path)
    try:
        plan = plan_dedupe(conn)
        if dry_run:
            return {
                "dry_run": True,
                "before": plan["before"],
                "after": plan["before"] - len(plan["delete_ids"]),
                "deleted": 0,
                "would_delete": len(plan["delete_ids"]),
                "distinct_refs": plan["distinct_refs"],
                "score_columns": plan["score_columns"],
                "index_created": False,
            }

        conn.execute("BEGIN IMMEDIATE")
        try:
            delete_ids = plan["delete_ids"]
            for start in range(0, len(delete_ids), 500):
                chunk = delete_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(
                    f"DELETE FROM bible_verses_import WHERE rowid IN ({placeholders})",
                    chunk,
                )
            # Installed in the same txn as the deletes so a partial run can
            # never leave duplicates behind an already-unique guard.
            conn.execute(UNIQUE_INDEX_DDL)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        after = int(
            conn.execute("SELECT COUNT(*) FROM bible_verses_import").fetchone()[0]
        )
        distinct = int(
            conn.execute(
                "SELECT COUNT(DISTINCT ref) FROM bible_verses_import"
            ).fetchone()[0]
        )
        has_index = (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (UNIQUE_INDEX_NAME,),
            ).fetchone()
            is not None
        )
        return {
            "dry_run": False,
            "before": plan["before"],
            "after": after,
            "deleted": plan["before"] - after,
            "would_delete": len(plan["delete_ids"]),
            "distinct_refs": distinct,
            "score_columns": plan["score_columns"],
            "index_created": has_index,
        }
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deduplicate bible_verses_import to one best row per ref"
    )
    parser.add_argument("--db", default=None, help="target sqlite database (default: canonical)")
    parser.add_argument("--dry-run", action="store_true", help="plan only, write nothing")
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db)
    else:
        from zolai.config import config

        db_path = Path(config.paths.zolai_db)

    try:
        preflight(db_path)
        summary = dedupe(db_path, dry_run=args.dry_run)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.code) if isinstance(exc.code, int) else 2
    except (RuntimeError, sqlite3.Error) as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if summary["dry_run"] else "WROTE"
    print(f"[G2] {mode} bible_verses_import {db_path}")
    print(f"  rows                : {summary['before']:,} → {summary['after']:,}")
    print(f"  distinct refs       : {summary['distinct_refs']:,}")
    print(f"  would delete        : {summary['would_delete']:,}")
    print(f"  unique index        : {summary['index_created']}")
    print(f"  score columns       : {summary['score_columns']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
