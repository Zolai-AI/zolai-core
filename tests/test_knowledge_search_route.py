"""``POST /knowledge/search`` degrades to an empty answer — core#21.

The legacy RAG route used to 500 whenever the knowledge layer was not in the
DB it ran against:

- a DB without the ``knowledge_vectors`` table raised
  ``sqlalchemy.exc.OperationalError: no such table: knowledge_vectors`` out of
  ``_index_cache_key()`` → ``load_index()``;
- a base-only install (no ``sentence-transformers``) raised
  ``ModuleNotFoundError`` because ``retrieve()`` imported the embedder *before*
  it looked at the index, so even "there is nothing to search" paid for an ml
  import.

Both are answered the same honest way now: no index → ``results: []`` and
``context: ""`` with **200**. Nothing is swallowed by a blanket try/except —
the route never reaches an exception path: the table is checked explicitly
(``_vector_table_exists``) and the index is loaded before the embedder.

Everything runs against a throwaway DB in ``tmp_path``; the canonical
2.4 GB ``data/zolai.db`` is never opened and never migrated (no lifespan, no
``ensure_schema_v2``).
"""

from __future__ import annotations

import sqlite3
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config


class _NoML(types.ModuleType):
    """Sentinel for ``sentence_transformers``: importing it is a test failure.

    ``from sentence_transformers import SentenceTransformer`` resolves the
    attribute through ``__getattr__``, so reaching that line in a request that
    has no index to search raises :class:`AssertionError` and fails the test.
    """

    def __getattr__(self, name: str):  # pragma: no cover - only on regression
        raise AssertionError(
            "sentence_transformers must not be imported when there is no index"
        )


@pytest.fixture(autouse=True)
def _clean_auth_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def no_index_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A DB with no ``knowledge_vectors`` table at all (read-only for us)."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE dictionary (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT)")
    conn.execute("INSERT INTO dictionary VALUES (1, 'pasian', 'God')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db_path)
    # No local bundle to ingest either: the empty-index path must not need one.
    monkeypatch.setattr(config.paths, "data_knowledge", tmp_path / "knowledge")
    return db_path


@pytest.fixture()
def client(no_index_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App without lifespan — no startup migrations, no network."""
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    monkeypatch.setitem(sys.modules, "sentence_transformers", _NoML("sentence_transformers"))
    return TestClient(create_app())


class TestKnowledgeSearchDegradation:
    def test_post_knowledge_search_is_200_empty_not_500(self, client: TestClient) -> None:
        """core#21: the reported 500 was the missing-table read inside load_index."""
        resp = client.post("/knowledge/search", json={"query": "zomi"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == "zomi"
        assert body["results"] == []
        assert body["context"] == ""

    def test_threshold_and_top_k_do_not_reach_an_exception(self, client: TestClient) -> None:
        """Explicit parameters take the same degraded path, not a different one."""
        resp = client.post(
            "/knowledge/search", json={"query": "zomi", "top_k": 3, "threshold": 0.1}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"] == []

    def test_missing_table_is_reported_as_no_index(self, no_index_db: Path) -> None:
        """Unit-level: the precondition check, not a caught driver error."""
        from zolai.knowledge import retrieve as R

        assert R._vector_table_exists() is False
        idx = R.load_index()
        assert idx.vectors is None
        assert idx.texts == []
