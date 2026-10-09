"""Admin user management API — scope/role gates and the CRUD happy paths.

``/api/v1/admin/users`` carries **both** router-level guards, and this file
proves each half separately:

- an anonymous caller is **401** in ``warn`` (``require_scope(..., strict=True)``
  and ``require_role("admin", strict=True)`` — only ``ZOLAI_API_AUTH=off``
  bypasses);
- a presented key without ``user:manage`` is **403** with
  ``reason=missing_scope``;
- a ``user:manage`` key (which is an admin-scoped key) is **200**.

Happy paths: create (optional ``role``) / list / enable / disable / role change
/ password / revoke-sessions — every response sanitized (no ``password_hash``,
no token), real timestamps, and a ``data_audit_log`` row for every mutation.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from zolai.api import auth, rbac, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table, create_user_session_tables

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway store with ``users``/``sessions``/``api_keys``, wired in."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'admin_users.db'}")
    mgr.init_db()
    create_user_session_tables(mgr)
    create_api_keys_table(mgr)
    monkeypatch.setattr(session_auth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ZOLAI_AUTH_SESSIONS", "on")
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    session_auth.reset_session_cache()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    session_auth.reset_session_cache()
    reset_rate_limiter()


@pytest.fixture()
def admin_key(admin_db: DatabaseManager) -> dict:
    """``admin_db`` is requested first so ``auth`` is wired to the throwaway DB."""
    return auth.create_api_key(name="user-admin", scopes=["user:manage"], created_by="test")


@pytest.fixture()
def member_key(admin_db: DatabaseManager) -> dict:
    return auth.create_api_key(name="plain-member", scopes=["dataset:read"], created_by="test")


@pytest.fixture()
def founder(admin_db: DatabaseManager) -> dict:
    return session_auth.create_user(
        username="founder", password=PASSWORD, role="admin", actor="test"
    )


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _audit_rows(mgr: DatabaseManager) -> list[tuple]:
    with mgr.engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                text(
                    "SELECT table_name, field, old_value, new_value, reason "
                    "FROM data_audit_log ORDER BY id"
                )
            )
        ]


# ---------------------------------------------------------------------------
# Gates — 401 anon, 403 wrong scope, 200 admin key
# ---------------------------------------------------------------------------


class TestGates:
    def test_anonymous_is_401_even_in_warn(self, client) -> None:
        response = client.get("/api/v1/admin/users")
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"

    def test_member_key_is_403_missing_scope(self, client, member_key) -> None:
        response = client.get("/api/v1/admin/users", headers=_headers(member_key))
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["error"] == "forbidden"
        assert detail["reason"] == "missing_scope"
        assert detail["action"] == "user:manage"

    def test_admin_key_is_allowed(self, client, admin_key) -> None:
        response = client.get("/api/v1/admin/users", headers=_headers(admin_key))
        assert response.status_code == 200
        assert response.json() == {"items": [], "count": 0}

    def test_off_mode_bypasses(self, client, admin_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "off")
        assert client.get("/api/v1/admin/users").status_code == 200

    def test_route_is_classified_admin(self) -> None:
        assert rbac.classify_route("GET", "/api/v1/admin/users") == "admin"
        assert rbac.classify_route("POST", "/api/v1/admin/users") == "admin"
        assert rbac.is_public_path("GET", "/api/v1/admin/users") is False

    def test_scope_vocabulary_has_user_manage(self) -> None:
        assert "user:manage" in auth.VALID_ACTIONS


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPaths:
    def test_create_returns_201_without_hash_and_with_timestamps(
        self, client, admin_key, admin_db
    ) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "Ada", "password": PASSWORD, "role": "admin"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 201
        user = response.json()["user"]
        assert user["username"] == "ada"
        assert user["role"] == "admin"
        assert user["enabled"] == 1
        assert user["created_at"] and user["updated_at"]
        assert "password_hash" not in user
        assert "password" not in user
        raw = response.text
        assert PASSWORD not in raw
        assert "$argon2" not in raw

    def test_create_defaults_to_member_role(self, client, admin_key) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "bob", "password": PASSWORD},
            headers=_headers(admin_key),
        )
        assert response.status_code == 201
        assert response.json()["user"]["role"] == "member"

    def test_create_duplicate_is_409(self, client, admin_key, founder) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "founder", "password": PASSWORD},
            headers=_headers(admin_key),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "username_taken"

    def test_create_invalid_role_is_400(self, client, admin_key) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "carol", "password": PASSWORD, "role": "superuser"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "invalid_role"

    def test_list_shows_every_account_never_a_hash(
        self, client, admin_key, founder, admin_db
    ) -> None:
        client.post(
            "/api/v1/admin/users",
            json={"username": "dana", "password": PASSWORD},
            headers=_headers(admin_key),
        )
        response = client.get("/api/v1/admin/users", headers=_headers(admin_key))
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        names = {item["username"] for item in body["items"]}
        assert names == {"founder", "dana"}
        assert "password_hash" not in response.text
        assert "$argon2" not in response.text

    def test_enable_and_disable_round_trip(self, client, admin_key, admin_db) -> None:
        client.post(
            "/api/v1/admin/users",
            json={"username": "erin", "password": PASSWORD},
            headers=_headers(admin_key),
        )
        disabled = client.put(
            "/api/v1/admin/users/erin",
            json={"enabled": False},
            headers=_headers(admin_key),
        )
        assert disabled.status_code == 200
        assert disabled.json()["user"]["enabled"] == 0

        enabled = client.put(
            "/api/v1/admin/users/erin",
            json={"enabled": True},
            headers=_headers(admin_key),
        )
        assert enabled.status_code == 200
        assert enabled.json()["user"]["enabled"] == 1

        reasons = [row[4] for row in _audit_rows(admin_db)]
        assert any("disabled" in reason for reason in reasons)
        assert any("enabled" in reason for reason in reasons)

    def test_role_change_updates_row_and_writes_audit(
        self, client, admin_key, founder, admin_db
    ) -> None:
        response = client.put(
            "/api/v1/admin/users/founder",
            json={"role": "member"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "member"
        assert session_auth.get_user("founder")["role"] == "member"

        role_rows = [row for row in _audit_rows(admin_db) if row[1] == "role"]
        assert role_rows, "a role change must be audited"
        assert role_rows[0][2:4] == ("admin", "member")
        assert "founder" in role_rows[0][4]

    def test_role_change_unknown_user_is_404(self, client, admin_key) -> None:
        response = client.put(
            "/api/v1/admin/users/ghost",
            json={"role": "admin"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "user_not_found"

    def test_role_change_invalid_role_is_400(self, client, admin_key, founder) -> None:
        response = client.put(
            "/api/v1/admin/users/founder",
            json={"role": "root"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "invalid_role"

    def test_empty_update_is_400(self, client, admin_key, founder) -> None:
        response = client.put(
            "/api/v1/admin/users/founder", json={}, headers=_headers(admin_key)
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "empty_update"

    def test_password_change_never_echoes_a_hash(
        self, client, admin_key, founder, admin_db
    ) -> None:
        response = client.put(
            "/api/v1/admin/users/founder/password",
            json={"password": "a brand new passphrase"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 200
        user = response.json()["user"]
        assert "password_hash" not in user
        assert "a brand new passphrase" not in response.text
        assert session_auth.verify_password_hash(
            session_auth.get_user("founder")["password_hash"], "a brand new passphrase"
        )
        assert any(row[1] == "password" for row in _audit_rows(admin_db))

    def test_revoke_sessions_reports_a_count_only(
        self, client, admin_key, founder, admin_db
    ) -> None:
        session = session_auth.issue_session(int(founder["id"]))
        response = client.post(
            "/api/v1/admin/users/founder/revoke-sessions", headers=_headers(admin_key)
        )
        assert response.status_code == 200
        body = response.json()
        assert body == {"username": "founder", "revoked": 1}
        assert session["token"] not in response.text
        assert session_auth.resolve_session(session["token"]) is None

    def test_session_actor_is_recorded_on_create(
        self, client, admin_key, founder, admin_db
    ) -> None:
        """The acting identity comes from ``request.state.session`` when present."""
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "founder", "password": PASSWORD},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "gina", "password": PASSWORD},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        assert response.json()["user"]["username"] == "gina"
        rows = _audit_rows(admin_db)
        assert any(
            row[1] == "create" and row[3] == "gina" and "by founder" in row[4] for row in rows
        ), rows
