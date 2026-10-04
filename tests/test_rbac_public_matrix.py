"""P2 — RBAC: public/member/admin matrix, public routes, roles, completeness.

Covers the plan's done-when:

- every ``/api/v1`` route is classified (completeness guard — a new route that
  joins the surface without declaring its class fails the suite);
- under temporary ``enforce``: public reads **and** (P4) ``POST
  /api/v1/assistant/chat`` → 200 with no key, while non-public routes and the
  admin surface still 401;
- ``GET /api/v1/auth/me`` reports ``anonymous`` / ``member`` / ``admin`` and
  never 401s;
- scope vocabulary 30 → 32 (``agent:read`` / ``agent:run``) + rate-limit rows;
- anonymous public paths get their own IP buckets (120/min, chat 10/min).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from zolai.api import auth, rbac
from zolai.api.auth_middleware import (
    DEFAULT_PUBLIC_CHAT_RATE_LIMIT_RPM,
    DEFAULT_PUBLIC_RATE_LIMIT_RPM,
    reset_rate_limiter,
)
from zolai.api.rate_limit import SCOPE_LIMITS
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
    pos_canonical TEXT
);
INSERT INTO dictionary VALUES (1, 'pasian', 'God', 'noun', 'test', 'pa|sian', 2, 'N');
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY,
    headword TEXT NOT NULL,
    translations TEXT,
    pos TEXT,
    source TEXT
);
INSERT INTO dictionary_en_zo VALUES (1, 'god', 'pasian', 'noun', 'test');
"""


@pytest.fixture(autouse=True)
def _clean_auth_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def seed_db(tmp_path, monkeypatch) -> Iterator[Path]:
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db_path)
    yield db_path


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'rbac.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def member_key() -> dict:
    return auth.create_api_key(name="rbac-member", scopes=["dataset:read"], created_by="test")


@pytest.fixture()
def admin_key() -> dict:
    return auth.create_api_key(name="rbac-admin", scopes=["settings:write"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _request(method: str = "GET", path: str = "/api/v1/agent/runs", state: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "state": dict(state or {}),
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Completeness guard
# ---------------------------------------------------------------------------


class TestRouteCompleteness:
    def test_every_api_v1_route_is_classified(self) -> None:
        """No ``/api/v1`` route may be neither public nor declared authed."""
        spec = create_app().openapi()
        unclassified: list[str] = []
        for path, operations in spec["paths"].items():
            if not path.startswith("/api/v1"):
                continue
            for method in operations:
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                if rbac.classify_route(method, path) == "unclassified":
                    unclassified.append(f"{method.upper()} {path}")
        assert unclassified == [], (
            "routes joined the /api/v1 surface without declaring a class in "
            f"zolai/api/rbac.py: {unclassified}"
        )

    def test_admin_surface_is_admin_class(self) -> None:
        for path in (
            "/api/v1/admin/api-keys",
            "/api/v1/admin/ai-providers",
            "/api/v1/admin/assistant/chat",
        ):
            assert rbac.classify_route("POST", path) == "admin", path

    def test_agent_and_member_routes_are_member_class(self) -> None:
        for path in (
            "/api/v1/agent/runs",
            "/api/v1/agent/runs/1",
            "/api/v1/records",
            "/api/v1/review/records/dictionary/1",
        ):
            assert rbac.classify_route("GET", path) == "member", path

    def test_plan_public_table_is_public(self) -> None:
        cases = [
            ("GET", "/api/v1/word/pasian"),
            ("GET", "/api/v1/word/pasian/evidence"),
            ("GET", "/api/v1/word/pasian/related"),
            ("GET", "/api/v1/search"),
            ("POST", "/api/v1/search"),
            ("POST", "/api/v1/analyze/sentence"),
            ("POST", "/api/v1/analyze/paragraph"),
            ("POST", "/api/v1/rag"),
            ("GET", "/api/v1/foundation/stats"),
            ("GET", "/api/v1/knowledge/version"),
            ("GET", "/api/v1/knowledge/statistics"),
            ("GET", "/api/v1/lexicon/pasian"),
            ("GET", "/api/v1/auth/me"),
            ("POST", "/api/v1/assistant/chat"),
        ]
        for method, path in cases:
            assert rbac.is_public_path(method, path), f"{method} {path} must be public"

    def test_non_public_routes_are_not_public(self) -> None:
        cases = [
            ("POST", "/api/v1/admin/assistant/chat"),
            ("GET", "/api/v1/admin/api-keys"),
            ("PUT", "/api/v1/admin/ai-providers/openai"),
            ("POST", "/api/v1/agent/runs"),
            ("GET", "/api/v1/records"),
            ("GET", "/api/v1/linguistics/pos"),
            ("GET", "/api/v1/definitely-missing"),
            ("GET", "/api/v1/foundation/review/queue"),
            ("POST", "/api/v1/admin/api-keys"),
        ]
        for method, path in cases:
            assert not rbac.is_public_path(method, path), f"{method} {path} must be gated"

    def test_public_method_matters(self) -> None:
        # /api/v1/rag is public for POST (the read) only.
        assert rbac.is_public_path("POST", "/api/v1/rag") is True
        assert rbac.is_public_path("GET", "/api/v1/rag") is False

    def test_chat_bucket_selection(self) -> None:
        assert rbac.is_public_chat_path("POST", "/api/v1/assistant/chat") is True
        assert rbac.is_public_chat_path("GET", "/api/v1/assistant/chat") is False
        assert rbac.is_public_chat_path("POST", "/api/v1/rag") is False


# ---------------------------------------------------------------------------
# Enforce — public open, everything else gated
# ---------------------------------------------------------------------------


class TestPublicUnderEnforce:
    def test_auth_me_is_200_and_anonymous(self, client, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "anonymous"
        assert body["scopes"] == []
        assert body["mode"] == "enforce"

    def test_public_word_read_is_200_without_key(
        self, client, seed_db, auth_db, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = client.get("/api/v1/lexicon/pasian")
        assert resp.status_code == 200

    def test_non_public_route_still_401s_under_enforce(
        self, client, auth_db, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        assert client.get("/api/v1/records").status_code == 401
        assert client.get("/api/v1/definitely-missing").status_code == 401

    def test_admin_surface_401s_under_enforce(self, client, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        assert client.post("/api/v1/admin/api-keys", json={"name": "x", "scopes": ["*"]}).status_code == 401

    def test_member_key_reports_member_role(self, client, auth_db, member_key) -> None:
        resp = client.get("/api/v1/auth/me", headers=_headers(member_key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "member"
        assert body["key_prefix"] == member_key["key_prefix"]
        assert body["scopes"] == ["dataset:read"]

    def test_admin_key_reports_admin_role(self, client, auth_db, admin_key) -> None:
        resp = client.get("/api/v1/auth/me", headers=_headers(admin_key))
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_public_path_still_serves_with_a_key(
        self, client, seed_db, auth_db, member_key, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = client.get("/api/v1/lexicon/pasian", headers=_headers(member_key))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Anonymous IP buckets on public paths
# ---------------------------------------------------------------------------


class TestPublicRateBuckets:
    def test_default_limits(self) -> None:
        assert DEFAULT_PUBLIC_RATE_LIMIT_RPM == 120
        assert DEFAULT_PUBLIC_CHAT_RATE_LIMIT_RPM == 10

    def test_public_bucket_returns_429_when_exhausted(
        self, client, auth_db, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        monkeypatch.setenv("ZOLAI_PUBLIC_RATE_LIMIT_RPM", "2")
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 200
        blocked = client.get("/api/v1/auth/me")
        assert blocked.status_code == 429
        detail = blocked.json()["detail"]
        assert detail["error"] == "rate_limited"
        assert detail["scope"] == "public"
        assert blocked.headers.get("retry-after")


# ---------------------------------------------------------------------------
# Role + dependency semantics
# ---------------------------------------------------------------------------


class TestRoles:
    def test_role_for_matrix(self) -> None:
        assert rbac.role_for(None) == "anonymous"
        assert rbac.role_for({}) == "anonymous"
        assert rbac.role_for({"scopes": ["dataset:read"]}) == "member"
        assert rbac.role_for({"scopes": ["dataset:read", "apikey:manage"]}) == "admin"
        assert rbac.role_for({"scopes": ["settings:write"]}) == "admin"
        assert rbac.role_for({"scopes": ["settings:read"]}) == "member"
        assert rbac.role_for({"scopes": ["*"]}) == "admin"

    def test_require_role_strict_rejects_anonymous_in_warn(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
        dep = rbac.require_role("member", strict=True)
        with pytest.raises(Exception) as exc:
            dep(_request())
        assert getattr(exc.value, "status_code", None) == 401

    def test_require_role_lenient_dual_accepts_in_warn(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
        dep = rbac.require_role("member", strict=False)
        assert dep(_request()) == {}

    def test_require_role_enforce_rejects_anonymous(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        dep = rbac.require_role("member", strict=False)
        with pytest.raises(Exception) as exc:
            dep(_request())
        assert getattr(exc.value, "status_code", None) == 401

    def test_require_role_403_on_insufficient_role(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        dep = rbac.require_role("admin", strict=True)
        request = _request(state={"api_key": {"scopes": ["dataset:read"], "key_prefix": "z"}})
        with pytest.raises(Exception) as exc:
            dep(request)
        assert getattr(exc.value, "status_code", None) == 403

    def test_require_role_allows_sufficient_role(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        dep = rbac.require_role("admin", strict=True)
        request = _request(state={"api_key": {"scopes": ["*"], "key_prefix": "z"}})
        assert dep(request)["scopes"] == ["*"]

    def test_require_role_never_blocks_public_paths(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        dep = rbac.require_role("admin", strict=True)
        assert dep(_request("POST", "/api/v1/assistant/chat")) == {}

    def test_require_scope_public_early_return(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        dep = auth.require_scope("dataset:read")
        # public read: anonymous allowed in enforce
        assert dep(_request("GET", "/api/v1/lexicon/pasian")) == {}
        # non-public read: still gated
        with pytest.raises(Exception) as exc:
            dep(_request("GET", "/api/v1/records"))
        assert getattr(exc.value, "status_code", None) == 401


# ---------------------------------------------------------------------------
# Scope vocabulary (30 → 32)
# ---------------------------------------------------------------------------


class TestScopeVocabulary:
    def test_agent_scopes_are_in_the_vocabulary(self) -> None:
        # 30 human actions (docs/admin/permissions.md §2) + ``rag:read`` (Phase 6,
        # code-only) + the P2 ``agent:*`` amendment = 33.
        assert len(auth.VALID_ACTIONS) == 33
        assert "agent:read" in auth.VALID_ACTIONS
        assert "agent:run" in auth.VALID_ACTIONS

    def test_agent_scope_sugar_validates(self) -> None:
        assert auth.validate_scopes(["agent:run"]) == ["agent:run"]
        assert auth.validate_scopes(["agent:*"]) == ["agent:*"]
        assert auth.validate_scopes(["*:read"]) == ["*:read"]

    def test_scope_allows_agent_actions(self) -> None:
        assert auth.scope_allows(["agent:run"], "agent:run") is True
        assert auth.scope_allows(["agent:read"], "agent:run") is False
        assert auth.scope_allows(["*"], "agent:run") is True

    def test_rate_limit_rows_exist(self) -> None:
        assert SCOPE_LIMITS["agent:run"] == 10
        assert SCOPE_LIMITS["agent:read"] == 60

    def test_unknown_scope_still_rejected(self) -> None:
        with pytest.raises(ValueError, match="32-action"):
            auth.validate_scopes(["not:a-scope"])
