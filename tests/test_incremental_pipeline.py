"""Incremental pipeline: change detection carries its source, upserts converge.

The pipeline was unusable before this suite existed — the processor imported
``_get_table_for_source`` from a module that never defined it and read
``changeset._source_file``, which ``detect_changes`` never set. These tests
pin the contract that made the fix necessary: a second pass over unchanged
input must report zero changes, which only works if ``content_hash`` is
actually written back.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from zolai.learning.incremental import processor as processor_mod
from zolai.learning.incremental.change_detector import ChangeSet, detect_changes
from zolai.learning.incremental.processor import process_changeset

DDL = """
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    english TEXT NOT NULL,
    frequency INTEGER NOT NULL DEFAULT 0,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
"""


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    with eng.begin() as conn:
        conn.execute(text(DDL))
    return eng


@pytest.fixture()
def quiet_discovery(monkeypatch):
    """Discovery/observation are out of scope here and would need a full schema."""
    calls: dict[str, int] = {"pos": 0, "morphology": 0, "observation": 0}

    def _pos(_engine, limit=0):
        calls["pos"] += 1
        return {"hypotheses_written": 0}

    def _morph(_engine, limit=0):
        calls["morphology"] += 1
        return {"hypotheses_written": 0}

    def _obs(_engine, limit=0):
        calls["observation"] += 1
        return {"word_observation_stats": 0}

    monkeypatch.setattr(processor_mod, "build_pos_hypotheses", _pos)
    monkeypatch.setattr(processor_mod, "build_morphology_hypotheses", _morph)
    monkeypatch.setattr(processor_mod, "build_observation", _obs)
    return calls


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _rows(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text("SELECT * FROM vocabulary"))]


def test_changeset_carries_source_file(tmp_path: Path, engine) -> None:
    source = _write_jsonl(
        tmp_path / "vocabulary_export.jsonl", [{"id": 1, "headword": "a", "english": "b"}]
    )
    changeset = detect_changes(engine, str(source))
    assert changeset.source_file == str(source)
    assert len(changeset.new) == 1


def test_empty_source_still_carries_source_file(tmp_path: Path, engine) -> None:
    source = tmp_path / "vocabulary_empty.jsonl"
    source.write_text("", encoding="utf-8")
    changeset = detect_changes(engine, str(source))
    assert changeset.source_file == str(source)
    assert changeset.total_incoming == 0


def test_process_rejects_changeset_without_source(engine) -> None:
    summary = process_changeset(engine, ChangeSet())
    assert summary.errors == ["Unknown table for source"]
    assert summary.upserted == 0


def test_upsert_converges_on_second_pass(
    tmp_path: Path, engine, quiet_discovery
) -> None:
    source = _write_jsonl(
        tmp_path / "vocabulary_export.jsonl",
        [
            {"id": 1, "headword": "tawng", "english": "language", "frequency": 5},
            {"id": 2, "headword": "lei", "english": "earth", "frequency": 3},
        ],
    )

    first = detect_changes(engine, str(source))
    assert len(first.new) == 2
    summary = process_changeset(engine, first)
    assert summary.upserted == 2

    rows = _rows(engine)
    assert {r["id"] for r in rows} == {1, 2}
    assert all(r["content_hash"] for r in rows), "content_hash must be written back"

    second = detect_changes(engine, str(source))
    assert second.total_changes == 0, second.summary()
    assert len(second.unchanged) == 2


def test_update_keeps_pk_and_refreshes_hash(
    tmp_path: Path, engine, quiet_discovery
) -> None:
    source = _write_jsonl(
        tmp_path / "vocabulary_export.jsonl",
        [{"id": 7, "headword": "tawng", "english": "language", "frequency": 5}],
    )
    process_changeset(engine, detect_changes(engine, str(source)))
    before = _rows(engine)[0]

    _write_jsonl(
        tmp_path / "vocabulary_export.jsonl",
        [{"id": 7, "headword": "tawng", "english": "speech", "frequency": 9}],
    )
    changed = detect_changes(engine, str(source))
    assert len(changed.changed) == 1
    process_changeset(engine, changed)

    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0]["id"] == 7
    assert rows[0]["english"] == "speech"
    assert rows[0]["content_hash"] != before["content_hash"]

    assert detect_changes(engine, str(source)).total_changes == 0


def test_dry_run_reports_without_writing(tmp_path: Path, engine, quiet_discovery) -> None:
    source = _write_jsonl(
        tmp_path / "vocabulary_export.jsonl",
        [{"id": 1, "headword": "tawng", "english": "language", "frequency": 5}],
    )
    changeset = detect_changes(engine, str(source))
    summary = process_changeset(engine, changeset, dry_run=True)

    assert summary.upserted == 0
    assert summary.would_upsert == 1
    assert summary.would_extract_words == 1  # only the Zolai field counts
    assert _rows(engine) == []
    assert quiet_discovery == {"pos": 0, "morphology": 0, "observation": 0}
