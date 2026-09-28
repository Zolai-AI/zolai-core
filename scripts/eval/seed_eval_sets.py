#!/usr/bin/env python3
"""Seed the DB-first eval tables (``eval_sets`` / ``eval_cases``).

Imports the bundled JSONL interchange files under ``zolai/eval/sets/`` into
the canonical store so ``zolai-eval --set db:<name>`` works::

    python scripts/eval/seed_eval_sets.py             # all sets
    python scripts/eval/seed_eval_sets.py --set smoke # one set
    python scripts/eval/seed_eval_sets.py --dry-run   # schema + counts only

Idempotent: each ``(set, lane)`` is replaced on re-run. Counts printed at the
end. Exit code 0 on success, 2 on a bad argument/unknown set.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from zolai.eval.store import (
    SEED_SOURCES,
    SETS_DIR,
    ensure_schema,
    fetch_cases,
    list_sets,
    seed_sets,
)


def _planned_counts(names: set[str] | None) -> dict[str, int]:
    """Count JSONL records that would be imported, per set."""
    counts: dict[str, int] = {}
    for set_name, filename, _kind in SEED_SOURCES:
        if names is not None and set_name not in names:
            continue
        path = SETS_DIR / filename
        records = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        counts[set_name] = counts.get(set_name, 0) + records
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_eval_sets",
        description="Seed eval_sets/eval_cases from the bundled JSONL fixtures.",
    )
    parser.add_argument(
        "--set",
        action="append",
        dest="sets",
        metavar="NAME",
        help="Seed only this set (repeatable): smoke, eval_v1, benchmark_qa.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create the schema and report counts without importing cases.",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Explicit DB path (default: $ZOLAI_DB_PATH or data/zolai.db).",
    )
    args = parser.parse_args(argv)

    names = set(args.sets) if args.sets else None
    try:
        db_path = ensure_schema(args.db)
    except OSError as exc:
        print(f"error: cannot open eval store: {exc}", file=sys.stderr)
        return 2

    print(f"eval store: {db_path}")
    if args.dry_run:
        try:
            planned = _planned_counts(names)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        existing = {row["set_name"]: row["case_count"] for row in list_sets(db_path=db_path)}
        for set_name, count in sorted(planned.items()):
            print(f"  {set_name}: would import {count} cases (in DB: {existing.get(set_name, 0)})")
        print(f"dry-run: {sum(planned.values())} cases planned, nothing written")
        return 0

    try:
        counts = seed_sets(names, db_path=db_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    total = 0
    for set_name, count in sorted(counts.items()):
        lanes = sorted({c["kind"] for c in fetch_cases(set_name, db_path=db_path)})
        print(f"  {set_name}: {count} cases ({'+'.join(lanes)})")
        total += count
    print(f"seeded {total} cases into {len(counts)} set(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
