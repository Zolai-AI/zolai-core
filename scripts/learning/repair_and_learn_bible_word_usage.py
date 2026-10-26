#!/usr/bin/env python3
"""G1 — repair corrupt ``word_usage.book`` rows and rebuild every Bible book.

Root cause (see plan): a buggy writer passed a dict as the ``book`` argument
(7,384 rows whose ``book`` looks like ``{'book': '1CH', ...}``) and the Bible
import only wrote top-N words for 59/66 books — 2.8% coverage, zero depth.

What this script does, in one transaction:

1. Preflight: the target must exist and be ≥1 GB (the repo-local 0-byte decoy
   is refused), ``busy_timeout=60s``, then ``BEGIN IMMEDIATE``.
2. Parse-validate every ``book LIKE '{%'`` row with ``ast.literal_eval``; the
   inner ``book`` must be one of the Bible book codes.  ANY unparseable /
   unknown row aborts the run (and rolls back) — unknown data is never dropped.
3. ``DELETE`` only the Bible-code rows and the corrupt rows.  Sentinel books
   (``ZOMIDAILY``, ``ZOMIDAILY_ARTICLES``, ``phrase:bigram``,
   ``phrase:trigram``, ``BIBLE``, ``MEANING_SHIFT`` …) are left untouched.
4. Relearn from ``bible_verses``: the five ZO translation columns of each verse
   are tokenized with :func:`zolai.shared.text.tokenize_words` and coalesced
   **per (ref, word)** — document-frequency semantics, so a word appearing in
   all five translations of a verse counts once (no 5x inflation).  Aggregate
   keys are unique by construction, so ``ix_usage_word_book_unique`` can never
   fire.  ``co_occurring_words`` holds verse co-occurrence for the top-300
   words per book (top-10 neighbours, ``[{"word": …, "count": …}]`` — the
   format already used by the good rows), ``myanmar`` is the first non-empty
   ``myanmar``/``myanmar_judson`` of a containing verse.
5. Print before/after counts (total, corrupt, per-book depth).

Idempotent: a rerun deletes the same set and relearns the same counts.
No network, no external AI provider calls — stdlib sqlite3 only.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # allows `python scripts/learning/…py`
    sys.path.insert(0, str(_REPO_ROOT))

from zolai.shared.text import tokenize_words  # noqa: E402  (path bootstrap first)

#: Refuse any target smaller than 1 GB — the repo-local decoy is 0 bytes.
MIN_TARGET_BYTES = 1_000_000_000
BUSY_TIMEOUT_MS = 60_000

#: The five Zolai translation columns that count towards word usage.
ZO_COLUMNS = ("zo_tdb77", "zo_tedim2010", "zo_tedim1932", "zo_hcl06", "zo_fcl")

CO_OCCURRENCE_TOP_WORDS = 300  # per book
CO_OCCURRENCE_TOP_NEIGHBOURS = 10


class CorruptParseError(RuntimeError):
    """A ``book LIKE '{%'`` row is not parseable into a Bible book code."""


# ── preflight / connection ──────────────────────────────────────────────────


def preflight(db_path: str | Path, *, min_bytes: int = MIN_TARGET_BYTES) -> Path:
    """Validate the target DB before any write is attempted.

    Args:
        db_path: Candidate database file.
        min_bytes: Size floor.  The CLI always uses the 1 GB default; tests
            lower it explicitly for synthetic ``tmp_path`` fixtures.

    Returns:
        Resolved path.

    Raises:
        SystemExit: missing file, or smaller than ``min_bytes`` (0-byte decoy).
    """
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"[G1] target database not found: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise SystemExit(
            f"[G1] target database {path} is {size:,} bytes — refusing: "
            f"a real target must be ≥1 GB ({min_bytes:,} bytes). "
            "The repo-local 0-byte zolai.db is a decoy; pass the canonical path."
        )
    return path


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # isolation_level=None → we own BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ── parse validation ────────────────────────────────────────────────────────


def load_book_codes(conn: sqlite3.Connection) -> set[str]:
    """The 66 Bible book codes, taken from the SoT table (never hardcoded)."""
    rows = conn.execute(
        "SELECT DISTINCT book FROM bible_verses"
        " WHERE book IS NOT NULL AND TRIM(book) <> ''"
    ).fetchall()
    codes = {row[0] for row in rows}
    if not codes:
        raise CorruptParseError(
            "bible_verses holds no book codes — refusing to rebuild word_usage"
        )
    return codes


def scan_corrupt(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """Every row whose ``book`` looks like a Python dict string."""
    return [
        (int(row["id"]), str(row["word"]), str(row["book"]))
        for row in conn.execute(
            "SELECT id, word, book FROM word_usage WHERE book LIKE '{%'"
        )
    ]


def parse_corrupt(
    rows: list[tuple[int, str, str]], book_codes: set[str]
) -> list[tuple[int, str, str, str]]:
    """Validate corrupt rows before anything is deleted.

    Every row must be an ``ast.literal_eval``-able dict carrying a ``book`` that
    is one of the Bible codes.  Anything else aborts the run — we never drop
    data we do not understand.

    Returns:
        ``(id, word, book, inner_book)`` tuples for the rows that are safe to
        delete (they are relearned from ``bible_verses``).

    Raises:
        CorruptParseError: at least one row is unparseable or unknown.
    """
    parsed: list[tuple[int, str, str, str]] = []
    bad: list[str] = []
    for row_id, word, raw in rows:
        try:
            payload = ast.literal_eval(raw)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
            bad.append(f"id={row_id} word={word!r} unparseable ({exc}): {raw[:80]!r}")
            continue
        if not isinstance(payload, dict):
            bad.append(f"id={row_id} word={word!r} not a dict: {raw[:80]!r}")
            continue
        inner = payload.get("book")
        if not isinstance(inner, str) or inner not in book_codes:
            bad.append(
                f"id={row_id} word={word!r} inner book {inner!r} is not a Bible code"
            )
            continue
        parsed.append((row_id, word, raw, inner))
    if bad:
        shown = "; ".join(bad[:5])
        raise CorruptParseError(
            f"{len(bad)} corrupt word_usage row(s) are not parseable into a Bible "
            f"book code — aborting (nothing deleted). {shown}"
        )
    return parsed


# ── aggregation over bible_verses ───────────────────────────────────────────


def _iter_verses(conn: sqlite3.Connection, book_codes: set[str]):
    """Yield ``(ref, book, token_set, myanmar)`` once per distinct ref.

    Document-frequency semantics: the five ZO translation columns of one verse
    contribute each distinct word once, and a repeated ``ref`` row never counts
    twice.
    """
    cols = ", ".join(ZO_COLUMNS)
    seen_refs: set[str] = set()
    query = (
        f"SELECT ref, book, {cols}, myanmar, myanmar_judson FROM bible_verses"
        " ORDER BY id"
    )
    for row in conn.execute(query):
        ref = row["ref"]
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        book = row["book"]
        if book not in book_codes:
            continue
        words: set[str] = set()
        for col in ZO_COLUMNS:
            words.update(tokenize_words(row[col] or ""))
        if not words:
            continue
        myanmar = (row["myanmar"] or "").strip() or (row["myanmar_judson"] or "").strip()
        yield ref, book, words, (myanmar or None)


def _aggregate(
    conn: sqlite3.Connection, book_codes: set[str]
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], str]]:
    """Per-(word, book) document frequency + first non-empty Myanmar gloss."""
    counts: dict[tuple[str, str], int] = {}
    myanmar_seen: dict[tuple[str, str], str] = {}
    for _ref, book, words, myanmar in _iter_verses(conn, book_codes):
        for word in words:
            key = (word, book)
            counts[key] = counts.get(key, 0) + 1
            if myanmar and key not in myanmar_seen:
                myanmar_seen[key] = myanmar
    return counts, myanmar_seen


def _cooccurrence(
    conn: sqlite3.Connection,
    book_codes: set[str],
    counts: dict[tuple[str, str], int],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Verse co-occurrence for the top-300 words of each book (top-10 each)."""
    per_book: dict[str, Counter[str]] = {}
    for (word, book), freq in counts.items():
        per_book.setdefault(book, Counter())[word] = freq
    top_words: dict[str, set[str]] = {}
    for book, counter in per_book.items():
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        top_words[book] = {word for word, _ in ranked[:CO_OCCURRENCE_TOP_WORDS]}

    pairs: dict[tuple[str, str], Counter[str]] = {}
    for _ref, book, words, _myanmar in _iter_verses(conn, book_codes):
        targets = words & top_words.get(book, set())
        if not targets:
            continue
        for target in targets:
            counter = pairs.setdefault((target, book), Counter())
            counter.update(words - {target})

    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for key, counter in pairs.items():
        neighbours = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        out[key] = [
            {"word": word, "count": count}
            for word, count in neighbours[:CO_OCCURRENCE_TOP_NEIGHBOURS]
        ]
    return out


def _content_hash(word: str, book: str, freq: int) -> str:
    return hashlib.sha256(f"{word}|{book}|{freq}|1".encode()).hexdigest()


# ── main step ───────────────────────────────────────────────────────────────


def learn_bible(db_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Repair + rebuild in one transaction (or compute it without writing).

    Args:
        db_path: Target database.
        dry_run: Parse-validate and aggregate, but never write.

    Returns:
        Summary dict (row counts only).

    Raises:
        CorruptParseError: an unparseable/unknown corrupt row was found —
            raised before any write, and any later failure rolls the
            transaction back.
    """
    conn = _connect(db_path)
    try:
        book_codes = load_book_codes(conn)
        corrupt = scan_corrupt(conn)
        parse_corrupt(corrupt, book_codes)  # aborts before the DELETE

        before_total = _count(conn, "word_usage")
        before_by_book = dict(
            conn.execute("SELECT book, COUNT(*) FROM word_usage GROUP BY book").fetchall()
        )
        sentinel_before = {
            book: count
            for book, count in before_by_book.items()
            if book not in book_codes and not book.startswith("{")
        }
        bible_rows_before = before_total - sum(sentinel_before.values()) - len(corrupt)

        if dry_run:
            counts, _myanmar = _aggregate(conn, book_codes)
            books_after = {book for _w, book in counts}
            return {
                "dry_run": True,
                "before_total": before_total,
                "after_total": before_total - len(corrupt) - bible_rows_before + len(counts),
                "corrupt_before": len(corrupt),
                "corrupt_after": 0,
                "deleted_rows": 0,
                "inserted_rows": len(counts),
                "would_delete": len(corrupt) + bible_rows_before,
                "would_insert": len(counts),
                "books_covered": len(books_after & book_codes),
                "zero_books": sorted(book_codes - books_after),
                "sentinels": sentinel_before,
                "per_book": {},
            }

        conn.execute("BEGIN IMMEDIATE")
        try:
            placeholders = ",".join("?" for _ in book_codes)
            params = sorted(book_codes)
            deleted = conn.execute(
                f"DELETE FROM word_usage WHERE book IN ({placeholders})"
                " OR book LIKE '{%'",
                params,
            ).rowcount
            counts, myanmar_seen = _aggregate(conn, book_codes)
            cooccurrence = _cooccurrence(conn, book_codes, counts)
            rows = [
                (
                    word,
                    book,
                    freq,
                    "[]",
                    json.dumps(
                        cooccurrence.get((word, book), []), ensure_ascii=False
                    ),
                    myanmar_seen.get((word, book)),
                    1,
                    _content_hash(word, book, freq),
                )
                for (word, book), freq in sorted(counts.items())
            ]
            conn.executemany(
                "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
                " co_occurring_words, myanmar, version, content_hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        after_total = _count(conn, "word_usage")
        corrupt_after = int(
            conn.execute(
                "SELECT COUNT(*) FROM word_usage WHERE book LIKE '{%'"
            ).fetchone()[0]
        )
        after_by_book = dict(
            conn.execute("SELECT book, COUNT(*) FROM word_usage GROUP BY book").fetchall()
        )
        sentinel_after = {
            book: count
            for book, count in after_by_book.items()
            if book not in book_codes and not book.startswith("{")
        }
        per_book = {
            book: after_by_book.get(book, 0) for book in sorted(book_codes)
        }
        zero_books = sorted(book for book, count in per_book.items() if count == 0)
        return {
            "dry_run": False,
            "before_total": before_total,
            "after_total": after_total,
            "corrupt_before": len(corrupt),
            "corrupt_after": corrupt_after,
            "deleted_rows": int(deleted),
            "inserted_rows": len(rows),
            "books_covered": sum(1 for count in per_book.values() if count > 0),
            "zero_books": zero_books,
            "sentinels": sentinel_after,
            "sentinels_unchanged": sentinel_after == sentinel_before,
            "per_book": per_book,
        }
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair corrupt word_usage rows and rebuild Bible word usage"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="target sqlite database (default: canonical zolai.db from config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse-validate and aggregate without writing a single row",
    )
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db)
    else:
        from zolai.config import config

        db_path = Path(config.paths.zolai_db)

    try:
        preflight(db_path)
        summary = learn_bible(db_path, dry_run=args.dry_run)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.code) if isinstance(exc.code, int) else 2
    except CorruptParseError as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if summary["dry_run"] else "WROTE"
    print(f"[G1] {mode} word_usage {db_path}")
    print(f"  before/after total : {summary['before_total']:,} → {summary['after_total']:,}")
    print(f"  corrupt rows       : {summary['corrupt_before']:,} → {summary['corrupt_after']:,}")
    print(f"  books covered      : {summary['books_covered']} (zero: {summary['zero_books']})")
    print(f"  sentinels          : {summary['sentinels']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
