"""RED-first suite for the bible_verses_import dedupe script (G2).

Synthetic ``tmp_path`` fixtures only — the live database is never opened.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "scripts.maintenance.dedupe_bible_verses_import"

# The live bible_verses_import schema (no id column, no unique key on ref).
IMPORT_DDL = """
CREATE TABLE bible_verses_import (
  "book" TEXT,
  "book_name" TEXT,
  "chapter" TEXT,
  "verse" TEXT,
  "ref" TEXT,
  "zo_tdb77" TEXT,
  "zo_tedim2010" TEXT,
  "en_kJV" TEXT,
  "import_batch_id" TEXT,
  "source_file" TEXT,
  "version" INTEGER DEFAULT 1,
  "imported_at" TEXT
);
CREATE INDEX idx_bible_verses_import_batch ON "bible_verses_import"(import_batch_id);
"""

# ref, zo_tdb77, zo_tedim2010, en_kjv, batch, imported_at
ROWS = [
    ("GEN 1:1", "a b c", "", "x", "batch-1", "2026-01-05"),
    ("GEN 1:1", "a b c", "d e", "x y", "batch-2", "2026-01-04"),
    ("GEN 1:1", "a b", "", "x", "batch-3", "2026-01-03"),
    ("JHN 1:1", "", "", "only en", "batch-1", "2026-02-02"),
    ("JHN 1:1", "", "", "only en", "batch-4", "2026-02-01"),
    ("OBA 1:1", "oba zo", "", "oba en", "batch-1", "2026-03-01"),
]


def _load():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:  # pragma: no cover — RED until implemented
        pytest.fail(f"{MODULE} not implemented yet: {exc}")


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(IMPORT_DDL)
        conn.executemany(
            "INSERT INTO bible_verses_import (ref, zo_tdb77, zo_tedim2010,"
            " en_kJV, import_batch_id, imported_at) VALUES (?,?,?,?,?,?)",
            ROWS,
        )
        conn.commit()
    finally:
        conn.close()


def _rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT ref, zo_tdb77, zo_tedim2010, en_kJV, imported_at"
            " FROM bible_verses_import ORDER BY ref, imported_at"
        ).fetchall()
    finally:
        conn.close()


def _has_unique_index(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name='ux_bible_verses_import_ref'"
        ).fetchone()
        return row is not None
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
    decoy.write_bytes(b"\0" * 16)
    assert mod.main(["--db", str(decoy)]) != 0


# ── dedupe rules ────────────────────────────────────────────────────────────


def test_keeps_highest_scoring_row_per_ref(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.dedupe(str(db))
    rows = _rows(db)
    assert len(rows) == 3
    gen = [r for r in rows if r[0] == "GEN 1:1"]
    assert len(gen) == 1
    # score = non-empty ZO/EN columns: batch-2 has tdb77 + tedim2010 + en_kJV
    assert gen[0][1] == "a b c" and gen[0][2] == "d e" and gen[0][3] == "x y"


def test_score_tie_keeps_earliest_imported_at(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.dedupe(str(db))
    jhn = [r for r in _rows(db) if r[0] == "JHN 1:1"]
    assert len(jhn) == 1
    assert jhn[0][4] == "2026-02-01"


def test_distinct_ref_count_matches_and_unique_index_created(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.dedupe(str(db))
    conn = sqlite3.connect(db)
    try:
        total, distinct = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ref) FROM bible_verses_import"
        ).fetchone()
    finally:
        conn.close()
    assert total == distinct == 3
    assert summary["before"] == 6
    assert summary["after"] == 3
    assert summary["deleted"] == 3
    assert _has_unique_index(db)


def test_unique_index_blocks_future_duplicate_imports(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.dedupe(str(db))
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bible_verses_import (ref, import_batch_id)"
                " VALUES ('GEN 1:1', 'batch-9')"
            )
    finally:
        conn.close()


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.dedupe(str(db))
    second = mod.dedupe(str(db))
    assert second["deleted"] == 0
    assert second["after"] == 3
    assert _has_unique_index(db)
    assert len(_rows(db)) == 3


def test_dry_run_deletes_nothing_and_creates_no_index(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.dedupe(str(db), dry_run=True)
    assert summary["dry_run"] is True
    assert summary["deleted"] == 0
    assert summary["would_delete"] == 3
    assert len(_rows(db)) == 6
    assert not _has_unique_index(db)


def test_empty_table_is_a_no_op(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    conn = sqlite3.connect(db)
    conn.executescript(IMPORT_DDL)
    conn.commit()
    conn.close()
    summary = mod.dedupe(str(db))
    assert summary["before"] == 0
    assert summary["deleted"] == 0
    assert _has_unique_index(db)
