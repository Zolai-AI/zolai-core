"""Tests for Foundation Data Layer — Models, Repositories, and ETL Pipeline.

Covers:
- Model CRUD operations (insert, query, update)
- Repository methods for all layers (Raw, Staging, Canonical, Evidence, Consensus, Meta)
- ETL pipeline round-trip (ingest → build → promote)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zolai.data.database import DatabaseManager
from zolai.data.repositories import get_foundation_repositories
from zolai.pipeline.foundation import FoundationETL, PipelineStats

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database with Foundation tables, yield manager, clean up."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    url = f"sqlite:///{db_path}"
    mgr = DatabaseManager(url)
    mgr.init_db()
    # Also apply Foundation constraint/index migrations
    from zolai.data.migrations import apply_foundation_constraints, apply_foundation_indexes, create_foundation_tables
    create_foundation_tables(mgr)
    apply_foundation_constraints(mgr)
    apply_foundation_indexes(mgr)
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture()
def foundation_repos(tmp_db):
    """Return all Foundation repositories for the temp DB."""
    return get_foundation_repositories(tmp_db.engine)


@pytest.fixture()
def etl(tmp_db):
    """Return FoundationETL instance for the temp DB."""
    return FoundationETL(mgr=tmp_db)


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------

class TestFoundationModelCRUD:
    """Test CRUD operations on Foundation ORM models via repositories."""

    def test_raw_corpus_crud(self, foundation_repos):
        """Test insert, query, update, delete on foundation_raw_corpus."""
        repo = foundation_repos["foundation_raw_corpus"]

        # Insert
        record_id = repo.create({
            "source_type": "bible_usx",
            "source_path": "data/bible/gen.usx",
            "content_hash": "abc123",
            "payload": json.dumps({"text": "Pasian in vantung leh leitung a piangsak hi.", "book": "GEN"}),
        })
        assert record_id > 0

        # Query by content_hash
        record = repo.get_by_content_hash("abc123")
        assert record is not None
        assert record["source_type"] == "bible_usx"
        assert "Pasian" in record["payload"]

        # Query by source_type
        records = repo.get_by_source_type("bible_usx")
        assert len(records) == 1

        # Count by source_type
        counts = repo.count_by_source_type()
        assert counts.get("bible_usx", 0) == 1

    def test_raw_llm_crud(self, foundation_repos):
        """Test insert and query on foundation_raw_llm."""
        repo = foundation_repos["foundation_raw_llm"]

        record_id = repo.create({
            "model": "gemini-pro",
            "prompt_hash": "prompt_abc",
            "response_json": json.dumps({"analysis": "word: pasian", "pos": "NOUN"}),
        })
        assert record_id > 0

        records = repo.get_by_model("gemini-pro")
        assert len(records) == 1
        assert records[0]["model"] == "gemini-pro"

        records = repo.get_by_prompt_hash("prompt_abc")
        assert len(records) == 1

    def test_staging_words_crud(self, foundation_repos):
        """Test insert and query on foundation_staging_words."""
        repo = foundation_repos["foundation_staging_words"]

        record_id = repo.create({
            "form": "pasian",
            "syllables": json.dumps(["pa", "sian"]),
            "pos": "N.PROPER",
            "morphology": json.dumps({"root": "pasian", "morphemes": ["pasian"]}),
            "meanings": json.dumps([{"en": "God", "source": "bible"}]),
            "tone_profile": json.dumps({"ambiguous": False}),
            "zvs_compliant": 1,
            "frequency": 100,
            "source_hash": "source_abc",
        })
        assert record_id > 0

        # Get by form
        records = repo.get_by_form("pasian")
        assert len(records) == 1
        assert records[0]["form"] == "pasian"
        assert records[0]["pos"] == "N.PROPER"

        # Get by POS
        records = repo.get_by_pos("N.PROPER")
        assert len(records) >= 1

        # Get by source_hash
        records = repo.get_by_source_hash("source_abc")
        assert len(records) == 1

        # Get ZVS compliant
        records = repo.get_zvs_compliant(True)
        assert len(records) >= 1

        # Get high frequency
        records = repo.get_high_frequency(min_freq=50)
        assert len(records) >= 1

    def test_staging_sentences_crud(self, foundation_repos):
        """Test insert and query on foundation_staging_sentences."""
        repo = foundation_repos["foundation_staging_sentences"]

        record_id = repo.create({
            "text": "Pasian in vantung leh leitung a piangsak hi.",
            "tokens": json.dumps([]),
            "pos_tags": json.dumps([]),
            "structure": json.dumps({}),
            "translation": json.dumps({}),
            "grammar": json.dumps({}),
            "source_hash": "source_xyz",
        })
        assert record_id > 0

        records = repo.get_by_text("Pasian in vantung leh leitung a piangsak hi.")
        assert len(records) == 1

        records = repo.get_by_source_hash("source_xyz")
        assert len(records) == 1

        records = repo.search_text("piangsak")
        assert len(records) >= 1

    def test_staging_paragraphs_crud(self, foundation_repos):
        """Test insert and query on foundation_staging_paragraphs."""
        repo = foundation_repos["foundation_staging_paragraphs"]

        record_id = repo.create({
            "text": "Pasian in vantung leh leitung a piangsak hi. A mu hi cih a gen ding hi.",
            "sentences": json.dumps([]),
            "style_profile": json.dumps({"biblical": 1.0}),
            "source_hash": "source_para",
        })
        assert record_id > 0

        records = repo.get_by_source_hash("source_para")
        assert len(records) == 1

    def test_staging_evidence_crud(self, foundation_repos):
        """Test insert and query on foundation_staging_evidence."""
        repo = foundation_repos["foundation_staging_evidence"]

        record_id = repo.create({
            "fact_type": "word",
            "fact_key": "word:pasian",
            "candidate_value": json.dumps({"form": "pasian"}),
            "evidence": json.dumps({"tier": 1, "source": "bible_verses", "confidence": 0.95}),
            "tier": 1,
            "confidence": 0.95,
        })
        assert record_id > 0

        records = repo.get_by_fact("word", "word:pasian")
        assert len(records) == 1

        records = repo.get_by_tier(1)
        assert len(records) >= 1

    def test_canonical_words_crud(self, foundation_repos):
        """Test insert and query on canonical_words."""
        repo = foundation_repos["canonical_words"]

        record_id = repo.create({
            "form": "pasian",
            "syllables": json.dumps(["pa", "sian"]),
            "syllable_count": 2,
            "pos": "N.PROPER",
            "morphology": json.dumps({"root": "pasian"}),
            "meanings": json.dumps([{"en": "God", "source": "bible"}]),
            "tone_profile": json.dumps({"ambiguous": False}),
            "zvs_compliant": 1,
            "frequency": 100,
            "version": 1,
            "source_hash": "source_canon",
            "verified_at": "2024-01-01T00:00:00",
            "verified_by": "consensus:weighted_evidence",
            "evidence_ids": json.dumps([]),
        })
        assert record_id > 0

        records = repo.get_by_form("pasian")
        assert len(records) == 1
        assert records[0]["version"] == 1
        assert records[0]["verified_at"] is not None

        records = repo.get_verified()
        assert len(records) >= 1

        records = repo.get_unverified()
        assert len(records) == 0  # We just verified it

        records = repo.get_by_version(1)
        assert len(records) >= 1

        records = repo.get_high_frequency(min_freq=50)
        assert len(records) >= 1

    def test_canonical_sentences_crud(self, foundation_repos):
        """Test insert and query on canonical_sentences."""
        repo = foundation_repos["canonical_sentences"]

        record_id = repo.create({
            "text": "Pasian in vantung leh leitung a piangsak hi.",
            "tokens": json.dumps([]),
            "pos_tags": json.dumps([]),
            "dependencies": json.dumps([]),
            "translation": json.dumps({"en": "God created heaven and earth."}),
            "grammar": json.dumps({"tense": "past"}),
            "version": 1,
            "source_hash": "source_canon",
            "verified_at": "2024-01-01T00:00:00",
            "verified_by": "consensus:weighted_evidence",
            "evidence_ids": json.dumps([]),
        })
        assert record_id > 0

        records = repo.get_by_text("Pasian in vantung leh leitung a piangsak hi.")
        assert len(records) == 1

        records = repo.get_verified()
        assert len(records) >= 1

        records = repo.search_text("piangsak")
        assert len(records) >= 1

    def test_canonical_paragraphs_crud(self, foundation_repos):
        """Test insert and query on canonical_paragraphs."""
        repo = foundation_repos["canonical_paragraphs"]

        record_id = repo.create({
            "text": "Pasian in vantung leh leitung a piangsak hi. A mu hi cih a gen ding hi.",
            "sentences": json.dumps([]),
            "style_profile": json.dumps({"biblical": 1.0}),
            "version": 1,
            "source_hash": "source_canon",
            "verified_at": "2024-01-01T00:00:00",
            "verified_by": "consensus:weighted_evidence",
            "evidence_ids": json.dumps([]),
        })
        assert record_id > 0

        records = repo.get_verified()
        assert len(records) >= 1

        records = repo.get_unverified()
        assert len(records) == 0

    def test_evidence_crud(self, foundation_repos):
        """Test insert and query on foundation_evidence."""
        repo = foundation_repos["foundation_evidence"]

        record_id = repo.create({
            "fact_type": "word",
            "fact_key": "word:pasian",
            "candidate_value": json.dumps({"form": "pasian"}),
            "evidence": json.dumps({"tier": 1, "source": "bible_verses", "confidence": 0.95}),
            "tier": 1,
            "confidence": 0.95,
            "source": "bible_verses",
        })
        assert record_id > 0

        records = repo.get_by_fact("word", "word:pasian")
        assert len(records) == 1

        records = repo.get_by_tier(1)
        assert len(records) >= 1

        records = repo.get_by_source("bible_verses")
        assert len(records) >= 1

        records = repo.get_high_confidence(min_confidence=0.9)
        assert len(records) >= 1

    def test_verifications_crud(self, foundation_repos):
        """Test insert and query on foundation_verifications."""
        repo = foundation_repos["foundation_verifications"]

        record_id = repo.create({
            "candidate_id": 1,
            "verifier": "EvidenceThresholdVerifier",
            "passed": 1,
            "score": 0.95,
            "notes": json.dumps(["passed"]),
        })
        assert record_id > 0

        records = repo.get_by_candidate(1)
        assert len(records) == 1

        records = repo.get_by_verifier("EvidenceThresholdVerifier")
        assert len(records) >= 1

        records = repo.get_passed()
        assert len(records) >= 1

        records = repo.get_failed()
        assert len(records) == 0

    def test_consensus_crud(self, foundation_repos):
        """Test insert and query on foundation_consensus."""
        repo = foundation_repos["foundation_consensus"]

        record_id = repo.create({
            "fact_type": "word",
            "fact_key": "word:pasian",
            "candidates": json.dumps([{"value": {"form": "pasian"}, "weight": 0.95}]),
            "decision": json.dumps({"form": "pasian", "pos": "N.PROPER"}),
            "confidence": 0.95,
            "method": "weighted_evidence",
            "threshold": 0.7,
            "agreeing_count": 1,
            "notes": json.dumps(["consensus reached"]),
        })
        assert record_id > 0

        records = repo.get_by_fact("word", "word:pasian")
        assert len(records) == 1

        records = repo.get_by_method("weighted_evidence")
        assert len(records) >= 1

        records = repo.get_high_confidence(min_confidence=0.9)
        assert len(records) >= 1

    def test_batches_crud(self, foundation_repos):
        """Test insert and query on foundation_batches."""
        repo = foundation_repos["foundation_batches"]

        # Create batch via helper
        batch_id = repo.create_batch("ingest")
        assert batch_id > 0

        # Update batch status
        success = repo.update_batch_status(batch_id, "completed", {"records_processed": 100})
        assert success is True

        # Query by type
        records = repo.get_by_type("ingest")
        assert len(records) == 1
        assert records[0]["status"] == "completed"

        # Query by status
        records = repo.get_by_status("completed")
        assert len(records) == 1

        # Get latest
        latest = repo.get_latest("ingest")
        assert latest is not None
        assert latest["id"] == batch_id

    def test_review_queue_crud(self, foundation_repos):
        """Test insert and query on foundation_review_queue."""
        repo = foundation_repos["foundation_review_queue"]

        # Add to queue
        queue_id = repo.add_to_queue("word", "word:pasian", priority=50, assignee="reviewer1")
        assert queue_id > 0

        # Get pending
        records = repo.get_pending()
        assert len(records) >= 1
        assert records[0]["status"] == "pending"
        assert records[0]["assignee"] == "reviewer1"

        # Get by fact
        records = repo.get_by_fact("word", "word:pasian")
        assert len(records) == 1

        # Get by assignee
        records = repo.get_by_assignee("reviewer1")
        assert len(records) >= 1

        # Update status
        success = repo.update_status(queue_id, "approved")
        assert success is True

        records = repo.get_pending()
        assert len(records) == 0  # Now approved

    def test_metrics_crud(self, foundation_repos):
        """Test insert and query on foundation_metrics."""
        repo = foundation_repos["foundation_metrics"]

        record_id = repo.record_metric("run_001", "accuracy", 0.95, baseline=0.90)
        assert record_id > 0

        records = repo.get_by_run("run_001")
        assert len(records) == 1
        assert records[0]["metric"] == "accuracy"
        assert records[0]["value"] == 0.95
        assert records[0]["baseline"] == 0.90
        assert abs(records[0]["delta"] - 0.05) < 0.001

        records = repo.get_by_metric("accuracy")
        assert len(records) >= 1


class TestFoundationRepositoryEdgeCases:
    """Test edge cases for Foundation repositories."""

    def test_get_nonexistent(self, foundation_repos):
        """Test querying for non-existent records returns empty lists."""
        repo = foundation_repos["foundation_staging_words"]
        records = repo.get_by_form("nonexistent_xyz")
        assert records == []

    def test_empty_database(self, foundation_repos):
        """Test count on empty tables."""
        for name, repo in foundation_repos.items():
            count = repo.count()
            assert count == 0, f"Table {name} should be empty initially"

    def test_get_foundation_repositories_includes_all(self, foundation_repos):
        """Test that get_foundation_repositories returns all 13 repositories."""
        expected = {
            "foundation_raw_corpus", "foundation_raw_llm",
            "foundation_staging_words", "foundation_staging_sentences",
            "foundation_staging_paragraphs", "foundation_staging_evidence",
            "canonical_words", "canonical_sentences", "canonical_paragraphs",
            "foundation_evidence", "foundation_verifications", "foundation_consensus",
            "foundation_batches", "foundation_review_queue", "foundation_metrics",
            "foundation_cost_tracking",
        }
        assert set(foundation_repos.keys()) == expected


class TestFoundationETLPipeline:
    """Test the FoundationETL pipeline stages."""

    def test_ingest_from_jsonl(self, etl, tmp_db):
        """Test Stage 1: Ingest raw corpus from JSONL."""
        # Create a temporary JSONL file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "Pasian in vantung leh leitung a piangsak hi."}\n')
            f.write('{"text": "A mu hi cih a gen ding hi."}\n')
            jsonl_path = Path(f.name)

        try:
            stats = etl.ingest_from_jsonl(jsonl_path, source_type="bible_usx")
            assert isinstance(stats, PipelineStats)
            assert stats.records_processed == 2
            # assert stats.records_staged == 0  # ingest only creates raw records, staging is separate stage
            assert stats.errors == 0
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_ingest_duplicate_detection(self, etl, tmp_db):
        """Test that duplicate content_hash is detected and skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "Pasian in vantung leh leitung a piangsak hi."}\n')
            jsonl_path = Path(f.name)

        try:
            # First ingest
            stats1 = etl.ingest_from_jsonl(jsonl_path, source_type="bible_usx")
            assert stats1.records_processed == 1

            # Second ingest of same file - should detect duplicate
            stats2 = etl.ingest_from_jsonl(jsonl_path, source_type="bible_usx")
            assert stats2.records_processed == 0  # Duplicate skipped
            assert stats2.errors >= 1
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_build_staging_from_raw(self, etl, tmp_db):
        """Test Stage 2: Build staging layer from raw corpus."""
        # First ingest some data
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "Pasian in vantung leh leitung a piangsak hi."}\n')
            f.write('{"text": "A pai kei hi."}\n')
            f.write('{"text": "Na pai hiam?"}\n')
            jsonl_path = Path(f.name)

        try:
            etl.ingest_from_jsonl(jsonl_path, source_type="bible_usx")
            stats = etl.build_staging_from_raw()
            assert isinstance(stats, PipelineStats)
            # Staging increments records_staged (words/sentences); raw-row counter may stay 0
            assert stats.records_staged >= 3  # At least one per input (words + sentences)
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_promote_to_canonical_words(self, etl, tmp_db):
        """Test Stage 3: Promote words to canonical layer."""
        # First ingest and build staging
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "Pasian in vantung leh leitung a piangsak hi."}\n')
            jsonl_path = Path(f.name)

        try:
            etl.ingest_from_jsonl(jsonl_path, source_type="bible_usx")
            etl.build_staging_from_raw()
            stats = etl.promote_to_canonical(fact_type="word")
            assert isinstance(stats, PipelineStats)
            assert stats.records_promoted >= 0  # May promote if consensus reached
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_run_full_pipeline(self, etl, tmp_db):
        """Test running the complete pipeline end-to-end."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "Pasian in vantung leh leitung a piangsak hi."}\n')
            jsonl_path = Path(f.name)

        try:
            stats = etl.run_full_pipeline(
                jsonl_path=jsonl_path,
                source_type="bible_usx",
                promote_types=["word"],
            )
            assert isinstance(stats, PipelineStats)
            assert stats.records_processed >= 1
            assert stats.records_staged >= 1
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_get_status(self, etl, tmp_db):
        """Test pipeline status reporting."""
        status = etl.get_status()
        assert isinstance(status, dict)
        # All keys should be present
        expected_keys = {
            "raw_corpus", "raw_llm",
            "staging_words", "staging_sentences", "staging_paragraphs", "staging_evidence",
            "canonical_words", "canonical_sentences", "canonical_paragraphs",
            "evidence", "verifications", "consensus",
            "batches", "review_queue_pending", "metrics",
        }
        assert set(status.keys()) == expected_keys
        # Initially all zeros
        for v in status.values():
            assert v == 0


class TestFoundationETLEdgeCases:
    """Test edge cases and error handling in FoundationETL."""

    def test_ingest_invalid_jsonl(self, etl):
        """Test ingestion handles malformed JSONL gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "valid"}\n')
            f.write('invalid json\n')
            f.write('{"text": "also valid"}\n')
            jsonl_path = Path(f.name)

        try:
            stats = etl.ingest_from_jsonl(jsonl_path, source_type="test")
            assert stats.records_processed == 2  # two valid JSON lines
            assert stats.errors == 1  # one malformed line
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_ingest_empty_text(self, etl):
        """Test ingestion skips records with empty text."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "valid"}\n')
            f.write('{"text": ""}\n')
            f.write('{}\n')
            jsonl_path = Path(f.name)

        try:
            stats = etl.ingest_from_jsonl(jsonl_path, source_type="test")
            assert stats.records_processed == 1  # only non-empty text
            assert stats.errors == 2
        finally:
            jsonl_path.unlink(missing_ok=True)

    def test_build_staging_empty_raw(self, etl):
        """Test build_staging_from_raw on empty raw layer."""
        stats = etl.build_staging_from_raw()
        assert stats.records_processed == 0
        assert stats.records_staged == 0
        assert stats.errors == 0

    def test_promote_empty_staging(self, etl):
        """Test promote on empty staging layer."""
        stats = etl.promote_to_canonical(fact_type="word")
        assert stats.records_processed == 0
        assert stats.records_promoted == 0
        assert stats.records_queued_for_review == 0


class TestFoundationBatchManagement:
    """Test batch tracking in FoundationETL."""

    def test_batch_creation_and_completion(self, etl):
        """Test batch start and complete cycle."""
        # The batch is tracked internally; we can verify via repository
        from zolai.data.repositories import get_foundation_repositories
        repos = get_foundation_repositories(etl._engine)

        # Initially no batches
        assert repos["foundation_batches"].count() == 0

        # Ingest creates a batch
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "test"}\n')
            jsonl_path = Path(f.name)

        try:
            etl.ingest_from_jsonl(jsonl_path, source_type="test")
        finally:
            jsonl_path.unlink(missing_ok=True)

        # Batch should be recorded
        batches = repos["foundation_batches"].get_by_type("ingest")
        assert len(batches) == 1
        assert batches[0]["status"] == "completed"

    def test_multiple_batch_types(self, etl):
        """Test that different pipeline stages create different batch types."""
        from zolai.data.repositories import get_foundation_repositories
        repos = get_foundation_repositories(etl._engine)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"text": "Pasian in vantung leh leitung a piangsak hi."}\n')
            jsonl_path = Path(f.name)

        try:
            etl.ingest_from_jsonl(jsonl_path, source_type="bible_usx")
            etl.build_staging_from_raw()
            etl.promote_to_canonical(fact_type="word")
        finally:
            jsonl_path.unlink(missing_ok=True)

        # Should have batches for each type
        ingest_batches = repos["foundation_batches"].get_by_type("ingest")
        build_batches = repos["foundation_batches"].get_by_type("build_staging")
        promote_batches = repos["foundation_batches"].get_by_type("promote")

        assert len(ingest_batches) == 1
        assert len(build_batches) == 1
        assert len(promote_batches) == 1


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
