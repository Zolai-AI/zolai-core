"""RED-first suite for the Bible grammar-pattern learning step (G5).

Synthetic ``tmp_path`` fixtures only — the live database is never opened.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "scripts.learning.learn_grammar_from_bible"

BIBLE_VERSES_DDL = """
CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    zo_tdb77 TEXT,
    en_kJV TEXT,
    myanmar TEXT,
    version INTEGER DEFAULT 1
)
"""

# Mirrors the live grammar_patterns table, including both unique pattern_id indexes.
GRAMMAR_PATTERNS_DDL = """
CREATE TABLE grammar_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL,
    pattern TEXT NOT NULL,
    description TEXT,
    function TEXT NOT NULL,
    examples TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT,
    normalized TEXT,
    components TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'OBSERVED',
    book TEXT NOT NULL DEFAULT ""
);
CREATE UNIQUE INDEX uniq_gp_v2_pattern_id ON grammar_patterns(pattern_id);
CREATE UNIQUE INDEX ix_grammar_pattern_id_unique ON grammar_patterns(pattern_id);
CREATE INDEX ix_grammar_book ON grammar_patterns(book);
"""

# ref, zo_tdb77, en_kJV — six verses across five pattern types
VERSES = [
    ("GEN 1:1", "GEN 1:1", "Pasian in a gam hi.", "God created the world"),
    ("EXO 1:1", "EXO 1:1", "Hiam a khat hi?", "Who is one?"),
    ("LEV 1:1", "LEV 1:1", "Kei a pai hi.", "Does not go"),
    ("NUM 1:1", "NUM 1:1", "Pasian a gam hi.", "God works"),
    ("DEU 1:1", "DEU 1:1", "Numei a gam hi.", "The woman works"),
    ("JOS 1:1", "JOS 1:1", "A ci hi.", "He says so"),
]

EXISTING_ROW = (
    "CORPUS_sov",
    "S + O + V",
    "sov",
    "[]",
    42,
    '["bible"]',
    0.5,
    "",
)


def _load():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:  # pragma: no cover — RED until implemented
        pytest.fail(f"{MODULE} not implemented yet: {exc}")


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(BIBLE_VERSES_DDL)
        conn.executescript(GRAMMAR_PATTERNS_DDL)
        conn.executemany(
            "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77, en_kJV)"
            " VALUES (?,?,?,?,?,?)",
            [(ref, ref.split()[0], 1, int(ref.split()[1].split(":")[1]), zo, en)
             for ref, _b, zo, en in VERSES],
        )
        conn.execute(
            "INSERT INTO grammar_patterns (pattern_id, pattern, function, examples,"
            " frequency, sources, confidence, book) VALUES (?,?,?,?,?,?,?,?)",
            EXISTING_ROW,
        )
        conn.commit()
    finally:
        conn.close()


def _bible_rows(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM grammar_patterns WHERE book='BIBLE' ORDER BY function"
        ).fetchall()
    finally:
        conn.close()


def _count(path: Path, where: str = "1=1") -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM grammar_patterns WHERE {where}").fetchone()[0]
    finally:
        conn.close()


# ── preflight guard ─────────────────────────────────────────────────────────


def test_preflight_refuses_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"")
    with pytest.raises(SystemExit) as excinfo:
        mod.preflight(decoy)
    assert "GB" in str(excinfo.value)


def test_cli_refuses_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"\0" * 4)
    assert mod.main(["--db", str(decoy)]) != 0


# ── learn + write ───────────────────────────────────────────────────────────


def test_learns_patterns_from_db_backed_verses(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.learn(str(db))
    rows = _bible_rows(db)
    assert rows, "book='BIBLE' rows must be written"
    assert summary["written"] == len(rows)
    assert summary["verses"] == 6
    functions = {r["function"] for r in rows}
    assert functions == {"ergative", "question", "negation", "sov", "quotative"}
    assert sum(r["frequency"] for r in rows) == 6
    sov = [r for r in rows if r["function"] == "sov"][0]
    assert sov["frequency"] == 2


def test_row_contract_matches_plan(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn(str(db))
    for row in _bible_rows(db):
        assert row["book"] == "BIBLE"
        assert json.loads(row["sources"]) == ["bible"]
        examples = json.loads(row["examples"])
        assert isinstance(examples, list) and examples
        assert all(set(item) >= {"zolai"} for item in examples)
        assert row["confidence"] is not None and 0 < row["confidence"] <= 1
        assert row["status"] == "OBSERVED"


def test_existing_empty_book_rows_untouched(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn(str(db))
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT pattern_id, frequency, book FROM grammar_patterns"
            " WHERE pattern_id='CORPUS_sov'"
        ).fetchone()
        empty_book = conn.execute(
            "SELECT COUNT(*) FROM grammar_patterns WHERE book=''"
        ).fetchone()[0]
    finally:
        conn.close()
    assert row == ("CORPUS_sov", 42, "")
    assert empty_book == 1


def test_pattern_id_is_unique_and_never_null(tmp_path: Path) -> None:
    """``pattern_id`` is NOT NULL in DDL, so the plan's NULL sentinel is impossible.

    The script uses a unique ``BIBLE_<type>`` id instead — pinned here so the
    unique indexes on ``pattern_id`` can never fire.
    """
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn(str(db))
    conn = sqlite3.connect(db)
    try:
        total, distinct, nulls = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT pattern_id), SUM(pattern_id IS NULL)"
            " FROM grammar_patterns"
        ).fetchone()
    finally:
        conn.close()
    assert nulls == 0
    assert total == distinct, "pattern_id must stay unique across both namespaces"


def test_rerun_is_count_stable(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    first = mod.learn(str(db))
    second = mod.learn(str(db))
    assert first["written"] == second["written"]
    assert second["deleted_previous"] >= 0
    assert _count(db, "book='BIBLE'") == first["written"]
    assert _count(db) == first["written"] + 1  # + the untouched CORPUS row


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.learn(str(db), dry_run=True)
    assert summary["dry_run"] is True
    assert summary["would_write"] == 5
    assert summary["written"] == 0
    assert _count(db, "book='BIBLE'") == 0


def test_aggregate_rows_picks_common_structure_and_confidence() -> None:
    mod = _load()
    from zolai.learning.bible_pattern_learner import SentencePattern

    patterns = {
        "sov": [
            SentencePattern("sov", "S + O + V", "a hi", "A"),
            SentencePattern("sov", "S + O + V", "b hi", "B"),
            SentencePattern("sov", "S + V", "c hi", "C"),
        ],
        "question": [SentencePattern("question", "S + V + hiam?", "d hiam?", "D")],
    }
    rows = mod.aggregate_rows(patterns)
    assert len(rows) == 2
    by_type = {row["function"]: row for row in rows}
    sov = by_type["sov"]
    assert sov["pattern"] == "S + O + V"
    assert sov["frequency"] == 3
    assert sov["confidence"] == pytest.approx(0.75)
    assert sov["pattern_id"] == "BIBLE_sov"
    assert len(json.loads(sov["examples"])) == 3
    assert by_type["question"]["frequency"] == 1
    assert by_type["question"]["confidence"] == pytest.approx(0.25)
