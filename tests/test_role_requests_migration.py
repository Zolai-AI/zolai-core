"""``role_requests`` + contribution-attribution migration tests.

Both helpers are additive DDL that fails quietly: a ``CREATE TABLE`` that is not
idempotent breaks every later startup, and a unique index that is not actually
*partial* turns "ask for a role twice a year" into a permanent refusal after the
first decision.  What is held here: ``create_role_request_tables`` creates once,
skips forever, and keeps the status domain in a ``CHECK`` a bad value cannot
walk past; the pending-rows-only index constrains one ask per
``(user_id, requested_role)``, freed the moment that row is decided and never
spanning two roles; a fresh store already carries the three attribution columns
while a pre-attribution store gains exactly the missing ones by guarded
``ALTER``, with every existing row intact and NULL; and the rollback SQL
documented beside the forward migration has not rotted.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from inspect import getsource
from pathlib import Path

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import inspect, text

from zolai.data import migrations
from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    CONTRIBUTIONS_ATTRIBUTION_COLUMNS,
    CONTRIBUTIONS_DDL,
    ROLE_REQUESTS_DDL,
    ROLE_REQUESTS_INDEXES,
    add_contribution_attribution_columns,
    create_role_request_tables,
    ensure_contributions_table,
    run_all_migrations,
)

EXPECTED_REQUEST_COLUMNS = {
    "id",
    "user_id",
    "username",
    "requested_role",
    "reason",
    "status",
    "created_at",
    "decided_by",
    "decided_at",
    "note",
}

#: The 13-column ``contributions`` table exactly as it stood **before**
#: attribution — the shape a live store still has, and the only shape
#: :func:`add_contribution_attribution_columns` was written to handle.
LEGACY_CONTRIBUTIONS_DDL = """
CREATE TABLE contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    word TEXT NOT NULL,
    field TEXT NOT NULL DEFAULT '',
    current_value TEXT NOT NULL DEFAULT '',
    suggested_value TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT
)
"""

#: Bare column names, then the ``table.column`` form the migration reports in
#: its ``added`` / ``skipped`` lists — keeping both from one source of truth so
#: neither test set can drift out of sync with the other.
ATTRIBUTION_NAMES = ("user_id", "submitted_by", "auth_source")
ATTRIBUTION_TRIPLE = tuple(f"contributions.{name}" for name in ATTRIBUTION_NAMES)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Iterator[DatabaseManager]:
    """Fresh, initialized store — neither table is ORM-defined, so neither exists."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'role_requests.db'}")
    mgr.init_db()
    yield mgr
    mgr.dispose()


@pytest.fixture()
def legacy_db(tmp_path: Path) -> Iterator[DatabaseManager]:
    """A store whose ``contributions`` predates attribution (13 columns, one row)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'legacy.db'}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        conn.execute(text(LEGACY_CONTRIBUTIONS_DDL))
        conn.execute(
            text(
                "INSERT INTO contributions (type, word, suggested_value, created_at, email) "
                "VALUES ('new_word', 'hlaw', 'a kind of pond', '2025-01-01 00:00:00', 'old@example.zo')"
            )
        )
    yield mgr
    mgr.dispose()


def _tables(mgr: DatabaseManager) -> set[str]:
    return set(inspect(mgr.engine).get_table_names())


def _columns(mgr: DatabaseManager, table: str) -> set[str]:
    return {c["name"] for c in inspect(mgr.engine).get_columns(table)}


def _indexes(mgr: DatabaseManager, table: str) -> set[str]:
    return {ix["name"] for ix in inspect(mgr.engine).get_indexes(table)}


def _rows(mgr: DatabaseManager, sql: str) -> list[tuple]:
    with mgr.engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql))]


def _insert_request(mgr: DatabaseManager, user_id: int, role: str, status: str = "pending") -> None:
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO role_requests (user_id, username, requested_role, reason, status) "
                "VALUES (:user_id, :username, :role, 'please', :status)"
            ),
            {"user_id": user_id, "username": f"user{user_id}", "role": role, "status": status},
        )


# ---------------------------------------------------------------------------
# create_role_request_tables — once, then forever skipped
# ---------------------------------------------------------------------------


class TestRoleRequestTableCreation:
    def test_creates_table_and_all_three_indexes(self, tmp_db: DatabaseManager) -> None:
        assert "role_requests" not in _tables(tmp_db)

        result = create_role_request_tables(tmp_db)

        assert result["created"] == ["role_requests"]
        assert result["skipped"] == []
        assert result["errors"] == []
        assert _indexes(tmp_db, "role_requests") == {
            "ix_role_requests_status",
            "ix_role_requests_user",
            "ux_role_requests_pending",
        }

    def test_columns_match_the_queue_contract(self, tmp_db: DatabaseManager) -> None:
        create_role_request_tables(tmp_db)

        assert _columns(tmp_db, "role_requests") == EXPECTED_REQUEST_COLUMNS
        with tmp_db.engine.connect() as conn:
            by_name = {r.name: r for r in conn.execute(text("PRAGMA table_info(role_requests)"))}
        # An ask is meaningless without a subject, a role and a justification.
        assert by_name["user_id"].notnull == 1
        assert by_name["requested_role"].notnull == 1
        assert by_name["reason"].notnull == 1
        assert by_name["status"].notnull == 1
        # The decision columns are NULL until a superadmin writes one.
        assert by_name["decided_by"].notnull == 0
        assert by_name["decided_at"].notnull == 0

    def test_second_run_skips_and_creates_nothing(self, tmp_db: DatabaseManager) -> None:
        assert create_role_request_tables(tmp_db)["created"] == ["role_requests"]
        _insert_request(tmp_db, 1, "admin")

        second = create_role_request_tables(tmp_db)

        assert second["skipped"] == ["role_requests (already exists)"]
        assert second["created"] == []
        assert second["errors"] == []
        # Idempotent means the ask survives the second run, not just the table.
        assert _rows(tmp_db, "SELECT user_id, requested_role, status FROM role_requests") == [
            (1, "admin", "pending")
        ]

    def test_ddl_is_if_not_exists_and_indexes_too(self) -> None:
        assert ROLE_REQUESTS_DDL.strip().startswith("CREATE TABLE IF NOT EXISTS role_requests")
        assert len(ROLE_REQUESTS_INDEXES) == 3
        for statement in ROLE_REQUESTS_INDEXES:
            assert "IF NOT EXISTS" in statement

    def test_no_foreign_keys(self, tmp_db: DatabaseManager) -> None:
        """Standalone — the repo's additive idiom (``users``/``sessions``/``api_keys``)."""
        create_role_request_tables(tmp_db)
        assert _rows(tmp_db, "PRAGMA foreign_key_list(role_requests)") == []


# ---------------------------------------------------------------------------
# The status CHECK and the created_at default
# ---------------------------------------------------------------------------


class TestStatusDomain:
    @pytest.mark.parametrize("status", ["pending", "approved", "rejected"])
    def test_the_three_documented_statuses_are_accepted(
        self, tmp_db: DatabaseManager, status: str
    ) -> None:
        create_role_request_tables(tmp_db)

        _insert_request(tmp_db, 1, "admin", status=status)

        assert _rows(tmp_db, "SELECT status FROM role_requests WHERE id = 1") == [(status,)]

    def test_a_fourth_status_is_refused(self, tmp_db: DatabaseManager) -> None:
        """A value outside the CHECK must not land — 'maybe' is not a decision.

        The route layer validates too, but the constraint is the last line: a
        hand-written row, a future writer that skips validation, or a restored
        dump all reach SQLite rather than the API.
        """
        create_role_request_tables(tmp_db)

        with pytest.raises((sa_exc.IntegrityError, sqlite3.IntegrityError)):
            _insert_request(tmp_db, 1, "admin", status="maybe")

        assert _rows(tmp_db, "SELECT COUNT(*) FROM role_requests") == [(0,)]

    def test_created_at_defaults_when_omitted(self, tmp_db: DatabaseManager) -> None:
        create_role_request_tables(tmp_db)
        with tmp_db.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO role_requests (user_id, requested_role, reason) "
                     "VALUES (42, 'admin', 'no stamp supplied')")
            )

        stamped = _rows(tmp_db, "SELECT created_at, status, username FROM role_requests")

        assert len(stamped) == 1
        created_at, status, username = stamped[0]
        assert created_at  # the DDL default fired
        assert status == "pending"  # and so did the status default
        assert username == ""  # NOT NULL DEFAULT '' rather than NULL

    def test_status_is_indexed_for_the_queue_read(self, tmp_db: DatabaseManager) -> None:
        """``(status, created_at)`` is how the admin queue opens newest-first."""
        create_role_request_tables(tmp_db)
        with tmp_db.engine.connect() as conn:
            status_columns = [
                row.name
                for row in conn.execute(text("PRAGMA index_info('ix_role_requests_status')"))
            ]
            index_lists = [
                dict(row._mapping)
                for row in conn.execute(text("PRAGMA index_list('role_requests')"))
            ]

        assert status_columns == ["status", "created_at"]
        # SQLite's own report of the ``WHERE`` clause: only the anti-flood index
        # is partial.  A plain unique index would read 0 here — the regression.
        partial_flags = {row["name"]: int(row["partial"]) for row in index_lists}
        assert partial_flags["ux_role_requests_pending"] == 1
        assert partial_flags["ix_role_requests_status"] == 0


# ---------------------------------------------------------------------------
# The partial unique index — partial, per-role, and freed by a decision
# ---------------------------------------------------------------------------


class TestPartialUniqueIndex:
    def test_two_pending_asks_for_one_role_are_refused(self, tmp_db: DatabaseManager) -> None:
        create_role_request_tables(tmp_db)

        _insert_request(tmp_db, 7, "admin")

        with pytest.raises((sa_exc.IntegrityError, sqlite3.IntegrityError)):
            _insert_request(tmp_db, 7, "admin")

    def test_a_decided_ask_frees_the_slot(self, tmp_db: DatabaseManager) -> None:
        """The index is *partial*: it counts pending rows and nothing else.

        Making it a plain unique index would block a legitimate second ask
        forever after the first was answered — the failure mode this test names.
        """
        create_role_request_tables(tmp_db)
        _insert_request(tmp_db, 7, "admin")
        with tmp_db.engine.begin() as conn:
            conn.execute(text("UPDATE role_requests SET status = 'approved' WHERE id = 1"))

        _insert_request(tmp_db, 7, "admin")  # must succeed

        assert _rows(tmp_db, "SELECT status FROM role_requests ORDER BY id") == [
            ("approved",),
            ("pending",),
        ]

    def test_rejected_also_frees_the_slot(self, tmp_db: DatabaseManager) -> None:
        create_role_request_tables(tmp_db)
        _insert_request(tmp_db, 7, "admin", status="rejected")

        _insert_request(tmp_db, 7, "admin")

        assert len(_rows(tmp_db, "SELECT id FROM role_requests")) == 2

    def test_different_roles_do_not_collide(self, tmp_db: DatabaseManager) -> None:
        """Asking for two roles at once is two asks, not a duplicate."""
        create_role_request_tables(tmp_db)

        _insert_request(tmp_db, 7, "admin")
        _insert_request(tmp_db, 7, "superadmin")

        assert _rows(tmp_db, "SELECT requested_role, status FROM role_requests ORDER BY id") == [
            ("admin", "pending"),
            ("superadmin", "pending"),
        ]

    def test_it_does_not_constrain_other_accounts(self, tmp_db: DatabaseManager) -> None:
        create_role_request_tables(tmp_db)

        _insert_request(tmp_db, 7, "admin")
        _insert_request(tmp_db, 8, "admin")

        assert _rows(tmp_db, "SELECT COUNT(*) FROM role_requests") == [(2,)]

    def test_the_index_is_declared_partial(self) -> None:
        """The ``WHERE`` clause is the whole point; losing it silently widens it."""
        partial = [s for s in ROLE_REQUESTS_INDEXES if s.startswith("CREATE UNIQUE INDEX")]
        assert len(partial) == 1
        assert "WHERE status = 'pending'" in partial[0]


# ---------------------------------------------------------------------------
# contributions — fresh shape vs. the guarded ALTER path
# ---------------------------------------------------------------------------


class TestContributionsAttribution:
    def test_fresh_store_gains_attribution_in_the_ddl(self, tmp_db: DatabaseManager) -> None:
        assert "contributions" not in _tables(tmp_db)

        result = ensure_contributions_table(tmp_db)

        assert result["created"] == ["contributions"]
        assert result["errors"] == []
        assert set(ATTRIBUTION_TRIPLE) <= {f"contributions.{c}" for c in _columns(tmp_db, "contributions")}
        # A new store needs no ALTER pass at all.
        assert add_contribution_attribution_columns(tmp_db)["added"] == []

    def test_attribution_columns_are_nullable(self, tmp_db: DatabaseManager) -> None:
        """NOT NULL would mislabel every historical row; NULL means anonymous."""
        ensure_contributions_table(tmp_db)
        with tmp_db.engine.connect() as conn:
            by_name = {r.name: r for r in conn.execute(text("PRAGMA table_info(contributions)"))}

        for column in ATTRIBUTION_NAMES:
            assert by_name[column].notnull == 0

    def test_existing_rows_are_null_not_invented(self, tmp_db: DatabaseManager) -> None:
        ensure_contributions_table(tmp_db)
        with tmp_db.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO contributions (type, word, suggested_value, created_at) "
                     "VALUES ('new_word', 'thang', 'meaning', '2026-01-01 00:00:00')")
            )
        add_contribution_attribution_columns(tmp_db)

        row = _rows(
            tmp_db,
            "SELECT word, user_id, submitted_by, auth_source FROM contributions WHERE id = 1",
        )
        assert row == [("thang", None, None, None)]

    def test_legacy_store_gains_exactly_the_missing_columns(self, legacy_db: DatabaseManager) -> None:
        mgr = legacy_db
        before = _columns(mgr, "contributions")
        assert not set(ATTRIBUTION_NAMES) & before

        result = add_contribution_attribution_columns(mgr)

        assert result["added"] == list(ATTRIBUTION_TRIPLE)
        assert result["skipped"] == []
        assert result["errors"] == []
        assert _columns(mgr, "contributions") == before | set(ATTRIBUTION_NAMES)
        # 16 columns, not a rebuild: nothing else moved.
        assert len(_columns(mgr, "contributions")) == 16

    def test_legacy_row_survives_with_nulls(self, legacy_db: DatabaseManager) -> None:
        mgr = legacy_db

        add_contribution_attribution_columns(mgr)

        assert _rows(
            mgr,
            "SELECT word, email, status, user_id, submitted_by, auth_source "
            "FROM contributions WHERE id = 1",
        ) == [("hlaw", "old@example.zo", "pending", None, None, None)]

    def test_second_alter_pass_is_a_no_op(self, legacy_db: DatabaseManager) -> None:
        mgr = legacy_db
        assert len(add_contribution_attribution_columns(mgr)["added"]) == 3

        again = add_contribution_attribution_columns(mgr)

        assert again["added"] == []
        assert again["errors"] == []
        assert sorted(again["skipped"]) == sorted(f"{name} (already exists)" for name in ATTRIBUTION_TRIPLE)
        assert _rows(mgr, "SELECT COUNT(*) FROM contributions") == [(1,)]

    def test_existing_table_is_never_recreated(self, legacy_db: DatabaseManager) -> None:
        """``ensure_contributions_table`` skips; the ALTER pass is a separate step."""
        mgr = legacy_db

        result = ensure_contributions_table(mgr)

        assert result["created"] == []
        assert result["skipped"] == ["contributions (already exists)"]
        assert "user_id" not in _columns(mgr, "contributions")  # still the legacy shape

        add_contribution_attribution_columns(mgr)
        assert "user_id" in _columns(mgr, "contributions")

    def test_missing_table_is_reported_not_created(self, tmp_db: DatabaseManager) -> None:
        """The ALTER helper refuses to invent a table it does not own."""
        assert "contributions" not in _tables(tmp_db)

        result = add_contribution_attribution_columns(tmp_db)

        assert result == {"added": [], "skipped": ["contributions (table missing)"], "errors": []}
        assert "contributions" not in _tables(tmp_db)

    def test_attribution_column_list_is_the_documented_three(self) -> None:
        assert CONTRIBUTIONS_ATTRIBUTION_COLUMNS == (
            ("contributions", "user_id", "INTEGER"),
            ("contributions", "submitted_by", "TEXT"),
            ("contributions", "auth_source", "TEXT"),
        )
        for name in ("user_id", "submitted_by", "auth_source"):
            assert name in CONTRIBUTIONS_DDL


# ---------------------------------------------------------------------------
# run_all_migrations wiring
# ---------------------------------------------------------------------------


class TestRunAllMigrations:
    def test_all_three_entries_are_reported(self, tmp_db: DatabaseManager) -> None:
        result = run_all_migrations(tmp_db)

        assert {"role_requests_table", "contributions_table", "contribution_attribution_columns"} <= set(result)
        assert result["role_requests_table"]["created"] == ["role_requests"]
        assert result["contributions_table"]["created"] == ["contributions"]
        assert result["contribution_attribution_columns"]["added"] == []
        for key in ("role_requests_table", "contributions_table", "contribution_attribution_columns"):
            assert result[key]["errors"] == []
        assert {"role_requests", "contributions"} <= _tables(tmp_db)

    def test_second_full_pass_stays_green(self, tmp_db: DatabaseManager) -> None:
        run_all_migrations(tmp_db)

        again = run_all_migrations(tmp_db)

        for key in ("role_requests_table", "contributions_table"):
            assert again[key]["errors"] == []
            assert again[key]["created"] == []
            assert again[key]["skipped"]
        assert again["contribution_attribution_columns"]["errors"] == []
        assert again["contribution_attribution_columns"]["added"] == []

    def test_run_all_on_a_legacy_store_adds_the_columns(self, legacy_db: DatabaseManager) -> None:
        """The aggregate is what a real startup runs, so it must close the gap."""
        result = run_all_migrations(legacy_db)

        assert result["contribution_attribution_columns"]["added"] == list(ATTRIBUTION_TRIPLE)
        assert result["contribution_attribution_columns"]["errors"] == []
        assert _rows(legacy_db, "SELECT COUNT(*) FROM contributions") == [(1,)]


# ---------------------------------------------------------------------------
# The rollback comment must not rot away from the DDL it describes
# ---------------------------------------------------------------------------


class TestRollbackStaysDocumented:
    @staticmethod
    def _rollback_block() -> str:
        """The comment block between the queue's header and the DDL it documents.

        Scoped to the section rather than the whole module on purpose: a rollback
        line that drifted elsewhere in the file would keep this test green while
        the block a human reads next to ``ROLE_REQUESTS_DDL`` said nothing.
        """
        source = getsource(migrations)
        ddl_at = source.index("ROLE_REQUESTS_DDL = ")
        block_at = source.rindex("# Reverse (rollback) SQL", 0, ddl_at)
        return source[block_at:ddl_at]

    def test_rollback_sql_names_every_object_the_migration_creates(self) -> None:
        block = self._rollback_block()

        # Kept beside the forward DDL for one reason: the day someone edits
        # ``ROLE_REQUESTS_DDL`` they see the teardown in the same screen, so the
        # two cannot drift apart over a release.
        assert "DROP TABLE IF EXISTS role_requests;" in block
        for column in ATTRIBUTION_NAMES:
            assert f"ALTER TABLE contributions DROP COLUMN {column};" in block
        # ``DROP TABLE`` takes its indexes with it, so only the two that may be
        # dropped while the table itself stays are listed individually — and
        # ``ix_role_requests_user`` is a read accelerator, not a constraint.
        assert "DROP INDEX IF EXISTS ux_role_requests_pending;" in block
        assert "DROP INDEX IF EXISTS ix_role_requests_status;" in block

    def test_rollback_sql_is_not_stale(self) -> None:
        """The other direction: nothing documented may name a gone object.

        A rollback line surviving a rename is worse than a missing one — an
        operator pastes it, SQLite errors, and they cannot tell whether the
        migration or the comment is the thing that moved.
        """
        block = self._rollback_block()
        created_indexes = " ".join(ROLE_REQUESTS_INDEXES)

        for name in re.findall(r"DROP INDEX IF EXISTS (\w+);", block):
            assert name in created_indexes, name
        for name in re.findall(r"DROP COLUMN (\w+);", block):
            assert name in ATTRIBUTION_NAMES, name

    def test_rollback_is_comment_only_and_never_executed(self) -> None:
        block = self._rollback_block()

        assert all(line.lstrip().startswith("#") for line in block.splitlines() if line.strip())

        body = getsource(migrations.create_role_request_tables)
        assert "DROP" not in body.upper()
        alter_body = getsource(migrations.add_contribution_attribution_columns)
        assert "DROP COLUMN" not in alter_body.upper()
