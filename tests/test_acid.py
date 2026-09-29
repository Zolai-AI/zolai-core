"""ACID hardening tests — per-connection PRAGMAs, immediate writes, integrity.

Covers:
- PRAGMAs are applied on **every fresh pooled connection** (``foreign_keys``
  is a per-connection setting, so a once-at-engine-creation PRAGMA is a bug).
- FK enforcement rejects orphan ``eval_cases`` rows.
- Session rollback leaves no partial rows.
- The ``ux_fraw_content_hash`` unique index rejects duplicates.
- 4 threads × 50 inserts through :meth:`DatabaseManager.write_session` with no
  ``SQLITE_BUSY`` escaping.
- ``PRAGMA integrity_check`` smoke + CHECK constraints.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from zolai.data.database import DatabaseManager
from zolai.data.integrity import (
    KIND_FOREIGN_KEY,
    KIND_FULL,
    fk_policy,
    foreign_key_check,
    full_integrity_check,
    startup_guard,
)
from zolai.eval.store import _SCHEMA_SQL

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mgr(tmp_path):
    """DatabaseManager over a throwaway SQLite file (ORM schema created)."""
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'acid.db'}")
    manager.init_db()
    yield manager
    manager.dispose()


def _exec_schema(manager: DatabaseManager, ddl: str) -> None:
    """Execute a multi-statement DDL script on ``manager``'s engine."""
    with manager.engine.connect() as conn:
        for stmt in ddl.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.commit()


@pytest.fixture()
def eval_schema(mgr):
    """Temp DB with the real ``eval_sets`` / ``eval_cases`` DDL applied."""
    _exec_schema(mgr, _SCHEMA_SQL)
    return mgr


# ---------------------------------------------------------------------------
# PRAGMA / connection-level guarantees
# ---------------------------------------------------------------------------


def test_foreign_keys_enabled_on_fresh_pooled_connection(mgr):
    """Each new pooled connection gets its own PRAGMA set."""
    for _ in range(3):
        with mgr.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
            assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1  # NORMAL
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"


def test_pragmas_survive_pool_reuse_and_new_connections(mgr):
    """Pool checkout #1 and a later checkout both carry the PRAGMAs."""
    with mgr.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    # Force a second, separate connection out of the pool.
    with mgr.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000


# ---------------------------------------------------------------------------
# Foreign keys + CHECK constraints
# ---------------------------------------------------------------------------


def test_bad_eval_case_insert_rejected(eval_schema):
    """An ``eval_cases`` row pointing at a missing set raises IntegrityError."""
    with eval_schema.session() as session:
        session.execute(
            text("INSERT INTO eval_sets(set_name, created_at) VALUES ('s1', 'now')")
        )

    with pytest.raises(IntegrityError):
        with eval_schema.session() as session:
            session.execute(
                text(
                    "INSERT INTO eval_cases(set_name, kind, payload) "
                    "VALUES ('does_not_exist', 'zvs', '{}')"
                )
            )

    with eval_schema.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM eval_cases")).scalar() == 0


def test_check_constraint_rejects_unknown_kind(eval_schema):
    """The ``kind`` CHECK constraint rejects out-of-vocabulary values."""
    with eval_schema.session() as session:
        session.execute(
            text("INSERT INTO eval_sets(set_name, created_at) VALUES ('s1', 'now')")
        )

    with pytest.raises(IntegrityError):
        with eval_schema.session() as session:
            session.execute(
                text(
                    "INSERT INTO eval_cases(set_name, kind, payload) "
                    "VALUES ('s1', 'not-a-lane', '{}')"
                )
            )


def test_foreign_key_check_reports_clean(eval_schema):
    """PRAGMA foreign_key_check is clean after a valid insert."""
    with eval_schema.session() as session:
        session.execute(
            text("INSERT INTO eval_sets(set_name, created_at) VALUES ('s1', 'now')")
        )
        session.execute(
            text(
                "INSERT INTO eval_cases(set_name, kind, payload) "
                "VALUES ('s1', 'zvs', '{}')"
            )
        )

    report = foreign_key_check(eval_schema)
    assert report["kind"] == KIND_FOREIGN_KEY
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["checked_at"]


# ---------------------------------------------------------------------------
# Rollback semantics
# ---------------------------------------------------------------------------


def test_session_rollback_leaves_no_partial_rows(eval_schema):
    """An exception mid-session undoes every statement in the block."""
    with pytest.raises(RuntimeError):
        with eval_schema.session() as session:
            session.execute(
                text("INSERT INTO eval_sets(set_name, created_at) VALUES ('a', 'now')")
            )
            session.execute(
                text("INSERT INTO eval_sets(set_name, created_at) VALUES ('b', 'now')")
            )
            raise RuntimeError("boom")

    with eval_schema.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM eval_sets")).scalar() == 0


def test_immediate_transaction_commits_and_rolls_back(mgr):
    """``immediate_transaction()`` is atomic in both directions."""
    _exec_schema(mgr, "CREATE TABLE acid_tx(id INTEGER PRIMARY KEY, v TEXT)")

    with mgr.immediate_transaction() as raw:
        raw.execute("INSERT INTO acid_tx(v) VALUES ('ok')")

    with mgr.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM acid_tx")).scalar() == 1

    with pytest.raises(ValueError):
        with mgr.immediate_transaction() as raw:
            raw.execute("INSERT INTO acid_tx(v) VALUES ('nope')")
            raise ValueError("rollback me")

    with mgr.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM acid_tx")).scalar() == 1


# ---------------------------------------------------------------------------
# Unique index
# ---------------------------------------------------------------------------


def test_unique_content_hash_rejects_duplicates(mgr):
    """``ux_fraw_content_hash`` mirrors the migrations DDL and rejects dups."""
    _exec_schema(
        mgr,
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_fraw_content_hash "
        "ON foundation_raw_corpus(content_hash)",
    )
    insert = (
        "INSERT INTO foundation_raw_corpus"
        "(source_type, source_path, content_hash, payload, imported_at) "
        "VALUES (:st, :sp, :ch, :p, 'now')"
    )
    params = {
        "st": "bible_usx",
        "sp": "/tmp/x.usx",
        "ch": "deadbeef",
        "p": "{}",
    }
    with mgr.session() as session:
        session.execute(text(insert), params)

    with pytest.raises(IntegrityError):
        with mgr.session() as session:
            session.execute(text(insert), params)

    with mgr.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM foundation_raw_corpus")).scalar() == 1


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_write_session_concurrent_no_sqlite_busy(mgr):
    """4 threads × 50 immediate writes — no SQLITE_BUSY escapes."""
    _exec_schema(mgr, "CREATE TABLE acid_probe(id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT)")

    threads: list[threading.Thread] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(worker_id: int) -> None:
        for i in range(50):
            try:
                with mgr.write_session() as session:
                    session.execute(
                        text("INSERT INTO acid_probe(msg) VALUES (:m)"),
                        {"m": f"{worker_id}-{i}"},
                    )
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assert
                with lock:
                    errors.append(exc)

    for worker_id in range(4):
        t = threading.Thread(target=worker, args=(worker_id,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"unexpected write errors: {errors[:3]}"
    with mgr.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM acid_probe")).scalar() == 200


def test_write_session_rolls_back_on_error(mgr):
    """``write_session()`` keeps the session() rollback contract."""
    _exec_schema(mgr, "CREATE TABLE acid_rollback(id INTEGER PRIMARY KEY, v TEXT)")

    with pytest.raises(ZeroDivisionError):
        with mgr.write_session() as session:
            session.execute(text("INSERT INTO acid_rollback(v) VALUES ('x')"))
            _ = 1 / 0

    with mgr.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM acid_rollback")).scalar() == 0


# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------


def test_integrity_check_smoke(mgr):
    """Full integrity_check on a fresh store reports exactly one 'ok' row."""
    report = full_integrity_check(mgr, write=False)
    assert report["kind"] == KIND_FULL
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["duration_ms"] >= 0


def test_startup_guard_keeps_policy_on_for_clean_store(mgr):
    """A clean store keeps FK enforcement on and reports no violations."""
    report = startup_guard(mgr)
    assert report["ok"] is True
    assert report["fk_policy"] == "on"
    assert fk_policy() == "on"


def test_fk_policy_is_settable_and_validated():
    from zolai.data.integrity import set_fk_policy

    original = fk_policy()
    try:
        assert set_fk_policy("deferred") == "deferred"
        assert fk_policy() == "deferred"
        with pytest.raises(ValueError):
            set_fk_policy("maybe")
    finally:
        set_fk_policy(original)


# ---------------------------------------------------------------------------
# CLI: `zolai db integrity`
# ---------------------------------------------------------------------------


def test_foreign_key_check_write_records_run(mgr):
    """An operator-invoked FK scan leaves an audit row in db_integrity_runs."""
    from sqlalchemy import text as _text

    with mgr.engine.connect() as conn:
        before = conn.execute(_text("SELECT count(*) FROM db_integrity_runs")).scalar()

    report = foreign_key_check(mgr, write=True)
    assert report["ok"] is True

    with mgr.engine.connect() as conn:
        after = conn.execute(_text("SELECT count(*) FROM db_integrity_runs")).scalar()
        row = conn.execute(
            _text(
                "SELECT check_type, ok FROM db_integrity_runs"
                " ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
    assert after == before + 1
    assert row[0] == KIND_FOREIGN_KEY
    assert row[1] == 1


def test_db_integrity_cli_reports_ok(mgr, monkeypatch):
    """`zolai db integrity --json` runs the fast FK scan and exits 0 when clean."""
    import json
    import re

    from typer.testing import CliRunner

    import zolai.data.database as database_module
    from zolai.cli.main import app as cli_app

    monkeypatch.setattr(database_module, "get_manager", lambda *a, **k: mgr)

    result = CliRunner().invoke(cli_app, ["db", "integrity", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout))
    assert payload["kind"] == KIND_FOREIGN_KEY
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["fk_policy"] in {"on", "deferred"}
