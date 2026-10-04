"""P4 — assistants: public chat (anonymous, enforce-safe) + strict admin chat.

Covers the done-when:

- ``POST /api/v1/assistant/chat`` answers anonymously in **warn and enforce**
  (listed in ``rbac.PUBLIC_ROUTES``) with ``retrieval_only: true`` and
  ``citations`` — honest fallback, never fake generation;
- the public route never persists (``persist`` is admin-only);
- ``POST /api/v1/admin/assistant/chat`` is strict: anon 401, member 403,
  admin key with ``agent:run`` → 200 + tool-call trace, ``persist: true``
  writes an ``agent_runs`` row;
- both routes return the ZVS review block and never leak a marker.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_agent_runs import _SEED_SQL

from zolai.api import auth, rbac
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_ai_provider_tables, create_api_keys_table


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
def data_db(tmp_path, monkeypatch) -> Iterator[Path]:
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_ai_provider_tables(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
    yield db_path
    mgr.dispose()


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'assistant_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def admin_key() -> dict:
    return auth.create_api_key(name="assistant-admin", scopes=["*"], created_by="test")


@pytest.fixture()
def member_key() -> dict:
    return auth.create_api_key(name="assistant-member", scopes=["dataset:read"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


class TestPublicAssistant:
    @pytest.mark.parametrize("mode", ["warn", "enforce"])
    def test_anonymous_chat_open_in_every_mode(
        self, client, data_db, auth_db, monkeypatch, mode
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", mode)
        resp = client.post("/api/v1/assistant/chat", json={"message": "pasian"})
        assert resp.status_code == 200, (mode, resp.text)
        body = resp.json()
        assert body["retrieval_only"] is True  # rule mode, no key → honest fallback
        assert body["mode"] == "rule"
        assert isinstance(body["citations"], list) and body["citations"]
        assert body["tool_calls"]
        assert body["answer"].strip()
        assert "<<<TOOL" not in body["answer"]
        assert set(body["zvs"]) == {"valid", "violations_before", "violations_after"}

    def test_public_route_is_declared_public(self) -> None:
        assert ("POST", "/api/v1/assistant/chat") in rbac.PUBLIC_ROUTES

    def test_public_never_persists(self, client, data_db, auth_db) -> None:
        resp = client.post(
            "/api/v1/assistant/chat", json={"message": "define gam", "persist": True}
        )
        assert resp.status_code == 200
        assert "persisted_run_id" not in resp.json()
        conn = sqlite3.connect(data_db)
        # the store table may exist (created lazily) but must hold no rows
        try:
            count = conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        except sqlite3.OperationalError:
            count = 0
        finally:
            conn.close()
        assert count == 0

    def test_empty_message_rejected(self, client, data_db, auth_db) -> None:
        resp = client.post("/api/v1/assistant/chat", json={"message": ""})
        assert resp.status_code == 422


class TestAdminAssistant:
    def test_anonymous_is_401_in_warn_and_enforce(
        self, client, data_db, auth_db, monkeypatch
    ) -> None:
        for mode in ("warn", "enforce"):
            monkeypatch.setenv("ZOLAI_API_AUTH", mode)
            resp = client.post(
                "/api/v1/admin/assistant/chat", json={"message": "hello"}
            )
            assert resp.status_code == 401, (mode, resp.status_code)

    def test_member_key_is_403(self, client, data_db, auth_db, member_key) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "hello"},
            headers=_headers(member_key),
        )
        assert resp.status_code == 403

    def test_admin_key_chat_returns_trace(
        self, client, data_db, auth_db, admin_key
    ) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "pasian"},
            headers=_headers(admin_key),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retrieval_only"] is True
        assert isinstance(body["tool_calls"], list) and body["tool_calls"]
        assert body["citations"]
        assert "<<<TOOL" not in body["answer"]
        assert set(body["zvs"]) == {"valid", "violations_before", "violations_after"}

    def test_admin_persist_writes_agent_run(
        self, client, data_db, auth_db, admin_key
    ) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "define pasian", "persist": True},
            headers=_headers(admin_key),
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["persisted_run_id"]
        assert isinstance(run_id, int)
        conn = sqlite3.connect(data_db)
        row = conn.execute(
            "SELECT status, outcome, created_by FROM agent_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        conn.close()
        assert row == ("succeeded", "chat", "admin-chat")
