"""Admin API-key endpoint tests — /api/v1/admin/api-keys.

Covers issue/list/rotate/revoke: plaintext returned once and never stored,
scope gate on every route, rotation semantics (issue-then-revoke), immediate
revocation, and ``data_audit_log`` rows on issue/rotate/revoke.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'admin.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch) -> None:
    """The admin surface is tested under real enforcement (401 without a key)."""
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")


@pytest.fixture()
def admin_key() -> dict:
    return auth.create_api_key(name="admin-key", scopes=["apikey:manage"], created_by="test")


@pytest.fixture()
def headers(admin_key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_key['plaintext']}"}


@pytest.fixture()
def target_key() -> dict:
    """Key under test for rotate/revoke.

    Deliberately *separate* from ``admin_key``: rotating or revoking the
    credential that authorizes the call would 401 the follow-up requests, so
    the observations need a stable admin session.
    """
    return auth.create_api_key(
        name="target-key", scopes=["apikey:manage"], created_by="test"
    )


def _db_values(mgr: DatabaseManager, table: str) -> list[str]:
    """Every textual value stored in ``table`` — used for plaintext scans."""
    with mgr.engine.connect() as conn:
        columns = [c["name"] for c in sa_inspect(mgr.engine).get_columns(table)]
        rows = conn.execute(text(f"SELECT * FROM {table}")).fetchall()
    values: list[str] = []
    for row in rows:
        for col in columns:
            value = row._mapping[col]
            if value is not None:
                values.append(str(value))
    return values


# ---------------------------------------------------------------------------
# Create / list
# ---------------------------------------------------------------------------


def test_create_returns_plaintext_once_and_db_stores_no_plaintext(
    client: TestClient, auth_db, headers
) -> None:
    created = client.post(
        "/api/v1/admin/api-keys",
        headers=headers,
        json={"name": "mcp-server", "scopes": ["dataset:read", "catalog:read"]},
    )
    assert created.status_code == 201
    body = created.json()
    plaintext = body["plaintext"]
    assert plaintext.startswith("zolai_sk_")
    assert body["key"]["key_prefix"] == plaintext[:16]
    assert body["key"]["key_hash"] == auth.hash_key(plaintext)

    # Plaintext is never persisted — scan every column of every api_keys row.
    stored = _db_values(auth_db, "api_keys")
    assert plaintext not in stored
    assert auth.hash_key(plaintext) in stored  # the hash IS at rest

    # Listing never reveals the secret again.
    listed = client.get("/api/v1/admin/api-keys", headers=headers)
    assert listed.status_code == 200
    assert plaintext not in listed.text
    items = listed.json()["items"]
    assert listed.json()["count"] == len(items)
    assert "mcp-server" in [item["name"] for item in items]
    assert "key_hash" in listed.text  # hash at rest is fine to expose


def test_create_requires_apikey_manage_scope(
    client: TestClient, auth_db, headers
) -> None:
    narrow = auth.create_api_key(name="reader", scopes=["dataset:read"])
    response = client.post(
        "/api/v1/admin/api-keys",
        headers={"Authorization": f"Bearer {narrow['plaintext']}"},
        json={"name": "x", "scopes": ["dataset:read"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["action"] == "apikey:manage"


def test_create_duplicate_name_is_409(client: TestClient, auth_db, headers) -> None:
    payload = {"name": "dupe", "scopes": ["dataset:read"]}
    assert client.post("/api/v1/admin/api-keys", headers=headers, json=payload).status_code == 201
    duplicate = client.post("/api/v1/admin/api-keys", headers=headers, json=payload)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]["error"]


def test_create_unknown_scope_is_422(client: TestClient, auth_db, headers) -> None:
    response = client.post(
        "/api/v1/admin/api-keys",
        headers=headers,
        json={"name": "bogus", "scopes": ["not-an-action"]},
    )
    assert response.status_code == 422


def test_create_wildcard_and_resource_star_scopes_accepted(
    client: TestClient, auth_db, headers
) -> None:
    for scopes in (["*"], ["dataset:*"], ["*:read"]):
        response = client.post(
            "/api/v1/admin/api-keys",
            headers=headers,
            json={"name": f"k-{scopes[0].replace(':', '-').replace('*', 'all')}", "scopes": scopes},
        )
        assert response.status_code == 201, scopes


def test_create_with_expiry_records_expires_at(
    client: TestClient, auth_db, headers
) -> None:
    response = client.post(
        "/api/v1/admin/api-keys",
        headers=headers,
        json={"name": "temp", "scopes": ["dataset:read"], "expires_days": 30},
    )
    assert response.status_code == 201
    assert response.json()["key"]["expires_at"] is not None


# ---------------------------------------------------------------------------
# Rotate
# ---------------------------------------------------------------------------


def test_rotate_issues_new_key_and_revokes_old(
    client: TestClient, auth_db, headers, target_key
) -> None:
    rotated = client.post(f"/api/v1/admin/api-keys/{target_key['id']}/rotate", headers=headers)
    assert rotated.status_code == 200
    body = rotated.json()
    new_plaintext = body["plaintext"]
    new_id = body["key"]["id"]
    assert body["old_id"] == target_key["id"]
    assert new_plaintext != target_key["plaintext"]
    assert body["key"]["old_revoked_at"] is not None  # old key revocation reported
    assert body["key"]["revoked_at"] is None  # the NEW key is live

    # Old secret is dead immediately; new secret works.
    old = client.get(
        "/api/v1/admin/api-keys",
        headers={"Authorization": f"Bearer {target_key['plaintext']}"},
    )
    assert old.status_code == 401
    new = client.get(
        "/api/v1/admin/api-keys",
        headers={"Authorization": f"Bearer {new_plaintext}"},
    )
    assert new.status_code == 200

    # Rows: admin + rotated target + revoked original target.
    items = client.get("/api/v1/admin/api-keys", headers=headers).json()["items"]
    assert len(items) == 3
    by_id = {item["id"]: item for item in items}
    assert by_id[target_key["id"]]["revoked_at"] is not None
    assert by_id[new_id]["revoked_at"] is None

    # Audit trail: issue (new) + revoke (old).
    with auth_db.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT row_id, field FROM data_audit_log WHERE table_name = 'api_keys'")
        ).fetchall()
    events = {(r.row_id, r.field) for r in rows}
    assert (new_id, "create") in events
    assert (target_key["id"], "revoke") in events

    # New plaintext never persisted.
    assert new_plaintext not in _db_values(auth_db, "api_keys")


def test_rotate_unknown_key_is_404(client: TestClient, auth_db, headers) -> None:
    response = client.post("/api/v1/admin/api-keys/9999/rotate", headers=headers)
    assert response.status_code == 404


def test_rotate_already_revoked_is_409(
    client: TestClient, auth_db, headers, target_key
) -> None:
    assert (
        client.post(f"/api/v1/admin/api-keys/{target_key['id']}/revoke", headers=headers).status_code
        == 200
    )
    response = client.post(f"/api/v1/admin/api-keys/{target_key['id']}/rotate", headers=headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Revoke + audit
# ---------------------------------------------------------------------------


def test_revoke_is_immediately_enforced_and_audited(
    client: TestClient, auth_db, headers, target_key
) -> None:
    plaintext = target_key["plaintext"]
    assert client.get(
        "/api/v1/admin/api-keys", headers={"Authorization": f"Bearer {plaintext}"}
    ).status_code == 200

    revoked = client.post(f"/api/v1/admin/api-keys/{target_key['id']}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"]

    # Same secret, very next request → 401 (cache invalidated in-process).
    assert client.get(
        "/api/v1/admin/api-keys", headers={"Authorization": f"Bearer {plaintext}"}
    ).status_code == 401

    with auth_db.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT field, reason FROM data_audit_log "
                "WHERE table_name = 'api_keys' AND row_id = :id "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"id": target_key["id"]},
        ).first()
    assert row is not None
    assert row.field == "revoke"
    assert "apikey revoked" in row.reason


def test_revoke_twice_is_409_and_unknown_is_404(
    client: TestClient, auth_db, headers, target_key
) -> None:
    assert (
        client.post(f"/api/v1/admin/api-keys/{target_key['id']}/revoke", headers=headers).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/admin/api-keys/{target_key['id']}/revoke", headers=headers).status_code
        == 409
    )
    assert client.post("/api/v1/admin/api-keys/9999/revoke", headers=headers).status_code == 404


def test_audit_never_stores_the_secret(
    client: TestClient, auth_db, headers, admin_key
) -> None:
    client.post(f"/api/v1/admin/api-keys/{admin_key['id']}/revoke", headers=headers)
    stored = _db_values(auth_db, "data_audit_log")
    assert admin_key["plaintext"] not in stored
