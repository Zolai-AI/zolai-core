"""Dictionary writes keep both mirrors and audit the real row id."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zolai.learning.dictionary_manager import DictionaryManager

SCHEMA = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT NOT NULL,
    english_clean TEXT,
    myanmar TEXT,
    pos TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    deleted_at TEXT
);
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    translations TEXT NOT NULL,
    translations_clean TEXT,
    pos TEXT,
    source TEXT NOT NULL DEFAULT ''
);
CREATE TABLE data_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    row_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);
"""


def _manager(tmp_path: Path) -> tuple[DictionaryManager, Path]:
    db = tmp_path / "dictionary.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    manager = DictionaryManager()
    manager._db_path = db
    return manager, db


def _rows(db: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def test_add_entry_fills_both_mirror_columns(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    result = manager.add_entry("Pasian", "God", myanmar="Pathian", pos="NOUN")

    assert result == {"success": True, "zolai": "Pasian", "english": "God"}

    (zo,) = _rows(db, "dictionary")
    assert zo["zolai"] == "pasian"
    assert zo["english"] == "God"
    assert zo["english_clean"] == "God"
    assert zo["myanmar"] == "Pathian"
    assert zo["pos"] == "NOUN"

    (en,) = _rows(db, "dictionary_en_zo")
    assert en["headword"] == "god"
    assert json.loads(en["translations"]) == ["Pasian"]
    assert en["translations_clean"] == "Pasian"

    # The audit row points at the ZO→EN row, not the reverse mirror.
    (audit,) = _rows(db, "data_audit_log")
    assert audit["table_name"] == "dictionary"
    assert audit["row_id"] == zo["id"]
    assert audit["new_value"] == "Pasian: God"


def test_add_entry_rejects_an_exact_duplicate(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    assert manager.add_entry("pasian", "God")["success"] is True
    second = manager.add_entry("pasian", "God")

    assert second["success"] is False
    assert second["error"] == "Duplicate entry"
    assert len(_rows(db, "dictionary")) == 1
    assert len(_rows(db, "dictionary_en_zo")) == 1


def test_search_reads_back_what_add_entry_wrote(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    manager.add_entry("gam", "land")

    zo_en = manager.search("gam", direction="zo-en")
    assert [r["english"] for r in zo_en] == ["land"]

    en_zo = manager.search("land", direction="en-zo")
    assert [r["zolai"] for r in en_zo] == ["gam"]


def test_update_entry_applies_and_audits_only_real_fields(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    manager.add_entry("gam", "land")

    result = manager.update_entry("gam", english="earth", myanmar="ram", bogus="x")
    assert result["success"] is True
    assert result["updated_fields"] == ["english", "myanmar"]

    (zo,) = _rows(db, "dictionary")
    assert zo["english"] == "earth"
    assert zo["english_clean"] == "earth"
    assert zo["myanmar"] == "ram"

    audited = {r["field"] for r in _rows(db, "data_audit_log") if r["field"] != "entry"}
    assert audited == {"english", "myanmar"}
