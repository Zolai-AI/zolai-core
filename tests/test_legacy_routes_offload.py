"""Legacy unversioned routes must not run their work on the event loop.

Same failure mode as core#24, one layer up: an ``async def`` handler that calls
synchronous SQLite / JSONL / filesystem code blocks the loop for the whole
duration, so a single request freezes *every* other in-flight request.

Measured on production (api.zolai.space, 2.4GB corpus), one anonymous
``GET /learning/dictionary?query=pasian`` at 8.5s was enough to push
``GET /health`` past a 60s timeout.

FastAPI's contract is the fix: a ``def`` handler is run in a worker threadpool,
an ``async def`` handler is run on the loop. Each probe below replaces one
route's heavy dependency with a recorder that notes *which thread it started
on*. The recorder raising (or returning) is not the assertion — the recorded
thread is, and the recorder being called at all is what proves the probe is
aimed at the route's real code path. The versioned RAG surface has its own file
(``test_rag_router_offload.py``); this one covers every other route that reaches
for SQLite, ``subprocess``, or a whole-corpus scan — the unversioned legacy
mounts, ``/desktop/*``, ``/desktop/jsonl/*``, and the prediction alias.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from zolai.analyzer.corpus import CorpusAnalyzer
from zolai.api import auth
from zolai.api import desktop_router as desktop_module
from zolai.api import prediction_api as prediction_module
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table
from zolai.knowledge import retrieve as knowledge_module
from zolai.learning.dictionary_manager import DictionaryManager

#: ``(method, path, json body, owner object, attribute name)`` — one per legacy
#: route that does synchronous I/O. ``POST /analyze`` needs an explicit ``{}``:
#: FastAPI still demands a body for its request model, and a 422 means the
#: probe was never reached.
PROBES: list[tuple[str, str, dict | None, object, str]] = [
    ("GET", "/learning/dictionary?query=pasian&limit=5", None, DictionaryManager, "search"),
    ("POST", "/analyze", {}, CorpusAnalyzer, "full_stats"),
    ("GET", "/bible/status", None, DatabaseManager, "count"),
    ("POST", "/dictionary/search", {"query": "pasian"}, DatabaseManager, "lookup_word"),
    ("GET", "/dictionary/search/all?q=pasian&limit=5", None, DatabaseManager, "search_dictionary"),
    ("GET", "/dictionary/search/my?q=pasian", None, DatabaseManager, "lookup_myanmar"),
    ("GET", "/bible/search?q=khi&limit=2", None, DatabaseManager, "search_bible"),
    ("POST", "/knowledge/search", {"query": "pasian"}, knowledge_module, "retrieve"),
    # ``/desktop/*`` counts every table in the 2.4GB corpus and shells out to
    # scripts with a 120s timeout — the most expensive version of this bug.
    ("GET", "/desktop/tables", None, desktop_module, "get_db"),
    ("GET", "/desktop/dict/non-zolai", None, desktop_module, "run_script"),
    ("GET", "/desktop/jsonl/tables", None, sqlalchemy, "inspect"),
    ("GET", "/api/v1/predictions/complete?prefix=khi", None, prediction_module, "load_ngram_tables"),
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
    """The probe raises; a 500 must come back as a response, not re-raise."""
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'legacy_offload_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.mark.parametrize(
    "method,path,body,owner,attr",
    PROBES,
    ids=[f"{attr} {method} {path}" for method, path, __, _, attr in PROBES],
)
def test_legacy_handler_work_runs_off_the_event_loop(
    client: TestClient,
    auth_db,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict | None,
    owner: object,
    attr: str,
) -> None:
    seen: dict[str, str] = {}

    def probe(*args, **kwargs):  # noqa: ANN002, ANN003
        try:
            asyncio.get_running_loop()
            seen["where"] = "inline"
        except RuntimeError:
            seen["where"] = "thread"
        raise RuntimeError("offload probe")

    monkeypatch.setattr(owner, attr, probe)

    client.request(method, path, json=body)

    label = f"{getattr(owner, '__name__', owner)}.{attr} {method} {path}"
    assert "where" in seen, f"probe never reached the handler — mis-aimed: {label}"
    assert seen["where"] == "thread", f"{label} ran on the event loop"
