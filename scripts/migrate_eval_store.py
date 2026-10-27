#!/usr/bin/env python3
"""Fold the evaluation lane into the canonical database.

One-shot, idempotent migration for the store consolidation:

1. widen ``eval_cases.kind`` so the gold lanes (pos / morph / grammar /
   tokenization) are allowed next to the metric lanes,
2. seed the bundled metric fixtures (``smoke``, ``eval_v1``, ``benchmark_qa``),
3. copy the 800-odd gold annotations out of the retired side file
   ``data/zolai_eval.db`` into ``eval_cases``.

The side file is left in place; deleting it is a separate, deliberate step.
Everything is counted before and after and the run refuses to finish if a lane
it reported is not in the target.

Usage::

    python scripts/migrate_eval_store.py --dry-run
    python scripts/migrate_eval_store.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zolai.config import config  # noqa: E402
from zolai.eval import store  # noqa: E402


def _snapshot(db_path: Path) -> dict[str, int]:
    """``"set/kind" -> rows`` for the current eval_cases contents."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT set_name, kind, COUNT(*) FROM eval_cases WHERE is_active = 1 "
            "GROUP BY set_name, kind ORDER BY set_name, kind"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}  # table not created yet
    finally:
        conn.close()
    return {f"{name}/{kind}": int(count) for name, kind, count in rows}


def _backup(db_path: Path, out_dir: Path) -> Path:
    """Dump both eval tables to JSON so the migration is undoable by hand."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        payload = {
            "eval_sets": [dict(r) for r in conn.execute("SELECT * FROM eval_sets ORDER BY set_name")],
            "eval_cases": [dict(r) for r in conn.execute("SELECT * FROM eval_cases ORDER BY id")],
        }
    finally:
        conn.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / "eval_store_preflight.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing.")
    parser.add_argument(
        "--source",
        default=None,
        metavar="PATH",
        help="Legacy side store (default: zolai_eval.db beside the canonical DB).",
    )
    args = parser.parse_args(argv)

    db_path = Path(config.paths.db)
    before = _snapshot(db_path)
    print(f"canonical database: {db_path}")
    print(f"  eval_cases lanes before: {len(before)} sets, {sum(before.values())} cases")

    legacy = Path(args.source) if args.source else store.legacy_store_path()
    if legacy.exists():
        planned = store.migrate_legacy_store(legacy, db_path=db_path, dry_run=True)
        print(f"legacy store {legacy.name}: {sum(planned.values())} items across {len(planned)} lanes")
        for key, count in planned.items():
            print(f"  {key}: {count}")
    else:
        print(f"legacy store not present at {legacy} — nothing to copy")

    if args.dry_run:
        print("dry run: no writes issued")
        return 0

    backup = _backup(db_path, Path(store.config.paths.db).parent / "backups")
    print(f"preflight dump: {backup}")

    store.ensure_schema(db_path)
    check = sqlite3.connect(str(db_path))
    try:
        ddl = check.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='eval_cases'").fetchone()[0]
    finally:
        check.close()
    missing = [kind for kind in store.KINDS if kind not in ddl]
    if missing:
        print(f"ERROR: eval_cases still rejects lanes {missing}", file=sys.stderr)
        return 1
    print("eval_cases accepts lanes: " + ", ".join(store.KINDS))

    seeded = store.seed_sets(db_path=db_path)
    print(f"seeded metric fixtures: {seeded}")

    if legacy.exists():
        copied = store.migrate_legacy_store(legacy, db_path=db_path)
        print(f"migrated gold lanes: {copied}")

    after = _snapshot(db_path)
    lost = {k: v for k, v in before.items() if after.get(k, 0) < v}
    if lost:
        print(f"ERROR: lanes lost rows: {lost}", file=sys.stderr)
        return 1
    print(f"eval_cases lanes after: {len(after)} sets, {sum(after.values())} cases")
    for key in sorted(after):
        print(f"  {key}: {after[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
