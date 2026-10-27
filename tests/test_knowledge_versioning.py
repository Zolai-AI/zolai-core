"""Knowledge version snapshots — content manifests, drift detection, CLI parity."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.knowledge.versioning import (
    CANONICAL_TABLES,
    create_knowledge_version,
    get_knowledge_version,
    verify_knowledge_version,
)


@pytest.fixture()
def version_db(tmp_path: Path) -> DatabaseManager:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'versioning.db'}")
    mgr.init_db()
    return mgr


def _insert_phrase(mgr: DatabaseManager, phrase: str) -> None:
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO phrases (zolai, english, frequency, examples) "
                "VALUES (:z, :e, 1, '[]')"
            ),
            {"z": phrase, "e": f"{phrase}-en"},
        )


def test_snapshot_records_fingerprints_and_verifies(version_db: DatabaseManager) -> None:
    version_id = create_knowledge_version(version_db.engine, "v-test-1")

    row = get_knowledge_version(version_db.engine, "v-test-1")
    assert row is not None
    assert row["id"] == version_id
    assert row["manifest_hash"]

    report = verify_knowledge_version(version_db.engine, "v-test-1")
    assert report["ok"] is True
    assert report["checked"] == len(CANONICAL_TABLES)
    assert report["drift"] == {}


def test_verify_detects_row_drift(version_db: DatabaseManager) -> None:
    create_knowledge_version(version_db.engine, "v-test-2")
    _insert_phrase(version_db, "lungdam")

    report = verify_knowledge_version(version_db.engine, "v-test-2")

    assert report["ok"] is False
    assert "phrases" in report["drift"]


def test_manifest_is_content_sensitive_not_just_counts(version_db: DatabaseManager) -> None:
    _insert_phrase(version_db, "note")
    create_knowledge_version(version_db.engine, "v-a")

    with version_db.engine.begin() as conn:
        conn.execute(text("UPDATE phrases SET english = 'changed' WHERE zolai = 'note'"))

    create_knowledge_version(version_db.engine, "v-b")

    first = get_knowledge_version(version_db.engine, "v-a")
    second = get_knowledge_version(version_db.engine, "v-b")
    assert first is not None and second is not None
    # Same row counts, different content → the manifest must differ.
    assert first["row_counts"] == second["row_counts"]
    assert first["manifest_hash"] != second["manifest_hash"]


def test_verify_unknown_version_is_not_ok(version_db: DatabaseManager) -> None:
    report = verify_knowledge_version(version_db.engine, "missing")

    assert report["ok"] is False
    assert report["error"] == "unknown version"
