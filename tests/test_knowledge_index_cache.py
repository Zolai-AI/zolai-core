"""The knowledge index must not pay for its own cache check.

``POST /knowledge/search`` was serving in 9–44 s on the deployment host. The
hydrated vector index is cached per process, but the *key* used to validate that
cache ran ``SELECT COUNT(*), MAX(imported_at)`` — and ``imported_at`` is not
indexed, so answering "is my cache still good?" scanned 274k rows of embedding
text (~4 s measured, ~15 s with a cold page cache) on every single retrieval,
which costs more than the hydration it was guarding. The key is now
``(db_url, COUNT(*), MAX(id))``: both aggregates are index-only, and an in-place
rebuild that leaves them intact is handled by the explicit
:func:`~zolai.knowledge.retrieve.invalidate_index_cache` the ingest path calls.

The embedder is cached for the same reason: construction loads torch and the
weights (0.3–1.5 s measured) and the model is read-only.

Everything here runs against a throwaway DB in ``tmp_path``; the canonical store
is never opened.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from zolai.config import config
from zolai.knowledge import retrieve as R

_SCHEMA = """
CREATE TABLE knowledge_vectors (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    metadata TEXT,
    embedding TEXT
)
"""

_ROWS = [
    ("a", "alpha chunk", json.dumps({"source_type": "wiki"}), "[1.0, 0.0]"),
    ("b", "beta chunk", "{}", "[0.0, 1.0]"),
]


def _make_db(path: Path, rows: list[tuple[str, str, str, str]] = _ROWS) -> None:
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.executemany("INSERT INTO knowledge_vectors VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fresh process caches and a scratch DB per test."""
    db_path = tmp_path / "zolai.db"
    _make_db(db_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    monkeypatch.setattr(config.paths, "data_knowledge", tmp_path / "knowledge")
    R.invalidate_index_cache()
    R._EMBEDDER_CACHE.clear()
    yield db_path
    R.invalidate_index_cache()
    R._EMBEDDER_CACHE.clear()


class _FakeST(types.ModuleType):
    """``sentence_transformers`` stand-in that counts model constructions."""

    def __init__(self) -> None:
        super().__init__("sentence_transformers")
        self.builds: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_FakeST":
        fake = self

        class _Model:
            def __init__(self, name: str, **kwargs: object) -> None:
                fake.builds.append(name)

            def encode(self, texts, normalize_embeddings: bool = True):  # noqa: ARG002
                return [np.array([1.0, 0.0], dtype=np.float32) for _ in texts]

        self.SentenceTransformer = _Model  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
        return fake


class TestIndexCacheKey:
    def test_key_never_reads_imported_at(self, _isolated_index: Path) -> None:
        """The scratch table has no ``imported_at`` column at all.

        Before the change the key raised ``OperationalError: no such column``
        here; reading only ``(count, max id)`` means the column is not part of
        the contract anymore, which is the whole point.
        """
        url, count, max_id = R._index_cache_key()
        assert count == 2
        assert max_id == "b"
        assert url.startswith("sqlite")

    def test_key_changes_when_rows_are_added(self, _isolated_index: Path) -> None:
        first = R._index_cache_key()
        assert R.load_index().vectors is not None
        conn = sqlite3.connect(_isolated_index)
        conn.execute(
            "INSERT INTO knowledge_vectors VALUES (?, ?, ?, ?)",
            ("c", "gamma chunk", "{}", "[0.5, 0.5]"),
        )
        conn.commit()
        conn.close()
        assert R._index_cache_key() != first

    def test_invalidate_forces_a_re_read(self, _isolated_index: Path) -> None:
        """An in-place rebuild is invisible to the key, so invalidation is explicit."""
        idx = R.load_index()
        assert idx.texts == ["alpha chunk", "beta chunk"]
        before = R._index_cache_key()

        conn = sqlite3.connect(_isolated_index)
        conn.execute("UPDATE knowledge_vectors SET text = 'rewritten' WHERE id = 'a'")
        conn.commit()
        conn.close()

        assert R._index_cache_key() == before, "same count, same max id"
        assert R.load_index().texts[0] == "alpha chunk", "cached index is reused"

        R.invalidate_index_cache()
        assert R.load_index().texts[0] == "rewritten"


class TestEmbedderCache:
    def test_embedder_is_constructed_once_per_model(
        self, _isolated_index: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeST().install(monkeypatch)
        R.retrieve("anything", top_k=1, model_name="model-one")
        R.retrieve("anything else", top_k=1, model_name="model-one")
        assert fake.builds == ["model-one"]

    def test_a_different_model_name_gets_its_own_instance(
        self, _isolated_index: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeST().install(monkeypatch)
        R.retrieve("anything", top_k=1, model_name="model-one")
        R.retrieve("anything", top_k=1, model_name="model-two")
        assert fake.builds == ["model-one", "model-two"]

    def test_retrieve_still_returns_hits_with_the_cached_model(
        self, _isolated_index: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Caching must not change what a search answers."""
        _FakeST().install(monkeypatch)
        hits = R.retrieve("anything", top_k=2, model_name="model-one")
        assert [h["text"] for h in hits] == ["alpha chunk"]
