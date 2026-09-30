"""``api_keys`` migration tests — ADR-014 / backlog P0-1.

Covers the additive DDL helper and its wiring into ``run_all_migrations``:

- creates the table + ``ix_api_keys_revoked`` on a fresh store
- idempotent (second run skips, never rebuilds)
- additive: leaves every pre-existing table untouched
- schema/DDL shape: hash-only secrets, ``scopes`` default, UNIQUE name/hash
- listed in ``run_all_migrations`` under ``api_keys_table``
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    API_KEYS_DDL,
    API_KEYS_INDEXES,
    create_api_keys_table,
    run_all_migrations,
)

EXPECTED_COLUMNS = {
    "id",
    "name",
    "key_prefix",
    "key_hash",
    "scopes",
    "created_by",
    "created_at",
    "expires_at",
    "last_used_at",
    "revoked_at",
}


@pytest.fixture()
def tmp_db() -> Iterator[DatabaseManager]:
    """Fresh, initialized SQLite store (no ``api_keys`` yet)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


def _tables(mgr: DatabaseManager) -> set[str]:
    return set(inspect(mgr.engine).get_table_names())


def _indexes(mgr: DatabaseManager) -> set[str]:
    return {ix["name"] for ix in inspect(mgr.engine).get_indexes("api_keys")}


def _columns(mgr: DatabaseManager) -> set[str]:
    return {c["name"] for c in inspect(mgr.engine).get_columns("api_keys")}


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestApiKeyTableCreation:
    def test_creates_table_and_index(self, tmp_db: DatabaseManager) -> None:
        assert "api_keys" not in _tables(tmp_db)

        result = create_api_keys_table(tmp_db)

        assert result["errors"] == []
        assert result["created"] == ["api_keys"]
        assert "api_keys" in _tables(tmp_db)
        assert "ix_api_keys_revoked" in _indexes(tmp_db)

    def test_schema_matches_auth_contract(self, tmp_db: DatabaseManager) -> None:
        create_api_keys_table(tmp_db)

        assert _columns(tmp_db) == EXPECTED_COLUMNS

        with tmp_db.engine.connect() as conn:
            row = conn.execute(text("PRAGMA table_info(api_keys)")).fetchall()
        by_name = {r.name: r for r in row}
        # Secrets at rest: hash only — no plaintext column exists at all.
        assert "key_hash" in by_name
        assert not any(c.startswith("key_plain") for c in by_name)
        # UNIQUE on both the display name and the hash.
        assert by_name["name"].notnull == 1
        assert by_name["key_hash"].notnull == 1

        with tmp_db.engine.connect() as conn:
            fks = conn.execute(text("PRAGMA foreign_key_list(api_keys)")).fetchall()
        assert fks == []  # standalone — no legacy table is referenced

    def test_unique_name_and_hash_enforced(self, tmp_db: DatabaseManager) -> None:
        create_api_keys_table(tmp_db)
        insert = text(
            "INSERT INTO api_keys (name, key_prefix, key_hash, scopes) "
            "VALUES (:name, :prefix, :hash, '[]')"
        )
        with tmp_db.engine.begin() as conn:
            conn.execute(
                insert, {"name": "a", "prefix": "zolai_sk_AAA", "hash": "h1"}
            )

        with pytest.raises(Exception) as dup_name:
            with tmp_db.engine.begin() as conn:
                conn.execute(
                    insert, {"name": "a", "prefix": "zolai_sk_BBB", "hash": "h2"}
                )
        assert "unique" in str(dup_name.value).lower()

        with pytest.raises(Exception) as dup_hash:
            with tmp_db.engine.begin() as conn:
                conn.execute(
                    insert,
                    {"name": "b", "prefix": "zolai_sk_BBB", "hash": "h1"},
                )
        assert "unique" in str(dup_hash.value).lower()

    def test_default_scope_and_timestamps(self, tmp_db: DatabaseManager) -> None:
        create_api_keys_table(tmp_db)
        with tmp_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_keys (name, key_prefix, key_hash) "
                    "VALUES ('defaults', 'zolai_sk_CCC', 'h3')"
                )
            )
            row = conn.execute(
                text("SELECT scopes, created_at, revoked_at, last_used_at FROM api_keys")
            ).first()
        assert row.scopes == "[]"
        assert row.created_at  # DEFAULT (datetime('now')) fired
        assert row.revoked_at is None
        assert row.last_used_at is None


# ---------------------------------------------------------------------------
# Idempotency + additivity
# ---------------------------------------------------------------------------


class TestIdempotentAndAdditive:
    def test_second_run_skips_without_rebuilding(self, tmp_db: DatabaseManager) -> None:
        first = create_api_keys_table(tmp_db)
        assert first["created"] == ["api_keys"]

        with tmp_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_keys (name, key_prefix, key_hash) "
                    "VALUES ('keep-me', 'zolai_sk_KEEP', 'hash-keep')"
                )
            )

        second = create_api_keys_table(tmp_db)

        assert second["created"] == []
        assert second["errors"] == []
        assert second["skipped"] == ["api_keys (already exists)"]
        with tmp_db.engine.connect() as conn:
            kept = conn.execute(text("SELECT name FROM api_keys")).fetchall()
        assert [r.name for r in kept] == ["keep-me"]  # existing rows survive

    def test_existing_tables_untouched(self, tmp_db: DatabaseManager) -> None:
        before = _tables(tmp_db)
        create_api_keys_table(tmp_db)
        after = _tables(tmp_db)

        assert after - before == {"api_keys"}  # nothing renamed or dropped
        assert before <= after

    def test_run_all_migrations_includes_api_keys(self, tmp_db: DatabaseManager) -> None:
        result = run_all_migrations(tmp_db)

        assert "api_keys_table" in result
        assert result["api_keys_table"]["errors"] == []
        assert "api_keys" in _tables(tmp_db)

        # Second full pass stays green (the whole migration set is idempotent).
        again = run_all_migrations(tmp_db)
        assert again["api_keys_table"]["errors"] == []
        assert "api_keys (already exists)" in again["api_keys_table"]["skipped"]


# ---------------------------------------------------------------------------
# DDL shape
# ---------------------------------------------------------------------------


class TestDdlShape:
    def test_ddl_is_create_if_not_exists_and_stores_no_plaintext(self) -> None:
        from inspect import getsource

        from zolai.data import migrations

        assert "CREATE TABLE IF NOT EXISTS api_keys" in API_KEYS_DDL
        assert "key_hash" in API_KEYS_DDL
        assert "plaintext" not in API_KEYS_DDL
        # Rollback documented as an additive DROP in the module source.
        assert "DROP TABLE IF EXISTS api_keys" in getsource(migrations)

    def test_indexes_are_idempotent_statements(self) -> None:
        assert API_KEYS_INDEXES
        for statement in API_KEYS_INDEXES:
            assert "IF NOT EXISTS" in statement
