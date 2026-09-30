"""Tests for the lexicon + linguistics read API (P1 /api/v1 core surface).

Covers:

- ``GET /api/v1/lexicon/{word}`` — ZO→EN + EN→ZO exact lookup, L1.3 POS
  columns *where present*, stored syllables, ``Cache-Control``
- ``GET /api/v1/lexicon/search`` — bilingual search, cursor pagination
- ``GET/POST /api/v1/linguistics/{pos,syllable}`` — rule tagger + segmenter
  and the foundation analysis/search delegate-mounts (same callables)
- auth: enforce 401 without a key, 403 for a wrong scope, 200 with
  ``dataset:read`` / ``pos:read``; warn-mode dual-accept unchanged
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SEED_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT,
    pos TEXT,
    source TEXT,
    syllables TEXT,
    syllable_count INTEGER,
    pos_canonical TEXT,
    pos_candidates TEXT DEFAULT '[]',
    pos_evidence TEXT DEFAULT 'unknown',
    morph_features TEXT DEFAULT '{}',
    review_status TEXT DEFAULT 'unknown',
    confidence REAL
);
INSERT INTO dictionary VALUES
    (1, 'pasian', 'God', 'noun', 'test', 'pa|sian', 2, 'N', '["N","PROPER"]', 'lexicon', '{}', 'unknown', 0.9),
    (2, 'gam', 'earth, land', 'noun', 'test', 'gam', 1, 'N', '["N"]', 'lexicon', '{}', 'unknown', 0.8),
    (3, 'tapa', 'son, life', 'noun', 'test', 'ta|pa', 2, 'N', '["N"]', 'lexicon', '{}', 'unknown', 0.7);
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY,
    headword TEXT NOT NULL,
    translations TEXT,
    pos TEXT,
    source TEXT
);
INSERT INTO dictionary_en_zo VALUES
    (1, 'god', 'pasian', 'noun', 'test'),
    (2, 'earth', 'gam', 'noun', 'test'),
    (3, 'son', 'tapa', 'noun', 'test');
"""

# Minimal dictionary table WITHOUT the L1.3 columns — proves the route
# degrades to the columns that actually exist.
_BARE_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT
);
INSERT INTO dictionary VALUES (1, 'khem', 'lie, deceive');
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY,
    headword TEXT NOT NULL,
    translations TEXT
);
"""

# Only the ZO→EN table exists — the single-table path must still round-trip
# composite ``<id>:<table>`` cursors.
_DICT_ONLY_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT
);
INSERT INTO dictionary VALUES
    (1, 'pasian', 'God'),
    (2, 'gam', 'earth, land'),
    (3, 'tapa', 'son, life');
"""


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


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str) -> Path:
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(sql)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db_path)
    return db_path


@pytest.fixture()
def api_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Seeded throwaway lexicon DB wired into config.paths.db."""
    yield _seed(tmp_path, monkeypatch, _SEED_SQL)


@pytest.fixture()
def bare_api_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Dictionary table with no L1.3 columns at all."""
    yield _seed(tmp_path, monkeypatch, _BARE_SQL)


@pytest.fixture()
def dict_only_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Only the ``dictionary`` table — exercises the single-table cursor path."""
    yield _seed(tmp_path, monkeypatch, _DICT_ONLY_SQL)


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway api_keys DB for the auth service (singleton seam)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    """App without lifespan — routes do not need startup migrations."""
    return TestClient(create_app())


def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _walk(client: TestClient, params: dict, max_pages: int = 20) -> list[tuple[int, str]]:
    """Follow ``next_cursor`` until exhausted; return ``(id, table)`` per row.

    Fails the test on any non-200 page or if pagination never terminates.
    """
    seen: list[tuple[int, str]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        page_params = dict(params)
        if cursor is not None:
            page_params["cursor"] = cursor
        resp = client.get("/api/v1/lexicon/search", params=page_params)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) <= params["limit"]
        seen.extend((item["id"], item["table"]) for item in data["items"])
        if not data["has_more"]:
            assert data["next_cursor"] is None
            return seen
        assert data["next_cursor"] is not None
        cursor = data["next_cursor"]
    pytest.fail("pagination did not terminate")  # pragma: no cover


# ---------------------------------------------------------------------------
# GET /api/v1/lexicon/{word}
# ---------------------------------------------------------------------------


class TestLexiconWordLookup:
    def test_zo_en_lookup_with_l1_3_columns(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/pasian")
        assert resp.status_code == 200
        data = resp.json()
        assert data["word"] == "pasian"
        assert data["found"] is True
        assert len(data["zo_en"]) == 1
        entry = data["zo_en"][0]
        assert entry["english"] == "God"
        assert entry["pos_canonical"] == "N"
        assert entry["pos_candidates"] == ["N", "PROPER"]  # JSON decoded
        assert entry["morph_features"] == {}  # JSON decoded
        assert entry["syllables"] == "pa|sian"
        assert entry["review_status"] == "unknown"

    def test_en_zo_lookup_by_english_headword(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/god")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["zo_en"] == []
        assert data["en_zo"][0]["translations"] == "pasian"

    def test_lookup_is_case_insensitive(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/God")
        assert resp.status_code == 200
        assert resp.json()["found"] is True

    def test_unknown_word_returns_found_false(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/zzzznotaword")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert data["zo_en"] == []
        assert data["en_zo"] == []

    def test_get_sets_cache_control(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/pasian")
        assert resp.headers["cache-control"].startswith("public, max-age=")

    def test_l1_3_columns_are_optional(self, client: TestClient, bare_api_db) -> None:
        """Where-present semantics: a dictionary without L1.3 columns still resolves."""
        resp = client.get("/api/v1/lexicon/khem")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        entry = data["zo_en"][0]
        assert entry["english"] == "lie, deceive"
        assert "pos_canonical" not in entry
        assert "syllables" not in entry


# ---------------------------------------------------------------------------
# GET /api/v1/lexicon/search
# ---------------------------------------------------------------------------


class TestLexiconSearch:
    def test_search_hits_both_directions(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/search", params={"q": "pasian"})
        assert resp.status_code == 200
        data = resp.json()
        tables = {item["table"] for item in data["items"]}
        assert tables == {"dictionary", "dictionary_en_zo"}
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_search_pagination_cursor_walk(self, client: TestClient, api_db) -> None:
        """limit=2 over 6 matches (3 per table) → pages join without duplicates."""
        seen = _walk(client, {"q": "a", "limit": 2})
        assert len(seen) == 6
        assert len(set(seen)) == 6  # no duplicates across pages

    def test_search_cursor_walk_limit_1_returns_colliding_ids_once(self, client: TestClient, api_db) -> None:
        """Page boundary ON a cross-table id tie must not drop the same-id twin.

        ids 1–3 exist in BOTH tables (6 rows).  With limit=1 every boundary
        splits a tie — the old bare-``id`` keyset lost all en_zo twins.
        """
        seen = _walk(client, {"q": "a", "limit": 1})
        assert seen == [
            (1, "dictionary"),
            (1, "dictionary_en_zo"),
            (2, "dictionary"),
            (2, "dictionary_en_zo"),
            (3, "dictionary"),
            (3, "dictionary_en_zo"),
        ]  # every colliding-id pair exactly once, merge order (id, table)

    def test_search_cursor_walk_odd_limit(self, client: TestClient, api_db) -> None:
        """limit=3 (odd) lands exactly on a tie boundary — still 6 distinct rows."""
        seen = _walk(client, {"q": "a", "limit": 3})
        assert len(seen) == 6
        assert len(set(seen)) == 6

    def test_next_cursor_is_composite_id_table(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/search", params={"q": "a", "limit": 1})
        data = resp.json()
        assert data["has_more"] is True
        assert data["next_cursor"] == "1:dictionary"

    def test_single_table_short_circuit_with_composite_cursor(self, client: TestClient, dict_only_db) -> None:
        """q hitting only one table still paginates with the composite format."""
        seen = _walk(client, {"q": "a", "limit": 1})
        assert seen == [(1, "dictionary"), (2, "dictionary"), (3, "dictionary")]

    @pytest.mark.parametrize(
        "bad_cursor",
        ["5", "abc", "1:", "1:nope", "dictionary:1", "1:dictionary:2", "-1:dictionary", "1.5:dictionary"],
    )
    def test_malformed_cursor_is_400(self, client: TestClient, api_db, bad_cursor: str) -> None:
        resp = client.get("/api/v1/lexicon/search", params={"q": "a", "cursor": bad_cursor})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_cursor"

    def test_search_requires_q(self, client: TestClient, api_db) -> None:
        assert client.get("/api/v1/lexicon/search").status_code == 422

    def test_search_rejects_bad_limits(self, client: TestClient, api_db) -> None:
        assert client.get("/api/v1/lexicon/search", params={"q": "a", "limit": 0}).status_code == 422
        assert client.get("/api/v1/lexicon/search", params={"q": "a", "limit": 501}).status_code == 422

    def test_search_no_match_is_empty_page(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/search", params={"q": "zzzz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_search_sets_cache_control(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/lexicon/search", params={"q": "a"})
        assert resp.headers["cache-control"].startswith("public, max-age=")

    def test_search_route_wins_over_word_path(self, client: TestClient, api_db) -> None:
        """/lexicon/search must not be captured by /lexicon/{word}."""
        resp = client.get("/api/v1/lexicon/search", params={"q": "x"})
        assert resp.status_code == 200
        assert "zo_en" not in resp.json()


# ---------------------------------------------------------------------------
# GET/POST /api/v1/linguistics/{pos,syllable} + foundation aliases
# ---------------------------------------------------------------------------


class TestLinguisticsRoutes:
    def test_pos_get_tags_sentence(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/linguistics/pos", params={"text": "Pasian in vantung a piangsak hi."})
        assert resp.status_code == 200
        tokens = resp.json()["tokens"]
        by_word = {t["word"]: t["pos"] for t in tokens}
        assert by_word["in"] == "PART.ERG"  # ergative marker
        assert by_word["a"] == "PRON"
        assert len(tokens) == 6

    def test_pos_post_twin(self, client: TestClient, api_db) -> None:
        resp = client.post("/api/v1/linguistics/pos", json={"text": "ka pai hi"})
        assert resp.status_code == 200
        assert resp.json()["text"] == "ka pai hi"

    def test_pos_requires_text(self, client: TestClient, api_db) -> None:
        assert client.get("/api/v1/linguistics/pos").status_code == 422
        assert client.post("/api/v1/linguistics/pos", json={"text": ""}).status_code == 422

    def test_syllable_get_segments_word(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/linguistics/syllable", params={"word": "vantung"})
        assert resp.status_code == 200
        assert resp.json()["syllables"] == ["van", "tung"]
        assert resp.json()["count"] == 2

    def test_syllable_post_twin(self, client: TestClient, api_db) -> None:
        resp = client.post("/api/v1/linguistics/syllable", json={"word": "pasian"})
        assert resp.status_code == 200
        assert resp.json()["syllables"] == ["pa", "sian"]

    def test_foundation_aliases_are_same_callables(self, api_db) -> None:
        """Migrate-not-rename: each alias mounts the foundation handler itself."""
        from fastapi.routing import APIRoute

        from zolai.api.foundation_router import router as foundation_router
        from zolai.api.linguistics_router import router as linguistics_router

        foundation = {
            route.path: route.endpoint
            for route in foundation_router.routes
            if isinstance(route, APIRoute)
            and (route.path.startswith("/foundation/analyze/") or route.path.startswith("/foundation/search/"))
        }
        aliases = {
            route.path: route.endpoint
            for route in linguistics_router.routes
            if isinstance(route, APIRoute)
            and ("/analyze/" in route.path or "/search/" in route.path)
        }
        assert foundation, "foundation analysis/search routes missing"
        assert len(foundation) == 7
        for fpath, endpoint in foundation.items():
            alias_path = "/api/v1/linguistics" + fpath[len("/foundation") :]
            assert alias_path in aliases, alias_path
            assert aliases[alias_path] is endpoint

    def test_alias_is_reachable_on_the_app(self, client: TestClient, api_db) -> None:
        """The alias answers (not the catch-all): the shared handler runs.

        The corpus analyzer singleton decides 200 vs 500 depending on which DB
        it first saw — what matters here is that BOTH paths reach the same
        handler (the GET-only catch-all would answer 405 to these POSTs).
        """
        resp = client.post("/api/v1/linguistics/analyze/corpus", json={"text": "ka pai hi"})
        original = client.post("/api/v1/foundation/analyze/corpus", json={"text": "ka pai hi"})
        assert resp.status_code in (200, 500)
        assert resp.status_code == original.status_code
        if resp.status_code == 200:
            assert set(resp.json()) == set(original.json())


# ---------------------------------------------------------------------------
# Auth — enforce 401 / 403 wrong scope / 200 with the right scope
# ---------------------------------------------------------------------------


class TestLexiconAuth:
    def test_enforce_missing_key_is_401(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        resp = client.get("/api/v1/lexicon/pasian")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "unauthorized"

    def test_enforce_wrong_scope_is_403(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="pos-only", scopes=["pos:read"])
        resp = client.get("/api/v1/lexicon/pasian", headers=_headers(key))
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["action"] == "dataset:read"
        assert detail["reason"] == "missing_scope"

    def test_enforce_dataset_read_key_is_200(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="reader", scopes=["dataset:read"])
        resp = client.get("/api/v1/lexicon/search", params={"q": "a"}, headers=_headers(key))
        assert resp.status_code == 200

    def test_warn_mode_still_dual_accepts(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        monkeypatch.delenv("ZOLAI_API_AUTH", raising=False)
        assert client.get("/api/v1/lexicon/pasian").status_code == 200

    def test_enforce_pos_scope_enforced_on_pos_route(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        wrong = auth.create_api_key(name="data", scopes=["dataset:read"])
        resp = client.get("/api/v1/linguistics/pos", params={"text": "ka"}, headers=_headers(wrong))
        assert resp.status_code == 403
        assert resp.json()["detail"]["action"] == "pos:read"

        right = auth.create_api_key(name="pos", scopes=["pos:read"])
        resp = client.get("/api/v1/linguistics/pos", params={"text": "ka"}, headers=_headers(right))
        assert resp.status_code == 200
