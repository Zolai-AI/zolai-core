"""RED-first suite for the G1 word_usage corruption repair + Bible rebuild.

Every fixture is a synthetic sqlite file built in ``tmp_path``.  The 2.3 GB live
database is never opened by this suite — the preflight guard tests prove that a
sub-1 GB target (e.g. the 0-byte repo-local decoy) cannot be written either.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "scripts.learning.repair_and_learn_bible_word_usage"

# ── fixtures DDL (mirrors the live tables the repair touches) ───────────────

BIBLE_VERSES_DDL = """
CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    zo_tdb77 TEXT,
    zo_tedim2010 TEXT,
    en_kJV TEXT,
    myanmar TEXT,
    zo_tedim1932 TEXT,
    zo_hcl06 TEXT,
    zo_fcl TEXT,
    myanmar_judson TEXT,
    book_name TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
)
"""

WORD_USAGE_DDL = """
CREATE TABLE word_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    book TEXT NOT NULL,
    total_freq INTEGER NOT NULL,
    meaning_shifts TEXT NOT NULL,
    co_occurring_words TEXT NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE UNIQUE INDEX ix_usage_word_book_unique ON word_usage(word, book);
CREATE UNIQUE INDEX uniq_wu_v2_word_book_meaning ON word_usage(word, book, meaning_shifts);
"""

# (ref, book, chapter, verse, tdb77, tedim2010, tedim1932, hcl06, fcl, en_kjv,
#  myanmar, myanmar_judson)
VERSES = [
    (
        "GEN 1:1", "GEN", 1, 1,
        "Pasian in a ziak hi.", "Pasian in a ziak hi.", "Pasian in a ziak hi.",
        "Pasian in a ziak hi.", "Pasian in a ziak hi.",
        "In the beginning God created", "mm-gen-1-1", "",
    ),
    ("GEN 1:2", "GEN", 1, 2, "A hi le a ne hi.", "", "", "", "",
     "the earth was", "", "mm-gen-1-2-judson"),
    ("JHN 1:1", "JHN", 1, 1, "Hiam a khat a hi?", "", "", "", "",
     "who was", "", ""),
    ("JUD 1:1", "JUD", 1, 1, "Kei a pai hi.", "", "", "", "",
     "keep yourself", "", ""),
    ("OBA 1:1", "OBA", 1, 1, "Pasian a hi.", "", "", "", "",
     "thus saith", "", ""),
    ("TIT 1:1", "TIT", 1, 1, "Tit a ka ne hi.", "", "", "", "",
     "a servant of God", "", ""),
]

SENTINELS = [
    ("zomidaily-word", "ZOMIDAILY", 900),
    ("phrase-word", "phrase:bigram", 700),
    ("phrase-word-3", "phrase:trigram", 500),
    ("bible-sentinel", "BIBLE", 400),
    ("meaning-shift-word", "MEANING_SHIFT", 300),
    ("zomidaily-articles", "ZOMIDAILY_ARTICLES", 200),
]

CORRUPT_GEN = (
    "{'book': 'GEN', 'book_name': 'Genesis', 'frequency': 1, "
    "'top_translations': [], 'co_occurring_words': []}"
)
CORRUPT_JHN = (
    "{'book': 'JHN', 'book_name': 'John', 'frequency': 2, "
    "'top_translations': [], 'co_occurring_words': []}"
)


def _load():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:  # pragma: no cover — RED until implemented
        pytest.fail(f"{MODULE} not implemented yet: {exc}")


def _build_db(path: Path, *, extra_corrupt: list[str] | None = None) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(BIBLE_VERSES_DDL)
        conn.executescript(WORD_USAGE_DDL)
        conn.executemany(
            "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77,"
            " zo_tedim2010, zo_tedim1932, zo_hcl06, zo_fcl, en_kJV, myanmar,"
            " myanmar_judson) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            VERSES,
        )
        conn.execute(
            "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
            " co_occurring_words) VALUES (?,?,?,?,?)",
            ("antang", "GEN", 10, "[]", "[]"),
        )
        conn.executemany(
            "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
            " co_occurring_words) VALUES (?,?,?,?,?)",
            [(word, book, freq, "[]", "[]") for word, book, freq in SENTINELS],
        )
        conn.execute(
            "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
            " co_occurring_words) VALUES (?,?,?,?,?)",
            ("old-corrupt-1", CORRUPT_GEN, 1, "[]", "[]"),
        )
        conn.execute(
            "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
            " co_occurring_words) VALUES (?,?,?,?,?)",
            ("old-corrupt-2", CORRUPT_JHN, 1, "[]", "[]"),
        )
        for n, book in enumerate(extra_corrupt or []):
            conn.execute(
                "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
                " co_occurring_words) VALUES (?,?,?,?,?)",
                (f"bad-{n}", book, 1, "[]", "[]"),
            )
        conn.commit()
    finally:
        conn.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        out = {
            "total": conn.execute("SELECT COUNT(*) FROM word_usage").fetchone()[0],
            "corrupt": conn.execute(
                "SELECT COUNT(*) FROM word_usage WHERE book LIKE '{%'"
            ).fetchone()[0],
            "sentinels": conn.execute(
                "SELECT COUNT(*) FROM word_usage WHERE book NOT LIKE '{%'"
                " AND book NOT IN (SELECT DISTINCT book FROM bible_verses)"
            ).fetchone()[0],
        }
        return out
    finally:
        conn.close()


# ── preflight guard ─────────────────────────────────────────────────────────


def test_preflight_refuses_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"")  # the 0-byte repo-local decoy
    with pytest.raises(SystemExit) as excinfo:
        mod.preflight(decoy)
    assert "GB" in str(excinfo.value)


def test_cli_refuses_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"\0" * 1024)
    assert mod.main(["--db", str(decoy)]) != 0


def test_cli_subprocess_refuses_decoy(tmp_path: Path) -> None:
    """End-to-end: the script binary itself must refuse a tiny target."""
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"")
    script = REPO_ROOT / "scripts" / "learning" / "repair_and_learn_bible_word_usage.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--db", str(decoy)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "GB" in (proc.stdout + proc.stderr)


# ── repair + rebuild ────────────────────────────────────────────────────────


def test_corrupt_dict_string_rows_are_deleted(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.learn_bible(str(db))
    assert _counts(db)["corrupt"] == 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM word_usage WHERE word LIKE 'old-corrupt%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
    assert summary["corrupt_before"] == 2
    assert summary["corrupt_after"] == 0


def test_unparseable_corrupt_row_aborts_rollback(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db, extra_corrupt=["{this is not parseable"])
    before = _counts(db)
    with pytest.raises(mod.CorruptParseError):
        mod.learn_bible(str(db))
    assert _counts(db) == before, "abort must leave every row in place"


def test_parseable_but_unknown_book_aborts(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db, extra_corrupt=["{'book': 'NOT_A_BIBLE_BOOK', 'frequency': 1}"])
    before = _counts(db)
    with pytest.raises(mod.CorruptParseError):
        mod.learn_bible(str(db))
    assert _counts(db) == before


def test_mid_run_failure_rolls_back_deletes(tmp_path: Path, monkeypatch) -> None:
    """A failure after the DELETE must restore the pre-run rows."""
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    before = _counts(db)

    def _boom(_text):  # pragma: no cover — failure injection
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(mod, "tokenize_words", _boom)
    with pytest.raises(RuntimeError):
        mod.learn_bible(str(db))
    assert _counts(db) == before, "failed rebuild must roll back to the baseline"


def test_sentinel_books_untouched(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn_bible(str(db))
    conn = sqlite3.connect(db)
    try:
        rows = dict(conn.execute(
            "SELECT book, COUNT(*) FROM word_usage WHERE book NOT LIKE '{%'"
            " AND book NOT IN (SELECT DISTINCT book FROM bible_verses)"
            " GROUP BY book"
        ).fetchall())
    finally:
        conn.close()
    assert rows == {book: 1 for _, book, _ in SENTINELS}


def test_doc_frequency_prevents_translation_inflation(tmp_path: Path) -> None:
    """GEN 1:1 carries 'pasian' in all five ZO columns → counted once."""
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn_bible(str(db))
    conn = sqlite3.connect(db)
    try:
        gen = conn.execute(
            "SELECT total_freq FROM word_usage WHERE word='pasian' AND book='GEN'"
        ).fetchone()
        oba = conn.execute(
            "SELECT total_freq FROM word_usage WHERE word='pasian' AND book='OBA'"
        ).fetchone()
    finally:
        conn.close()
    assert gen is not None and gen[0] == 1
    assert oba is not None and oba[0] == 1


def test_every_book_covered_including_previously_zero(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn_bible(str(db))
    conn = sqlite3.connect(db)
    try:
        covered = {
            r[0]
            for r in conn.execute("SELECT DISTINCT book FROM word_usage").fetchall()
            if r[0] in {"GEN", "JHN", "JUD", "OBA", "TIT"}
        }
        depth = dict(conn.execute(
            "SELECT book, COUNT(*) FROM word_usage"
            " WHERE book IN ('JUD','OBA','TIT') GROUP BY book"
        ).fetchall())
    finally:
        conn.close()
    assert covered == {"GEN", "JHN", "JUD", "OBA", "TIT"}
    assert set(depth) == {"JUD", "OBA", "TIT"}
    assert all(v > 0 for v in depth.values())


def test_rerun_is_count_stable(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    first = mod.learn_bible(str(db))
    second = mod.learn_bible(str(db))
    assert first["after_total"] == second["after_total"]
    assert second["corrupt_before"] == 0
    mod.learn_bible(str(db))

    def _snapshot() -> list[tuple]:
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "SELECT word, book, total_freq FROM word_usage ORDER BY word, book"
            ).fetchall()
        finally:
            conn.close()

    again = _snapshot()
    assert len(again) == first["after_total"]
    # no duplicate (word, book) pair may ever exist (unique index contract)
    assert len(set((w, b) for w, b, _ in again)) == len(again)


def test_integrity_check_clean_after_rebuild(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn_bible(str(db))
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_myanmar_and_cooccurrence_populated(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.learn_bible(str(db))
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT myanmar, co_occurring_words, meaning_shifts, version,"
            " content_hash FROM word_usage WHERE word='pasian' AND book='GEN'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    myanmar, co_occurring, meaning_shifts, version, content_hash = row
    assert myanmar == "mm-gen-1-1"
    assert meaning_shifts == "[]"
    assert version == 1
    assert content_hash and len(content_hash) == 64
    co = json.loads(co_occurring)
    assert isinstance(co, list) and co, co
    assert all(set(item) == {"word", "count"} for item in co)
    assert all(item["count"] >= 1 for item in co)
    words = {item["word"] for item in co}
    assert "ziak" in words


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    before = _counts(db)
    summary = mod.learn_bible(str(db), dry_run=True)
    assert summary["dry_run"] is True
    assert summary["corrupt_before"] == 2
    assert summary["inserted_rows"] > 0
    assert _counts(db) == before, "dry-run must not touch a single row"
