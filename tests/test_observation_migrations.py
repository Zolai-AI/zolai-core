"""Tests for the additive Phase 2 observation migrations (Master Prompt §36).

Covers (pattern: test_contracts_migrations.py):

- the 3 new tables (``observations`` / ``word_observation_stats`` /
  ``attestation_index``) + 4 indexes land on a pre-Phase-2 store
- migration is idempotent (second run: 0 created, everything skipped, 0 errors)
- legacy tables and their row counts are byte-identical before/after
- UNIQUE ``ux_obs_source_ref`` is enforced (idempotency key)
- source scan of migrations.py: no DROP / RENAME / TRUNCATE outside comments,
  every new table DDL uses CREATE TABLE IF NOT EXISTS, no ALTER anywhere in the
  Phase 2 DDL, and every index DDL uses IF NOT EXISTS
- wiring into run_all_migrations + MODEL_REGISTRY registration
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    ATTESTATION_INDEX_DDL,
    OBSERVATION_TABLE_INDEXES,
    OBSERVATIONS_DDL,
    WORD_OBSERVATION_STATS_DDL,
    create_observation_tables,
    run_all_migrations,
)

MIGRATIONS_PATH = Path(__file__).resolve().parents[1] / "zolai" / "data" / "migrations.py"

NEW_TABLE_DDL = {
    "observations": OBSERVATIONS_DDL,
    "word_observation_stats": WORD_OBSERVATION_STATS_DDL,
    "attestation_index": ATTESTATION_INDEX_DDL,
}

NEW_TABLES = tuple(NEW_TABLE_DDL)
NEW_INDEXES = tuple(name for name, _ in OBSERVATION_TABLE_INDEXES)

# Pre-Phase 2 (legacy) tables — the shape the live store had before this phase.
LEGACY_DDL = (
    """
    CREATE TABLE dictionary (
        id INTEGER PRIMARY KEY,
        zolai TEXT NOT NULL,
        english TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE bible_verses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT NOT NULL,
        book TEXT NOT NULL,
        zo_tdb77 TEXT
    )
    """,
    """
    CREATE TABLE translations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        direction TEXT NOT NULL,
        reference TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE phrases (
        id INTEGER PRIMARY KEY,
        zolai TEXT NOT NULL,
        english TEXT NOT NULL DEFAULT '',
        frequency INTEGER NOT NULL DEFAULT 0
    )
    """,
)

SEED_SQL = (
    "INSERT INTO dictionary (id, zolai, english) VALUES (1, 'pasian', 'God')",
    "INSERT INTO dictionary (id, zolai, english) VALUES (2, 'gam', 'earth')",
    "INSERT INTO bible_verses (id, ref, book, zo_tdb77) VALUES (1, 'GEN 1:1', 'GEN', 'Pasian hi.')",
    "INSERT INTO translations (id, source, target, direction, reference) "
    "VALUES (1, 'pasian', 'God', 'zo_to_en', 'GEN 1:1')",
    "INSERT INTO phrases (id, zolai, english, frequency) VALUES (1, 'vantung', 'heaven', 3)",
)

LEGACY_TABLES = ("dictionary", "bible_verses", "translations", "phrases")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_db() -> DatabaseManager:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    mgr = DatabaseManager(f"sqlite:///{tmp.name}")
    with mgr.engine.begin() as conn:
        for stmt in LEGACY_DDL:
            conn.execute(text(stmt))
        for stmt in SEED_SQL:
            conn.execute(text(stmt))
    return mgr


@pytest.fixture()
def legacy_db():
    """Temporary DB with the pre-Phase 2 shape, seeded."""
    mgr = _make_db()
    yield mgr
    path = mgr.engine.url.database
    mgr.dispose()
    Path(path).unlink(missing_ok=True)


@pytest.fixture()
def tmp_db():
    """Temporary DB built from the ORM models (Phase 2 tables already present)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _columns(mgr: DatabaseManager, table: str) -> list[str]:
    with mgr.engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return [r.name for r in rows]


def _row_count(mgr: DatabaseManager, table: str) -> int:
    with mgr.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def _snapshot(mgr: DatabaseManager, table: str) -> bytes:
    """Byte-level snapshot of every column, ordered by id."""
    cols = _columns(mgr, table)
    selected = ", ".join(cols)
    with mgr.engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {selected} FROM {table} ORDER BY id")).all()
    payload = b""
    for row in rows:
        for value in tuple(row):
            payload += b"<NULL>" if value is None else str(value).encode()
            payload += b"\x00"
        payload += b"\x1e"
    return payload


def _indexes(mgr: DatabaseManager, table: str) -> set[str]:
    return {i["name"] for i in inspect(mgr.engine).get_indexes(table)}


def _existing_tables(mgr: DatabaseManager) -> set[str]:
    return set(inspect(mgr.engine).get_table_names())


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------


class TestCreateObservationTables:
    def test_creates_three_tables_and_four_indexes(self, legacy_db: DatabaseManager) -> None:
        result = create_observation_tables(legacy_db)
        assert result["errors"] == []
        tables = _existing_tables(legacy_db)
        assert set(NEW_TABLES) <= tables
        assert set(NEW_TABLES) | set(NEW_INDEXES) <= set(result["created"])

    def test_idempotent_second_run(self, legacy_db: DatabaseManager) -> None:
        first = create_observation_tables(legacy_db)
        second = create_observation_tables(legacy_db)
        assert len(first["created"]) == len(NEW_TABLES) + len(NEW_INDEXES)
        assert second["created"] == []
        assert second["errors"] == []
        assert len(second["skipped"]) == len(NEW_TABLES) + len(NEW_INDEXES)

    def test_legacy_row_counts_and_bytes_untouched(self, legacy_db: DatabaseManager) -> None:
        counts_before = {t: _row_count(legacy_db, t) for t in LEGACY_TABLES}
        snaps_before = {t: _snapshot(legacy_db, t) for t in LEGACY_TABLES}
        cols_before = {t: _columns(legacy_db, t) for t in LEGACY_TABLES}
        create_observation_tables(legacy_db)
        assert {t: _row_count(legacy_db, t) for t in LEGACY_TABLES} == counts_before
        assert {t: _snapshot(legacy_db, t) for t in LEGACY_TABLES} == snaps_before
        # Column sets are untouched: no ALTER of any existing table.
        for table in LEGACY_TABLES:
            assert _columns(legacy_db, table) == cols_before[table]

    def test_orm_db_migration_is_noop(self, tmp_db: DatabaseManager) -> None:
        """Fresh ORM DB already has everything — the migration adds nothing."""
        result = create_observation_tables(tmp_db)
        assert result["created"] == []
        assert result["errors"] == []

    def test_new_tables_in_orm_registry(self) -> None:
        from zolai.data.models import (
            MODEL_REGISTRY,
            AttestationIndexRecord,
            ObservationRecord,
            WordObservationStatsRecord,
        )

        assert MODEL_REGISTRY["observations"] is ObservationRecord
        assert MODEL_REGISTRY["word_observation_stats"] is WordObservationStatsRecord
        assert MODEL_REGISTRY["attestation_index"] is AttestationIndexRecord


# ---------------------------------------------------------------------------
# Schema contracts
# ---------------------------------------------------------------------------


class TestObservationSchema:
    def test_observations_has_contract_fields_and_unique_ref(
        self, legacy_db: DatabaseManager
    ) -> None:
        create_observation_tables(legacy_db)
        cols = _columns(legacy_db, "observations")
        assert cols == [
            "id",
            "text",
            "tokens",
            "context",
            "source_ref",
            "source_id",
            "document_id",
            "sentence_id",
            "method",
            "extractor",
            "metadata",
            "created_at",
        ]
        with legacy_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO observations (text, source_ref) VALUES ('Pasian hi.', 'bible_verses:zo_tdb77:1')"
                )
            )
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO observations (text, source_ref) VALUES ('dup', 'bible_verses:zo_tdb77:1')"
                    )
                )

    def test_stats_table_primary_key_and_scalars(self, legacy_db: DatabaseManager) -> None:
        create_observation_tables(legacy_db)
        cols = _columns(legacy_db, "word_observation_stats")
        assert cols[0] == "normalized_form"
        for scalar in ("frequency", "doc_freq", "sent_freq", "source_count", "diversity"):
            assert scalar in cols
        for surface in ("surface_forms", "contexts", "neighbors", "collocations", "attestation"):
            assert surface in cols
        with legacy_db.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO word_observation_stats (normalized_form) VALUES ('pasian')")
            )
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO word_observation_stats (normalized_form) VALUES ('pasian')")
                )

    def test_attestation_index_composite_primary_key(self, legacy_db: DatabaseManager) -> None:
        create_observation_tables(legacy_db)
        with legacy_db.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO attestation_index (word, source) VALUES ('pasian', 'bible')")
            )
            # Same word, different source is a distinct row.
            conn.execute(
                text("INSERT INTO attestation_index (word, source) VALUES ('pasian', 'dict')")
            )
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO attestation_index (word, source) VALUES ('pasian', 'bible')")
                )

    def test_expected_indexes_present(self, legacy_db: DatabaseManager) -> None:
        create_observation_tables(legacy_db)
        assert "ux_obs_source_ref" in _indexes(legacy_db, "observations")
        assert "ix_obs_sentence" in _indexes(legacy_db, "observations")
        assert "ix_obs_document" in _indexes(legacy_db, "observations")
        assert "ix_attestation_source" in _indexes(legacy_db, "attestation_index")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestRunAllMigrationsWiring:
    def test_keys_present_and_idempotent(self, legacy_db: DatabaseManager) -> None:
        first = run_all_migrations(legacy_db)
        assert "observation_tables" in first
        assert first["observation_tables"]["errors"] == []
        assert len(first["observation_tables"]["created"]) == len(NEW_TABLES) + len(NEW_INDEXES)

        second = run_all_migrations(legacy_db)
        assert second["observation_tables"]["created"] == []
        assert second["observation_tables"]["errors"] == []


# ---------------------------------------------------------------------------
# Additive-only source scan
# ---------------------------------------------------------------------------


class TestAdditiveOnlySourceScan:
    def test_no_destructive_ddl_outside_comments(self) -> None:
        source = MIGRATIONS_PATH.read_text(encoding="utf-8")
        forbidden = re.compile(r"\b(DROP|RENAME|TRUNCATE)\b")
        violations: list[str] = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # rollback documentation comments are allowed
            if forbidden.search(line):
                violations.append(f"L{lineno}: {stripped}")
        assert violations == [], f"destructive DDL outside comments: {violations}"

    @pytest.mark.parametrize("name", list(NEW_TABLE_DDL))
    def test_new_table_ddl_is_if_not_exists(self, name: str) -> None:
        ddl = NEW_TABLE_DDL[name].strip()
        assert ddl.startswith("CREATE TABLE IF NOT EXISTS"), ddl.splitlines()[0]
        assert "DROP" not in ddl
        assert "ALTER" not in ddl

    @pytest.mark.parametrize("name,ddl", OBSERVATION_TABLE_INDEXES)
    def test_new_index_ddl_is_if_not_exists(self, name: str, ddl: str) -> None:
        assert ddl.startswith("CREATE INDEX IF NOT EXISTS") or ddl.startswith(
            "CREATE UNIQUE INDEX IF NOT EXISTS"
        ), ddl
        assert name in ddl
        assert "DROP" not in ddl
        assert "ALTER" not in ddl

    def test_phase2_ddl_never_touches_existing_tables(self) -> None:
        """The Phase 2 DDL contains no statement against a legacy table."""
        phase2_ddl = " ".join(NEW_TABLE_DDL.values()) + " " + " ".join(
            ddl for _, ddl in OBSERVATION_TABLE_INDEXES
        )
        for legacy in LEGACY_TABLES:
            assert legacy not in phase2_ddl, f"Phase 2 DDL references {legacy}"
