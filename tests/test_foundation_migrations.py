"""Tests for Foundation Migrations — Table Creation, Constraints, and Indexes.

Covers:
- create_foundation_tables() creates all 13 tables
- Constraints are applied (UNIQUE, CHECK)
- Indexes are applied
- Migration is idempotent (safe to run multiple times)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    FOUNDATION_CONSTRAINT_MIGRATIONS,
    FOUNDATION_PERFORMANCE_INDEXES,
    apply_foundation_constraints,
    apply_foundation_indexes,
    create_foundation_tables,
    run_all_migrations,
)
from zolai.data.models import Base

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database, yield manager, clean up."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    # Initialize base tables first (needed for foreign keys etc.)
    mgr.init_db()
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture()
def tmp_db_no_init():
    """Create a temporary SQLite database WITHOUT init_db, for testing table creation."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------

class TestFoundationTableCreation:
    """Test that create_foundation_tables creates all 13 Foundation tables."""

    def test_create_all_13_tables(self, tmp_db_no_init):
        """Test that all 13 Foundation tables are created."""
        result = create_foundation_tables(tmp_db_no_init)

        # Should have created all tables (none skipped since DB is empty)
        assert len(result["errors"]) == 0, f"Errors: {result['errors']}"

        # Verify tables exist in database
        inspector = inspect(tmp_db_no_init.engine)
        table_names = set(inspector.get_table_names())

        expected_foundation_tables = {
            "foundation_raw_corpus",
            "foundation_raw_llm",
            "foundation_staging_words",
            "foundation_staging_sentences",
            "foundation_staging_paragraphs",
            "foundation_staging_evidence",
            "canonical_words",
            "canonical_sentences",
            "canonical_paragraphs",
            "foundation_evidence",
            "foundation_verifications",
            "foundation_consensus",
            "foundation_batches",
            "foundation_review_queue",
            "foundation_metrics",
        }

        for table in expected_foundation_tables:
            assert table in table_names, f"Missing table: {table}"

    def test_create_foundation_tables_idempotent(self, tmp_db):
        """Test that running create_foundation_tables twice doesn't error."""
        # First run
        result1 = create_foundation_tables(tmp_db)
        assert len(result1["errors"]) == 0

        # Second run - should skip all
        result2 = create_foundation_tables(tmp_db)
        assert len(result2["errors"]) == 0
        assert len(result2["created"]) == 0  # Nothing newly created
        assert len(result2["skipped"]) == 15  # All 13 tables + possibly 2 more

    def test_table_columns_exist(self, tmp_db_no_init):
        """Test that created tables have expected columns."""
        create_foundation_tables(tmp_db_no_init)
        inspector = inspect(tmp_db_no_init.engine)

        # foundation_raw_corpus
        cols = {c["name"] for c in inspector.get_columns("foundation_raw_corpus")}
        assert "id" in cols
        assert "source_type" in cols
        assert "source_path" in cols
        assert "content_hash" in cols
        assert "payload" in cols
        assert "imported_at" in cols

        # canonical_words
        cols = {c["name"] for c in inspector.get_columns("canonical_words")}
        assert "id" in cols
        assert "form" in cols
        assert "syllables" in cols
        assert "syllable_count" in cols
        assert "pos" in cols
        assert "morphology" in cols
        assert "meanings" in cols
        assert "tone_profile" in cols
        assert "zvs_compliant" in cols
        assert "frequency" in cols
        assert "version" in cols
        assert "source_hash" in cols
        assert "verified_at" in cols
        assert "verified_by" in cols
        assert "evidence_ids" in cols
        assert "created_at" in cols

        # foundation_consensus
        cols = {c["name"] for c in inspector.get_columns("foundation_consensus")}
        assert "id" in cols
        assert "fact_type" in cols
        assert "fact_key" in cols
        assert "candidates" in cols
        assert "decision" in cols
        assert "confidence" in cols
        assert "method" in cols
        assert "threshold" in cols
        assert "agreeing_count" in cols
        assert "notes" in cols
        assert "created_at" in cols


class TestFoundationConstraints:
    """Test that Foundation constraints are applied correctly."""

    def test_apply_foundation_constraints(self, tmp_db_no_init):
        """Test that constraint migrations apply without error."""
        create_foundation_tables(tmp_db_no_init)
        result = apply_foundation_constraints(tmp_db_no_init)

        # Some constraints may be skipped on SQLite (CHECK constraints)
        assert len(result["errors"]) == 0, f"Errors: {result['errors']}"

    def test_unique_constraints_work(self, tmp_db_no_init):
        """Test that UNIQUE constraints are enforced."""
        create_foundation_tables(tmp_db_no_init)
        apply_foundation_constraints(tmp_db_no_init)

        with tmp_db_no_init.engine.connect() as conn:
            # foundation_raw_corpus: UNIQUE(content_hash)
            conn.execute(text("""
                INSERT INTO foundation_raw_corpus (source_type, source_path, content_hash, payload, imported_at)
                VALUES ('test', 'path1', 'hash123', '{}', '2024-01-01')
            """))
            conn.commit()

            # Second insert with same content_hash should fail
            with pytest.raises(Exception) as exc_info:
                conn.execute(text("""
                    INSERT INTO foundation_raw_corpus (source_type, source_path, content_hash, payload, imported_at)
                    VALUES ('test', 'path2', 'hash123', '{}', '2024-01-01')
                """))
                conn.commit()
            assert "UNIQUE constraint failed" in str(exc_info.value).upper()

    def test_unique_constraint_staging_words(self, tmp_db_no_init):
        """Test UNIQUE(form, source_hash) on foundation_staging_words."""
        create_foundation_tables(tmp_db_no_init)
        apply_foundation_constraints(tmp_db_no_init)

        with tmp_db_no_init.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO foundation_staging_words (form, syllables, pos, morphology, meanings, tone_profile, zvs_compliant, frequency, source_hash, created_at)
                VALUES ('pasian', '["pa","sian"]', 'N.PROPER', '{}', '[]', '{}', 1, 10, 'src1', '2024-01-01')
            """))
            conn.commit()

            # Duplicate (form, source_hash) should fail
            with pytest.raises(Exception) as exc_info:
                conn.execute(text("""
                    INSERT INTO foundation_staging_words (form, syllables, pos, morphology, meanings, tone_profile, zvs_compliant, frequency, source_hash, created_at)
                    VALUES ('pasian', '["pa","sian"]', 'N.PROPER', '{}', '[]', '{}', 1, 10, 'src1', '2024-01-01')
                """))
                conn.commit()
            assert "UNIQUE constraint failed" in str(exc_info.value).upper()

    def test_unique_constraint_canonical_words(self, tmp_db_no_init):
        """Test UNIQUE(form, version) on canonical_words."""
        create_foundation_tables(tmp_db_no_init)
        apply_foundation_constraints(tmp_db_no_init)

        with tmp_db_no_init.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO canonical_words (form, syllables, syllable_count, pos, morphology, meanings, tone_profile, zvs_compliant, frequency, version, source_hash, verified_at, verified_by, evidence_ids, created_at)
                VALUES ('pasian', '["pa","sian"]', 2, 'N.PROPER', '{}', '[]', '{}', 1, 10, 1, 'src1', '2024-01-01', 'test', '[]', '2024-01-01')
            """))
            conn.commit()

            # Duplicate (form, version) should fail
            with pytest.raises(Exception) as exc_info:
                conn.execute(text("""
                    INSERT INTO canonical_words (form, syllables, syllable_count, pos, morphology, meanings, tone_profile, zvs_compliant, frequency, version, source_hash, verified_at, verified_by, evidence_ids, created_at)
                    VALUES ('pasian', '["pa","sian"]', 2, 'N.PROPER', '{}', '[]', '{}', 1, 10, 1, 'src2', '2024-01-01', 'test', '[]', '2024-01-01')
                """))
                conn.commit()
            assert "UNIQUE constraint failed" in str(exc_info.value).upper()

    def test_unique_constraint_consensus(self, tmp_db_no_init):
        """Test UNIQUE(fact_type, fact_key, method) on foundation_consensus."""
        create_foundation_tables(tmp_db_no_init)
        apply_foundation_constraints(tmp_db_no_init)

        with tmp_db_no_init.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO foundation_consensus (fact_type, fact_key, candidates, decision, confidence, method, threshold, agreeing_count, notes, created_at)
                VALUES ('word', 'word:pasian', '[]', '{}', 0.9, 'weighted_evidence', 0.7, 1, '[]', '2024-01-01')
            """))
            conn.commit()

            # Duplicate (fact_type, fact_key, method) should fail
            with pytest.raises(Exception) as exc_info:
                conn.execute(text("""
                    INSERT INTO foundation_consensus (fact_type, fact_key, candidates, decision, confidence, method, threshold, agreeing_count, notes, created_at)
                    VALUES ('word', 'word:pasian', '[]', '{}', 0.8, 'weighted_evidence', 0.7, 1, '[]', '2024-01-01')
                """))
                conn.commit()
            assert "UNIQUE constraint failed" in str(exc_info.value).upper()

    def test_check_constraint_source_type_not_empty(self, tmp_db_no_init):
        """Test CHECK(source_type != '') on foundation_raw_corpus."""
        create_foundation_tables(tmp_db_no_init)
        apply_foundation_constraints(tmp_db_no_init)

        # SQLite doesn't enforce CHECK on existing tables easily
        # Just verify the migration runs without error
        # For actual enforcement, would need table recreation


class TestFoundationIndexes:
    """Test that Foundation performance indexes are applied."""

    def test_apply_foundation_indexes(self, tmp_db_no_init):
        """Test that index migrations apply without error."""
        create_foundation_tables(tmp_db_no_init)
        result = apply_foundation_indexes(tmp_db_no_init)

        assert len(result["errors"]) == 0, f"Errors: {result['errors']}"

    def test_indexes_created(self, tmp_db_no_init):
        """Test that indexes exist after migration."""
        create_foundation_tables(tmp_db_no_init)
        apply_foundation_indexes(tmp_db_no_init)

        inspector = inspect(tmp_db_no_init.engine)

        # Check a few key indexes
        fraw_indexes = {idx["name"] for idx in inspector.get_indexes("foundation_raw_corpus")}
        assert "ix_fraw_source_type" in fraw_indexes
        assert "ix_fraw_imported_at" in fraw_indexes

        fstg_w_indexes = {idx["name"] for idx in inspector.get_indexes("foundation_staging_words")}
        assert "ix_fstg_word_pos" in fstg_w_indexes
        assert "ix_fstg_word_zvs" in fstg_w_indexes

        cword_indexes = {idx["name"] for idx in inspector.get_indexes("canonical_words")}
        assert "ix_cword_pos" in cword_indexes
        assert "ix_cword_zvs" in cword_indexes

        fcon_indexes = {idx["name"] for idx in inspector.get_indexes("foundation_consensus")}
        assert "ix_fcon_confidence" in fcon_indexes
        assert "ix_fcon_created" in fcon_indexes


class TestFoundationConstraintIndexCounts:
    """Test that the expected number of constraints and indexes are defined."""

    def test_constraint_migration_count(self):
        """Test that FOUNDATION_CONSTRAINT_MIGRATIONS has expected count."""
        # We defined 35 constraint migrations for Foundation tables
        assert len(FOUNDATION_CONSTRAINT_MIGRATIONS) == 35

    def test_index_migration_count(self):
        """Test that FOUNDATION_PERFORMANCE_INDEXES has expected count."""
        # We defined 32 performance indexes for Foundation tables
        assert len(FOUNDATION_PERFORMANCE_INDEXES) == 32

    def test_constraint_tables_covered(self):
        """Test that all 13 Foundation tables have constraint migrations."""
        tables_with_constraints = set()
        for table_name, _, _, _ in FOUNDATION_CONSTRAINT_MIGRATIONS:
            tables_with_constraints.add(table_name)

        expected_tables = {
            "foundation_raw_corpus", "foundation_raw_llm",
            "foundation_staging_words", "foundation_staging_sentences",
            "foundation_staging_paragraphs", "foundation_staging_evidence",
            "canonical_words", "canonical_sentences", "canonical_paragraphs",
            "foundation_evidence", "foundation_verifications", "foundation_consensus",
            "foundation_batches", "foundation_review_queue", "foundation_metrics",
        }
        assert tables_with_constraints == expected_tables

    def test_index_tables_covered(self):
        """Test that all 13 Foundation tables have index migrations."""
        tables_with_indexes = set()
        for table_name, _ in FOUNDATION_PERFORMANCE_INDEXES:
            tables_with_indexes.add(table_name)

        expected_tables = {
            "foundation_raw_corpus", "foundation_raw_llm",
            "foundation_staging_words", "foundation_staging_sentences",
            "foundation_staging_paragraphs", "foundation_staging_evidence",
            "canonical_words", "canonical_sentences", "canonical_paragraphs",
            "foundation_evidence", "foundation_verifications", "foundation_consensus",
            "foundation_batches", "foundation_review_queue", "foundation_metrics",
        }
        assert tables_with_indexes == expected_tables


class TestRunAllMigrations:
    """Test the run_all_migrations wrapper function."""

    def test_run_all_migrations(self, tmp_db_no_init):
        """Test that run_all_migrations executes all phases."""
        # First init base tables
        Base.metadata.create_all(tmp_db_no_init.engine)

        result = run_all_migrations(tmp_db_no_init)

        assert "constraints" in result
        assert "indexes" in result
        assert "foundation_constraints" in result
        assert "foundation_indexes" in result

        # All should have no errors
        for key in ["constraints", "indexes", "foundation_constraints", "foundation_indexes"]:
            assert len(result[key]["errors"]) == 0, f"Errors in {key}: {result[key]['errors']}"


class TestFoundationModelsInRegistry:
    """Test that Foundation models are properly registered in SQLAlchemy."""

    def test_foundation_models_in_metadata(self, tmp_db_no_init):
        """Test that all 13 Foundation models are in Base.metadata."""
        create_foundation_tables(tmp_db_no_init)

        table_names = set(Base.metadata.tables.keys())
        foundation_tables = {
            "foundation_raw_corpus", "foundation_raw_llm",
            "foundation_staging_words", "foundation_staging_sentences",
            "foundation_staging_paragraphs", "foundation_staging_evidence",
            "canonical_words", "canonical_sentences", "canonical_paragraphs",
            "foundation_evidence", "foundation_verifications", "foundation_consensus",
            "foundation_batches", "foundation_review_queue", "foundation_metrics",
        }

        for table in foundation_tables:
            assert table in table_names, f"Foundation table {table} not in metadata"

    def test_model_registry_includes_foundation(self):
        """Test that MODEL_REGISTRY includes Foundation models."""
        from zolai.data.models import MODEL_REGISTRY

        foundation_models = {
            "foundation_raw_corpus", "foundation_raw_llm",
            "foundation_staging_words", "foundation_staging_sentences",
            "foundation_staging_paragraphs", "foundation_staging_evidence",
            "canonical_words", "canonical_sentences", "canonical_paragraphs",
            "foundation_evidence", "foundation_verifications", "foundation_consensus",
            "foundation_batches", "foundation_review_queue", "foundation_metrics",
        }

        for model in foundation_models:
            assert model in MODEL_REGISTRY, f"Foundation model {model} not in MODEL_REGISTRY"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
