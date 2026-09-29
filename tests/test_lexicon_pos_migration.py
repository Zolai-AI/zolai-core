"""Tests for the L1.3 lexicon POS / provenance migration.

Covers:
- 13 additive columns land on each of dictionary / vocabulary / zolai_vocabulary
- migration is idempotent (second run: 0 added, everything skipped, 0 errors)
- the legacy ``pos`` column and row counts are byte-identical before/after
- ambiguous legacy labels keep ``pos_canonical IS NULL`` with valid candidate JSON
- empty ``pos`` rows are left completely untouched
- the 3 partial ``pos_canonical`` indexes are created once
- run_all_migrations is safe when ``zolai_vocabulary`` is absent
- ORM round-trip on all three tables
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    LEXICON_BACKFILL_VERSION,
    LEXICON_POS_COLUMNS,
    LEXICON_POS_INDEXES,
    LEXICON_TABLES,
    add_lexicon_pos_columns,
    backfill_pos_canonical,
    create_lexicon_pos_indexes,
    run_all_migrations,
)
from zolai.data.models import (
    Base,
    DictionaryEntry,
    VocabularyEntry,
    ZolaiVocabularyEntry,
)

NEW_COLUMNS = [name for name, _ in LEXICON_POS_COLUMNS]

# Legacy (pre-L1.3) table definitions — mirrors the live schema shape.
LEGACY_DDL = (
    """
    CREATE TABLE dictionary (
        id INTEGER PRIMARY KEY,
        zolai TEXT NOT NULL,
        english TEXT NOT NULL,
        myanmar TEXT,
        pos TEXT,
        source TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE vocabulary (
        id INTEGER PRIMARY KEY,
        headword TEXT NOT NULL,
        english TEXT NOT NULL DEFAULT '',
        pos TEXT
    )
    """,
    # `confidence` predates L1.3 on the live table — it must be skipped, not re-added.
    """
    CREATE TABLE zolai_vocabulary (
        id INTEGER PRIMARY KEY,
        zolai TEXT NOT NULL,
        english TEXT,
        myanmar TEXT,
        pos TEXT,
        confidence REAL DEFAULT 1.0
    )
    """,
)

# (table, id, pos) seed rows shared by the backfill tests.
SEED_ROWS = (
    ("dictionary", 1, "noun"),
    ("dictionary", 2, "conjunction"),
    ("dictionary", 3, "pt par"),
    ("dictionary", 4, ""),
    ("dictionary", 5, None),
    ("vocabulary", 1, "transitive verb"),
    ("vocabulary", 2, "V, adjective"),
    ("vocabulary", 3, ""),
    ("zolai_vocabulary", 1, "Noun"),
    ("zolai_vocabulary", 2, "adv & a"),
    ("zolai_vocabulary", 3, "unknown"),
    ("zolai_vocabulary", 4, ""),
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_db(ddl: tuple[str, ...]) -> DatabaseManager:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    mgr = DatabaseManager(f"sqlite:///{tmp.name}")
    with mgr.engine.begin() as conn:
        for stmt in ddl:
            conn.execute(text(stmt))
    return mgr


@pytest.fixture()
def legacy_db():
    """Temporary DB with the three legacy lexicon tables (no L1.3 columns)."""
    mgr = _make_db(LEGACY_DDL)
    yield mgr
    path = mgr.engine.url.database
    mgr.dispose()
    Path(path).unlink(missing_ok=True)


@pytest.fixture()
def legacy_db_no_zolai_vocabulary():
    """Temporary DB with only dictionary + vocabulary (no zolai_vocabulary)."""
    mgr = _make_db(LEGACY_DDL[:2])
    yield mgr
    path = mgr.engine.url.database
    mgr.dispose()
    Path(path).unlink(missing_ok=True)


@pytest.fixture()
def seeded_legacy_db(legacy_db):
    """Legacy DB populated with seed rows (empty / NULL pos included)."""
    with legacy_db.engine.begin() as conn:
        for table, row_id, pos in SEED_ROWS:
            if table == "dictionary":
                conn.execute(
                    text("INSERT INTO dictionary (id, zolai, english, pos) VALUES (:i, :z, :e, :p)"),
                    {"i": row_id, "z": f"word{row_id}", "e": f"eng{row_id}", "p": pos},
                )
            elif table == "vocabulary":
                conn.execute(
                    text("INSERT INTO vocabulary (id, headword, english, pos) VALUES (:i, :z, :e, :p)"),
                    {"i": row_id, "z": f"word{row_id}", "e": f"eng{row_id}", "p": pos},
                )
            else:
                conn.execute(
                    text("INSERT INTO zolai_vocabulary (id, zolai, english, pos) VALUES (:i, :z, :e, :p)"),
                    {"i": row_id, "z": f"word{row_id}", "e": f"eng{row_id}", "p": pos},
                )
    return legacy_db


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


def _columns(mgr: DatabaseManager, table: str) -> set[str]:
    return {c["name"] for c in inspect(mgr.engine).get_columns(table)}


def _row_count(mgr: DatabaseManager, table: str) -> int:
    with mgr.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def _pos_snapshot(mgr: DatabaseManager, table: str) -> bytes:
    """Byte-level snapshot of the legacy ``pos`` column (id-ordered)."""
    with mgr.engine.connect() as conn:
        rows = conn.execute(text(f"SELECT id, pos FROM {table} ORDER BY id")).all()
    payload = b""
    for row in rows:
        value = "<NULL>" if row.pos is None else row.pos
        payload += f"{row.id}\x00{value}\x00".encode()
    return payload


def _fetch(mgr: DatabaseManager, table: str, row_id: int) -> dict:
    with mgr.engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {table} WHERE id = :i"), {"i": row_id}
        ).mappings().first()
    return dict(row)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAddLexiconPosColumns:
    def test_column_spec_has_13_entries(self) -> None:
        assert len(LEXICON_POS_COLUMNS) == 13
        assert len(LEXICON_TABLES) == 3
        names = [name for name, _ in LEXICON_POS_COLUMNS]
        assert len(set(names)) == 13

    def test_adds_columns_to_each_table(self, legacy_db) -> None:
        result = add_lexicon_pos_columns(legacy_db)

        assert result["errors"] == [], result["errors"]
        # zolai_vocabulary.confidence pre-exists → 12 of 13 are added there.
        assert len(result["added"]) == 38

        for table in LEXICON_TABLES:
            cols = _columns(legacy_db, table)
            missing = [c for c in NEW_COLUMNS if c not in cols]
            assert missing == [], f"{table} missing {missing}"

    def test_preexisting_confidence_is_skipped(self, legacy_db) -> None:
        result = add_lexicon_pos_columns(legacy_db)
        assert "zolai_vocabulary.confidence (already exists)" in result["skipped"]

    def test_idempotent_second_run(self, legacy_db) -> None:
        first = add_lexicon_pos_columns(legacy_db)
        assert first["errors"] == []

        second = add_lexicon_pos_columns(legacy_db)
        assert second["added"] == []
        assert second["errors"] == []
        assert len(second["skipped"]) == 39  # 13 columns × 3 tables

    def test_missing_table_is_skipped(self, legacy_db_no_zolai_vocabulary) -> None:
        result = add_lexicon_pos_columns(legacy_db_no_zolai_vocabulary)
        assert result["errors"] == []
        assert "zolai_vocabulary (table missing)" in result["skipped"]
        assert len(result["added"]) == 26  # 13 × dictionary + vocabulary
        assert "zolai_vocabulary" not in inspect(
            legacy_db_no_zolai_vocabulary.engine
        ).get_table_names()


class TestBackfillPosCanonical:
    def test_pos_column_and_row_counts_unchanged(self, seeded_legacy_db) -> None:
        mgr = seeded_legacy_db
        before_pos = {t: _pos_snapshot(mgr, t) for t in LEXICON_TABLES}
        before_counts = {t: _row_count(mgr, t) for t in LEXICON_TABLES}

        add_lexicon_pos_columns(mgr)
        backfill_pos_canonical(mgr)

        after_pos = {t: _pos_snapshot(mgr, t) for t in LEXICON_TABLES}
        after_counts = {t: _row_count(mgr, t) for t in LEXICON_TABLES}
        assert after_pos == before_pos
        assert after_counts == before_counts

    def test_unambiguous_rows_get_canonical(self, seeded_legacy_db) -> None:
        mgr = seeded_legacy_db
        add_lexicon_pos_columns(mgr)
        backfill_pos_canonical(mgr)

        assert _fetch(mgr, "dictionary", 1)["pos"] == "noun"
        row = _fetch(mgr, "dictionary", 1)
        assert row["pos_canonical"] == "NOUN"
        assert row["pos_evidence"] == "inferred"
        assert row["pos_candidates"] == "[]"
        assert row["processing_version"] == LEXICON_BACKFILL_VERSION
        assert row["import_date"]

        assert _fetch(mgr, "vocabulary", 1)["pos_canonical"] == "VERB"
        assert _fetch(mgr, "zolai_vocabulary", 1)["pos_canonical"] == "NOUN"

    def test_ambiguous_rows_keep_null_canonical_with_json(
        self, seeded_legacy_db
    ) -> None:
        mgr = seeded_legacy_db
        add_lexicon_pos_columns(mgr)
        backfill_pos_canonical(mgr)

        for table, row_id in (("dictionary", 2), ("vocabulary", 2), ("zolai_vocabulary", 2)):
            row = _fetch(mgr, table, row_id)
            assert row["pos_canonical"] is None, f"{table}#{row_id}"
            candidates = json.loads(row["pos_candidates"])
            assert isinstance(candidates, list)
            assert len(candidates) >= 2
            for item in candidates:
                assert set(item) == {"upos", "confidence"}
            assert row["pos_evidence"] == "inferred"
            assert row["processing_version"] == LEXICON_BACKFILL_VERSION

    def test_unmapped_rows_are_unknown(self, seeded_legacy_db) -> None:
        mgr = seeded_legacy_db
        add_lexicon_pos_columns(mgr)
        backfill_pos_canonical(mgr)

        for table, row_id in (("dictionary", 3), ("zolai_vocabulary", 3)):
            row = _fetch(mgr, table, row_id)
            assert row["pos_canonical"] is None
            assert row["pos_candidates"] == "[]"
            assert row["pos_evidence"] == "unknown"
            assert row["processing_version"] == LEXICON_BACKFILL_VERSION

    def test_empty_pos_rows_untouched(self, seeded_legacy_db) -> None:
        mgr = seeded_legacy_db
        add_lexicon_pos_columns(mgr)
        backfill_pos_canonical(mgr)

        for table, row_id in (("dictionary", 4), ("dictionary", 5), ("vocabulary", 3)):
            row = _fetch(mgr, table, row_id)
            assert row["pos_canonical"] is None
            assert row["pos_evidence"] == "unknown"
            assert row["processing_version"] is None
            assert row["import_date"] is None

    def test_backfill_is_idempotent(self, seeded_legacy_db) -> None:
        mgr = seeded_legacy_db
        add_lexicon_pos_columns(mgr)
        first = backfill_pos_canonical(mgr)
        assert first["errors"] == []
        assert first["updated"] == 8  # 12 seeds − 4 empty/NULL rows

        snapshot = {t: _pos_snapshot(mgr, t) for t in LEXICON_TABLES}
        second = backfill_pos_canonical(mgr)
        assert second["errors"] == []
        assert second["updated"] == 0
        assert {t: _pos_snapshot(mgr, t) for t in LEXICON_TABLES} == snapshot

    def test_backfill_requires_columns(self, legacy_db) -> None:
        result = backfill_pos_canonical(legacy_db)
        assert result["errors"] == []
        assert result["updated"] == 0
        assert any("no pos_canonical column" in s for s in result["skipped"])


class TestLexiconPosIndexes:
    def test_creates_three_partial_indexes(self, legacy_db) -> None:
        add_lexicon_pos_columns(legacy_db)
        result = create_lexicon_pos_indexes(legacy_db)

        assert result["errors"] == []
        assert len(result["created"]) == 3

        inspector = inspect(legacy_db.engine)
        for index_name, table_name, _ in LEXICON_POS_INDEXES:
            indexes = {i["name"] for i in inspector.get_indexes(table_name)}
            assert index_name in indexes

    def test_index_is_idempotent(self, legacy_db) -> None:
        add_lexicon_pos_columns(legacy_db)
        create_lexicon_pos_indexes(legacy_db)

        second = create_lexicon_pos_indexes(legacy_db)
        assert second["created"] == []
        assert second["errors"] == []
        assert len(second["skipped"]) == 3

    def test_missing_table_skipped(self, legacy_db_no_zolai_vocabulary) -> None:
        add_lexicon_pos_columns(legacy_db_no_zolai_vocabulary)
        result = create_lexicon_pos_indexes(legacy_db_no_zolai_vocabulary)
        assert result["errors"] == []
        assert "zolai_vocabulary (table missing)" in result["skipped"]
        assert len(result["created"]) == 2


class TestRunAllMigrations:
    def test_full_pipeline_on_legacy_db(self, legacy_db) -> None:
        result = run_all_migrations(legacy_db)

        for key in ("lexicon_pos_columns", "lexicon_pos_backfill", "lexicon_pos_indexes"):
            assert result[key]["errors"] == [], f"{key}: {result[key]['errors']}"
        for table in LEXICON_TABLES:
            missing = [c for c in NEW_COLUMNS if c not in _columns(legacy_db, table)]
            assert missing == [], f"{table} missing {missing}"

        # Second pass through run_all_migrations must not add or error.
        again = run_all_migrations(legacy_db)
        assert again["lexicon_pos_columns"]["added"] == []
        assert again["lexicon_pos_columns"]["errors"] == []
        assert again["lexicon_pos_backfill"]["errors"] == []
        assert again["lexicon_pos_indexes"]["created"] == []
        assert again["lexicon_pos_indexes"]["errors"] == []

    def test_fresh_db_without_zolai_vocabulary_has_no_errors(self, tmp_db) -> None:
        with tmp_db.engine.begin() as conn:
            conn.execute(text("DROP TABLE zolai_vocabulary"))

        result = run_all_migrations(tmp_db)

        for key in (
            "constraints",
            "indexes",
            "foundation_constraints",
            "foundation_indexes",
            "lexicon_pos_columns",
            "lexicon_pos_backfill",
            "lexicon_pos_indexes",
        ):
            assert result[key]["errors"] == [], f"{key}: {result[key]['errors']}"

        assert "zolai_vocabulary (table missing)" in result["lexicon_pos_columns"]["skipped"]
        assert "zolai_vocabulary (table missing)" in result["lexicon_pos_indexes"]["skipped"]
        assert "zolai_vocabulary (table missing)" in result["lexicon_pos_backfill"]["skipped"]

    def test_initialized_db_skips_all_columns(self, tmp_db) -> None:
        result = run_all_migrations(tmp_db)
        assert result["lexicon_pos_columns"]["added"] == []
        assert result["lexicon_pos_columns"]["errors"] == []
        assert len(result["lexicon_pos_columns"]["skipped"]) == 39


class TestOrmRoundTrip:
    def test_models_declare_all_new_columns(self) -> None:
        for model in (DictionaryEntry, VocabularyEntry, ZolaiVocabularyEntry):
            model_cols = {c.name for c in model.__table__.columns}
            missing = [c for c in NEW_COLUMNS if c not in model_cols]
            assert missing == [], f"{model.__name__} missing {missing}"

    def test_dictionary_entry_round_trip(self, tmp_db) -> None:
        with tmp_db.session() as session:
            session.add(
                DictionaryEntry(
                    zolai="pasian",
                    english="God",
                    pos="noun",
                    pos_canonical="NOUN",
                    pos_candidates="[]",
                    pos_evidence="expert-validated",
                    source_type="dictionary",
                    processing_version=LEXICON_BACKFILL_VERSION,
                    review_status="unknown",
                    confidence=0.9,
                )
            )
        with tmp_db.session() as session:
            row = session.query(DictionaryEntry).filter_by(zolai="pasian").one()
            assert row.pos == "noun"  # legacy value untouched
            assert row.pos_canonical == "NOUN"
            assert row.pos_evidence == "expert-validated"
            assert row.pos_candidates == "[]"
            assert row.confidence == pytest.approx(0.9)
            assert row.processing_version == LEXICON_BACKFILL_VERSION

    def test_vocabulary_entry_round_trip(self, tmp_db) -> None:
        with tmp_db.session() as session:
            session.add(
                VocabularyEntry(
                    headword="gam",
                    english="earth",
                    pos_canonical="NOUN",
                    pos_evidence="inferred",
                )
            )
        with tmp_db.session() as session:
            row = session.query(VocabularyEntry).filter_by(headword="gam").one()
            assert row.pos_canonical == "NOUN"
            assert row.pos_evidence == "inferred"
            assert row.morph_features == "{}"
            assert row.source_type == "unknown"

    def test_zolai_vocabulary_entry_round_trip(self, tmp_db) -> None:
        with tmp_db.session() as session:
            session.add(
                ZolaiVocabularyEntry(
                    zolai="tapa",
                    english="life",
                    pos="n",
                    pos_canonical="NOUN",
                    pos_evidence="corpus-observed",
                )
            )
        with tmp_db.session() as session:
            row = session.query(ZolaiVocabularyEntry).filter_by(zolai="tapa").one()
            assert row.pos == "n"
            assert row.pos_canonical == "NOUN"
            assert row.pos_evidence == "corpus-observed"
            assert row.review_status == "unknown"

    def test_model_registry_includes_zolai_vocabulary(self) -> None:
        from zolai.data.models import MODEL_REGISTRY

        assert MODEL_REGISTRY["zolai_vocabulary"] is ZolaiVocabularyEntry

    def test_base_metadata_contains_zolai_vocabulary(self, tmp_db) -> None:
        assert "zolai_vocabulary" in Base.metadata.tables
        assert "zolai_vocabulary" in inspect(tmp_db.engine).get_table_names()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
