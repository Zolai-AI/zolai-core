"""Stage 1 contract tests for GET /api/v1/auth/capabilities.

Additive, public-safe, server-authoritative. No secrets/tokens/passwords.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth, rbac, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    create_ai_provider_tables,
    create_api_keys_table,
    create_assistant_chat_feedback_table,
    create_notification_tables,
    create_user_session_tables,
)

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _clean_auth_state() -> Iterator[None]:
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
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    from zolai.data import database as database_mod

    db_path = tmp_path / "capabilities.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_api_keys_table(mgr)
    create_user_session_tables(mgr)
    create_ai_provider_tables(mgr)
    create_notification_tables(mgr)
    create_assistant_chat_feedback_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(session_auth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(config.paths, "db", db_path)
    database_mod.reset_manager()
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _session_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


FORBIDDEN_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "plaintext",
    "key_hash",
    "secret",
    "api_key",
    "authorization",
}


def _assert_no_secret_leak(payload: dict) -> None:
    blob = str(payload).lower()
    for key in FORBIDDEN_KEYS:
        assert key not in payload
    assert "zolai_sk_" not in blob
    assert "zolai_ss_" not in blob
    assert "zolai_at_" not in blob
    assert PASSWORD not in blob


class TestCapabilitiesContract:
    def test_public_anonymous_never_401(self, client, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = client.get("/api/v1/auth/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "anonymous"
        assert body["canonical_role"] == "anonymous"
        assert body["scopes"] == []
        assert body["auth_source"] == "anonymous"
        assert body["mode"] == "enforce"
        assert body["contract_version"] == rbac.CAPABILITIES_CONTRACT_VERSION
        assert isinstance(body["capabilities"], dict)
        assert isinstance(body["feature_flags"], dict)
        _assert_no_secret_leak(body)

    def test_capabilities_shape_is_additive_and_public_safe(self, client, auth_db) -> None:
        resp = client.get("/api/v1/auth/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "role",
            "canonical_role",
            "scopes",
            "capabilities",
            "feature_flags",
            "auth_source",
            "mode",
            "contract_version",
        }
        assert set(body["capabilities"]) >= {
            "can_read_public",
            "can_chat_assistant",
            "can_run_agent",
            "can_read_agent",
            "can_manage_users",
            "can_manage_api_keys",
            "can_manage_settings",
            "can_manage_roles",
            "can_view_audit",
        }
        _assert_no_secret_leak(body)

    def test_member_key_source_and_scopes(self, client, auth_db) -> None:
        key = auth.create_api_key(name="cap-member", scopes=["dataset:read", "agent:read"], created_by="test")
        resp = client.get("/api/v1/auth/capabilities", headers=_headers(key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "member"
        assert body["canonical_role"] == "member"
        assert body["auth_source"] == "api_key"
        assert body["scopes"] == ["dataset:read", "agent:read"]
        assert body["capabilities"]["can_read_agent"] is True
        assert body["capabilities"]["can_run_agent"] is False
        assert body["capabilities"]["can_manage_settings"] is False
        _assert_no_secret_leak(body)

    def test_admin_key_role_source(self, client, auth_db) -> None:
        key = auth.create_api_key(name="cap-admin", scopes=["settings:write"], created_by="test")
        body = client.get("/api/v1/auth/capabilities", headers=_headers(key)).json()
        assert body["role"] == "admin"
        assert body["canonical_role"] == "admin"
        assert body["auth_source"] == "api_key"
        assert body["capabilities"]["can_manage_settings"] is True
        assert body["capabilities"]["can_manage_roles"] is False
        _assert_no_secret_leak(body)

    def test_superadmin_key_role_source(self, client, auth_db) -> None:
        key = auth.create_api_key(name="cap-super", scopes=["role:manage"], created_by="test")
        body = client.get("/api/v1/auth/capabilities", headers=_headers(key)).json()
        assert body["role"] == "superadmin"
        assert body["canonical_role"] == "superadmin"
        assert body["capabilities"]["can_manage_roles"] is True
        _assert_no_secret_leak(body)

    def test_session_like_record_shape_has_no_secret_leakage(self, client, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_AUTH_SESSIONS", "on")
        session_auth.create_user(username="cap-user", password=PASSWORD, role="admin", actor="test")
        token = session_auth.login("cap-user", PASSWORD)["token"]
        resp = client.get("/api/v1/auth/capabilities", headers=_session_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "admin"
        assert body["canonical_role"] == "admin"
        assert body["auth_source"] == "session"
        assert "username" not in body
        assert "display_name" not in body
        assert "user_id" not in body
        assert "expires_at" not in body
        assert "key_prefix" not in body
        _assert_no_secret_leak(body)

    def test_missing_identity_fields_tolerated(self) -> None:
        # Unit-level safety: resolver should degrade unknown/missing identity safely.
        assert rbac._capability_auth_source(type("R", (), {"state": type("S", (), {})()})(), None) == "anonymous"
        assert rbac._capability_auth_source(
            type("R", (), {"state": type("S", (), {"session": {"id": 1}})()})(),
            {"scopes": []},
        ) == "session"

    def test_auth_me_unchanged_contract_still_holds(self, client, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"role", "key_prefix", "scopes", "mode", "auth_source"}
        _assert_no_secret_leak(body)

    def test_frozen_scope_vocab_unchanged(self) -> None:
        assert len(auth.VALID_ACTIONS) == 33
        assert "role:manage" in auth.VALID_ACTIONS
        assert "agent:run" in auth.VALID_ACTIONS
