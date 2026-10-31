"""Tests for the dictionary lifecycle-column migration (soft delete + ZVS).

The canonical ``dictionary``/``dictionary_en_zo`` tables were replaced by their
phase2d ``_v2`` versions, which added four columns the ORM models never
declared.  Any store bootstrapped from ``init_db`` alone therefore lacked them,
and publishing crashed on ``zolai_vocabulary``-style queries that read
``zvs_compliance_status`` unconditionally.

Covers:
- the four columns land on both lexicon tables
- an existing row survives and picks up the column defaults
- a second run adds nothing (idempotent) and reports no errors
- ``run_all_migrations`` wires the migration in
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    DICTIONARY_LIFECYCLE_COLUMNS,
    add_dictionary_lifecycle_columns,
    run_all_migrations,
)

WANTED = {name for name, _ in DICTIONARY_LIFECYCLE_COLUMNS}


def _columns(mgr: DatabaseManager, table: str) -> set[str]:
    return {c["name"] for c in sa_inspect(mgr.engine).get_columns(table)}


def test_adds_columns_to_both_lexicon_tables(tmp_path) -> None:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    mgr.init_db()
    assert not (WANTED & _columns(mgr, "dictionary")), "fixture already migrated"

    result = add_dictionary_lifecycle_columns(mgr)

    assert result["errors"] == []
    assert WANTED <= _columns(mgr, "dictionary")
    assert WANTED <= _columns(mgr, "dictionary_en_zo")
    mgr.dispose()


def test_existing_rows_keep_their_values_and_get_defaults(tmp_path) -> None:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dictionary (id, zolai, english, pos, source) "
                "VALUES (1, 'pasian', 'God', 'n', 'test')"
            )
        )

    add_dictionary_lifecycle_columns(mgr)

    with mgr.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT zolai, english, zvs_compliance_status, is_deleted, deleted_at "
                "FROM dictionary WHERE id = 1"
            )
        ).one()
    assert tuple(row) == ("pasian", "God", "pending", 0, None)
    mgr.dispose()


def test_second_run_is_a_noop(tmp_path) -> None:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    mgr.init_db()

    first = add_dictionary_lifecycle_columns(mgr)
    second = add_dictionary_lifecycle_columns(mgr)

    assert first["added"] and first["errors"] == []
    assert second["added"] == []
    assert second["errors"] == []
    mgr.dispose()


def test_run_all_migrations_includes_the_columns(tmp_path) -> None:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    mgr.init_db()

    result = run_all_migrations(mgr)

    assert result["dictionary_lifecycle_columns"]["errors"] == []
    assert WANTED <= _columns(mgr, "dictionary")
    assert WANTED <= _columns(mgr, "dictionary_en_zo")
    mgr.dispose()
