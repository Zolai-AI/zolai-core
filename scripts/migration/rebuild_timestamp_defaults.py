#!/usr/bin/env python3
"""Rebuild every table whose timestamp column still uses ``DEFAULT (datetime('now'))``.

SQLite has no ``ALTER COLUMN DEFAULT``; the only way to change a column default
is to recreate the table. Same as ``normalize_timestamps.py``: this is a
backup-first, revertible migration.

Why the defaults matter even after the writers are fixed: a Python writer that
passes ``created_at=stamp()`` is canonical, but any INSERT that omits the column
gets whatever ``DEFAULT`` says. The 2026-10-28 RAG build wrote 266,470
``knowledge_vectors.imported_at`` rows in the space format
(``YYYY-MM-DD HH:MM:SS``) because that table's default was still
``datetime('now')`` — the 2026-10-28 writer flip never touched DDL.

Two edge cases this script handles explicitly:

``wiki_content``
    Is the external-content parent of ``wiki_content_fts``
    (``content=wiki_content, content_rowid=id``). Dropping and recreating
    ``wiki_content`` leaves the FTS index pointing at the same table name and
    the same rowids, so the FTS content stays consistent — but the script calls
    ``INSERT INTO wiki_content_fts(wiki_content_fts) VALUES('rebuild')``
    afterwards to be certain.

``learning_runs``
    Has an FK pointing at ``knowledge_versions``. ``ALTER TABLE ... RENAME TO``
    rewrites child FK references since SQLite 3.25, and the new table has the
    same name, so the FK keeps resolving. ``PRAGMA foreign_keys=OFF`` during the
    migration avoids the DROP failing on a populated child.

Default is ``--dry-run``. ``--apply`` writes; every rewritten table's original
DDL goes to a sidecar so the migration is revertible::

    python scripts/migration/rebuild_timestamp_defaults.py                 # report
    python scripts/migration/rebuild_timestamp_defaults.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLKIT_ROOT))

from zolai.shared.clock import SQL_DEFAULT, SQL_NOW  # noqa: E402

#: ``PRAGMA table_info`` hands back the bare expression (``datetime('now')``);
#: the ``DEFAULT (...)`` wrapper only exists in the stored CREATE TABLE text.
STALE_EXPR = re.compile(r"^\s*datetime\s*\(\s*'now'\s*\)\s*$", re.I)
#: The same default as it appears inside a full CREATE TABLE statement, with or
#: without the surrounding parentheses.
STALE_DDL = re.compile(r"DEFAULT\s*(?:\(\s*)?datetime\s*\(\s*'now'\s*\)\s*\)?", re.I)
NORMALIZE_EXPR = SQL_DEFAULT


def _is_fts_shadow(conn: sqlite3.Connection, table: str) -> bool:
    """FTS5 shadow tables must never be rebuilt by hand."""
    suffixes = ("_data", "_idx", "_content", "_docsize", "_config")
    for suffix in suffixes:
        if table.endswith(suffix) and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? AND sql LIKE '%fts5%'",
            (table[: -len(suffix)],),
        ).fetchone():
            return True
    return False


def tables_with_stale_defaults(conn: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    """Every ``(table, [column, ...])`` with a stale timestamp default."""
    out: list[tuple[str, list[str]]] = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        if _is_fts_shadow(conn, name):
            continue
        hits: list[str] = []
        for _cid, column, _ctype, _nn, default, _pk in conn.execute(f'PRAGMA table_info("{name}")'):
            if default and STALE_EXPR.search(default):
                hits.append(column)
        if hits:
            out.append((name, hits))
    return out


def _rewrite_create(name: str, ddl: str, new_name: str) -> str:
    """Swap the table name and the stale defaults, leave everything else identical."""
    pattern = re.compile(rf'(CREATE\s+TABLE\s+)("?{re.escape(name)}"?)', re.I)
    ddl = pattern.sub(lambda m: f'{m.group(1)}"{new_name}"', ddl, count=1)
    return STALE_DDL.sub(lambda _m: NORMALIZE_EXPR, ddl)


def _index_sql(conn: sqlite3.Connection, table: str) -> list[str]:
    """Explicit ``CREATE INDEX`` text (skips SQLite autoindexes which have sql=NULL)."""
    return [
        sql
        for (sql,) in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
            (table,),
        )
    ]


def _trigger_sql(conn: sqlite3.Connection, table: str) -> list[str]:
    """``CREATE TRIGGER`` text — dropped with the table, so it must be replayed."""
    return [
        sql
        for (sql,) in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
            (table,),
        )
    ]


def _is_fts_external_content(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql LIKE '%fts5%' AND sql LIKE ?",
        (f"%content={table}%",),
    ).fetchone()
    return row is not None


def _copy_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Writable columns — generated columns cannot appear in an INSERT target."""
    out: list[str] = []
    for row in conn.execute(f'PRAGMA table_xinfo("{table}")'):
        name, hidden = row[1], row[6]
        # hidden: 0 normal, 1 hidden (virtual-table), 2 virtual generated, 3 stored generated
        if hidden in (2, 3):
            continue
        out.append(name)
    return out


def rebuild(conn: sqlite3.Connection, table: str, *, apply: bool) -> int:
    """Rebuild one table; returns rows copied (0 in dry-run)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no CREATE TABLE for {table}")
    ddl: str = row[0]
    new_table = f"_{table}__rebuild"
    new_ddl = _rewrite_create(table, ddl, new_table)

    # Does the table have AUTOINCREMENT? If so, we need the sqlite_sequence
    # row carried across explicitly (bulk INSERT SELECT does not update it for
    # AUTOINCREMENT tables when the id column is supplied). Read it before the
    # DROP, which deletes the row — the sequence may exceed max(rowid) after
    # deletions, and AUTOINCREMENT must not reuse those ids.
    autoinc = bool(re.search(r"\bAUTOINCREMENT\b", ddl, re.I))
    orig_seq = None
    if autoinc:
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
        orig_seq = int(row[0]) if row else None

    if not apply:
        return 0

    conn.execute(new_ddl)
    try:
        cols = _copy_columns(conn, new_table)
        col_list = ", ".join(f'"{c}"' for c in cols)
        copied = conn.execute(f'INSERT INTO "{new_table}" ({col_list}) SELECT {col_list} FROM "{table}"').rowcount

        indexes = _index_sql(conn, table)
        triggers = _trigger_sql(conn, table)
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{new_table}" RENAME TO "{table}"')

        for index_sql in indexes:
            conn.execute(index_sql)
        for trigger_sql in triggers:
            conn.execute(trigger_sql)

        if autoinc:
            max_id = conn.execute(f'SELECT MAX(rowid) FROM "{table}"').fetchone()[0]
            candidates = [v for v in (max_id, orig_seq) if v is not None]
            if candidates:
                # sqlite_sequence has no UNIQUE constraint on ``name``, so
                # ON CONFLICT is not an option here.
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
                conn.execute(
                    "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                    (table, int(max(candidates))),
                )

        if _is_fts_external_content(conn, table):
            conn.execute(f'INSERT INTO "{table}_fts"("{table}_fts") VALUES(\'rebuild\')')

        return copied
    except Exception:
        # Recreate the original if we crashed after creating the new table —
        # leaves the live schema where it started.
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{new_table}"')
        except sqlite3.Error:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("/home/peter/Documents/Projects/zolai-ai/data/zolai.db"),
    )
    parser.add_argument("--apply", action="store_true", help="write (default: report only)")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("/home/peter/Documents/Projects/zolai-ai/data/backups/ddl_default_rebuild.jsonl"),
    )
    parser.add_argument("--table", action="append", help="limit to these tables (repeatable)")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(args.db), isolation_level=None, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = OFF")
    # The swap renames ``_<table>__rebuild`` to ``<table>``; in modern SQLite the
    # rename would try to fix up references to the *old* name in other tables and
    # fail on the transient state. Legacy semantics leave them untouched, which is
    # what we want — the final name is the original one.
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        targets = tables_with_stale_defaults(conn)
        if args.table:
            keep = set(args.table)
            targets = [(t, cols) for t, cols in targets if t in keep]
        total_cols = sum(len(c) for _t, c in targets)
        print(f"{'APPLYING' if args.apply else 'DRY RUN'} — {args.db}")
        print(f"{len(targets)} table(s), {total_cols} stale DEFAULT(s)")

        if not args.apply:
            for table, cols in targets:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                print(f"  {table}: {count} rows, {len(cols)} default(s): {', '.join(cols)}")
            print("(no writes issued; re-run with --apply)")
            return 0

        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(args.ledger, "a", encoding="utf-8") as revert_log:
            for table, cols in targets:
                original = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                indexes = _index_sql(conn, table)
                triggers = _trigger_sql(conn, table)
                copied = 0
                for attempt in range(1, 7):
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        copied = rebuild(conn, table, apply=True)
                        conn.execute("COMMIT")
                        break
                    except sqlite3.OperationalError as exc:
                        if conn.in_transaction:
                            conn.execute("ROLLBACK")
                        if "locked" not in str(exc).lower() or attempt == 6:
                            raise
                        print(f"  {table}: locked, retry {attempt}/5")
                        time.sleep(5 * attempt)
                revert_log.write(
                    json.dumps(
                        {
                            "table": table,
                            "columns": cols,
                            "original_ddl": original[0],
                            "indexes": indexes,
                            "triggers": triggers,
                            "rows": copied,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                revert_log.flush()
                written += 1
                print(f"  rebuilt {table}: {copied} rows")

        conn.execute("PRAGMA foreign_keys = ON")
        remaining = tables_with_stale_defaults(conn)
        print(f"rebuilt {written} table(s); ledger: {args.ledger}")
        if remaining:
            print(f"REMAINING STALE DEFAULTS: {len(remaining)}", file=sys.stderr)
            for table, cols in remaining:
                print(f"  {table}: {', '.join(cols)}", file=sys.stderr)
            return 1
        print("every timestamp column default now uses clock.SQL_DEFAULT")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
