"""Incremental RAG updates: bounded, per-table, embed-before-delete.

The existing builder can only skip known ids, so a changed row was never
re-embedded and a deleted row's vectors stayed searchable forever. These tests
pin the two invariants that make the new path safe on the live index: both
Bible specs move together, and an embed failure never deletes a live vector.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from zolai.data.migrations import create_learning_run_tables
from zolai.rag import incremental
from zolai.rag.incremental import update_knowledge_vectors_incremental

DIM = 384

DDL = """
CREATE TABLE knowledge_vectors (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    metadata TEXT,
    embedding TEXT,
    source_type TEXT,
    source TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);
CREATE TABLE bible_verses (
    ref TEXT PRIMARY KEY,
    zo_tdb77 TEXT,
    zo_tedim2010 TEXT,
    updated_at TEXT
);
CREATE TABLE grammar_patterns (
    pattern_id TEXT PRIMARY KEY,
    pattern TEXT,
    function TEXT,
    examples TEXT,
    frequency INTEGER,
    updated_at TEXT
);
"""


class FakeEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    def encode(self, texts, **_kwargs):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embedder exploded")
        return [[0.01 * (i + 1)] * DIM for i in range(len(texts))]


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{tmp_path / 'rag.db'}")
    with eng.begin() as conn:
        for statement in DDL.split(";"):
            if statement.strip():
                conn.execute(text(statement))
    create_learning_run_tables(eng)
    return eng


def _add_vector(engine, *, vec_id: str, source: str, source_id: str | None) -> None:
    metadata = {"source": source}
    if source_id is not None:
        metadata["source_id"] = source_id
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO knowledge_vectors "
                "(id, text, metadata, embedding, source_type, source, imported_at) "
                "VALUES (:id, 'old', :m, '[]', :st, :s, '2026-01-01T00:00:00+00:00')"
            ),
            {
                "id": vec_id,
                "m": json.dumps(metadata),
                "st": source,
                "s": source,
            },
        )


def _vectors(engine, source: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r._mapping)
            for r in conn.execute(
                text("SELECT id, text, metadata FROM knowledge_vectors WHERE source = :s"),
                {"s": source},
            ).fetchall()
        ]


def _seed_bible(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bible_verses (ref, zo_tdb77, zo_tedim2010, updated_at) "
                "VALUES ('GEN 1:1', 'Pasian in a gam hi.', 'Pasian in gam hi.', "
                "'2026-10-20T00:00:00+00:00')"
            )
        )


def test_dry_run_reports_without_embedding(engine, monkeypatch) -> None:
    _seed_bible(engine)

    def _boom():
        raise AssertionError("dry-run must not load the embedder")

    monkeypatch.setattr(incremental, "_get_embedder", _boom)
    summary = update_knowledge_vectors_incremental(
        engine, sources=["bible_verses"], reconcile=True, dry_run=True
    )

    assert summary["mode"] == "dry_run"
    assert summary["tables"]["bible_verses"]["candidates"] == 1
    assert summary["planned_chunks"] == 2  # both Bible specs
    assert summary["eta_seconds"] > 0
    assert _vectors(engine, "bible_verses") == []
    # touched-row counters stay 0; the index size is reported separately so a
    # dry-run on a populated index cannot read as "empty table".
    assert summary["tables"]["bible_verses"]["vectors_before"] == 0
    assert summary["tables"]["bible_verses"]["vectors_in_index"] == 0


def test_dry_run_reports_the_real_index_size(engine) -> None:
    _seed_bible(engine)
    _add_vector(engine, vec_id="v1", source="bible_verses", source_id="GEN 1:1")
    _add_vector(engine, vec_id="v2", source="grammar_patterns", source_id="p1")

    summary = update_knowledge_vectors_incremental(
        engine, sources=["bible_verses"], reconcile=True, dry_run=True
    )

    assert summary["tables"]["bible_verses"]["vectors_in_index"] == 1
    assert summary["tables"]["bible_verses"]["vectors_before"] == 0
    assert summary["vectors_in_index"] == 2


def test_bible_row_reembeds_both_specs(engine) -> None:
    _seed_bible(engine)
    summary = update_knowledge_vectors_incremental(
        engine,
        sources=["bible_verses"],
        reconcile=True,
        embedder=FakeEmbedder(),
    )

    vectors = _vectors(engine, "bible_verses")
    assert len(vectors) == 2
    assert {json.loads(v["metadata"])["source_id"] for v in vectors} == {"GEN 1:1"}
    assert summary["reembedded"] == 1
    assert summary["chunks"] == 2


def test_reembed_writes_the_embedding_model_identity(engine) -> None:
    """Every re-embedded row must carry the model tag the full build writes.

    Without it the row is indistinguishable from a foreign embedding: the next
    full build purges it as untagged, and a reader cannot tell which vector
    space it belongs to.
    """
    from zolai.rag.build import EMBEDDING_MODEL, VECTOR_DIM

    _seed_bible(engine)
    update_knowledge_vectors_incremental(
        engine,
        sources=["bible_verses"],
        reconcile=True,
        embedder=FakeEmbedder(),
    )

    for vector in _vectors(engine, "bible_verses"):
        meta = json.loads(vector["metadata"])
        assert meta["embedding_model"] == EMBEDDING_MODEL
        assert meta["embedding_dim"] == VECTOR_DIM
        assert meta["source_table"] == "bible_verses"


def test_reembed_ids_match_the_full_build(engine) -> None:
    """A resumed full build must recognise the rows incremental already wrote.

    ``build_knowledge_vectors`` skips ids that exist; if incremental derived
    different ids, the next build would insert a second copy of every
    re-embedded row.
    """
    from zolai.rag.build import _vector_id

    _seed_bible(engine)
    update_knowledge_vectors_incremental(
        engine,
        sources=["bible_verses"],
        reconcile=True,
        embedder=FakeEmbedder(),
    )

    for vector in _vectors(engine, "bible_verses"):
        meta = json.loads(vector["metadata"])
        expected = _vector_id(
            "bible_verses", meta["source_id"], 0, vector["text"], chunked=False
        )
        assert vector["id"] == expected


def test_embed_failure_keeps_old_vectors_and_records_retryable_state(engine) -> None:
    _seed_bible(engine)
    _add_vector(engine, vec_id="old-1", source="bible_verses", source_id="GEN 1:1")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO learning_runs (run_id, mode, status, started_at) "
                "VALUES ('lr_test', 'apply', 'RUNNING', '2026-10-28T00:00:00+00:00')"
            )
        )

    summary = update_knowledge_vectors_incremental(
        engine,
        sources=["bible_verses"],
        reconcile=True,
        embedder=FakeEmbedder(fail=True),
        run_id="lr_test",
    )

    assert summary["failed"] == 1
    assert [v["id"] for v in _vectors(engine, "bible_verses")] == ["old-1"]
    with engine.connect() as conn:
        state = conn.execute(
            text("SELECT action, status, error_class FROM learning_vectors_state")
        ).fetchall()
    assert [tuple(r) for r in state] == [("failed", "failed", "RuntimeError")]


def test_limit_bounds_rows_processed(engine) -> None:
    with engine.begin() as conn:
        for i in range(3):
            conn.execute(
                text(
                    "INSERT INTO bible_verses (ref, zo_tdb77, zo_tedim2010, updated_at) "
                    "VALUES (:r, 'a b c', 'd e f', :t)"
                ),
                {"r": f"GEN 1:{i + 1}", "t": f"2026-10-2{i}T00:00:00+00:00"},
            )

    summary = update_knowledge_vectors_incremental(
        engine,
        sources=["bible_verses"],
        reconcile=True,
        limit=1,
        embedder=FakeEmbedder(),
    )
    assert summary["reembedded"] == 1
    assert len(_vectors(engine, "bible_verses")) == 2  # one row × two specs


def test_max_chunks_defers_a_row_that_does_not_fit(engine) -> None:
    _seed_bible(engine)
    summary = update_knowledge_vectors_incremental(
        engine,
        sources=["bible_verses"],
        reconcile=True,
        max_chunks=1,
        embedder=FakeEmbedder(),
    )
    assert summary["reembedded"] == 0
    assert summary["tables"]["bible_verses"]["budget_skipped"] == 1
    assert _vectors(engine, "bible_verses") == []


def test_prune_removes_orphans_but_never_legacy_vectors(engine) -> None:
    _add_vector(engine, vec_id="orphan", source="bible_verses", source_id="GONE 9:9")
    _add_vector(engine, vec_id="legacy", source="bible_verses", source_id=None)

    pruned = incremental.prune_stale_vectors(engine, "bible_verses")

    assert pruned == {"pruned": 1, "unattributable": 1, "candidates": 1}
    assert [v["id"] for v in _vectors(engine, "bible_verses")] == ["legacy"]


def test_watermark_is_used_as_effective_since(engine) -> None:
    _seed_bible(engine)
    _add_vector(engine, vec_id="wm", source="bible_verses", source_id="GEN 1:1")

    mark = incremental.effective_since(engine, "bible_verses", None)
    assert mark == "2026-01-01T00:00:00+00:00"

    newer = incremental.effective_since(engine, "bible_verses", "2026-10-27T00:00:00+00:00")
    assert newer == "2026-10-27T00:00:00+00:00"
