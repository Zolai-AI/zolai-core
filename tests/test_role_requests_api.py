"""The role-request queue closes the "promote yourself over HTTP" hole.

Before this feature an account with a ``user:manage`` credential could write any
role straight into ``users.role``.  The queue replaces that with an *ask a peer
answers*: a member stores a pending request, and only a superadmin applies it,
and even then **only** through :func:`zolai.api.session_auth.set_user_role` —
the single guarded writer that refuses a self-change and the removal of the last
enabled superadmin.  These tests pin the contract that makes that true:

- the two member routes require a **person** (a ``session`` credential), so an
  API key is 403 ``session_required`` and an anonymous caller is 401;
- only ``admin``/``superadmin`` are requestable, one pending ask per
  (account, role), and the ask itself is rate limited;
- the queue and the decision route need ``role:manage`` **and** the superadmin
  role, and a decision writes exactly one ``role_requests`` audit row (plus the
  writer's ``users``/``role`` row on an approval);
- a self-approval and an approval of a deleted account are refused *before* the
  queue row is flipped, so it stays ``pending``.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from zolai.api import auth, role_requests_router, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table, create_role_request_tables, create_user_session_tables

PASSWORD = "correct horse battery staple"
REASON = "I moderate the annotation queue and need review access."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rr_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway store with ``users``/``sessions``/``api_keys``/``role_requests``."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'role_requests.db'}")
    mgr.init_db()  # data_audit_log is an ORM table — this creates it
    create_user_session_tables(mgr)
    create_api_keys_table(mgr)
    create_role_request_tables(mgr)
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
    role_requests_router._reset_ask_bucket()
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    session_auth.reset_session_cache()
    reset_rate_limiter()
    yield
    role_requests_router._reset_ask_bucket()
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    session_auth.reset_session_cache()
    reset_rate_limiter()


@pytest.fixture()
def member_key(rr_db: DatabaseManager) -> dict:
    """A ``dataset:read`` key — a member, but *not a person*."""
    return auth.create_api_key(name="plain-member", scopes=["dataset:read"], created_by="test")


@pytest.fixture()
def admin_key(rr_db: DatabaseManager) -> dict:
    """``user:manage`` — admin rank, but no ``role:manage``."""
    return auth.create_api_key(name="user-admin", scopes=["user:manage"], created_by="test")


@pytest.fixture()
def superadmin_key(rr_db: DatabaseManager) -> dict:
    """A key holding ``role:manage`` — the only key that may read/decide the queue."""
    return auth.create_api_key(
        name="role-admin", scopes=["user:manage", "role:manage"], created_by="test"
    )


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ask(client: TestClient, token: str, role: str = "admin", reason: str = REASON):
    return client.post(
        "/api/v1/role-requests",
        json={"requested_role": role, "reason": reason},
        headers=_bearer(token),
    )


def _make_member(client: TestClient, username: str) -> tuple[dict, str]:
    """Create a member account and return ``(user, session_token)``."""
    user = session_auth.create_user(username=username, password=PASSWORD, actor="test")
    token = session_auth.login(username, PASSWORD)["token"]
    return user, token


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


def _has_key(obj: Any, needle: str) -> bool:
    if isinstance(obj, dict):
        return needle in obj or any(_has_key(value, needle) for value in obj.values())
    if isinstance(obj, list):
        return any(_has_key(item, needle) for item in obj)
    return False


# ---------------------------------------------------------------------------
# The ask — member session
# ---------------------------------------------------------------------------


class TestAsk:
    def test_anonymous_is_401(self, client) -> None:
        # The route is strict, so 401 in warn mode too (not a dual-accept).
        response = client.post(
            "/api/v1/role-requests",
            json={"requested_role": "admin", "reason": REASON},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"

    def test_member_key_is_403_session_required(self, client, member_key) -> None:
        response = client.post(
            "/api/v1/role-requests",
            json={"requested_role": "admin", "reason": REASON},
            headers=_headers(member_key),
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "session_required"

    def test_member_session_is_201_pending(self, client, rr_db) -> None:
        _user, token = _make_member(client, "alice")
        response = _ask(client, token, role="admin")
        assert response.status_code == 201
        request = response.json()["request"]
        assert request["status"] == "pending"
        assert request["username"] == "alice"
        assert request["requested_role"] == "admin"

    def test_member_role_is_not_requestable(self, client, rr_db) -> None:
        _user, token = _make_member(client, "bob")
        response = _ask(client, token, role="member")
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "role_not_requestable"

    def test_unholdable_role_is_not_requestable(self, client, rr_db) -> None:
        _user, token = _make_member(client, "carol")
        response = _ask(client, token, role="owner")
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "role_not_requestable"

    def test_already_held_role_is_409(self, client, rr_db) -> None:
        session_auth.create_user(username="dana", password=PASSWORD, role="admin", actor="test")
        token = session_auth.login("dana", PASSWORD)["token"]
        response = _ask(client, token, role="admin")
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "role_already_held"

    def test_duplicate_pending_is_409(self, client, rr_db) -> None:
        _user, token = _make_member(client, "erin")
        assert _ask(client, token, role="admin").status_code == 201
        # The partial unique index ux_role_requests_pending is the backstop.
        second = _ask(client, token, role="admin")
        assert second.status_code == 409
        assert second.json()["detail"]["error"] == "duplicate_pending"

    def test_short_reason_is_422(self, client, rr_db) -> None:
        _user, token = _make_member(client, "frank")
        response = _ask(client, token, role="admin", reason="too short")
        assert response.status_code == 422

    def test_rate_limit_is_429(self, client, rr_db) -> None:
        """Only ``admin``/``superadmin`` are requestable, so five *legal* asks in
        one test is impossible — a second ask hits ``duplicate_pending`` or
        ``role_already_held`` first.  We therefore fill the route's own bucket to
        its ceiling and prove the ceiling answers 429; that is the honest way to
        exercise the budget without faking role legality."""
        alice, token = _make_member(client, "grace")
        bucket_key = f"user:{int(alice['id'])}"
        role_requests_router._ASK_BUCKET[bucket_key] = [
            time.monotonic()
        ] * role_requests_router.ASKS_PER_MINUTE
        response = _ask(client, token, role="admin")
        assert response.status_code == 429
        assert response.json()["detail"]["error"] == "rate_limited"
        assert "retry-after" in {h.lower() for h in response.headers}


# ---------------------------------------------------------------------------
# This account's own list
# ---------------------------------------------------------------------------


class TestMyList:
    def test_mine_sees_only_own_rows_and_shape(self, client, rr_db) -> None:
        alice, alice_token = _make_member(client, "alice")
        bob, bob_token = _make_member(client, "bob")
        ra = _ask(client, alice_token, role="admin")
        rb = _ask(client, bob_token, role="admin")
        assert ra.status_code == 201 and rb.status_code == 201

        mine = client.get("/api/v1/role-requests/mine", headers=_bearer(alice_token))
        assert mine.status_code == 200
        body = mine.json()
        assert set(body) == {"items", "count"}
        assert body["count"] == 1
        assert {item["id"] for item in body["items"]} == {ra.json()["request"]["id"]}
        # bob's row is alice-invisible, and vice versa
        bob_mine = client.get("/api/v1/role-requests/mine", headers=_bearer(bob_token)).json()
        assert {item["id"] for item in bob_mine["items"]} == {rb.json()["request"]["id"]}
        assert alice["id"] != bob["id"]

    def test_mine_key_is_403_session_required(self, client, member_key) -> None:
        response = client.get("/api/v1/role-requests/mine", headers=_headers(member_key))
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "session_required"

    def test_mine_anonymous_is_401(self, client) -> None:
        response = client.get("/api/v1/role-requests/mine")
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"


# ---------------------------------------------------------------------------
# The queue — superadmin
# ---------------------------------------------------------------------------


class TestQueue:
    def test_member_key_is_403(self, client, member_key) -> None:
        response = client.get("/api/v1/role-requests", headers=_headers(member_key))
        assert response.status_code == 403

    def test_admin_key_is_403(self, client, admin_key) -> None:
        # user:manage/apikey:manage but no role:manage.
        response = client.get("/api/v1/role-requests", headers=_headers(admin_key))
        assert response.status_code == 403

    def test_role_manage_key_is_200(self, client, superadmin_key) -> None:
        response = client.get("/api/v1/role-requests", headers=_headers(superadmin_key))
        assert response.status_code == 200
        assert set(response.json()) == {"status", "items", "count"}

    def test_superadmin_session_is_200(self, client, rr_db) -> None:
        session_auth.create_user(
            username="root", password=PASSWORD, role="superadmin", actor="test"
        )
        token = session_auth.login("root", PASSWORD)["token"]
        response = client.get("/api/v1/role-requests", headers=_bearer(token))
        assert response.status_code == 200

    def test_defaults_to_pending_hiding_decided_rows(self, client, rr_db, superadmin_key) -> None:
        alice = session_auth.create_user(username="alice", password=PASSWORD, actor="test")
        req = session_auth.create_role_request(
            user_id=int(alice["id"]),
            username="alice",
            requested_role="admin",
            reason=REASON,
        )
        # Flip the queue row's status only (the store function does not touch the role).
        session_auth.decide_role_request(req["id"], approve=True, decided_by="test")

        default = client.get("/api/v1/role-requests", headers=_headers(superadmin_key))
        assert default.status_code == 200
        assert default.json()["status"] == "pending"
        assert req["id"] not in {item["id"] for item in default.json()["items"]}

        everything = client.get(
            "/api/v1/role-requests",
            params={"status": "all"},
            headers=_headers(superadmin_key),
        )
        assert req["id"] in {item["id"] for item in everything.json()["items"]}

    def test_limit_above_200_is_422(self, client, superadmin_key) -> None:
        response = client.get(
            "/api/v1/role-requests", params={"limit": 201}, headers=_headers(superadmin_key)
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# The decision — superadmin applies through the one writer
# ---------------------------------------------------------------------------


class TestDecision:
    def _pending_ask(self, rr_db, username: str, role: str = "admin") -> dict:
        user = session_auth.create_user(username=username, password=PASSWORD, actor="test")
        return session_auth.create_role_request(
            user_id=int(user["id"]), username=username, requested_role=role, reason=REASON
        )

    def test_approve_grants_role_and_sanitizes(self, client, rr_db, superadmin_key) -> None:
        req = self._pending_ask(rr_db, "heidi")
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "approve"},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["request"]["status"] == "approved"
        assert body["user"]["role"] == "admin"
        assert session_auth.get_user("heidi")["role"] == "admin"
        # no password hash anywhere in the body
        assert "password_hash" not in response.text
        assert not _has_key(body, "password_hash")

    def test_approval_goes_through_the_writer_only(self) -> None:
        """The router must not write ``users.role`` itself — only set_user_role may.

        The check is on the *executable* source, not the raw text: the module
        docstring legitimately names the phrase (``no ``UPDATE users`` statement,
        and a test asserts that``), so a naive substring test would fire on
        documentation.  We parse to AST, drop every docstring, then unparsing
        discards comments too — leaving only code that could hold a raw UPDATE.
        """
        src = Path(role_requests_router.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    node.body = node.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        assert "UPDATE users" not in code
        # Belt-and-braces: the router never touches a SQL driver at all.
        assert "sqlalchemy" not in code
        assert "text(" not in code

    def test_reject_without_note_is_400(self, client, rr_db, superadmin_key) -> None:
        req = self._pending_ask(rr_db, "ivan")
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "reject"},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "note_required"

    def test_reject_with_note_keeps_role(self, client, rr_db, superadmin_key) -> None:
        req = self._pending_ask(rr_db, "judy")
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "reject", "note": "No moderation history yet."},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 200
        assert response.json()["request"]["status"] == "rejected"
        assert session_auth.get_user("judy")["role"] == "member"
        role_rows = [r for r in _audit_rows(rr_db) if r[0] == "users" and r[1] == "role"]
        assert role_rows == []

    def test_invalid_decision_is_400(self, client, rr_db, superadmin_key) -> None:
        req = self._pending_ask(rr_db, "kate")
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "maybe"},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "invalid_decision"

    def test_deciding_twice_is_409(self, client, rr_db, superadmin_key) -> None:
        req = self._pending_ask(rr_db, "leo")
        first = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "approve"},
            headers=_headers(superadmin_key),
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "approve"},
            headers=_headers(superadmin_key),
        )
        assert second.status_code == 409
        assert second.json()["detail"]["error"] == "already_decided"

    def test_unknown_id_is_404(self, client, superadmin_key) -> None:
        response = client.post(
            "/api/v1/role-requests/999999/decision",
            json={"decision": "approve"},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "request_not_found"

    def test_approve_deleted_account_is_404_and_stays_pending(
        self, client, rr_db, superadmin_key
    ) -> None:
        req = self._pending_ask(rr_db, "ghost")
        # No delete API exists, so remove the row directly to simulate a deleted account.
        with rr_db.engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE username = 'ghost'"))
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "approve"},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "user_not_found"
        # The refusal came from the writer before the queue row was flipped.
        assert session_auth.get_role_request(req["id"])["status"] == "pending"

    def test_self_approval_is_403_and_stays_pending(self, client, rr_db) -> None:
        sadmin = session_auth.create_user(
            username="solo", password=PASSWORD, role="superadmin", actor="test"
        )
        # A superadmin cannot create this ask through the route (role_already_held),
        # so store it directly — the point is the decision, which the writer refuses.
        req = session_auth.create_role_request(
            user_id=int(sadmin["id"]),
            username="solo",
            requested_role="superadmin",
            reason="Confirm my own operator status.",
        )
        token = session_auth.login("solo", PASSWORD)["token"]
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "approve"},
            headers=_bearer(token),
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "self_role_change"
        assert session_auth.get_role_request(req["id"])["status"] == "pending"

    def test_one_queue_audit_row_per_decision(self, client, rr_db, superadmin_key) -> None:
        req = self._pending_ask(rr_db, "mallory")
        response = client.post(
            f"/api/v1/role-requests/{req['id']}/decision",
            json={"decision": "approve"},
            headers=_headers(superadmin_key),
        )
        assert response.status_code == 200
        rows = _audit_rows(rr_db)
        queue_rows = [r for r in rows if r[0] == "role_requests" and r[1] == "status"]
        writer_rows = [r for r in rows if r[0] == "users" and r[1] == "role"]
        assert len(queue_rows) == 1
        assert queue_rows[0][2:4] == ("pending", "approved")
        # an approval additionally runs the guarded writer, which audits the role
        assert len(writer_rows) == 1
        assert writer_rows[0][2:4] == ("member", "admin")
