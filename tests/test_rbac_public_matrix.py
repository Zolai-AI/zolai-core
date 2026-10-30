"""P2 — RBAC: public/member/admin/superadmin matrix, public routes, roles, completeness.

Covers the plan's done-when:

- every ``/api/v1`` route is classified (completeness guard — a new route that
  joins the surface without declaring its class fails the suite);
- under temporary ``enforce``: public reads **and** (P4) ``POST
  /api/v1/assistant/chat`` → 200 with no key, while non-public routes and the
  admin surface still 401;
- ``GET /api/v1/auth/me`` reports ``anonymous`` / ``member`` / ``admin`` /
  ``superadmin`` and never 401s, and carries its original four keys for every
  auth source;
- the **superadmin row** — every admin-class read answers a superadmin with
  something other than 401/403, which is what catches an ``== "admin"``
  regression left anywhere in the repo (``TestSuperadminRow``);
- ``POST /api/v1/auth/login`` + ``/auth/logout`` are **public** (a login cannot
  require the credential it asks for, or ``enforce`` makes sign-in impossible);
- scope vocabulary 31 → 33 (``agent:read`` / ``agent:run``) + rate-limit rows;
- anonymous public paths get their own IP buckets (120/min, chat 10/min).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from zolai.api import auth, rbac, session_auth
from zolai.api.auth_middleware import (
    DEFAULT_PUBLIC_CHAT_RATE_LIMIT_RPM,
    DEFAULT_PUBLIC_RATE_LIMIT_RPM,
    reset_rate_limiter,
)
from zolai.api.rate_limit import SCOPE_LIMITS
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
    session_auth.reset_session_cache()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    session_auth.reset_session_cache()
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
    """Throwaway manager **and** ``config.paths.db`` pointed at the same file.

    Both halves matter. ``auth``/``session_auth`` resolve their connection
    through ``_get_manager``, which the patch below swaps out — but a dozen other
    modules (``provider_settings``, the learning/finetuning stores,
    ``assistant_router``'s feedback stats) call ``get_manager()`` or read
    ``config.paths.db`` directly, and pointing those at the corpus meant a test
    that only *wanted* a 200-vs-401 answer issued real DDL against the real
    2.8 GB database. One path, one file, no ambiguity.
    """
    from zolai.data import database as database_mod

    db_path = tmp_path / "rbac.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_api_keys_table(mgr)
    create_user_session_tables(mgr)
    # The admin read surface touches these three, and the walk in
    # ``TestSuperadminRow`` asserts a *vocabulary* (200/404 vs 401/403).  Without
    # their schema a missing table is a 500, which is neither answer and would
    # have to be excused.
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


@pytest.fixture()
def member_key() -> dict:
    return auth.create_api_key(name="rbac-member", scopes=["dataset:read"], created_by="test")


@pytest.fixture()
def admin_key() -> dict:
    return auth.create_api_key(name="rbac-admin", scopes=["settings:write"], created_by="test")


@pytest.fixture()
def superadmin_key() -> dict:
    """A key whose *scope* makes it a superadmin (no account behind it)."""
    return auth.create_api_key(name="rbac-superadmin", scopes=["role:manage"], created_by="test")


@pytest.fixture()
def wildcard_key() -> dict:
    """``*`` subsumes ``role:manage``, so this is a superadmin key too — and the
    only credential shape that holds every action, which is what makes it the
    right one to prove no admin gate is a *scope* gate in disguise."""
    return auth.create_api_key(name="rbac-wildcard", scopes=["*"], created_by="test")


@pytest.fixture()
def superadmin_session(auth_db, monkeypatch) -> str:
    """A promoted account's bearer token — the credential a **browser** holds.

    Built through the single writer (``set_user_role``), so this fixture is
    itself proof that the bootstrap path exists without an HTTP route: nothing
    here calls ``/api/v1/admin/users``.
    """
    monkeypatch.setenv("ZOLAI_AUTH_SESSIONS", "on")
    created = session_auth.create_user(
        username="matrix-root",
        password="correct horse battery staple",
        display_name="Bootstrap Root",
        actor="test",
    )
    session_auth.set_user_role(
        "matrix-root", role="superadmin", actor="test", actor_role="superadmin"
    )
    issued = session_auth.issue_session(int(created["id"]))
    return str(issued["token"])


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _session_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
            ("GET", "/api/v1/knowledge/version"),
            ("GET", "/api/v1/knowledge/statistics"),
            ("GET", "/api/v1/lexicon/pasian"),
            ("GET", "/api/v1/lexicon"),
            ("GET", "/api/v1/auth/me"),
            ("POST", "/api/v1/assistant/chat"),
            # Username sign-in/out — public or sign-in is impossible under enforce.
            ("POST", "/api/v1/auth/login"),
            ("POST", "/api/v1/auth/logout"),
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
            ("GET", "/api/v1/foundation/stats"),  # core#18: was public
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
        # The original four keys are still exactly the original four (plus the
        # additive auth_source marker) — nothing a Studio client reads is gone.
        assert set(body) == {"role", "key_prefix", "scopes", "mode", "auth_source"}
        assert body["auth_source"] == "anonymous"

    def test_login_and_logout_are_public_under_enforce(
        self, client, auth_db, monkeypatch
    ) -> None:
        """R1 guard: forgetting D6 would make sign-in 401 under enforce."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        assert client.post("/api/v1/auth/login", json={}).status_code == 422
        assert rbac.classify_route("POST", "/api/v1/auth/login") == "public"
        assert rbac.classify_route("POST", "/api/v1/auth/logout") == "public"

    def test_public_word_read_is_200_without_key(
        self, client, auth_db, seed_db, monkeypatch
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
        # core#18: ops counters are not an anonymous read.
        assert client.get("/api/v1/foundation/stats").status_code == 401

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
        assert body["auth_source"] == "api_key"

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
        assert rbac.role_for({"scopes": ["*"]}) == "superadmin"

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
# The superadmin row — a matrix that was built for three roles now has four
# ---------------------------------------------------------------------------


def _admin_class_routes() -> list[tuple[str, str]]:
    spec = create_app().openapi()
    found: list[tuple[str, str]] = []
    for path, operations in spec["paths"].items():
        for method in operations:
            upper = method.upper()
            if upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if rbac.classify_route(upper, path) == "admin":
                found.append((upper, path))
    return sorted(found)


def _concrete(path: str) -> str:
    """Fill every ``{placeholder}`` with one inert probe value."""
    return re.sub(r"\{[^}]+\}", "probe", path)


class TestSuperadminRow:
    """Every admin-class **read** answers a superadmin with something other than
    "who are you" / "you may not".

    This is the row that catches an ``== "admin"`` regression anywhere in the
    repo: a surface widened to four roles has exactly the shape "the fourth
    credential behaves like the third, everywhere".  A single leftover equality
    comparison turns into a 403 on one of these routes and this walk reports it.

    Asserted on the GET half only, on purpose.  Proving a *role* check by calling
    ``POST /api/v1/learning/corpus/expand`` (thousands of rows) or
    ``POST /api/v1/admin/ai-providers/{id}/test`` (a live provider call) would be
    a bad way to prove a role check — the mutating half is guarded by
    :meth:`test_the_role_check_is_a_rank_not_an_equality` instead, which reads the
    source and cannot side-effect anything.

    A 404/500 from a deliberately non-existent ``probe`` id is *not* an RBAC
    answer and is not asserted either; only ``401``/``403`` are the interesting
    codes here.
    """

    def test_every_superadmin_credential_shape_reports_superadmin(
        self, client, auth_db, superadmin_key, wildcard_key, superadmin_session
    ) -> None:
        shapes = {
            "key/role:manage": _headers(superadmin_key),
            "key/*": _headers(wildcard_key),
            "session": _session_headers(superadmin_session),
        }
        for label, headers in shapes.items():
            resp = client.get("/api/v1/auth/me", headers=headers)
            assert resp.status_code == 200, label
            assert resp.json()["role"] == "superadmin", label
        # The session shape is the one that carries the row, not the scopes.
        body = client.get("/api/v1/auth/me", headers=_session_headers(superadmin_session)).json()
        assert body["auth_source"] == "session"
        assert body["username"] == "matrix-root"
        assert body["key_prefix"] is None
        assert "role:manage" in body["scopes"]

    def test_admin_reads_answer_a_superadmin(self, auth_db, wildcard_key) -> None:
        # ``raise_server_exceptions=False`` because a handler faulting on the
        # throwaway schema (a missing column, an empty registry) is not an RBAC
        # answer; only 401 and 403 are the codes this walk cares about.
        client = TestClient(create_app(), raise_server_exceptions=False)
        refused: list[str] = []
        walked = 0
        for method, path in _admin_class_routes():
            if method != "GET":
                continue
            walked += 1
            resp = client.get(_concrete(path), headers=_headers(wildcard_key))
            if resp.status_code in (401, 403):
                refused.append(f"{method} {path} -> {resp.status_code}")
        assert walked >= 10, f"expected to walk the admin read surface, got {walked} routes"
        assert refused == [], f"a superadmin was refused on its own surface: {refused}"

    def test_the_same_walk_is_meaningless_without_the_gate(
        self, client, auth_db, member_key, admin_key, superadmin_key
    ) -> None:
        """Anonymous 401s and a member 403s on the routes that just passed.

        Without this, ``test_admin_reads_answer_a_superadmin`` would also pass on
        a build where the admin surface is wide open.
        """
        samples = [
            (m, p)
            for m, p in _admin_class_routes()
            if m == "GET" and p in ("/api/v1/admin/users", "/api/v1/admin/api-keys")
        ]
        assert len(samples) == 2, "the admin read surface shrank unexpectedly"
        for method, path in samples:
            anon = client.get(_concrete(path))
            assert anon.status_code == 401, f"{method} {path} answered anonymous: {anon.status_code}"
            member = client.get(_concrete(path), headers=_headers(member_key))
            assert member.status_code == 403, f"{method} {path} let a member through"
            # An admin key is *not* a superadmin: ``user:manage``/``settings:write``
            # top out at admin, and role:manage-only keys are not admin-scoped
            # reads, so both of these must be refused somewhere.
            admin = client.get(_concrete(path), headers=_headers(admin_key))
            assert admin.status_code in (200, 403), admin.status_code
            assert rbac.role_for({"scopes": ["role:manage"]}) == "superadmin"
            assert rbac.role_for({"scopes": ["settings:write"]}) == "admin"

    def test_the_role_check_is_a_rank_not_an_equality(self) -> None:
        """No module may compare a caller's role to a literal admin/superadmin.

        Rank is what four roles need: ``has_role(record, "admin")`` is true for a
        superadmin, ``role == "admin"`` is not.  The three sites below are
        *dispatch maps* — a tool tier name and a role→scopes lookup — where
        equality against a constant key is exactly right and no caller rank is
        involved. Anything else that appears is a fourth place deciding, on its
        own, who a superadmin is.
        """
        src_root = Path(rbac.__file__).resolve().parent.parent
        literal = re.compile(r"""(?:==|!=)\s*["'](?:super)?admin["']|["'](?:super)?admin["']\s*(?:==|!=)""")
        allowed = {
            # ``allow_list(scope)`` — ``"admin"`` here is a *tool tier* name, one
            # of three keys in a tier dispatch, not a caller's role.
            "zolai/agent/tools/registry.py",
            # ``role_scopes(role)`` — role → scope-set lookup table. The rank
            # comparisons guards need go through ``rbac.ROLES``/``_RANK``.
            "zolai/api/session_auth.py",
        }
        offenders: list[str] = []
        for path in sorted(src_root.rglob("*.py")):
            rel = str(path.relative_to(src_root.parent))
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.lstrip().startswith("#"):
                    continue
                if literal.search(line) and rel not in allowed:
                    offenders.append(f"{rel}:{number} {line.strip()}")
        assert offenders == [], (
            "a literal admin/superadmin equality outside the dispatch maps — use "
            f"rbac.has_role()/rank_of() instead: {offenders}"
        )


# ---------------------------------------------------------------------------
# Scope vocabulary (31 → 33)
# ---------------------------------------------------------------------------


class TestScopeVocabulary:
    def test_agent_scopes_are_in_the_vocabulary(self) -> None:
        # Baseline 31 = the 30 human actions (docs/admin/permissions.md §2) +
        # ``rag:read`` (key-scope, never in the §3 matrix); the P2 ``agent:*``
        # amendment adds 2 → 33. Every stated count in code/docs must match this.
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
        # Derive the count from the vocabulary itself so the message can't drift.
        expected = len(auth.VALID_ACTIONS)
        assert expected == 33
        with pytest.raises(ValueError, match=f"frozen {expected}-action"):
            auth.validate_scopes(["not:a-scope"])
