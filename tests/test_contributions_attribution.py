"""Contribution attribution, and the PII gate that arrived with it.

The corpus stays open on purpose — an anonymous POST's shape changed not at all.
What changed is that a row may now say *who sent it*, and the only allowed
source for that is the credential the middleware resolved, because a body field
naming an identity is a field anybody can set.  Four invariants held here:
attribution is derived, never declared (session → ``user_id`` + username, key →
the key's name with ``user_id`` NULL, anonymous → three NULLs and the old body);
``GET /contributions/mine`` is a **person** surface — 401 anonymous, 403
``session_required`` for a key, never another account's rows; ``email`` stays an
accepted reply address but leaves neither ``/mine`` nor ``/records``, where
contributions now need ``dataset:edit`` **strict** while other tables keep
``dataset:read``; and an admin **session** reaches the review queue it always
owned — the scope was missing from ``ADMIN_EXTRA_SCOPES``, hence member 403s.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from zolai.api import auth, contributions_router, records_router, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table, ensure_contributions_table

#: A fake, never-real passphrase.  A minted plaintext key is used only in-memory
#: as ``key["plaintext"]`` — never printed, asserted, or interpolated anywhere.
PASSWORD = "correct horse battery staple"

#: The eight columns ``MINE_COLUMNS`` projects.  Written out literally so that a
#: widened ``SELECT`` in the router is a failing test rather than a passing one.
MINE_FIELDS = {"id", "type", "word", "field", "status", "created_at", "reviewed_at", "reviewed_by"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch) -> Iterator[None]:
    """Sessions on, warn-mode API auth, and no cache/bucket residue across tests."""
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
def db(tmp_path, monkeypatch) -> Iterator[tuple[DatabaseManager, Path]]:
    """One throwaway database shared by every writer and reader in this module.

    The split is real, which is why three seams are wired: ``contributions_router``
    writes through ``get_manager()``, while ``records_router`` and
    ``record_review_router`` open ``config.paths.db`` themselves.  Pointing both at
    the same file is what lets a POST made over HTTP be read back through
    ``/records`` — the read path must see the very row the write path produced.
    """
    db_path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_api_keys_table(mgr)
    ensure_contributions_table(mgr)
    with mgr.engine.begin() as conn:
        # ``source``/``pos`` are NOT NULL with Python-side defaults only, so a
        # raw INSERT has to name them.
        conn.execute(
            text(
                "INSERT INTO dictionary (zolai, english, source, pos) "
                "VALUES ('pasian', 'God', 'test_seed', 'noun')"
            )
        )
    monkeypatch.setattr(config.paths, "db", db_path)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(session_auth, "_get_manager", lambda: mgr)
    # The router resolves its own manager, so it gets patched like the other two.
    monkeypatch.setattr(contributions_router, "get_manager", lambda: mgr)
    yield mgr, db_path
    mgr.dispose()


@pytest.fixture()
def client(db) -> TestClient:
    """App without lifespan — these routers create what they need on demand."""
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(credential: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


def _contribution(word: str = "thang", **extra) -> dict:
    body = {"type": "new_word", "word": word, "suggested_value": "a new sense"}
    body.update(extra)
    return body


def _submit(client: TestClient, word: str = "thang", headers: dict | None = None, **extra):
    return client.post("/api/v1/contributions", json=_contribution(word, **extra), headers=headers or {})


def _row(db_path: Path, contribution_id: int) -> dict:
    """Read the row straight out of the file — not through the API that wrote it."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        found = conn.execute("SELECT * FROM contributions WHERE id = ?", (contribution_id,)).fetchone()
        return dict(found) if found else {}
    finally:
        conn.close()


def _signup(username: str, role: str = "member") -> tuple[int, str]:
    """Create an account in the live throwaway store, then sign in to it.

    ``session_auth._get_manager`` is patched by the ``db`` fixture, so the row
    lands in the same file the API reads.
    """
    user = session_auth.create_user(username=username, password=PASSWORD, role=role, actor="test")
    return int(user["id"]), session_auth.login(username, PASSWORD)["token"]


# ---------------------------------------------------------------------------
# POST /api/v1/contributions — attribution is derived from the credential
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_anonymous_post_is_unchanged(self, client: TestClient, db) -> None:
        """The open door stays open — and stays as anonymous as it was."""
        _mgr, db_path = db

        response = _submit(client)

        assert response.status_code == 200
        # Exactly the pre-attribution body, plus ``attributed_to: null``; an
        # anonymous row gains no marker that could mislead an editor later.
        assert response.json() == {
            "id": 1,
            "status": "pending",
            "message": "Contribution received and queued for editorial review.",
            "attributed_to": None,
        }
        row = _row(db_path, 1)
        assert row["user_id"] is None
        assert row["submitted_by"] is None
        assert row["auth_source"] is None
        assert row["status"] == "pending"

    def test_session_post_is_attributed_to_the_account(self, client: TestClient, db) -> None:
        _mgr, db_path = db
        user_id, token = _signup("koa")

        response = _submit(client, word="hlaw", headers=_bearer(token))

        assert response.status_code == 200
        assert response.json()["attributed_to"] == "koa"
        row = _row(db_path, response.json()["id"])
        assert row["user_id"] == user_id
        assert row["submitted_by"] == "koa"
        assert row["auth_source"] == "session"

    def test_api_key_post_is_attributed_to_the_key_not_an_account(
        self, client: TestClient, db
    ) -> None:
        """A key is not a person: ``user_id`` stays NULL however useful the name."""
        _mgr, db_path = db
        key = auth.create_api_key(name="corpus-bot", scopes=["dataset:read"], actor="test")

        response = _submit(client, word="paw", headers=_bearer(key["plaintext"]))

        assert response.status_code == 200
        assert response.json()["attributed_to"] == "corpus-bot"
        row = _row(db_path, response.json()["id"])
        assert row["user_id"] is None
        assert row["submitted_by"] == "corpus-bot"
        assert row["auth_source"] == "api_key"

    def test_body_cannot_name_its_own_identity(self, client: TestClient, db) -> None:
        """A self-declared identity is a frame-up; only the credential is believed."""
        _mgr, db_path = db

        response = client.post(
            "/api/v1/contributions",
            json={
                **_contribution("ziat"),
                "user_id": 7,
                "submitted_by": "someone-else",
                "auth_source": "session",
            },
        )

        assert response.status_code == 200
        row = _row(db_path, response.json()["id"])
        assert row["user_id"] is None
        assert row["submitted_by"] is None
        assert row["auth_source"] is None

    def test_email_is_still_accepted_for_a_reply(self, client: TestClient, db) -> None:
        """A contributor may ask to be contacted; the address is stored, not echoed."""
        _mgr, db_path = db

        response = _submit(client, email="koa@example.zo")

        assert response.status_code == 200
        assert "email" not in response.json()
        assert _row(db_path, response.json()["id"])["email"] == "koa@example.zo"


# ---------------------------------------------------------------------------
# GET /api/v1/contributions/mine — a person-only read-back
# ---------------------------------------------------------------------------


class TestMine:
    def test_anonymous_is_401(self, client: TestClient) -> None:
        """Strict even in ``warn``: "my" rows cannot be answered to nobody."""
        response = client.get("/api/v1/contributions/mine")

        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"

    def test_api_key_is_403_session_required(self, client: TestClient) -> None:
        key = auth.create_api_key(name="reader", scopes=["dataset:read"], actor="test")

        response = client.get("/api/v1/contributions/mine", headers=_bearer(key["plaintext"]))

        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "session_required"

    def test_sees_only_its_own_rows(self, client: TestClient, db) -> None:
        _mgr, db_path = db
        first_id, first_token = _signup("koa")
        second_id, second_token = _signup("zahng")

        mine = _submit(client, word="mine-only", headers=_bearer(first_token)).json()["id"]
        theirs = _submit(client, word="theirs", headers=_bearer(second_token)).json()["id"]
        anonymous = _submit(client, word="nobody-claims-this").json()["id"]

        assert _row(db_path, mine)["user_id"] == first_id
        assert _row(db_path, theirs)["user_id"] == second_id

        mine_items = client.get("/api/v1/contributions/mine", headers=_bearer(first_token)).json()
        their_items = client.get("/api/v1/contributions/mine", headers=_bearer(second_token)).json()

        assert [item["id"] for item in mine_items["items"]] == [mine]
        assert [item["id"] for item in their_items["items"]] == [theirs]
        assert mine_items["count"] == 1
        assert their_items["count"] == 1
        # No ``user_id`` parameter exists on this route, so a row that belongs to
        # nobody can be read back by nobody — not by either of these accounts.
        listed = {item["id"] for item in mine_items["items"]} | {item["id"] for item in their_items["items"]}
        assert anonymous not in listed

    def test_items_never_carry_email(self, client: TestClient, db) -> None:
        """The reply address is stored for editors, not handed to a list endpoint."""
        _mgr, db_path = db
        _id, token = _signup("ralph")
        submitted = _submit(client, email="ralph@example.zo", headers=_bearer(token))
        assert _row(db_path, submitted.json()["id"])["email"] == "ralph@example.zo"

        page = client.get("/api/v1/contributions/mine", headers=_bearer(token))

        assert page.status_code == 200
        items = page.json()["items"]
        assert len(items) == 1
        for item in items:
            assert set(item) == MINE_FIELDS
            assert "email" not in item
        assert "ralph@example.zo" not in page.text

    def test_default_limit_is_twenty_and_newest_first(
        self, client: TestClient, db, monkeypatch
    ) -> None:
        # 22 submissions on one session; the per-key bucket is not the subject.
        monkeypatch.setenv("ZOLAI_API_RATE_LIMIT_RPM", "500")
        _mgr, _db_path = db
        _id, token = _signup("thangja")
        posted = [_submit(client, word=f"w{i}", headers=_bearer(token)).json()["id"] for i in range(22)]

        page = client.get("/api/v1/contributions/mine", headers=_bearer(token)).json()

        assert page["count"] == 20
        assert [item["id"] for item in page["items"]] == sorted(posted, reverse=True)[:20]

    def test_limit_is_honoured_and_bounded(self, client: TestClient, db) -> None:
        _mgr, _db_path = db
        _id, token = _signup("kamhnua")
        for i in range(3):
            _submit(client, word=f"w{i}", headers=_bearer(token))

        page = client.get("/api/v1/contributions/mine", params={"limit": 2}, headers=_bearer(token))

        assert page.status_code == 200
        assert [item["id"] for item in page.json()["items"]] == [3, 2]
        # ``le=100`` — one over the cap is a validation failure, not a wide page.
        assert (
            client.get("/api/v1/contributions/mine", params={"limit": 101}, headers=_bearer(token)).status_code
            == 422
        )
        assert (
            client.get("/api/v1/contributions/mine", params={"limit": 0}, headers=_bearer(token)).status_code
            == 422
        )

    def test_account_that_never_submitted_gets_an_empty_page(self, client: TestClient, db) -> None:
        _mgr, _db_path = db
        _id, token = _signup("silent")
        _submit(client, word="not-mine")  # somebody else's row must not appear

        page = client.get("/api/v1/contributions/mine", headers=_bearer(token))

        assert page.status_code == 200
        assert page.json() == {"items": [], "count": 0}


# ---------------------------------------------------------------------------
# GET /api/v1/records?table=contributions — the address list that was closed
# ---------------------------------------------------------------------------


class TestRecordsPii:
    def test_anonymous_is_401(self, client: TestClient) -> None:
        """Strict, not dual-accept: a PII read guarded only by ``warn`` is unguarded."""
        response = client.get("/api/v1/records", params={"table": "contributions"})

        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"

    def test_dataset_read_key_is_403(self, client: TestClient) -> None:
        key = auth.create_api_key(name="reader", scopes=["dataset:read"], actor="test")

        response = client.get(
            "/api/v1/records", params={"table": "contributions"}, headers=_bearer(key["plaintext"])
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["error"] == "forbidden"
        assert detail["action"] == "dataset:edit"
        assert detail["reason"] == "missing_scope"

    def test_dataset_edit_key_gets_rows_without_email(self, client: TestClient, db) -> None:
        _mgr, db_path = db
        key = auth.create_api_key(name="editor", scopes=["dataset:edit"], actor="test")
        submitted = _submit(client, email="secret@example.zo", headers=_bearer(key["plaintext"])).json()["id"]
        assert _row(db_path, submitted)["email"] == "secret@example.zo"

        response = client.get(
            "/api/v1/records", params={"table": "contributions"}, headers=_bearer(key["plaintext"])
        )

        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["id"] for item in items] == [submitted]
        for item in items:
            assert "email" not in item
        # Redacted at projection, so it is absent from the payload, not blanked.
        assert "secret@example.zo" not in response.text

    def test_dictionary_still_answers_dataset_read(self, client: TestClient, db) -> None:
        """The raising is per-table — a plain read key keeps every other table."""
        _mgr, _db_path = db
        key = auth.create_api_key(name="reader", scopes=["dataset:read"], actor="test")

        response = client.get("/api/v1/records", params={"table": "dictionary"}, headers=_bearer(key["plaintext"]))

        assert response.status_code == 200
        assert [item["zolai"] for item in response.json()["items"]] == ["pasian"]

    def test_email_is_neither_a_search_field_nor_a_returned_column(self) -> None:
        """Source-shape guard: ``email`` left the search tuple and is redacted.

        Searching by address is a way to ask "is this person in the store?", so
        its removal has to be an assertion the code keeps passing, not a comment.
        """
        assert "email" not in records_router.RECORDS_WHITELIST["contributions"]
        assert set(records_router.RECORDS_REDACTED_COLUMNS["contributions"]) == {"email"}
        assert records_router.RECORDS_READ_SCOPE == {"contributions": "dataset:edit"}


# ---------------------------------------------------------------------------
# PATCH /api/v1/review/records/{table}/{id} — admin sessions can review again
# ---------------------------------------------------------------------------


class TestReviewAsAdmin:
    def test_admin_session_can_patch_a_contribution(self, client: TestClient, db) -> None:
        """The bug this closes: an admin **account** held no ``dataset:edit`` at all.

        A session's scopes come from :func:`zolai.api.session_auth.role_scopes`,
        so one missing entry in ``ADMIN_EXTRA_SCOPES`` 403'd every admin account
        on the Review queue while an admin **key** — same action, different
        source — sailed straight through it.
        """
        _mgr, db_path = db
        _id, token = _signup("zothan", role="admin")
        submitted = _submit(client, word="cihn", headers=_bearer(token)).json()["id"]

        response = client.patch(
            f"/api/v1/review/records/contributions/{submitted}",
            json={"corrected_fields": {"status": "accepted"}, "reason": "clearly useful"},
            headers=_bearer(token),
        )

        assert response.status_code not in (401, 403)
        assert response.status_code == 200
        assert response.json()["updated_fields"] == ["status"]
        assert _row(db_path, submitted)["status"] == "accepted"

    def test_member_session_is_403(self, client: TestClient, db) -> None:
        """Rank alone is not the scope: the member answer is 403, and nothing writes."""
        _mgr, db_path = db
        _aid, admin_token = _signup("zothan", role="admin")
        _mid, member_token = _signup("ralph")
        submitted = _submit(client, word="cihn", headers=_bearer(admin_token)).json()["id"]

        response = client.patch(
            f"/api/v1/review/records/contributions/{submitted}",
            json={"corrected_fields": {"status": "rejected"}, "reason": "spam"},
            headers=_bearer(member_token),
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["error"] == "forbidden"
        assert detail["action"] == "dataset:edit"
        assert _row(db_path, submitted)["status"] == "pending"

    def test_admin_role_actually_holds_the_edit_scope(self) -> None:
        """The root cause, asserted where it was fixed."""
        assert "dataset:edit" in session_auth.ADMIN_EXTRA_SCOPES
        assert "dataset:edit" in session_auth.role_scopes("admin")
        assert "dataset:edit" not in session_auth.MEMBER_SCOPES
        assert "dataset:edit" in session_auth.role_scopes("superadmin")
