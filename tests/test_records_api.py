"""Tests for the records + audit read API (P1 /api/v1 core surface).

Covers:

- ``GET /api/v1/records`` — whitelisted row reads (``RECORDS_WHITELIST``),
  free-text ``q`` with LIKE-wildcard escaping, ``{items, next_cursor,
  has_more}`` cursor pagination (api-design §4: default 50, cap 500)
- sensitive tables rejected with **400** *before* a query runs —
  ``api_keys``, ``data_audit_log`` and every ``*_import`` staging table
- ``GET /api/v1/audit`` — read-only ``data_audit_log`` tail, newest first,
  cursor walks downwards (``id < cursor``), scope ``audit:read``
- auth: enforce 401 without a key, 403 for a wrong scope (including
  ``dataset:read`` being insufficient for ``/audit``), 200 with the right
  scope; warn-mode dual-accept unchanged
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
    zolai TEXT,
    english TEXT,
    myanmar TEXT,
    pos TEXT
);
INSERT INTO dictionary VALUES
    (1, 'pasian', 'God', 'bu-1', 'noun'),
    (2, 'gam', 'earth, land', 'bu-2', 'noun'),
    (3, 'tapa', 'son, life', 'bu-3', 'noun');
CREATE TABLE phrases (
    id INTEGER PRIMARY KEY,
    zolai TEXT,
    english TEXT,
    examples TEXT,
    frequency INTEGER
);
INSERT INTO phrases VALUES
    (1, 'a pai hi', 'he goes', 'A pai hi.', 10),
    (2, 'ka mu khin hi', 'I have seen', 'Ka mu khin hi.', 5);
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY,
    headword TEXT,
    english TEXT,
    examples TEXT
);
INSERT INTO vocabulary VALUES (1, 'khem', 'lie, deceive', 'khem a hi');
CREATE TABLE data_audit_log (
    id INTEGER PRIMARY KEY,
    table_name TEXT,
    row_id INTEGER,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT,
    reason TEXT
);
INSERT INTO data_audit_log VALUES
    (1, 'dictionary', 1, 'english', 'Gpd', 'God', '2026-09-01 10:00:00', 'seed one'),
    (2, 'dictionary', 1, 'pos', 'ver', 'noun', '2026-09-02 11:00:00', 'seed two'),
    (3, 'phrases', 1, 'english', 'he go', 'he goes', '2026-09-03 12:00:00', 'seed three');
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


@pytest.fixture()
def api_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Seeded throwaway records DB wired into config.paths.db."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db_path)
    yield db_path


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


# ---------------------------------------------------------------------------
# GET /api/v1/records
# ---------------------------------------------------------------------------


class TestRecordsList:
    def test_lists_whitelisted_dictionary(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/records", params={"table": "dictionary"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["table"] == "dictionary"
        assert [item["id"] for item in data["items"]] == [1, 2, 3]  # ascending
        assert data["has_more"] is False
        assert data["next_cursor"] is None
        assert data["items"][0]["zolai"] == "pasian"

    def test_q_filters_over_search_fields(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/records", params={"table": "dictionary", "q": "earth"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == 2

    def test_q_escapes_like_wildcards(self, client: TestClient, api_db) -> None:
        """``%``/``_`` in user input must match literally, not everything."""
        resp = client.get("/api/v1/records", params={"table": "dictionary", "q": "%"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        resp = client.get("/api/v1/records", params={"table": "dictionary", "q": "_"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_pagination_cursor_walk(self, client: TestClient, api_db) -> None:
        page1 = client.get("/api/v1/records", params={"table": "dictionary", "limit": 2}).json()
        assert [i["id"] for i in page1["items"]] == [1, 2]
        assert page1["has_more"] is True
        assert page1["next_cursor"] == "2"

        page2 = client.get(
            "/api/v1/records",
            params={"table": "dictionary", "limit": 2, "cursor": page1["next_cursor"]},
        ).json()
        assert [i["id"] for i in page2["items"]] == [3]
        assert page2["has_more"] is False
        assert page2["next_cursor"] is None

    def test_bad_table_is_400_with_allowed_list(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/records", params={"table": "api_keys"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "table_not_allowed"
        assert detail["table"] == "api_keys"
        assert "dictionary" in detail["allowed"]

    @pytest.mark.parametrize(
        "table",
        ["api_keys", "data_audit_log", "dictionary_en_zo_import", "dictionary_import", "zz_not_a_table"],
    )
    def test_sensitive_or_unknown_tables_rejected(self, client: TestClient, api_db, table: str) -> None:
        """Rejection happens on the whitelist — before any query can run."""
        resp = client.get("/api/v1/records", params={"table": table})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "table_not_allowed"

    def test_missing_table_param_is_422(self, client: TestClient, api_db) -> None:
        assert client.get("/api/v1/records").status_code == 422

    def test_limit_bounds_are_422(self, client: TestClient, api_db) -> None:
        assert client.get("/api/v1/records", params={"table": "dictionary", "limit": 0}).status_code == 422
        assert (
            client.get("/api/v1/records", params={"table": "dictionary", "limit": 501}).status_code
            == 422
        )

    def test_cursor_must_be_non_negative(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/records", params={"table": "dictionary", "cursor": -1})
        assert resp.status_code == 422

    def test_whitelisted_but_missing_table_is_500(self, client: TestClient, api_db) -> None:
        """Allowed by the whitelist, absent in this throwaway DB → 500."""
        resp = client.get("/api/v1/records", params={"table": "bible_verses"})
        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "table_unavailable"


# ---------------------------------------------------------------------------
# GET /api/v1/audit
# ---------------------------------------------------------------------------


class TestAuditTail:
    def test_default_is_newest_first(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert [item["id"] for item in data["items"]] == [3, 2, 1]
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_filter_by_table_and_row(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/audit", params={"table": "dictionary", "row_id": 1})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [i["id"] for i in items] == [2, 1]
        assert {i["field"] for i in items} == {"english", "pos"}

    def test_pagination_walks_downwards(self, client: TestClient, api_db) -> None:
        seen: list[int] = []
        cursor = None
        for _ in range(5):
            params: dict = {"limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            data = client.get("/api/v1/audit", params=params).json()
            seen.extend(item["id"] for item in data["items"])
            if not data["has_more"]:
                assert data["next_cursor"] is None
                break
            cursor = data["next_cursor"]
        else:  # pragma: no cover — pagination must terminate
            pytest.fail("audit pagination did not terminate")

        assert seen == [3, 2, 1]  # newest → oldest, no duplicates

    def test_unknown_row_is_empty_page(self, client: TestClient, api_db) -> None:
        resp = client.get("/api/v1/audit", params={"table": "dictionary", "row_id": 999})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_limit_bounds_are_422(self, client: TestClient, api_db) -> None:
        assert client.get("/api/v1/audit", params={"limit": 0}).status_code == 422
        assert client.get("/api/v1/audit", params={"limit": 501}).status_code == 422


# ---------------------------------------------------------------------------
# Auth — enforce 401 / 403 wrong scope / 200 with the right scope
# ---------------------------------------------------------------------------


class TestRecordsAuth:
    def test_enforce_missing_key_is_401(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        resp = client.get("/api/v1/records", params={"table": "dictionary"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "unauthorized"

    def test_enforce_wrong_scope_is_403(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="pos-only", scopes=["pos:read"])
        resp = client.get("/api/v1/records", params={"table": "dictionary"}, headers=_headers(key))
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["action"] == "dataset:read"
        assert detail["reason"] == "missing_scope"

    def test_enforce_dataset_read_key_is_200(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="reader", scopes=["dataset:read"])
        resp = client.get("/api/v1/records", params={"table": "dictionary"}, headers=_headers(key))
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 3

    def test_audit_needs_audit_read_not_dataset_read(
        self, client: TestClient, api_db, auth_db, monkeypatch
    ) -> None:
        _enforce(monkeypatch)
        dataset_key = auth.create_api_key(name="reader", scopes=["dataset:read"])
        resp = client.get("/api/v1/audit", headers=_headers(dataset_key))
        assert resp.status_code == 403
        assert resp.json()["detail"]["action"] == "audit:read"

        audit_key = auth.create_api_key(name="auditor", scopes=["audit:read"])
        resp = client.get("/api/v1/audit", headers=_headers(audit_key))
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 3

    def test_enforce_missing_key_on_audit_is_401(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        assert client.get("/api/v1/audit").status_code == 401

    def test_warn_mode_still_dual_accepts(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        monkeypatch.delenv("ZOLAI_API_AUTH", raising=False)
        assert client.get("/api/v1/records", params={"table": "dictionary"}).status_code == 200
        assert client.get("/api/v1/audit").status_code == 200
