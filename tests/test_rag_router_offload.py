"""Every retriever call in the RAG router must leave the event loop.

core#24 fixed this for ``/monitor/health`` and the disease is still here: each
handler in :mod:`zolai.api.rag_router` is an ``async def`` that calls a
synchronous SQLite/numpy/model path directly, so one request freezes every
other in-flight request for its whole duration.

Measured on production (api.zolai.space, 2.4GB corpus, 269k vector rows):

- ``POST /api/v1/search`` with a multi-token query — **8.4s** steady state,
  **32s** on the first call, which pays the embedder load and the vector-index
  build;
- while one of those ran, ``GET /health`` timed out at 60s.

The probe below does not care what the retriever *returns*: it records which
thread the call started on, then raises. That keeps the test free of a corpus
dependency while still failing the exact bug — a handler that calls inline is
caught before it can freeze anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table
from zolai.rag.retrieve import UnifiedRetriever

#: ``(method, path, json body, retriever method the route is expected to call)``.
PROBES: list[tuple[str, str, dict | None, str]] = [
    ("GET", "/api/v1/word/pasian", None, "query_word"),
    ("GET", "/api/v1/word/pasian/forms", None, "query_word_forms"),
    ("GET", "/api/v1/word/pasian/contexts", None, "query_word_contexts"),
    ("GET", "/api/v1/word/pasian/collocations", None, "query_word_collocations"),
    ("GET", "/api/v1/word/pasian/patterns", None, "query_word_patterns"),
    ("GET", "/api/v1/word/pasian/evidence", None, "query_word_evidence"),
    ("GET", "/api/v1/word/pasian/related", None, "related_words"),
    ("POST", "/api/v1/analyze/word", {"word": "pasian"}, "analyze_sentence"),
    ("POST", "/api/v1/analyze/sentence", {"text": "Pasian in leitung a piangsak hi."}, "analyze_sentence"),
    ("POST", "/api/v1/analyze/paragraph", {"text": "Pasian in leitung a piangsak hi."}, "analyze_paragraph"),
    ("POST", "/api/v1/search", {"query": "pasian is god", "limit": 10}, "search"),
    ("POST", "/api/v1/rag", {"question": "what is pasian?"}, "rag_query"),
    ("GET", "/api/v1/knowledge/version", None, "get_knowledge_version"),
    ("GET", "/api/v1/knowledge/statistics", None, "get_knowledge_statistics"),
]


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def client() -> TestClient:
    """The probe raises inside the handler, so a 500 must come back as a
    response instead of re-raising into the test."""
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'rag_offload_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.mark.parametrize("method,path,body,attr", PROBES, ids=[p[3] + " " + p[1] for p in PROBES])
def test_retriever_work_runs_off_the_event_loop(
    client: TestClient, auth_db, monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict | None, attr: str
) -> None:
    """The call must start on a worker thread: ``asyncio.get_running_loop()``
    raises there and succeeds on the loop thread."""
    seen: dict[str, str] = {}

    def probe(self, *args, **kwargs):  # noqa: ANN001
        try:
            asyncio.get_running_loop()
            seen["where"] = "inline"
        except RuntimeError:
            seen["where"] = "thread"
        raise RuntimeError("offload probe")

    monkeypatch.setattr(UnifiedRetriever, attr, probe)

    resp = client.request(method, path, json=body)

    assert resp.status_code == 500, (path, resp.text)
    assert seen.get("where") == "thread", f"{attr} ran on the event loop: {path}"
