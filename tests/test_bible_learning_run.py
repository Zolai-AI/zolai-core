"""RED-first suite for the Bible learning run orchestrator.

Synthetic ``tmp_path`` fixtures only — the live 2.3 GB database is never opened
here.  The orchestrator's backup / transaction / dry-run logic is exercised on
the fixture database.
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

MODULE = "scripts.learning.bible_learning_run"

DDL = """
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
    myanmar_judson TEXT
);
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
CREATE TABLE bible_verses_import (
  "book" TEXT, "book_name" TEXT, "chapter" TEXT, "verse" TEXT, "ref" TEXT,
  "zo_tdb77" TEXT, "zo_tedim2010" TEXT, "en_kJV" TEXT, "import_batch_id" TEXT,
  "source_file" TEXT, "version" INTEGER DEFAULT 1, "imported_at" TEXT
);
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    english TEXT NOT NULL,
    frequency INTEGER NOT NULL DEFAULT 0,
    books TEXT NOT NULL DEFAULT '[]',
    examples TEXT NOT NULL DEFAULT '[]',
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    review_status TEXT DEFAULT 'unknown',
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'OBSERVED'
);
CREATE TABLE attestation_index (
    word TEXT NOT NULL, source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (word, source)
);
CREATE TABLE word_observation_stats (
    normalized_form TEXT PRIMARY KEY,
    frequency INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    attestation TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE word_alignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL, zolai_word TEXT NOT NULL,
    english_word TEXT NOT NULL, position INTEGER NOT NULL
);
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL, english TEXT, english_clean TEXT,
    is_deleted INTEGER DEFAULT 0
);
CREATE TABLE zolai_vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL, english TEXT, confidence REAL
);
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
"""

CORRUPT_GEN = (
    "{'book': 'GEN', 'book_name': 'Genesis', 'frequency': 1,"
    " 'top_translations': [], 'co_occurring_words': []}"
)

TOUCHED_TABLES = (
    "word_usage",
    "bible_verses_import",
    "vocabulary",
    "grammar_patterns",
    "attestation_index",
)


def _load():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:  # pragma: no cover — RED until implemented
        pytest.fail(f"{MODULE} not implemented yet: {exc}")


def _build_db(path: Path, *, with_bad_corrupt: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77,"
            " en_kJV, myanmar) VALUES (?,?,?,?,?,?,?)",
            [
                ("GEN 1:1", "GEN", 1, 1, "Pasian in a gam hi.", "God created", "mm-gen"),
                ("GEN 1:2", "GEN", 1, 2, "A hi le a ne hi.", "was", ""),
                ("JUD 1:1", "JUD", 1, 1, "Kei a pai hi.", "do not go", ""),
                ("OBA 1:1", "OBA", 1, 1, "Hiam a khat hi?", "who", ""),
                ("TIT 1:1", "TIT", 1, 1, "Numei a gam hi.", "woman", ""),
                ("JOS 1:1", "JOS", 1, 1, "A ci hi.", "he says", ""),
            ],
        )
        conn.executemany(
            "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
            " co_occurring_words) VALUES (?,?,?,?,?)",
            [
                ("zomidaily-word", "ZOMIDAILY", 10, "[]", "[]"),
                ("phrase-word", "phrase:bigram", 8, "[]", "[]"),
                ("antang", "GEN", 4, "[]", "[]"),
                ("old-corrupt", CORRUPT_GEN, 1, "[]", "[]"),
            ],
        )
        if with_bad_corrupt:
            conn.execute(
                "INSERT INTO word_usage (word, book, total_freq, meaning_shifts,"
                " co_occurring_words) VALUES ('bad', '{oops', 1, '[]', '[]')"
            )
        conn.executemany(
            "INSERT INTO bible_verses_import (ref, zo_tdb77, zo_tedim2010, en_kJV,"
            " import_batch_id, imported_at) VALUES (?,?,?,?,?,?)",
            [
                ("GEN 1:1", "a b c", "d e", "x y", "batch-2", "2026-01-04"),
                ("GEN 1:1", "a b c", "", "x", "batch-1", "2026-01-05"),
                ("JUD 1:1", "", "", "only en", "batch-1", "2026-02-01"),
                ("JUD 1:1", "", "", "only en", "batch-3", "2026-02-02"),
            ],
        )
        conn.executemany(
            "INSERT INTO vocabulary (headword, english, review_status, confidence)"
            " VALUES (?,?,?,?)",
            [
                ("alpha", "alpha gloss", "unknown", None),
                ("beta", "beta gloss", "unknown", None),
                ("sanga", "31", "unknown", None),
                ("human", "human gloss", "reviewed", None),
            ],
        )
        conn.executemany(
            "INSERT INTO attestation_index (word, source) VALUES (?,?)",
            [("beta", "dict"), ("beta", "corpus"), ("human", "dict")],
        )
        conn.executemany(
            "INSERT INTO word_alignments (ref, zolai_word, english_word, position)"
            " VALUES (?,?,?,?)",
            [(f"GEN 1:{i}", "sanga", "thou", i) for i in range(1, 6)],
        )
        conn.execute(
            "INSERT INTO dictionary (zolai, english, english_clean) VALUES (?,?,?)",
            ("sanga", "31", None),
        )
        conn.execute(
            "INSERT INTO grammar_patterns (pattern_id, pattern, function, examples,"
            " frequency, sources, confidence, book) VALUES (?,?,?,?,?,?,?,?)",
            ("CORPUS_sov", "S + O + V", "sov", "[]", 42, '["bible"]', 0.5, ""),
        )
        conn.commit()
    finally:
        conn.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TOUCHED_TABLES
        }
    finally:
        conn.close()


def _stub_attestation(db_path, *, force: bool = True):
    return {"rebuilt": True, "pairs_written": 0, "force": force}


# ── preflight ───────────────────────────────────────────────────────────────


def test_preflight_rejects_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"")
    with pytest.raises(SystemExit) as excinfo:
        mod.preflight(decoy)
    assert "GB" in str(excinfo.value)


def test_cli_rejects_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"\0" * 32)
    assert mod.main(["--db", str(decoy)]) != 0


def test_preflight_rejects_low_free_disk(tmp_path: Path, monkeypatch) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)

    class _Usage:
        total = 1 << 40
        used = 1 << 40
        free = 1024  # 1 KB free — far below the 6 GB floor

    monkeypatch.setattr(mod.shutil, "disk_usage", lambda _p: _Usage())
    with pytest.raises(SystemExit) as excinfo:
        mod.preflight(db, min_bytes=0)
    assert "GB" in str(excinfo.value)


# ── backup ──────────────────────────────────────────────────────────────────


def test_make_backup_then_verify(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    backup = mod.make_backup(db, tmp_path / "backups")
    assert backup.exists()
    assert mod.verify_backup(db, backup) >= backup.stat().st_size > 0


def test_verify_backup_rejects_missing_and_mis_sized(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    with pytest.raises(SystemExit):
        mod.verify_backup(db, tmp_path / "nope.bak")
    tiny = tmp_path / "tiny.bak"
    tiny.write_bytes(b"x" * 10)
    with pytest.raises(SystemExit):
        mod.verify_backup(db, tiny, min_ratio=0.8)


# ── full run ────────────────────────────────────────────────────────────────


def test_full_run_backup_steps_report(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    before = _counts(db)
    report_path = tmp_path / "report.json"
    report = mod.run(
        str(db),
        min_bytes=0,
        disk_min_bytes=0,
        backup_dir=tmp_path / "backups",
        report_path=report_path,
        attestation_builder=_stub_attestation,
    )
    assert report["ok"] is True
    assert report["quick_check"] == "ok"
    backup = report["backup"]
    assert backup["ok"] is True
    assert Path(backup["path"]).exists()
    assert backup["size_bytes"] >= int(Path(backup["path"]).stat().st_size)
    assert backup["size_bytes"] >= int(db.stat().st_size * 0.5)

    for table in TOUCHED_TABLES:
        assert table in report["before"], table
        assert table in report["after"], table
    # G1 rebuild + G3 wiring + G2 dedupe + G5 grammar, all reflected after-run
    assert report["after"]["word_usage"] > before["word_usage"]
    assert report["after"]["bible_verses_import"] < before["bible_verses_import"]
    assert report["after"]["grammar_patterns"] > before["grammar_patterns"]
    assert report["after"]["attestation_index"] >= before["attestation_index"]
    assert report["after"]["vocabulary"] == before["vocabulary"]

    assert report_path.exists()
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["quick_check"] == "ok"
    assert set(written["before"]) >= set(TOUCHED_TABLES)

    # the corruption is gone and the dedupe index is live
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM word_usage WHERE book LIKE '{%'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ref) FROM bible_verses_import"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name='ux_bible_verses_import_ref'"
        ).fetchone() is not None
    finally:
        conn.close()


def test_step_order_matches_plan(tmp_path: Path, monkeypatch) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    order: list[str] = []

    def _step(name):
        def _fn(db_path, **_kwargs):
            order.append(name)
            return {"step": name}

        return _fn

    monkeypatch.setattr(mod, "learn_bible", _step("g1"))
    monkeypatch.setattr(mod, "wire", _step("g3"))
    monkeypatch.setattr(mod, "dedupe", _step("g2"))
    monkeypatch.setattr(mod, "learn_grammar_patterns", _step("g5"))

    def _attestation(db_path, *, force: bool = True):
        order.append("attestation")
        return {"pairs_written": 0}

    mod.run(
        str(db),
        min_bytes=0,
        disk_min_bytes=0,
        backup_dir=tmp_path / "backups",
        report_path=tmp_path / "report.json",
        attestation_builder=_attestation,
    )
    assert order == ["g1", "attestation", "g3", "g2", "g5"]


def test_attestation_builder_receives_force(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    seen: dict[str, bool] = {}

    def _builder(db_path, *, force: bool = False):
        seen["force"] = force
        return {"pairs_written": 1}

    mod.run(
        str(db),
        min_bytes=0,
        disk_min_bytes=0,
        backup_dir=tmp_path / "backups",
        report_path=tmp_path / "report.json",
        attestation_builder=_builder,
    )
    assert seen["force"] is True


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    before = _counts(db)
    called: list[str] = []

    def _builder(db_path, *, force: bool = True):  # pragma: no cover — must not run
        called.append("attestation")
        return {}

    report = mod.run(
        str(db),
        dry_run=True,
        min_bytes=0,
        disk_min_bytes=0,
        backup_dir=tmp_path / "backups",
        report_path=tmp_path / "report.json",
        attestation_builder=_builder,
    )
    assert report["dry_run"] is True
    assert report["backup"] is None
    assert called == [], "attestation index rebuild writes — it must be skipped dry-run"
    assert _counts(db) == before, "dry-run must not touch a single row"
    backup_dir = tmp_path / "backups"
    assert not backup_dir.exists() or not any(backup_dir.iterdir())
    assert json.loads((tmp_path / "report.json").read_text())["dry_run"] is True


def test_failure_rolls_back_the_active_step(tmp_path: Path) -> None:
    """A corrupt row the parser rejects aborts the run and leaves rows intact."""
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db, with_bad_corrupt=True)
    before = _counts(db)
    with pytest.raises(mod.StepFailure) as excinfo:
        mod.run(
            str(db),
            min_bytes=0,
            disk_min_bytes=0,
            backup_dir=tmp_path / "backups",
            report_path=tmp_path / "report.json",
            attestation_builder=_stub_attestation,
        )
    assert "ROLLBACK" in str(excinfo.value) or "g1" in str(excinfo.value)
    assert _counts(db) == before
    assert not (tmp_path / "report.json").exists()


def test_step_failure_exits_nonzero_from_cli_path(tmp_path: Path, monkeypatch) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    # The CLI enforces the ≥1 GB floor on every invocation; this fixture is
    # smaller, so the guard itself is stubbed out to reach the step code.
    monkeypatch.setattr(mod, "preflight", lambda path, **_kwargs: Path(path))
    monkeypatch.setattr(
        mod, "run_attestation", lambda db_path, *, force=True: {"pairs_written": 0}
    )

    def _boom(db_path, **kwargs):  # pragma: no cover — failure injection
        raise RuntimeError("g3 exploded")

    monkeypatch.setattr(mod, "wire", _boom)
    rc = mod.main(
        [
            "--db", str(db),
            "--backup-dir", str(tmp_path / "backups"),
            "--report", str(tmp_path / "report.json"),
        ]
    )
    assert rc != 0
    assert not (tmp_path / "report.json").exists()
