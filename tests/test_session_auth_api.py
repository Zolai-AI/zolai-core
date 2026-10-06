"""Username + password accounts — :mod:`zolai.api.session_auth` service tests.

The identity counterpart to :mod:`zolai.api.auth` (machine API keys). Covers the
service half of the contract; the HTTP surface (``/api/v1/auth/login``,
``/api/v1/auth/logout``) is tested in the API half of this same file.

Highlights
- passwords are stored as an argon2id PHC hash and verified only against that
- a login returns a ``zolai_ss_*`` token whose **SHA-256** is what gets stored
- unknown user / wrong password / disabled all share one exception shape, and
  an unknown user still pays a dummy argon2 verify so timing does not split
- disabling an account, changing its password, or ``revoke-sessions`` revokes
  its live sessions; re-enabling never resurrects a revoked one
- every mutation appends a ``data_audit_log`` row carrying **no secret**
- scope sets are strict subsets of the frozen 33-action vocabulary (no ``*``)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text

from zolai.api import auth, rbac, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_user_session_tables

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Store with ``users``/``sessions`` on a pre-migration shape."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'sessions.db'}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS sessions"))
        conn.execute(text("DROP TABLE IF EXISTS users"))
    create_user_session_tables(mgr)
    from zolai.data.migrations import create_api_keys_table

    create_api_keys_table(mgr)
    monkeypatch.setattr(session_auth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ZOLAI_AUTH_SESSIONS", "on")
    monkeypatch.delenv("ZOLAI_SESSION_TTL_HOURS", raising=False)
    monkeypatch.delenv("ZOLAI_LOGIN_RATE_LIMIT_RPM", raising=False)
    monkeypatch.delenv("ZOLAI_LOGIN_RATE_LIMIT_USER_RPM", raising=False)
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
def founder(session_db: DatabaseManager) -> dict:
    return session_auth.create_user(
        username="founder", password=PASSWORD, role="admin", actor="test"
    )


def _login_body(username: str = "founder", password: str = PASSWORD) -> dict:
    return {"username": username, "password": password}


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
# Service: hashing primitives
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_is_argon2id_phc_and_never_the_plaintext(self) -> None:
        digest = session_auth.hash_password(PASSWORD)
        assert digest.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert PASSWORD not in digest
        assert digest != session_auth.hash_password(PASSWORD)  # unique salt

    def test_verify_accepts_only_the_right_password(self) -> None:
        digest = session_auth.hash_password(PASSWORD)
        assert session_auth.verify_password_hash(digest, PASSWORD) is True
        assert session_auth.verify_password_hash(digest, PASSWORD + "x") is False

    def test_verify_on_a_corrupt_hash_is_false_not_an_exception(self) -> None:
        assert session_auth.verify_password_hash("not-a-phc-string", PASSWORD) is False

    def test_short_password_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            session_auth.hash_password("short")

    def test_username_normalization_and_validation(self) -> None:
        assert session_auth.normalize_username("  Founder.User ") == "founder.user"
        for bad in ("", "has space", "üser", "x" * 65, "-leading-hyphen"):
            with pytest.raises(ValueError):
                session_auth.normalize_username(bad)


# ---------------------------------------------------------------------------
# Service: sessions
# ---------------------------------------------------------------------------


class TestSessionService:
    def test_login_returns_a_token_that_is_not_stored(self, founder, session_db) -> None:
        result = session_auth.login("founder", PASSWORD, ip="10.0.0.1")

        assert result["token"].startswith("zolai_ss_")
        assert result["token_type"] == "bearer"
        assert result["expires_at"]
        assert result["user"]["username"] == "founder"
        assert result["user"]["role"] == "admin"
        assert result["user"]["id"] == founder["id"]

        with session_db.engine.connect() as conn:
            stored = conn.execute(text("SELECT token_hash FROM sessions")).scalars().all()
        assert len(stored) == 1
        assert stored[0] == session_auth.hash_token(result["token"])
        assert result["token"] not in stored[0]

    def test_admin_scope_set_has_no_wildcard(self, founder) -> None:
        result = session_auth.login("founder", PASSWORD)
        assert "*" not in result["user"]["scopes"]
        assert set(result["user"]["scopes"]) <= set(auth.VALID_ACTIONS)
        assert len(auth.VALID_ACTIONS) == 33  # vocabulary stays frozen

    def test_member_scope_set_is_the_frozen_subset(self, session_db) -> None:
        session_auth.create_user(username="member1", password=PASSWORD, role="member")
        scopes = session_auth.login("member1", PASSWORD)["user"]["scopes"]
        assert scopes == list(session_auth.MEMBER_SCOPES)
        assert "*" not in scopes
        assert "user:manage" not in scopes
        assert "settings:write" not in scopes

    def test_unknown_role_falls_back_to_member(self) -> None:
        scopes = session_auth.role_scopes("wizard")
        assert scopes == list(session_auth.MEMBER_SCOPES)
        assert rbac.role_for({"scopes": scopes}) == rbac.ROLE_MEMBER
        assert session_auth.role_scopes("ADMIN") == list(session_auth.ADMIN_SCOPES)

    def test_resolve_publishes_a_key_compatible_auth_record(self, founder) -> None:
        token = session_auth.login("founder", PASSWORD)["token"]

        record = session_auth.resolve_session(token)

        assert record["auth_source"] == "session"
        assert record["username"] == "founder"
        assert record["user_id"] == founder["id"]
        assert record["key_prefix"] is None  # D7: a session has no key prefix
        assert record["role"] == "admin"
        assert rbac.role_for(record) == rbac.ROLE_ADMIN
        assert record["expires_at"]

    def test_api_key_token_is_never_resolved_as_a_session(self, founder) -> None:
        assert session_auth.resolve_session("zolai_sk_anything") is None
        assert session_auth.resolve_session("not-a-token") is None
        assert session_auth.resolve_session(None) is None
        assert session_auth.is_session_token("zolai_ss_x") is True

    def test_logout_revokes_only_the_presented_session(self, founder) -> None:
        first = session_auth.login("founder", PASSWORD)["token"]
        second = session_auth.login("founder", PASSWORD)["token"]

        assert session_auth.revoke_session(first) is True
        assert session_auth.resolve_session(first) is None
        assert session_auth.resolve_session(second) is not None
        # Idempotent + non-probing: a second revoke, an unknown token and no
        # token all answer False rather than raising.
        assert session_auth.revoke_session(first) is False
        assert session_auth.revoke_session("zolai_ss_never-issued") is False
        assert session_auth.revoke_session(None) is False

    def test_expired_session_is_rejected(self, founder, session_db) -> None:
        token = session_auth.login("founder", PASSWORD)["token"]
        session_auth.invalidate_session_cache()
        with session_db.engine.begin() as conn:
            conn.execute(text("UPDATE sessions SET expires_at = '2000-01-01 00:00:00'"))
        assert session_auth.resolve_session(token) is None

    def test_ttl_is_configurable(self, founder, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_SESSION_TTL_HOURS", "2")
        assert session_auth.session_ttl_hours() == 2
        monkeypatch.setenv("ZOLAI_SESSION_TTL_HOURS", "not-a-number")
        assert session_auth.session_ttl_hours() == session_auth.DEFAULT_SESSION_TTL_HOURS
        monkeypatch.delenv("ZOLAI_SESSION_TTL_HOURS")
        assert session_auth.session_ttl_hours() == session_auth.DEFAULT_SESSION_TTL_HOURS

    def test_disabling_revokes_live_sessions(self, founder) -> None:
        token = session_auth.login("founder", PASSWORD)["token"]
        assert session_auth.resolve_session(token) is not None

        session_auth.set_user_enabled("founder", enabled=False, actor="test")

        assert session_auth.resolve_session(token) is None
        with pytest.raises(session_auth.LoginRejected) as rejected:
            session_auth.login("founder", PASSWORD)
        assert rejected.value.reason == session_auth.REASON_DISABLED

    def test_reenabling_does_not_resurrect_a_revoked_session(self, founder) -> None:
        token = session_auth.login("founder", PASSWORD)["token"]
        session_auth.set_user_enabled("founder", enabled=False, actor="test")
        session_auth.set_user_enabled("founder", enabled=True, actor="test")
        assert session_auth.resolve_session(token) is None
        assert session_auth.login("founder", PASSWORD)["token"] != token

    def test_password_change_revokes_live_sessions(self, founder) -> None:
        token = session_auth.login("founder", PASSWORD)["token"]

        session_auth.change_password("founder", "a brand new passphrase", actor="test")

        assert session_auth.resolve_session(token) is None
        with pytest.raises(session_auth.LoginRejected):
            session_auth.login("founder", PASSWORD)
        assert session_auth.login("founder", "a brand new passphrase")["token"]

    def test_revoke_sessions_counts_and_reports_username(self, founder) -> None:
        session_auth.login("founder", PASSWORD)
        session_auth.login("founder", PASSWORD)

        result = session_auth.revoke_user_sessions("founder", actor="test")

        assert result == {"username": "founder", "revoked": 2}
        assert session_auth.revoke_user_sessions("founder", actor="test")["revoked"] == 0
        with pytest.raises(LookupError):
            session_auth.revoke_user_sessions("ghost", actor="test")

    def test_duplicate_username_is_refused(self, founder) -> None:
        with pytest.raises(ValueError, match="already exists"):
            session_auth.create_user(username="FOUNDER", password=PASSWORD)
        with pytest.raises(ValueError, match="unknown role"):
            session_auth.create_user(username="other", password=PASSWORD, role="wizard")

    def test_unknown_user_lookup_raises(self, founder, session_db) -> None:
        with pytest.raises(LookupError):
            session_auth.set_user_enabled("ghost", enabled=True)
        with pytest.raises(LookupError):
            session_auth.change_password("ghost", "another passphrase")
        with pytest.raises(LookupError):
            session_auth.list_user_sessions("ghost")

    def test_list_users_and_sessions_never_expose_a_secret(self, founder) -> None:
        session_auth.login("founder", PASSWORD, ip="10.0.0.9")

        listed = session_auth.list_users()
        assert [u["username"] for u in listed] == ["founder"]
        assert "password_hash" in listed[0]  # the raw service record …
        assert "password_hash" not in session_auth.sanitize_user(listed[0])  # … has a safe shape
        assert "password_hash" not in session_auth.sanitize_users(listed)[0]

        sessions = session_auth.list_user_sessions("founder")
        assert len(sessions) == 1
        assert "token_hash" not in sessions[0]  # ids and stamps only
        assert sessions[0]["created_by_ip"] == "10.0.0.9"

    def test_unknown_user_pays_the_argon2_cost(self, founder, monkeypatch) -> None:
        """A dummy verify keeps unknown-user and wrong-password timings equal."""
        calls: list[str] = []
        real_verify = session_auth.verify_password_hash

        def counting_verify(stored_hash: str, password: str) -> bool:
            calls.append(stored_hash)
            return real_verify(stored_hash, password)

        monkeypatch.setattr(session_auth, "verify_password_hash", counting_verify)

        with pytest.raises(session_auth.LoginRejected) as unknown:
            session_auth.login("ghost", PASSWORD)
        assert calls, "unknown user must still run an argon2 verify"

        calls.clear()
        with pytest.raises(session_auth.LoginRejected):
            session_auth.login("founder", "the wrong passphrase")
        assert calls

        assert unknown.value.reason == session_auth.REASON_UNKNOWN_USER

    def test_all_three_failures_share_one_shape(self, founder) -> None:
        """Only ``reason`` distinguishes them — and only for the audit row."""
        reasons = []
        for username, password in (
            ("ghost", PASSWORD),
            ("founder", "the wrong passphrase"),
        ):
            with pytest.raises(session_auth.LoginRejected) as rejected:
                session_auth.login(username, password)
            reasons.append((str(rejected.value), rejected.value.reason))

        session_auth.set_user_enabled("founder", enabled=False, actor="test")
        with pytest.raises(session_auth.LoginRejected) as disabled:
            session_auth.login("founder", PASSWORD)
        reasons.append((str(disabled.value), disabled.value.reason))

        assert {message for message, _reason in reasons} == {"invalid_credentials"}
        assert {reason for _message, reason in reasons} == {
            session_auth.REASON_UNKNOWN_USER,
            session_auth.REASON_BAD_PASSWORD,
            session_auth.REASON_DISABLED,
        }

    def test_cache_is_bounded_under_a_garbage_token_flood(self, founder, monkeypatch) -> None:
        cap = 128
        monkeypatch.setattr(session_auth, "_CACHE_PRUNE_SIZE", cap)
        for i in range(cap * 2 + 32):
            assert session_auth.resolve_session(f"zolai_ss_garbage-{i:05d}") is None
        assert len(session_auth._CACHE) <= cap


# ---------------------------------------------------------------------------
# Service: audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_login_logout_and_user_lifecycle_are_audited(self, founder, session_db) -> None:
        token = session_auth.login("founder", PASSWORD, ip="10.0.0.1")["token"]
        session_auth.revoke_session(token)
        session_auth.set_user_enabled("founder", enabled=False, actor="test")
        session_auth.set_user_enabled("founder", enabled=True, actor="test")
        session_auth.login("founder", PASSWORD)
        session_auth.revoke_user_sessions("founder", actor="test")
        session_auth.change_password("founder", "yet another passphrase", actor="test")

        fields = [row[1] for row in _audit_rows(session_db)]
        assert "create" in fields
        assert fields.count("login") == 2
        assert fields.count("logout") == 1
        assert "disabled" in fields
        assert "enabled" in fields
        assert "revoke_sessions" in fields
        assert "password" in fields

    def test_no_secret_ever_reaches_an_audit_row(self, founder, session_db) -> None:
        digest = founder["password_hash"]
        token = session_auth.login("founder", PASSWORD)["token"]
        session_auth.revoke_session(token)
        session_auth.login("founder", PASSWORD)
        session_auth.change_password("founder", "a different passphrase", actor="test")

        blob = " | ".join(str(value) for row in _audit_rows(session_db) for value in row)

        assert PASSWORD not in blob
        assert digest not in blob
        assert "$argon2id" not in blob
        assert token not in blob
        assert "zolai_ss_" not in blob
        assert session_auth.hash_token(token) not in blob

    def test_rejected_login_reason_is_audited_without_a_secret(
        self, founder, session_db
    ) -> None:
        with pytest.raises(session_auth.LoginRejected):
            session_auth.login("founder", "the wrong passphrase")
        # The service itself does not audit a rejection (the router does, with
        # the reason) — assert what the service guarantees: nothing leaked.
        blob = " | ".join(str(value) for row in _audit_rows(session_db) for value in row)
        assert "the wrong passphrase" not in blob
