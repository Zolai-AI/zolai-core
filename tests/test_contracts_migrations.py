"""Tests for the additive Phase 1 contract migrations (Master Prompt §36).

Covers (pattern: test_lexicon_pos_migration.py):
- 16 contract columns land on vocabulary / foundation_evidence /
  grammar_patterns / provenance; 2 indexes; 4 new knowledge tables + indexes
- migration is idempotent (second run: 0 changes, everything skipped, 0 errors)
- legacy columns and row counts are byte-identical before/after
- column sets only GROW (ALTER TABLE ADD COLUMN appends)
- expression-unique claim key + UNIQUE claim_evidence pair + FKs enforced
- source scan of migrations.py: no DROP / RENAME / TRUNCATE outside comments,
  and every new table DDL uses CREATE TABLE IF NOT EXISTS
- wiring into run_all_migrations
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
    CLAIM_EVIDENCE_DDL,
    CONTRACT_COLUMNS,
    CONTRACT_INDEXES,
    HYPOTHESES_DDL,
    KNOWLEDGE_CLAIMS_DDL,
    KNOWLEDGE_TABLE_INDEXES,
    KNOWLEDGE_VERSIONS_DDL,
    add_contract_columns,
    create_contract_indexes,
    create_knowledge_tables,
    run_all_migrations,
)

MIGRATIONS_PATH = Path(__file__).resolve().parents[1] / "zolai" / "data" / "migrations.py"

NEW_TABLE_DDL = {
    "hypotheses": HYPOTHESES_DDL,
    "knowledge_claims": KNOWLEDGE_CLAIMS_DDL,
    "claim_evidence": CLAIM_EVIDENCE_DDL,
    "knowledge_versions": KNOWLEDGE_VERSIONS_DDL,
}

# Expected additive columns: table → ordered column names.
EXPECTED_NEW_COLUMNS: dict[str, tuple[str, ...]] = {
    "vocabulary": ("status",),
    "foundation_evidence": (
        "source_id",
        "document_id",
        "sentence_id",
        "observed_text",
        "method",
        "extractor",
    ),
    "grammar_patterns": (
        "normalized",
        "components",
        "sources",
        "evidence_ids",
        "confidence",
        "status",
    ),
    "provenance": ("source_type", "pipeline_version", "extractor_version"),
}

# Legacy (pre-Phase 1) table definitions — mirrors the live schema shape.
LEGACY_DDL = (
    """
    CREATE TABLE vocabulary (
        id INTEGER PRIMARY KEY,
        headword TEXT NOT NULL,
        english TEXT NOT NULL DEFAULT '',
        pos TEXT
    )
    """,
    """
    CREATE TABLE foundation_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fact_type TEXT NOT NULL,
        fact_key TEXT NOT NULL,
        tier INTEGER NOT NULL,
        source TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.0,
        provenance_hash TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE grammar_patterns (
        id INTEGER PRIMARY KEY,
        pattern_id TEXT NOT NULL,
        pattern TEXT NOT NULL,
        description TEXT,
        function TEXT NOT NULL DEFAULT '',
        examples TEXT NOT NULL DEFAULT '[]',
        frequency INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE provenance (
        id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT NOT NULL DEFAULT '',
        row_count INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        generator_script TEXT NOT NULL DEFAULT '',
        version TEXT NOT NULL DEFAULT '1.0',
        status TEXT NOT NULL DEFAULT 'active',
        updated_at TEXT NOT NULL DEFAULT '',
        change_log TEXT NOT NULL DEFAULT '[]'
    )
    """,
    """
    CREATE TABLE eval_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        set_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        case_count INTEGER NOT NULL DEFAULT 0,
        duration_ms REAL NOT NULL DEFAULT 0,
        gate_passed INTEGER NOT NULL DEFAULT 1,
        metrics TEXT NOT NULL DEFAULT '{}',
        source TEXT NOT NULL DEFAULT 'db'
    )
    """,
)

SEED_SQL = (
    "INSERT INTO vocabulary (id, headword, english, pos) VALUES (1, 'gam', 'earth', 'noun')",
    "INSERT INTO vocabulary (id, headword, english, pos) VALUES (2, 'vantung', 'heaven', NULL)",
    "INSERT INTO foundation_evidence (fact_type, fact_key, tier, source, confidence, created_at) "
    "VALUES ('word', 'word:gam', 1, 'bible_verses', 0.9, '2026-10-01T00:00:00+00:00')",
    "INSERT INTO foundation_evidence (fact_type, fact_key, tier, source, confidence, created_at) "
    "VALUES ('grammar', 'sentence:GEN 1:1', 3, 'grammar_patterns', 0.8, '2026-10-01T00:00:00+00:00')",
    "INSERT INTO grammar_patterns (id, pattern_id, pattern, frequency) VALUES (1, 'gp-1', 'S O V', 42)",
    "INSERT INTO provenance (id, filename, sha256, row_count) VALUES (1, 'dict.jsonl', 'deadbeef', 100)",
)

WRAPPED_TABLES = tuple(EXPECTED_NEW_COLUMNS)


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
    """Temporary DB with the legacy (pre-Phase 1) shape, seeded."""
    mgr = _make_db()
    yield mgr
    path = mgr.engine.url.database
    mgr.dispose()
    Path(path).unlink(missing_ok=True)


@pytest.fixture()
def tmp_db():
    """Temporary DB built from the ORM models (columns already present)."""
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
    """Ordered column names (table_info order)."""
    with mgr.engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return [r.name for r in rows]


def _row_count(mgr: DatabaseManager, table: str) -> int:
    with mgr.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def _snapshot(mgr: DatabaseManager, table: str, columns: list[str] | None = None) -> bytes:
    """Byte-level snapshot of the given (legacy) columns, ordered by id."""
    cols = columns if columns is not None else _columns(mgr, table)
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
# Contract columns
# ---------------------------------------------------------------------------


class TestAddContractColumns:
    def test_adds_all_16_columns(self, legacy_db: DatabaseManager) -> None:
        result = add_contract_columns(legacy_db)
        assert result["errors"] == []
        assert sorted(result["added"]) == sorted(
            f"{table}.{col}" for table, cols in EXPECTED_NEW_COLUMNS.items() for col in cols
        )
        for table, cols in EXPECTED_NEW_COLUMNS.items():
            before_after = _columns(legacy_db, table)
            for col in cols:
                assert col in before_after

    def test_idempotent_second_run_adds_nothing(self, legacy_db: DatabaseManager) -> None:
        first = add_contract_columns(legacy_db)
        second = add_contract_columns(legacy_db)
        assert len(first["added"]) == 16
        assert second["added"] == []
        assert second["errors"] == []
        assert len(second["skipped"]) == 16

    def test_column_sets_only_grow(self, legacy_db: DatabaseManager) -> None:
        before = {t: _columns(legacy_db, t) for t in WRAPPED_TABLES}
        add_contract_columns(legacy_db)
        for table, cols in EXPECTED_NEW_COLUMNS.items():
            after = _columns(legacy_db, table)
            assert after[: len(before[table])] == before[table], f"{table} columns reordered"
            assert after[len(before[table]) :] == list(cols)
            assert set(before[table]) < set(after)

    def test_row_counts_and_legacy_bytes_unchanged(self, legacy_db: DatabaseManager) -> None:
        legacy_cols = {t: _columns(legacy_db, t) for t in WRAPPED_TABLES}
        counts_before = {t: _row_count(legacy_db, t) for t in WRAPPED_TABLES}
        snaps_before = {
            t: _snapshot(legacy_db, t, legacy_cols[t]) for t in WRAPPED_TABLES
        }
        add_contract_columns(legacy_db)
        create_contract_indexes(legacy_db)
        create_knowledge_tables(legacy_db)
        assert {t: _row_count(legacy_db, t) for t in WRAPPED_TABLES} == counts_before
        assert {
            t: _snapshot(legacy_db, t, legacy_cols[t]) for t in WRAPPED_TABLES
        } == snaps_before

    def test_defaults_applied_to_existing_rows(self, legacy_db: DatabaseManager) -> None:
        add_contract_columns(legacy_db)
        with legacy_db.engine.connect() as conn:
            status = conn.execute(text("SELECT status FROM vocabulary WHERE id = 1")).scalar()
            evidence_ids = conn.execute(
                text("SELECT evidence_ids FROM grammar_patterns WHERE id = 1")
            ).scalar()
        assert status == "OBSERVED"
        assert evidence_ids == "[]"

    def test_missing_table_skipped(self, legacy_db: DatabaseManager) -> None:
        with legacy_db.engine.begin() as conn:
            conn.execute(text("DROP TABLE provenance"))
        result = add_contract_columns(legacy_db)
        assert result["errors"] == []
        assert "provenance (table missing)" in result["skipped"]
        assert not any(s.startswith("provenance.") for s in result["added"])


class TestContractIndexes:
    def test_creates_both_indexes(self, legacy_db: DatabaseManager) -> None:
        add_contract_columns(legacy_db)
        result = create_contract_indexes(legacy_db)
        assert result["errors"] == []
        assert result["created"] == ["ix_vocabulary_status", "ix_fev_document"]
        assert "ix_vocabulary_status" in _indexes(legacy_db, "vocabulary")
        assert "ix_fev_document" in _indexes(legacy_db, "foundation_evidence")

    def test_idempotent(self, legacy_db: DatabaseManager) -> None:
        add_contract_columns(legacy_db)
        create_contract_indexes(legacy_db)
        second = create_contract_indexes(legacy_db)
        assert second["created"] == []
        assert second["errors"] == []
        assert len(second["skipped"]) == 2

    def test_column_guard_skips_without_contract_columns(self, legacy_db: DatabaseManager) -> None:
        """Index creation without add_contract_columns skips with a reason."""
        result = create_contract_indexes(legacy_db)
        assert result["created"] == []
        assert result["errors"] == []
        assert any("run add_contract_columns" in s for s in result["skipped"])


# ---------------------------------------------------------------------------
# Knowledge tables
# ---------------------------------------------------------------------------


class TestCreateKnowledgeTables:
    def test_creates_four_tables_and_indexes(self, legacy_db: DatabaseManager) -> None:
        result = create_knowledge_tables(legacy_db)
        assert result["errors"] == []
        tables = _existing_tables(legacy_db)
        assert {"hypotheses", "knowledge_claims", "claim_evidence", "knowledge_versions"} <= tables
        assert {
            "hypotheses",
            "knowledge_claims",
            "claim_evidence",
            "knowledge_versions",
            "ix_hyp_kind_subject",
            "ix_hyp_status",
            "ux_kc_claim_key",
            "ux_claim_evidence_pair",
            "ix_claim_ev_evidence",
        } <= set(result["created"])

    def test_idempotent_second_run(self, legacy_db: DatabaseManager) -> None:
        first = create_knowledge_tables(legacy_db)
        second = create_knowledge_tables(legacy_db)
        assert len(first["created"]) == 9
        assert second["created"] == []
        assert second["errors"] == []
        assert len(second["skipped"]) == 9

    def test_existing_row_counts_untouched(self, legacy_db: DatabaseManager) -> None:
        before = {t: _row_count(legacy_db, t) for t in WRAPPED_TABLES}
        create_knowledge_tables(legacy_db)
        assert {t: _row_count(legacy_db, t) for t in WRAPPED_TABLES} == before

    def test_expression_unique_claim_key(self, legacy_db: DatabaseManager) -> None:
        create_knowledge_tables(legacy_db)
        with legacy_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO knowledge_claims (claim_type, subject, predicate) "
                    "VALUES ('lexicon', 'word:gam', 'means')"
                )
            )
        # Same S/P/O with object NULL must collide (NULL folds via COALESCE).
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO knowledge_claims (claim_type, subject, predicate) "
                        "VALUES ('lexicon', 'word:gam', 'means')"
                    )
                )
        # A different object is a different claim.
        with legacy_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO knowledge_claims (claim_type, subject, predicate, object) "
                    "VALUES ('lexicon', 'word:gam', 'means', 'earth')"
                )
            )

    def test_claim_evidence_unique_pair_and_fk(self, legacy_db: DatabaseManager) -> None:
        create_knowledge_tables(legacy_db)
        with legacy_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO knowledge_claims (claim_type, subject, predicate) "
                    "VALUES ('lexicon', 'word:gam', 'means')"
                )
            )
            conn.execute(
                text("INSERT INTO claim_evidence (claim_id, evidence_id) VALUES (1, 1)")
            )
        # UNIQUE (claim_id, evidence_id)
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO claim_evidence (claim_id, evidence_id) VALUES (1, 1)")
                )
        # FK → foundation_evidence.id
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO claim_evidence (claim_id, evidence_id) VALUES (1, 99999)")
                )

    def test_knowledge_versions_unique_version_and_row_version(self, legacy_db: DatabaseManager) -> None:
        create_knowledge_tables(legacy_db)
        with legacy_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO knowledge_versions (version) VALUES ('2026.10.0')"))
        with pytest.raises(IntegrityError):
            with legacy_db.engine.begin() as conn:
                conn.execute(text("INSERT INTO knowledge_versions (version) VALUES ('2026.10.0')"))
        with legacy_db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, row_version, eval_run_id FROM knowledge_versions")
            ).first()
        assert row.status == "OBSERVED"
        assert row.row_version == 1
        assert row.eval_run_id is None


# ---------------------------------------------------------------------------
# Wiring + ORM side
# ---------------------------------------------------------------------------


class TestRunAllMigrationsWiring:
    def test_keys_present_and_idempotent(self, legacy_db: DatabaseManager) -> None:
        first = run_all_migrations(legacy_db)
        assert "contract_columns" in first
        assert "contract_indexes" in first
        assert "knowledge_tables" in first
        assert len(first["contract_columns"]["added"]) == 16
        assert first["contract_columns"]["errors"] == []
        assert first["knowledge_tables"]["errors"] == []

        second = run_all_migrations(legacy_db)
        assert second["contract_columns"]["added"] == []
        assert second["contract_indexes"]["created"] == []
        assert second["knowledge_tables"]["created"] == []
        assert second["contract_columns"]["errors"] == []
        assert second["knowledge_tables"]["errors"] == []

    def test_orm_db_migration_is_noop(self, tmp_db: DatabaseManager) -> None:
        """Fresh ORM DB already has everything — migrations add nothing."""
        cols = add_contract_columns(tmp_db)
        idx = create_contract_indexes(tmp_db)
        tables = create_knowledge_tables(tmp_db)
        assert cols["added"] == []
        assert cols["errors"] == []
        assert idx["created"] == []
        assert tables["created"] == []
        assert tables["errors"] == []

    def test_new_tables_in_orm_registry(self) -> None:
        from zolai.data.models import (
            MODEL_REGISTRY,
            ClaimEvidenceRecord,
            HypothesisRecord,
            KnowledgeClaimRecord,
            KnowledgeVersionRecord,
        )

        assert MODEL_REGISTRY["hypotheses"] is HypothesisRecord
        assert MODEL_REGISTRY["knowledge_claims"] is KnowledgeClaimRecord
        assert MODEL_REGISTRY["claim_evidence"] is ClaimEvidenceRecord
        assert MODEL_REGISTRY["knowledge_versions"] is KnowledgeVersionRecord


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

    @pytest.mark.parametrize("name,_table,_column,ddl", CONTRACT_INDEXES)
    def test_contract_index_ddl_is_if_not_exists(self, name: str, _table: str, _column: str, ddl: str) -> None:
        assert ddl.startswith("CREATE INDEX IF NOT EXISTS"), ddl
        assert name in ddl

    @pytest.mark.parametrize("name,ddl", KNOWLEDGE_TABLE_INDEXES)
    def test_knowledge_index_ddl_is_if_not_exists(self, name: str, ddl: str) -> None:
        assert "CREATE" in ddl.split(name)[0]
        assert "IF NOT EXISTS" in ddl
        assert "DROP" not in ddl

    def test_contract_columns_are_add_alter_specs(self) -> None:
        for table, col, ddl in CONTRACT_COLUMNS:
            assert table in EXPECTED_NEW_COLUMNS, table
            assert col in EXPECTED_NEW_COLUMNS[table]
            assert not re.search(r"\b(DROP|RENAME|TRUNCATE)\b", ddl)

    def test_new_tables_have_no_alter_or_rebuild(self) -> None:
        for name, ddl in NEW_TABLE_DDL.items():
            assert "ALTER" not in ddl, name
            assert "CREATE TABLE IF NOT EXISTS" in ddl
