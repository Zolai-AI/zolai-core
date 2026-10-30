"""The escalation paths the 4-role split was written to close.

Two HTTP surfaces could previously mint or apply a privilege: the admin-users
router (write ``users.role`` / disable an admin) and the api-keys router (mint a
``*`` / ``role:manage`` key).  The fix routes every role write through the single
guarded writer and caps key minting at the caller's own rank.  These tests prove
that over the real routes on a throwaway database:

- ``user:manage`` creates members and disables members, but a role write, an
  above-member create, or touching an admin/superadmin account is refused;
- ``apikey:manage`` mints keys at-or-below its own rank, never ``*`` or
  ``role:manage``, and rotating a legacy ``*`` key is refused outright rather
  than handing ``*`` back;
- nobody demotes/disables *themselves* and nobody removes the last enabled
  superadmin — until a second one exists;
- every successful role change is on ``data_audit_log`` with actor, transition.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from zolai.api import auth, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table, create_user_session_tables

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def guards_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway store with ``users``/``sessions``/``api_keys`` wired into auth."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'superadmin_guards.db'}")
    mgr.init_db()  # data_audit_log is an ORM table — this creates it
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
def admin_key(guards_db: DatabaseManager) -> dict:
    """``user:manage`` — an admin credential, no ``role:manage``."""
    return auth.create_api_key(name="user-admin", scopes=["user:manage"], created_by="test")


@pytest.fixture()
def superadmin_key(guards_db: DatabaseManager) -> dict:
    """``role:manage`` — the only credential that may write a role."""
    return auth.create_api_key(
        name="role-admin", scopes=["user:manage", "role:manage"], created_by="test"
    )


@pytest.fixture()
def key_admin(guards_db: DatabaseManager) -> dict:
    """``apikey:manage`` only — may mint keys, at-or-below admin rank."""
    return auth.create_api_key(name="key-admin", scopes=["apikey:manage"], created_by="test")


@pytest.fixture()
def superadmin_minter(guards_db: DatabaseManager) -> dict:
    """``apikey:manage`` + ``role:manage`` — the only minter allowed to reach ``*``."""
    return auth.create_api_key(
        name="sa-minter", scopes=["apikey:manage", "role:manage"], created_by="test"
    )


@pytest.fixture()
def founder(guards_db: DatabaseManager) -> dict:
    return session_auth.create_user(username="founder", password=PASSWORD, role="admin", actor="test")


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
# The admin-users surface
# ---------------------------------------------------------------------------


def test_admin_key_cannot_change_another_role(client, admin_key, founder) -> None:
    response = client.put(
        "/api/v1/admin/users/founder", json={"role": "member"}, headers=_headers(admin_key)
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "role_change_requires_superadmin"
    assert session_auth.get_user("founder")["role"] == "admin"


def test_admin_key_cannot_create_an_admin(client, admin_key) -> None:
    # NOTE: the code is role_change_requires_superadmin (the implementation's
    # actual slug), not target_requires_superadmin — see admin_users_router.create_user.
    response = client.post(
        "/api/v1/admin/users",
        json={"username": "escalated", "password": PASSWORD, "role": "admin"},
        headers=_headers(admin_key),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "role_change_requires_superadmin"
    assert session_auth.get_user("escalated") is None


def test_superadmin_key_creates_a_member(client, superadmin_key) -> None:
    response = client.post(
        "/api/v1/admin/users",
        json={"username": "plain", "password": PASSWORD, "role": "member"},
        headers=_headers(superadmin_key),
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "member"


def test_superadmin_session_cannot_demote_itself(client, guards_db) -> None:
    session_auth.create_user(username="solo", password=PASSWORD, role="superadmin", actor="test")
    token = session_auth.login("solo", PASSWORD)["token"]
    response = client.put(
        "/api/v1/admin/users/solo",
        json={"role": "member"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "self_role_change"


def test_superadmin_session_cannot_disable_itself(client, guards_db) -> None:
    session_auth.create_user(username="solo", password=PASSWORD, role="superadmin", actor="test")
    token = session_auth.login("solo", PASSWORD)["token"]
    response = client.put(
        "/api/v1/admin/users/solo",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "self_disable"


def test_demoting_the_last_superadmin_is_409_then_ok(client, guards_db, superadmin_key) -> None:
    # Only one enabled superadmin exists; the key is not a user, so it is not counted.
    session_auth.create_user(username="solo", password=PASSWORD, role="superadmin", actor="test")
    blocked = client.put(
        "/api/v1/admin/users/solo", json={"role": "member"}, headers=_headers(superadmin_key)
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "last_superadmin"

    # Promote a second, and the same demotion is now legal.
    session_auth.create_user(username="second", password=PASSWORD, role="member", actor="test")
    client.put(
        "/api/v1/admin/users/second",
        json={"role": "superadmin"},
        headers=_headers(superadmin_key),
    )
    ok = client.put(
        "/api/v1/admin/users/solo", json={"role": "member"}, headers=_headers(superadmin_key)
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "member"


def test_disabling_the_last_superadmin_is_409_then_ok(client, guards_db, superadmin_key) -> None:
    session_auth.create_user(username="solo", password=PASSWORD, role="superadmin", actor="test")
    blocked = client.put(
        "/api/v1/admin/users/solo", json={"enabled": False}, headers=_headers(superadmin_key)
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "last_superadmin"

    session_auth.create_user(username="second", password=PASSWORD, role="member", actor="test")
    client.put(
        "/api/v1/admin/users/second",
        json={"role": "superadmin"},
        headers=_headers(superadmin_key),
    )
    ok = client.put(
        "/api/v1/admin/users/solo", json={"enabled": False}, headers=_headers(superadmin_key)
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["enabled"] == 0


def test_admin_key_cannot_disable_an_admin_or_superadmin(
    client, guards_db, admin_key, founder
) -> None:
    session_auth.create_user(username="boss", password=PASSWORD, role="superadmin", actor="test")
    for target in ("founder", "boss"):
        response = client.put(
            f"/api/v1/admin/users/{target}", json={"enabled": False}, headers=_headers(admin_key)
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "target_requires_superadmin"


def test_admin_key_can_disable_a_member(client, admin_key) -> None:
    session_auth.create_user(username="wallflower", password=PASSWORD, actor="test")
    response = client.put(
        "/api/v1/admin/users/wallflower", json={"enabled": False}, headers=_headers(admin_key)
    )
    assert response.status_code == 200
    assert response.json()["user"]["enabled"] == 0


# ---------------------------------------------------------------------------
# The api-keys minting surface
# ---------------------------------------------------------------------------


def test_admin_minting_wildcard_is_refused(client, key_admin) -> None:
    response = client.post(
        "/api/v1/admin/api-keys",
        json={"name": "godmode", "scopes": ["*"]},
        headers=_headers(key_admin),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "scope_above_actor"


def test_admin_minting_role_manage_is_refused(client, key_admin) -> None:
    response = client.post(
        "/api/v1/admin/api-keys",
        json={"name": "rolekey", "scopes": ["role:manage"]},
        headers=_headers(key_admin),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "scope_above_actor"


def test_superadmin_can_mint_wildcard(client, superadmin_minter) -> None:
    response = client.post(
        "/api/v1/admin/api-keys",
        json={"name": "wild", "scopes": ["*"]},
        headers=_headers(superadmin_minter),
    )
    assert response.status_code == 201


def test_admin_rotating_a_legacy_wildcard_is_refused(client, guards_db, key_admin) -> None:
    # A legacy "*" key is one that predates the minting cap — created directly,
    # never through the route.  Rotating it must NOT hand "*" back to an admin.
    legacy = auth.create_api_key(name="legacy-wild", scopes=["*"], created_by="test")
    response = client.post(
        f"/api/v1/admin/api-keys/{legacy['id']}/rotate", headers=_headers(key_admin)
    )
    # The implementation *refuses* (403 scope_above_actor) rather than silently
    # narrowing the replacement — the honest reading of "does not hand * back".
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "scope_above_actor"


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


def test_successful_role_change_is_audited_with_actor_and_transition(
    client, guards_db, superadmin_key, founder
) -> None:
    response = client.put(
        "/api/v1/admin/users/founder", json={"role": "member"}, headers=_headers(superadmin_key)
    )
    assert response.status_code == 200
    role_rows = [r for r in _audit_rows(guards_db) if r[0] == "users" and r[1] == "role"]
    assert len(role_rows) == 1
    _table, _field, old_value, new_value, reason = role_rows[0]
    assert (old_value, new_value) == ("admin", "member")
    # The writer's reason format: "role of NAME 'old' -> 'new' by ACTOR (ROLE)".
    assert "->" in reason
    assert "role-admin" in reason  # the resolved actor label, never a caller-typed field
