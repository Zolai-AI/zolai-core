"""Tests for the word-engine prediction routes (P1 /api/v1 core surface).

Covers:

- ``GET/POST /api/v1/predictions/next`` — next-word prediction
- ``GET/POST /api/v1/predictions/complete`` — plan §P1 name; must return
  exactly what the original ``/predictions/completions`` path returns
  (migrate-not-rename: shared handler, no logic duplicated)
- ``GET/POST /api/v1/predictions/corrections`` — spelling suggestions
- ``GET /api/v1/predictions/health`` — n-gram table status
- validation (422 for missing/out-of-range params)
- auth: enforce 401 without a key, 403 for a wrong scope, 200 with
  ``dataset:read``; warn-mode dual-accept unchanged

The n-gram engine is patched with small deterministic tables
(``load_ngram_tables``) — no 2.4GB DB dependency, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

# ── Deterministic n-gram tables ────────────────────────────────────────

UNIGRAMS = {
    "khi": 100,
    "kha": 80,
    "khe": 60,
    "khu": 40,
    "le": 200,
}
BIGRAMS = {
    ("khi", "a"): 50,
    ("khi", "b"): 30,
    ("khi", "c"): 20,
    ("le", "a"): 100,
}
TABLES = {"unigrams": UNIGRAMS, "bigrams": BIGRAMS}
_EMPTY_TABLES: dict = {"unigrams": {}, "bigrams": {}}

#: Patch target inside the prediction module (its own import binding).
_NGRAM = "zolai.api.prediction_api.load_ngram_tables"


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_auth_state() -> Iterator[None]:
    """Isolate key cache / failure log / rate buckets around each test."""
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def client() -> TestClient:
    """App without lifespan — routes do not need startup migrations."""
    return TestClient(create_app())


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway api_keys DB for the auth service (singleton seam)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


# ── Functional behaviour ───────────────────────────────────────────────


class TestNextWord:
    def test_get_next_word(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get("/api/v1/predictions/next", params={"word": "khi", "top_k": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["word"] == "khi"
        assert len(data["predictions"]) == 2
        assert data["predictions"][0] == {"next": "a", "count": 50}

    def test_post_twin(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.post("/api/v1/predictions/next", params={"word": "le"})
        assert resp.status_code == 200
        assert resp.json()["predictions"][0] == {"next": "a", "count": 100}

    def test_unknown_word_backs_off_without_error(self, client: TestClient) -> None:
        """Unknown context → unigram-marginal backoff (engine behaviour), still 200."""
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get("/api/v1/predictions/next", params={"word": "zzzz"})
        assert resp.status_code == 200
        predictions = resp.json()["predictions"]
        assert predictions, "expected unigram backoff for an unknown word"
        assert predictions[0] == {"next": "le", "count": 200}  # most frequent unigram

    def test_empty_tables(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=_EMPTY_TABLES):
            resp = client.get("/api/v1/predictions/next", params={"word": "khi"})
        assert resp.status_code == 200
        assert resp.json()["predictions"] == []


class TestCompletions:
    def test_complete_alias_matches_completions(self, client: TestClient) -> None:
        """The plan §P1 ``/complete`` name and the original path are twins."""
        with patch(_NGRAM, return_value=TABLES):
            alias = client.get("/api/v1/predictions/complete", params={"prefix": "khi", "top_k": 3})
            original = client.get("/api/v1/predictions/completions", params={"prefix": "khi", "top_k": 3})
        assert alias.status_code == 200
        assert original.status_code == 200
        assert alias.json() == original.json()
        assert alias.json()["prefix"] == "khi"
        assert alias.json()["completions"], "expected completions for prefix 'khi'"

    def test_completions_original_path_still_works(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get("/api/v1/predictions/completions", params={"prefix": "k"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["prefix"] == "k"
        assert data["completions"], "expected completions for prefix 'k' over the test tables"

    def test_complete_post_twin(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.post("/api/v1/predictions/complete", params={"prefix": "khi"})
        assert resp.status_code == 200
        assert resp.json()["prefix"] == "khi"


class TestCorrections:
    def test_corrections(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get("/api/v1/predictions/corrections", params={"word": "khi"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["word"] == "khi"
        assert data["corrections"][0] == {"candidate": "khi", "distance": 0}

    def test_corrections_post_twin(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.post("/api/v1/predictions/corrections", params={"word": "khi", "top_k": 2})
        assert resp.status_code == 200
        assert len(resp.json()["corrections"]) == 2


class TestHealth:
    def test_health_with_tables(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get("/api/v1/predictions/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["tables_loaded"] is True
        assert data["unigram_count"] == len(UNIGRAMS)
        assert data["bigram_count"] == len(BIGRAMS)

    def test_health_empty_tables(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=_EMPTY_TABLES):
            resp = client.get("/api/v1/predictions/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_tables"
        assert data["tables_loaded"] is False


class TestValidation:
    def test_next_requires_word(self, client: TestClient) -> None:
        assert client.get("/api/v1/predictions/next").status_code == 422

    def test_complete_requires_prefix(self, client: TestClient) -> None:
        assert client.get("/api/v1/predictions/complete").status_code == 422

    def test_top_k_bounds(self, client: TestClient) -> None:
        with patch(_NGRAM, return_value=TABLES):
            assert (
                client.get("/api/v1/predictions/next", params={"word": "khi", "top_k": 0}).status_code
                == 422
            )
            assert (
                client.get("/api/v1/predictions/next", params={"word": "khi", "top_k": 21}).status_code
                == 422
            )


# ── Auth — enforce 401 / 403 wrong scope / 200 with the right scope ────


class TestPredictionAuth:
    def test_enforce_missing_key_is_401(self, client: TestClient, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        resp = client.get("/api/v1/predictions/next", params={"word": "khi"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "unauthorized"

    def test_enforce_wrong_scope_is_403(self, client: TestClient, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="pos-only", scopes=["pos:read"])
        resp = client.get("/api/v1/predictions/next", params={"word": "khi"}, headers=_headers(key))
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["action"] == "dataset:read"
        assert detail["reason"] == "missing_scope"

    def test_enforce_dataset_read_key_is_200(self, client: TestClient, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="reader", scopes=["dataset:read"])
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get(
                "/api/v1/predictions/next",
                params={"word": "khi"},
                headers=_headers(key),
            )
        assert resp.status_code == 200
        assert resp.json()["predictions"]

    def test_enforce_health_needs_scope_too(self, client: TestClient, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        resp = client.get("/api/v1/predictions/health")
        assert resp.status_code == 401

    def test_warn_mode_still_dual_accepts(self, client: TestClient, auth_db, monkeypatch) -> None:
        monkeypatch.delenv("ZOLAI_API_AUTH", raising=False)
        with patch(_NGRAM, return_value=TABLES):
            resp = client.get("/api/v1/predictions/next", params={"word": "khi"})
        assert resp.status_code == 200
