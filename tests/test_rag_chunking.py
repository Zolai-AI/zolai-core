"""Chunking bounds for the wiki RAG source."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from zolai.rag.build import (
    EMBEDDING_MODEL,
    MAX_CHUNK_CHARS,
    VECTOR_DIM,
    _chunk_text,
    _insert_vectors,
    _purge_foreign_vectors,
    _split_oversized,
    _vector_id,
)


def test_short_text_is_one_chunk():
    assert _chunk_text("Pasian in leitung a bawl hi.") == ["Pasian in leitung a bawl hi."]


def test_sentences_split_into_multiple_chunks():
    text = ". ".join(f"sentence number {i} with some words" for i in range(200)) + "."
    chunks = _chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)


def test_boundaryless_run_is_hard_split():
    # A generated wordlist with no periods used to become one multi-hundred-KB
    # "sentence"; the build stalled encoding it.
    text = "khat nih thum li nga guk sagi giat kua sawm " * 500
    chunks = _chunk_text(text)
    assert len(chunks) > 1
    assert max(len(c) for c in chunks) <= MAX_CHUNK_CHARS


def test_split_oversized_prefers_word_boundary():
    piece = ("word " * 1000).strip()
    parts = _split_oversized(piece, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert all(p.endswith("word") for p in parts)


def test_insert_retries_while_the_database_is_locked(monkeypatch):
    # A concurrent writer held the lock past busy_timeout during the 2026-10-29
    # wiki build and three 64-chunk flushes were dropped. The insert must wait
    # it out instead of losing the batch.
    conn = mock.MagicMock()
    conn.execute.side_effect = [
        OperationalError("INSERT", {}, Exception("database is locked")),
        OperationalError("INSERT", {}, Exception("database is locked")),
        None,
    ]
    slept: list[float] = []
    monkeypatch.setattr("zolai.rag.build.time.sleep", slept.append)

    _insert_vectors(conn, [{"id": "x"}])

    assert conn.execute.call_count == 3
    assert conn.commit.call_count == 1
    assert slept == [2.0, 4.0]


def test_insert_gives_up_on_a_non_lock_error(monkeypatch):
    conn = mock.MagicMock()
    conn.execute.side_effect = OperationalError("INSERT", {}, Exception("disk I/O error"))
    monkeypatch.setattr("zolai.rag.build.time.sleep", lambda _s: None)

    with pytest.raises(OperationalError):
        _insert_vectors(conn, [{"id": "x"}])

    assert conn.execute.call_count == 1
    assert conn.commit.call_count == 0


# ── Embedding identity ───────────────────────────────────────────────────────


def test_vector_id_changes_when_text_changes_past_char_50():
    # The old key hashed text[:50], so an edit at char 60 was skipped as if the
    # vector were current.
    prefix = "x" * 60
    a = _vector_id("t", "1", 0, prefix + "old tail", chunked=False)
    b = _vector_id("t", "1", 0, prefix + "new tail", chunked=False)
    assert a != b


def test_vector_id_changes_with_the_embedding_model(monkeypatch):
    before = _vector_id("t", "1", 0, "same", chunked=False)
    monkeypatch.setattr("zolai.rag.build.EMBEDDING_MODEL", "another-model")
    after = _vector_id("t", "1", 0, "same", chunked=False)
    assert before != after


def test_vector_id_is_stable_for_identical_input():
    assert _vector_id("t", "1", 2, "same", chunked=True) == _vector_id(
        "t", "1", 2, "same", chunked=True
    )
    assert _vector_id("t", "1", 2, "same", chunked=True) != _vector_id(
        "t", "1", 3, "same", chunked=True
    )


def _vector_db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE knowledge_vectors (id TEXT PRIMARY KEY, metadata TEXT)"
            )
        )
        conn.execute(
            text("INSERT INTO knowledge_vectors VALUES (:id, :meta)"),
            [
                {"id": "keep", "meta": '{"embedding_model": "%s"}' % EMBEDDING_MODEL},
                {"id": "old", "meta": '{"embedding_model": "all-MiniLM-L6-v2-legacy"}'},
                {"id": "ancient", "meta": "{}"},
            ],
        )
    return engine


def test_purge_removes_vectors_from_other_models_only():
    engine = _vector_db()
    with engine.connect() as conn:
        assert _purge_foreign_vectors(conn) == 2
        remaining = [
            r[0] for r in conn.execute(text("SELECT id FROM knowledge_vectors")).fetchall()
        ]
    assert remaining == ["keep"]


def test_purge_is_a_no_op_when_everything_matches():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE knowledge_vectors (id TEXT PRIMARY KEY, metadata TEXT)")
        )
        conn.execute(
            text("INSERT INTO knowledge_vectors VALUES ('a', :m)"),
            {"m": '{"embedding_model": "%s", "embedding_dim": %d}' % (EMBEDDING_MODEL, VECTOR_DIM)},
        )
    with engine.connect() as conn:
        assert _purge_foreign_vectors(conn) == 0
