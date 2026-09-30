"""Tests for the record-correction review API (P1 /api/v1 core surface).

``PATCH /api/v1/review/records/{table}/{row_id}`` — body
``{corrected_fields: {column: value}, reason: "..."}``, scope ``dataset:edit``.

Covers:

- happy path: column updated, ``review_status`` stamped ``reviewed`` **only
  where the column exists** (L1.3 lexicon tables; no ALTER ever), one
  ``data_audit_log`` row per written field (old → new, actor + reason)
- tables without ``review_status`` (``phrases``) are corrected without a
  schema change
- 400 (non-whitelisted table / unknown or immutable ``id`` column), 404
  (missing row), 422 (bad body)
- auth: enforce 401 without a key, 403 for a wrong scope (``dataset:read``
  ≠ ``dataset:edit``), 200 with ``dataset:edit``; warn-mode dual-accept
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

#: ``dictionary``/``vocabulary`` carry the L1.3 ``review_status`` column;
#: ``phrases`` deliberately does NOT (proves "no ALTER, where present only").
_SEED_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT,
    english TEXT,
    pos TEXT,
    review_status TEXT DEFAULT 'unknown'
);
INSERT INTO dictionary VALUES
    (1, 'pasian', 'Gpd', 'ver', 'unknown'),
    (2, 'gam', 'earth, land', 'noun', 'unknown');
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY,
    headword TEXT,
    english TEXT,
    review_status TEXT DEFAULT 'unknown'
);
INSERT INTO vocabulary VALUES (1, 'khem', 'lie, deceive', 'unknown');
CREATE TABLE phrases (
    id INTEGER PRIMARY KEY,
    zolai TEXT,
    english TEXT,
    examples TEXT
);
INSERT INTO phrases VALUES (1, 'a pai hi', 'he go', 'A pai hi.');
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
    """Seeded throwaway review DB wired into config.paths.db."""
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


def _patch(client: TestClient, table: str, row_id: int, fields: dict, reason: str = "typo fix"):
    return client.patch(
        f"/api/v1/review/records/{table}/{row_id}",
        json={"corrected_fields": fields, "reason": reason},
    )


def _row(db_path: Path, table: str, row_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Happy path — correction + review_status + audit
# ---------------------------------------------------------------------------


class TestPatchDictionary:
    def test_corrects_field_and_stamps_review_status(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "dictionary", 1, {"english": "God"})
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "table": "dictionary",
            "id": 1,
            "updated_fields": ["english", "review_status"],
            "review_status": "reviewed",
            "audit_rows": 2,
        }
        row = _row(api_db, "dictionary", 1)
        assert row["english"] == "God"
        assert row["review_status"] == "reviewed"

    def test_audit_rows_written_old_to_new(self, client: TestClient, api_db) -> None:
        _patch(client, "dictionary", 1, {"english": "God"})
        resp = client.get("/api/v1/audit", params={"table": "dictionary", "row_id": 1})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert {i["field"] for i in items} == {"english", "review_status"}
        english = next(i for i in items if i["field"] == "english")
        assert english["old_value"] == "Gpd"
        assert english["new_value"] == "God"
        assert "typo fix" in english["reason"]
        status = next(i for i in items if i["field"] == "review_status")
        assert status["old_value"] == "unknown"
        assert status["new_value"] == "reviewed"

    def test_multiple_fields_one_patch(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "dictionary", 2, {"english": "land", "pos": "noun"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated_fields"] == ["english", "pos", "review_status"]
        assert data["audit_rows"] == 3

    def test_second_patch_skips_already_reviewed_status(self, client: TestClient, api_db) -> None:
        """review_status already ``reviewed`` → no duplicate audit row."""
        _patch(client, "dictionary", 1, {"english": "God"})
        resp = _patch(client, "dictionary", 1, {"pos": "noun"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated_fields"] == ["pos"]
        assert data["audit_rows"] == 1
        assert data["review_status"] == "reviewed"

    def test_null_value_is_allowed(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "dictionary", 1, {"english": None})
        assert resp.status_code == 200
        row = _row(api_db, "dictionary", 1)
        assert row["english"] is None

        audit = client.get("/api/v1/audit", params={"table": "dictionary", "row_id": 1}).json()
        english = next(i for i in audit["items"] if i["field"] == "english")
        assert english["old_value"] == "Gpd"
        assert english["new_value"] is None  # None stays NULL


class TestPatchOtherTables:
    def test_vocabulary_review_status_where_present(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "vocabulary", 1, {"english": "lie"})
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "reviewed"
        assert _row(api_db, "vocabulary", 1)["review_status"] == "reviewed"

    def test_phrases_without_review_status_no_schema_change(
        self, client: TestClient, api_db
    ) -> None:
        before = _columns(api_db, "phrases")
        resp = _patch(client, "phrases", 1, {"english": "he goes"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["review_status"] is None
        assert data["updated_fields"] == ["english"]
        assert data["audit_rows"] == 1
        assert _columns(api_db, "phrases") == before  # no ALTER, no new column
        assert "review_status" not in _columns(api_db, "phrases")

        row = _row(api_db, "phrases", 1)
        assert row["english"] == "he goes"

        audit = client.get("/api/v1/audit", params={"table": "phrases", "row_id": 1}).json()
        items = audit["items"]
        assert [i["field"] for i in items] == ["english"]
        assert items[0]["old_value"] == "he go"
        assert items[0]["new_value"] == "he goes"


# ---------------------------------------------------------------------------
# Errors — 400 / 404 / 422
# ---------------------------------------------------------------------------


class TestPatchErrors:
    @pytest.mark.parametrize("table", ["api_keys", "data_audit_log", "dictionary_import"])
    def test_non_whitelisted_table_is_400(self, client: TestClient, api_db, table: str) -> None:
        resp = _patch(client, table, 1, {"english": "x"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "table_not_allowed"
        assert detail["table"] == table
        assert "dictionary" in detail["allowed"]

    def test_unknown_column_is_400(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "dictionary", 1, {"not_a_column": "x"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "column_not_correctable"
        assert detail["field"] == "not_a_column"

    def test_id_is_immutable(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "dictionary", 1, {"id": 2})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "column_not_correctable"
        assert _row(api_db, "dictionary", 1)["id"] == 1  # unchanged

    def test_missing_row_is_404(self, client: TestClient, api_db) -> None:
        resp = _patch(client, "dictionary", 999, {"english": "x"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "row_not_found"

    def test_missing_reason_is_422(self, client: TestClient, api_db) -> None:
        resp = client.patch(
            "/api/v1/review/records/dictionary/1",
            json={"corrected_fields": {"english": "God"}},
        )
        assert resp.status_code == 422

    def test_empty_corrected_fields_is_422(self, client: TestClient, api_db) -> None:
        resp = client.patch(
            "/api/v1/review/records/dictionary/1",
            json={"corrected_fields": {}, "reason": "nope"},
        )
        assert resp.status_code == 422

    def test_missing_body_field_is_422(self, client: TestClient, api_db) -> None:
        resp = client.patch(
            "/api/v1/review/records/dictionary/1",
            json={"reason": "no fields at all"},
        )
        assert resp.status_code == 422

    def test_non_numeric_row_id_is_422(self, client: TestClient, api_db) -> None:
        resp = client.patch(
            "/api/v1/review/records/dictionary/not-a-number",
            json={"corrected_fields": {"english": "x"}, "reason": "typo"},
        )
        assert resp.status_code == 422

    def test_failed_patch_writes_nothing(self, client: TestClient, api_db) -> None:
        """Validation failures happen before any UPDATE/audit INSERT."""
        _patch(client, "dictionary", 1, {"english": "God"})  # audit now has 2 rows
        resp = _patch(client, "dictionary", 1, {"nope": "x"})
        assert resp.status_code == 400
        assert _row(api_db, "dictionary", 1)["english"] == "God"  # first patch only
        audit = client.get("/api/v1/audit", params={"table": "dictionary", "row_id": 1}).json()
        assert len(audit["items"]) == 2  # the failed patch added none


# ---------------------------------------------------------------------------
# Auth — enforce 401 / 403 wrong scope / 200 with dataset:edit
# ---------------------------------------------------------------------------


class TestPatchAuth:
    def test_enforce_missing_key_is_401(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        resp = _patch(client, "dictionary", 1, {"english": "God"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "unauthorized"
        assert _row(api_db, "dictionary", 1)["english"] == "Gpd"  # untouched

    def test_enforce_read_key_cannot_edit(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="reader", scopes=["dataset:read"])
        resp = client.patch(
            "/api/v1/review/records/dictionary/1",
            json={"corrected_fields": {"english": "God"}, "reason": "typo fix"},
            headers=_headers(key),
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["action"] == "dataset:edit"
        assert detail["reason"] == "missing_scope"
        assert _row(api_db, "dictionary", 1)["english"] == "Gpd"  # untouched

    def test_enforce_edit_key_is_200_and_actor_is_key_prefix(
        self, client: TestClient, api_db, auth_db, monkeypatch
    ) -> None:
        _enforce(monkeypatch)
        key = auth.create_api_key(name="reviewer", scopes=["dataset:edit", "audit:read"])
        resp = client.patch(
            "/api/v1/review/records/dictionary/1",
            json={"corrected_fields": {"english": "God"}, "reason": "typo fix"},
            headers=_headers(key),
        )
        assert resp.status_code == 200
        assert _row(api_db, "dictionary", 1)["english"] == "God"

        # Actor comes from the presented key (never client-supplied).
        audit = client.get(
            "/api/v1/audit",
            params={"table": "dictionary", "row_id": 1},
            headers=_headers(key),
        ).json()
        english = next(i for i in audit["items"] if i["field"] == "english")
        assert key["key_prefix"] in english["reason"]
        assert "typo fix" in english["reason"]

    def test_warn_mode_still_dual_accepts(self, client: TestClient, api_db, auth_db, monkeypatch) -> None:
        monkeypatch.delenv("ZOLAI_API_AUTH", raising=False)
        resp = _patch(client, "dictionary", 1, {"english": "God"})
        assert resp.status_code == 200
        # No presented key → actor falls back to the generic "api".
        audit = client.get("/api/v1/audit", params={"table": "dictionary", "row_id": 1}).json()
        english = next(i for i in audit["items"] if i["field"] == "english")
        assert "record_review by api:" in english["reason"]
