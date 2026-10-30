#!/usr/bin/env python3
"""One UTC serialisation for every timestamp column in ``data/zolai.db``.

Why this exists (measured 2026-10-27, 201 timestamp columns across 121 tables):
eleven columns carry two or three serialisations of the same instant at once.

    data_audit_log.changed_at      40,067 with +00:00  +  5,977 space-separated
    foundation_evidence.created_at  1,862 with +00:00  +    380 naive
    knowledge_claims.created_at      48 with +00:00  +    124 space-separated
    import_log.imported_at            96 with +00:00  +     95 space-separated

The writers were ``datetime('now')`` in SQL (SQLite answers ``YYYY-MM-DD
HH:MM:SS``, space-separated), column ``DEFAULT (datetime('now'))``, and Python
stamps that each picked their own format.  TEXT timestamps only order as text,
and ``" "`` is ``0x20`` while ``"T"`` is ``0x54`` — so ``ORDER BY changed_at
DESC`` puts a 01:00 ``T``-format row *above* a 23:59 space-format row of the
same day, and ``WHERE changed_at >= '2026-10-03T10:00:00'`` silently drops every
space-format row of that day.  An audit trail that loses five thousand rows
without an error is worse than one that fails.

This script rewrites the non-canonical values in place to the canonical
``ISO-8601 + 00:00`` form produced by :func:`zolai.shared.clock.stamp`, which is
what 88% of the corpus already stores.  The instant never changes: each value is
parsed with the offset made explicit (a bare or space-separated stamp is read as
UTC, matching ``datetime('now')``), then re-serialised.

Default is ``--dry-run``.  ``--apply`` writes, and every rewritten row is
recorded to a sidecar JSONL so the migration is revertible row-by-row::

    python scripts/normalize_timestamps.py                 # report only
    python scripts/normalize_timestamps.py --apply
    python scripts/normalize_timestamps.py --revert data/backups/timestamp_normalization.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import timezone
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT_ROOT))

from zolai.shared.clock import parse, stamp  # noqa: E402

CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
TIMESTAMP_COLUMN = re.compile(r"(_at|_timestamp|^timestamp[s]?|_time)$")

#: Rows per transaction. Small enough that the write lock is released often,
#: large enough that 346k rewrites stay a few dozen commits rather than 346k.
CHUNK_SIZE = 5000


def classify(value: str) -> str:
    """Name the serialisation ``value`` is written in."""
    if CANONICAL.match(value):
        return "canonical"
    if " " in value:
        return "space"
    if value.endswith("Z"):
        return "zulu"
    if "+00:00" in value or re.search(r"[+-]\d{2}:\d{2}$", value):
        return "other-offset"
    if "T" in value:
        return "naive"
    return "unknown"


def timestamp_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every ``(table, column)`` whose name says "stamp" — not filtered by declared type.

    The corpus declares these columns as ``TEXT`` (168), ``VARCHAR`` (21),
    ``TIMESTAMP`` (8) and ``DATETIME`` (1).  Filtering on ``TEXT`` quietly skips
    30 of them, among them ``foundation_evidence.created_at`` with 380 naive
    stamps in it — a check that passed because it looked at less than it claimed.
    The value test (``typeof(col)='text'`` in :func:`scan`) is the one that can
    actually see data.
    """
    out: list[tuple[str, str]] = []
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        for _cid, column, _ctype, _notnull, _default, _pk in conn.execute(f'PRAGMA table_info("{table}")'):
            if column and TIMESTAMP_COLUMN.search(column):
                out.append((table, column))
    return out


def scan(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, int]]]:
    """Per table, per column, a count of rows in each serialisation."""
    report: dict[str, dict[str, dict[str, int]]] = {}
    for table, column in timestamp_columns(conn):
        counts: dict[str, int] = {}
        try:
            rows = conn.execute(
                f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND typeof("{column}")=\'text\''
            ).fetchall()
        except sqlite3.Error:
            continue
        for (value,) in rows:
            kind = classify(value)
            counts[kind] = counts.get(kind, 0) + 1
        if counts:
            report.setdefault(table, {})[column] = counts
    return report


def mixed(report: dict) -> list[tuple[str, str, dict[str, int]]]:
    """Columns holding more than one serialisation — the ones that mis-sort."""
    return [
        (table, column, counts)
        for table, columns in report.items()
        for column, counts in columns.items()
        if sum(1 for kind, n in counts.items() if n and kind != "unknown") > 1
    ]


def convertible(conn: sqlite3.Connection, table: str, column: str) -> list[tuple[int, str, str]]:
    """``(rowid, old, new)`` for every non-canonical value that parses.

    Every text value is read back through :func:`parse` rather than filtered with
    a ``GLOB``, because SQLite pattern matching is case-insensitive for ASCII by
    default and a predicate naming the digits ``0-9`` would also pass ``a-f``.
    """
    rows = conn.execute(
        f'SELECT rowid, "{column}" FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL AND typeof("{column}")=\'text\' ORDER BY rowid'
    ).fetchall()
    out: list[tuple[int, str, str]] = []
    for rowid, value in rows:
        if "T" not in value and " " not in value:
            # A date-only value ("2026-10-03") is not a stamp in the wrong
            # format; inventing a midnight time would state something the row
            # never said. Left alone, and reported as unparseable-by-design.
            continue
        try:
            moment = parse(value)
        except ValueError:
            continue
        replacement = moment.astimezone(timezone.utc).isoformat()
        if replacement != value:
            out.append((rowid, value, replacement))
    return out


def apply_migration(db: Path, *, apply: bool, sidecar: Path) -> int:
    read_only = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        report = scan(read_only)
        targets = mixed(report)
        plan: list[tuple[str, str, list[tuple[int, str, str]]]] = []
        for table, column, _counts in targets:
            changes = convertible(read_only, table, column)
            if changes:
                plan.append((table, column, changes))
    finally:
        read_only.close()

    total = sum(len(changes) for _table, _column, changes in plan)
    print(f"{'APPLYING' if apply else 'DRY RUN'} — {db}")
    print(f"{len(plan)} column(s), {total} row value(s) to rewrite")
    for table, column, changes in plan:
        examples = ", ".join(f"{old!r} -> {new!r}" for _, old, new in changes[:2])
        print(f"  {table}.{column}: {len(changes)} row(s)  e.g. {examples}")

    if not apply:
        if not plan:
            print("nothing to rewrite — every column already holds one serialisation")
        print("(no writes issued; re-run with --apply)")
        return 0

    if total == 0:
        print("nothing to rewrite")
        return 0

    holders = _other_writers(db)
    if holders:
        # ``fuser`` reports every process with the file open, readers included —
        # a ``zolai rag search`` holding a shared WAL handle is not a conflict.
        # ``BEGIN IMMEDIATE`` below is the real serialisation point: it takes the
        # write lock or waits for it, so the PID list is advisory only.
        print(f"note: PID(s) {holders} also hold the database; waiting on the write lock")

    conn = sqlite3.connect(db, isolation_level=None, timeout=120.0)
    written = 0
    try:
        conn.execute("PRAGMA busy_timeout = 120000")
        pending = [
            (table, column, rowid, old, new)
            for table, column, changes in plan
            for rowid, old, new in changes
        ]
        # Commit in chunks rather than one 346k-row transaction: the learning
        # cycle and the RAG build write to this database too, and each respects
        # a 60 s busy timeout. A single long transaction would outlast that and
        # fail their statements. Every chunk is atomic, and the sidecar records
        # only rows that actually committed, so ``--revert`` stays exact.
        with open(sidecar, "a", encoding="utf-8") as revert_log:
            for start in range(0, len(pending), CHUNK_SIZE):
                batch = pending[start : start + CHUNK_SIZE]
                for attempt in range(1, 7):
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        for table, column, rowid, _old, new in batch:
                            conn.execute(
                                f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?', (new, rowid)
                            )
                        conn.execute("COMMIT")
                        break
                    except sqlite3.OperationalError as exc:
                        if conn.in_transaction:
                            conn.execute("ROLLBACK")
                        if "locked" not in str(exc).lower() or attempt == 6:
                            raise
                        print(f"  locked, retry {attempt}/5", file=sys.stderr)
                        time.sleep(5 * attempt)
                for table, column, rowid, old, new in batch:
                    revert_log.write(
                        json.dumps(
                            {"table": table, "column": column, "rowid": rowid, "old": old, "new": new},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                revert_log.flush()
                written += len(batch)
    finally:
        conn.close()

    verify = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        still_mixed = mixed(scan(verify))
    finally:
        verify.close()
    print(f"rewrote {written} value(s); revert ledger: {sidecar}")
    if still_mixed:
        print(f"REMAINING MIXED COLUMNS: {len(still_mixed)}", file=sys.stderr)
        for table, column, counts in still_mixed:
            print(f"  {table}.{column}: {counts}", file=sys.stderr)
        return 1
    print("every timestamp column now holds exactly one serialisation")
    return 0


def _other_writers(db: Path) -> list[int]:
    """PIDs holding ``db`` open — the same single-writer gate the DB tracks use."""
    try:
        proc = subprocess.run(["fuser", str(db)], capture_output=True, text=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [int(tok) for tok in proc.stdout.split() if tok.isdigit() and int(tok) != os.getpid()]


def revert(sidecar: Path, db: Path) -> int:
    if not sidecar.exists():
        print(f"no ledger at {sidecar}", file=sys.stderr)
        return 2
    entries = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not entries:
        print("ledger is empty — nothing to restore")
        return 0
    holders = _other_writers(db)
    if holders:
        print(f"note: PID(s) {holders} also hold the database; waiting on the write lock")
    conn = sqlite3.connect(str(db), isolation_level=None, timeout=120.0)
    restored = 0
    try:
        conn.execute("PRAGMA busy_timeout = 120000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for entry in reversed(entries):
                cur = conn.execute(
                    f'UPDATE "{entry["table"]}" SET "{entry["column"]}" = ? '
                    f'WHERE rowid = ? AND "{entry["column"]}" = ?',
                    (entry["old"], entry["rowid"], entry["new"]),
                )
                restored += cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    print(f"restored {restored} value(s) from {sidecar}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=None, help="defaults to zolai.config.paths.db")
    parser.add_argument("--apply", action="store_true", help="write (default: report only)")
    parser.add_argument("--report", action="store_true", help="print every column and its format mix")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=TOOLKIT_ROOT.parent / "data" / "backups" / "timestamp_normalization.jsonl",
    )
    parser.add_argument("--revert", type=Path, default=None, help="restore every row in this ledger")
    args = parser.parse_args()

    if args.db is None:
        from zolai.config import config

        db = Path(config.paths.db)
    else:
        db = args.db
    if not db.exists():
        print(f"no database at {db}", file=sys.stderr)
        return 2

    if args.revert:
        return revert(args.revert, db)

    if args.report:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            report = scan(conn)
        finally:
            conn.close()
        for table, columns in sorted(report.items()):
            for column, counts in sorted(columns.items()):
                flag = "MIXED" if sum(1 for k, n in counts.items() if n and k != "unknown") > 1 else "     "
                print(f"{flag} {table}.{column} {counts}")
        return 0

    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    return apply_migration(db, apply=args.apply, sidecar=args.ledger)


if __name__ == "__main__":
    raise SystemExit(main())
