"""Word attestation reads the canonical columns and never hides a broken probe."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from zolai.learning.dictionary_manager import DictionaryManager

SCHEMA = """
CREATE TABLE dictionary (zolai TEXT);
CREATE TABLE bible_verses (ref TEXT, zo_tdb77 TEXT);
CREATE TABLE vocabulary (headword TEXT);
"""


def _manager(tmp_path: Path, schema: str = SCHEMA) -> DictionaryManager:
    db = tmp_path / "attest.db"
    conn = sqlite3.connect(db)
    conn.executescript(schema)
    conn.execute("INSERT INTO dictionary VALUES ('pasian')")
    conn.execute(
        "INSERT INTO bible_verses VALUES ('GEN 1:1', 'Pasian in leitung a piangsak hi')"
    )
    if "CREATE TABLE vocabulary" in schema:
        conn.execute("INSERT INTO vocabulary VALUES ('gam')")
    conn.commit()
    conn.close()

    manager = DictionaryManager()
    manager._db_path = db
    return manager


def test_attestation_reads_dictionary_and_bible_columns(tmp_path: Path) -> None:
    result = _manager(tmp_path).check_attestation("pasian")

    assert result["attested"] is True
    # The Bible probe uses the real column (zo_tdb77), so it must actually run.
    assert set(result["sources"]) == {"dictionary", "bible"}
    assert result["count"] == 2
    assert "errors" not in result


def test_attestation_uses_the_canonical_vocabulary_table(tmp_path: Path) -> None:
    result = _manager(tmp_path).check_attestation("gam")

    assert result["sources"] == ["vocab"]
    assert "errors" not in result


def test_attestation_names_probes_that_could_not_run(tmp_path: Path) -> None:
    schema = SCHEMA.replace("CREATE TABLE vocabulary (headword TEXT);", "")
    result = _manager(tmp_path, schema).check_attestation("pasian")

    # A missing table must not read as "not attested anywhere".
    assert result["errors"] == ["vocab"]
    assert result["attested"] is True
    assert result["sources"] == ["dictionary", "bible"]
