#!/usr/bin/env python3
"""G5 — learn real Zolai grammar patterns from the Bible into ``grammar_patterns``.

Uses the clean, DB-backed :class:`~zolai.learning.bible_pattern_learner.BiblePatternLearner`
(``zo_tdb77`` + ``en_kJV`` from ``bible_verses``) to extract every sentence
pattern, then aggregates:

    pattern      = the most frequent abstract structure of that pattern type
    function     = the pattern type (question / negation / ergative / sov / …)
    frequency    = number of verses classified as that type
    examples     = JSON list of the first EXAMPLE_LIMIT verses (zolai + english)
    sources      = ["bible"]
    confidence   = verses of this type / all patterned verses
    book         = 'BIBLE' idempotency sentinel

The write is a single transaction: ``DELETE … WHERE book='BIBLE'`` followed by
the INSERT, so a rerun is count-stable.  The 6,166 existing rows with an empty
``book`` are untouched, and ``pattern_id`` gets a unique ``BIBLE_<type>`` value
(the column is ``NOT NULL`` with two unique indexes, so a NULL sentinel is
impossible — pinned by tests).

Local only: stdlib sqlite3, no network, no external AI provider calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # allows `python scripts/learning/…py`
    sys.path.insert(0, str(_REPO_ROOT))

from zolai.learning.bible_pattern_learner import BiblePatternLearner  # noqa: E402

MIN_TARGET_BYTES = 1_000_000_000
BUSY_TIMEOUT_MS = 60_000

#: Idempotency namespace — no other writer uses this ``book`` value.
BIBLE_BOOK_SENTINEL = "BIBLE"
EXAMPLE_LIMIT = 5


# ── preflight / connection ──────────────────────────────────────────────────


def preflight(db_path: str | Path, *, min_bytes: int = MIN_TARGET_BYTES) -> Path:
    """Refuse missing / sub-1 GB targets (the 0-byte decoy can never be written)."""
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"[G5] target database not found: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise SystemExit(
            f"[G5] target database {path} is {size:,} bytes — refusing: "
            f"a real target must be ≥1 GB ({min_bytes:,} bytes)."
        )
    return path


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # isolation_level=None → we own BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def build_learner(db_path: str | Path) -> BiblePatternLearner:
    """Construct the stock learner with its verses loaded from ``db_path``.

    ``BiblePatternLearner.__init__`` binds the *canonical* database (and prints
    a load line), which tests and multi-DB runs must never touch — so the
    instance is created without ``__init__`` and the exact same attributes are
    populated from the requested database instead.
    """
    learner = BiblePatternLearner.__new__(BiblePatternLearner)
    learner.verses = []
    learner.patterns = defaultdict(list)
    learner.word_freq = Counter()
    learner.phrase_patterns = Counter()

    conn = _connect(db_path)
    try:
        for ref, zo_tdb77, en_kjv in conn.execute(
            "SELECT ref, zo_tdb77, en_kJV FROM bible_verses ORDER BY id"
        ):
            zo = zo_tdb77 or ""
            if zo.strip():
                learner.verses.append(
                    {"ref": ref or "", "zo_tdb77": zo, "en_kJV": en_kjv or ""}
                )
    finally:
        conn.close()
    return learner


# ── aggregation ─────────────────────────────────────────────────────────────


def aggregate_rows(
    patterns: dict[str, list], *, example_limit: int = EXAMPLE_LIMIT
) -> list[dict[str, Any]]:
    """Collapse per-verse patterns into one insert-ready row per pattern type."""
    total = sum(len(items) for items in patterns.values())
    rows: list[dict[str, Any]] = []
    for pattern_type in sorted(patterns):
        items = patterns[pattern_type]
        if not items:
            continue
        frequency = len(items)
        structures = Counter(item.structure for item in items)
        structure = sorted(structures.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        examples = [
            {"zolai": item.example_zo, "english": item.example_en}
            for item in items[:example_limit]
        ]
        rows.append(
            {
                "pattern_id": f"{BIBLE_BOOK_SENTINEL}_{pattern_type}",
                "pattern": structure,
                "description": (
                    f"Learned from Bible: {pattern_type} pattern ({frequency} verses)"
                ),
                "function": pattern_type,
                "examples": json.dumps(examples, ensure_ascii=False),
                "frequency": frequency,
                "sources": json.dumps(["bible"]),
                "confidence": round(frequency / total, 4) if total else 0.0,
                "book": BIBLE_BOOK_SENTINEL,
            }
        )
    return rows


def _content_hash(row: dict[str, Any]) -> str:
    payload = f"{row['pattern_id']}|{row['pattern']}|{row['function']}|{row['frequency']}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ── main step ───────────────────────────────────────────────────────────────


def learn(
    db_path: str | Path,
    *,
    dry_run: bool = False,
    learner: BiblePatternLearner | None = None,
) -> dict[str, Any]:
    """Extract Bible sentence patterns and write them under ``book='BIBLE'``.

    Args:
        db_path: Target database (source of ``bible_verses`` and the write).
        dry_run: Extract + aggregate without writing.
        learner: Optional pre-built learner (tests inject one); by default the
            verses are loaded from ``db_path``.

    Returns:
        Summary dict (row counts only).
    """
    active = learner if learner is not None else build_learner(db_path)
    if not getattr(active, "patterns", None):
        active.extract_patterns()
    rows = aggregate_rows(active.patterns)
    verses = len(getattr(active, "verses", []))

    if dry_run:
        return {
            "dry_run": True,
            "verses": verses,
            "types": len(rows),
            "frequency_total": sum(row["frequency"] for row in rows),
            "deleted_previous": 0,
            "would_write": len(rows),
            "written": 0,
        }

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            deleted = conn.execute(
                "DELETE FROM grammar_patterns WHERE book = ?",
                (BIBLE_BOOK_SENTINEL,),
            ).rowcount
            conn.executemany(
                "INSERT INTO grammar_patterns (pattern_id, pattern, description,"
                " function, examples, frequency, sources, confidence, book,"
                " content_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        row["pattern_id"],
                        row["pattern"],
                        row["description"],
                        row["function"],
                        row["examples"],
                        row["frequency"],
                        row["sources"],
                        row["confidence"],
                        row["book"],
                        _content_hash(row),
                    )
                    for row in rows
                ],
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        written = int(
            conn.execute(
                "SELECT COUNT(*) FROM grammar_patterns WHERE book = ?",
                (BIBLE_BOOK_SENTINEL,),
            ).fetchone()[0]
        )
        return {
            "dry_run": False,
            "verses": verses,
            "types": len(rows),
            "frequency_total": sum(row["frequency"] for row in rows),
            "deleted_previous": int(deleted),
            "would_write": len(rows),
            "written": written,
        }
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Learn grammar patterns from the Bible into grammar_patterns (book=BIBLE)"
    )
    parser.add_argument("--db", default=None, help="target sqlite database (default: canonical)")
    parser.add_argument("--dry-run", action="store_true", help="extract only, write nothing")
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db)
    else:
        from zolai.config import config

        db_path = Path(config.paths.zolai_db)

    try:
        preflight(db_path)
        summary = learn(db_path, dry_run=args.dry_run)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.code) if isinstance(exc.code, int) else 2
    except (RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if summary["dry_run"] else "WROTE"
    print(f"[G5] {mode} grammar_patterns (book={BIBLE_BOOK_SENTINEL}) {db_path}")
    print(f"  verses              : {summary['verses']:,}")
    print(f"  pattern types       : {summary['types']} (freq total {summary['frequency_total']:,})")
    print(f"  deleted previous    : {summary['deleted_previous']}")
    print(f"  written rows        : {summary['written']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
