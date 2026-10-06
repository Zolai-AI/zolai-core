"""``users`` + ``sessions`` migration tests — username accounts, revocable sessions.

Covers the additive DDL helper and its wiring into ``run_all_migrations``:

- creates both tables + the three indexes on a fresh store
- idempotent (second run skips both tables, never rebuilds)
- additive: leaves every pre-existing table untouched
- schema/DDL shape: argon2id PHC hash column, UNIQUE username/token_hash,
  ``sessions`` defaults, NO foreign keys (standalone like ``api_keys``)
- no plaintext-password or plaintext-token column exists anywhere
- listed in ``run_all_migrations`` under ``user_session_tables``
- DDL is ``IF NOT EXISTS`` throughout and the rollback SQL lives in comments
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from inspect import getsource
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from zolai.data import migrations
from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    SESSIONS_DDL,
    USER_SESSION_INDEXES,
    USERS_DDL,
    create_user_session_tables,
    run_all_migrations,
)

EXPECTED_USER_COLUMNS = {
    "id",
    "username",
    "password_hash",
    "display_name",
    "role",
    "enabled",
    "created_at",
    "updated_at",
    "last_login",
}

EXPECTED_SESSION_COLUMNS = {
    "id",
    "user_id",
    "token_hash",
    "created_at",
    "expires_at",
    "revoked_at",
    "last_used_at",
    "created_by_ip",
}


@pytest.fixture()
def tmp_db() -> Iterator[DatabaseManager]:
    """A **pre-migration** store: initialized, then the two tables removed.

    ``init_db()`` materializes ``MODEL_REGISTRY``, which includes the
    ``users`` / ``sessions`` ORM records, so dropping them here reproduces the
    shape of the live 2.4 GB database before this migration runs — which is
    exactly what the additive path has to handle.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS sessions"))
        conn.execute(text("DROP TABLE IF EXISTS users"))
    assert not {"users", "sessions"} & _tables(mgr)
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


def _tables(mgr: DatabaseManager) -> set[str]:
    return set(inspect(mgr.engine).get_table_names())


def _indexes(mgr: DatabaseManager, table: str) -> set[str]:
    return {ix["name"] for ix in inspect(mgr.engine).get_indexes(table)}


def _columns(mgr: DatabaseManager, table: str) -> set[str]:
    return {c["name"] for c in inspect(mgr.engine).get_columns(table)}


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestUserSessionTableCreation:
    def test_creates_both_tables_and_indexes(self, tmp_db: DatabaseManager) -> None:
        assert "users" not in _tables(tmp_db)
        assert "sessions" not in _tables(tmp_db)

        result = create_user_session_tables(tmp_db)

        assert result["errors"] == []
        assert sorted(result["created"]) == ["sessions", "users"]
        assert "users" in _tables(tmp_db)
        assert "sessions" in _tables(tmp_db)
        assert _indexes(tmp_db, "sessions") == {"ix_sessions_user_id", "ix_sessions_expires"}
        assert _indexes(tmp_db, "users") == {"ix_users_enabled"}

    def test_schema_matches_auth_contract(self, tmp_db: DatabaseManager) -> None:
        create_user_session_tables(tmp_db)

        assert _columns(tmp_db, "users") == EXPECTED_USER_COLUMNS
        assert _columns(tmp_db, "sessions") == EXPECTED_SESSION_COLUMNS

        with tmp_db.engine.connect() as conn:
            users = {r.name: r for r in conn.execute(text("PRAGMA table_info(users)"))}
            sessions = {r.name: r for r in conn.execute(text("PRAGMA table_info(sessions)"))}

        # Credential columns are NOT NULL — a row without them is meaningless.
        assert users["username"].notnull == 1
        assert users["password_hash"].notnull == 1
        assert sessions["token_hash"].notnull == 1
        assert sessions["user_id"].notnull == 1
        assert sessions["expires_at"].notnull == 1

    def test_defaults_are_member_enabled_and_unrevoked(self, tmp_db: DatabaseManager) -> None:
        create_user_session_tables(tmp_db)
        with tmp_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash) "
                    "VALUES ('founder', '$argon2id$v=19$m=65536,t=3,p=4$dummy$hash')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, expires_at) "
                    "VALUES (1, 'hash-abc', '2099-01-01 00:00:00')"
                )
            )
            user = conn.execute(
                text("SELECT role, enabled, display_name, last_login FROM users")
            ).first()
            session = conn.execute(
                text("SELECT revoked_at, last_used_at, created_by_ip, created_at FROM sessions")
            ).first()

        assert user.role == "member"
        assert user.enabled == 1
        assert user.display_name is None
        assert user.last_login is None
        assert session.revoked_at is None
        assert session.last_used_at is None
        assert session.created_by_ip is None
        assert session.created_at  # DEFAULT (datetime('now')) fired

    def test_username_and_token_hash_are_unique(self, tmp_db: DatabaseManager) -> None:
        create_user_session_tables(tmp_db)
        with tmp_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash) VALUES ('ada', 'h1')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, expires_at) "
                    "VALUES (1, 's1', '2099-01-01 00:00:00')"
                )
            )

        with pytest.raises(Exception) as dup_user:
            with tmp_db.engine.begin() as conn:
                conn.execute(text("INSERT INTO users (username, password_hash) VALUES ('ada', 'h2')"))
        assert "unique" in str(dup_user.value).lower()

        with pytest.raises(Exception) as dup_token:
            with tmp_db.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO sessions (user_id, token_hash, expires_at) "
                        "VALUES (2, 's1', '2099-01-01 00:00:00')"
                    )
                )
        assert "unique" in str(dup_token.value).lower()

    def test_no_foreign_keys(self, tmp_db: DatabaseManager) -> None:
        """Standalone tables — the additive idiom (mirrors ``api_keys``)."""
        create_user_session_tables(tmp_db)
        with tmp_db.engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_key_list(users)")).fetchall() == []
            assert conn.execute(text("PRAGMA foreign_key_list(sessions)")).fetchall() == []


# ---------------------------------------------------------------------------
# Idempotency + additivity
# ---------------------------------------------------------------------------


class TestIdempotentAndAdditive:
    def test_second_run_skips_without_rebuilding(self, tmp_db: DatabaseManager) -> None:
        first = create_user_session_tables(tmp_db)
        assert sorted(first["created"]) == ["sessions", "users"]

        with tmp_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash) "
                    "VALUES ('keep-me', 'hash-keep')"
                )
            )

        second = create_user_session_tables(tmp_db)

        assert second["created"] == []
        assert second["errors"] == []
        assert sorted(second["skipped"]) == ["sessions (already exists)", "users (already exists)"]
        with tmp_db.engine.connect() as conn:
            kept = conn.execute(text("SELECT username FROM users")).fetchall()
        assert [r.username for r in kept] == ["keep-me"]  # existing rows survive

    def test_partial_store_creates_only_the_missing_table(self, tmp_db: DatabaseManager) -> None:
        with tmp_db.engine.connect() as conn:
            conn.execute(text(USERS_DDL))
            conn.commit()  # type: ignore[union-attr]

        result = create_user_session_tables(tmp_db)

        assert result["created"] == ["sessions"]
        assert result["skipped"] == ["users (already exists)"]
        assert result["errors"] == []

    def test_existing_tables_untouched(self, tmp_db: DatabaseManager) -> None:
        before = _tables(tmp_db)
        create_user_session_tables(tmp_db)
        after = _tables(tmp_db)

        assert after - before == {"users", "sessions"}  # nothing renamed or dropped
        assert before <= after

    def test_run_all_migrations_includes_user_sessions(self, tmp_db: DatabaseManager) -> None:
        result = run_all_migrations(tmp_db)

        assert "user_session_tables" in result
        assert result["user_session_tables"]["errors"] == []
        assert {"users", "sessions"} <= _tables(tmp_db)

        # Second full pass stays green (the whole migration set is idempotent).
        again = run_all_migrations(tmp_db)
        assert again["user_session_tables"]["errors"] == []
        assert again["user_session_tables"]["created"] == []
        assert "users (already exists)" in again["user_session_tables"]["skipped"]

    def test_second_pass_leaves_rows_untouched(self, tmp_db: DatabaseManager) -> None:
        create_user_session_tables(tmp_db)
        with tmp_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, role) "
                    "VALUES ('founder', 'argon2-hash', 'admin')"
                )
            )

        create_user_session_tables(tmp_db)

        with tmp_db.engine.connect() as conn:
            row = conn.execute(text("SELECT username, password_hash, role FROM users")).first()
        assert (row.username, row.password_hash, row.role) == ("founder", "argon2-hash", "admin")


# ---------------------------------------------------------------------------
# DDL shape — additive only, no plaintext credential column
# ---------------------------------------------------------------------------


class TestDdlShape:
    @pytest.mark.parametrize("ddl", [USERS_DDL, SESSIONS_DDL])
    def test_ddl_is_create_if_not_exists(self, ddl: str) -> None:
        stripped = ddl.strip()
        assert stripped.startswith("CREATE TABLE IF NOT EXISTS")
        assert "ALTER" not in stripped
        assert not re.search(r"\b(DROP|RENAME|TRUNCATE)\b", stripped)

    def test_no_plaintext_credential_column(self) -> None:
        users = USERS_DDL.lower()
        assert "plaintext" not in users
        assert "plain_password" not in users
        assert "password_hash" in users

        sessions = SESSIONS_DDL.lower()
        assert "plaintext" not in sessions
        assert "token" in sessions
        assert "token_hash" in sessions
        assert "token" not in sessions.replace("token_hash", "")

    def test_indexes_are_idempotent_statements(self) -> None:
        assert USER_SESSION_INDEXES
        for statement in USER_SESSION_INDEXES:
            assert statement.startswith("CREATE INDEX IF NOT EXISTS")

    def test_rollback_documented_as_comments(self) -> None:
        source = getsource(migrations)
        assert "DROP TABLE IF EXISTS sessions" in source
        assert "DROP TABLE IF EXISTS users" in source
        # …and the rollback SQL is comment-only: the helper's own body has none.
        body = getsource(migrations.create_user_session_tables)
        assert not re.search(r"\b(DROP|RENAME|TRUNCATE)\b", body)
