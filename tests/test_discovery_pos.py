"""Tests for POS Discovery.

These run against a throwaway database: discovery writes ``foundation_evidence``
and ``hypotheses`` rows, and doing that through ``get_engine()`` mutated the
canonical corpus on every suite run (the ``no_canonical_db_writes`` fence now
fails it).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.learning.discovery.pos import POSDiscovery

#: Extractors POS discovery stamps on the evidence it writes.
_POS_EXTRACTORS = {
    "pos_normalize_backfill",
    "ZolaiPOSTagger",
    "word_usage_table",
    "observation_pipeline",
    "bible_verses",
}


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    """A fresh schema seeded with a tiny lexicon and attested sentences."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name

    manager = DatabaseManager(f"sqlite:///{path}")
    manager.init_db()
    with manager.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dictionary (zolai, english, pos, pos_canonical, source) VALUES "
                "('pasian', 'God', 'n', 'NOUN', 'test'), "
                "('piangsak', 'to create', 'v', 'VERB', 'test'), "
                "('gam', 'earth, land', 'n', 'NOUN', 'test'), "
                "('leh', 'and, then', 'conj', 'CCONJ', 'test'), "
                "('in', 'we (inclusive)', 'pron', 'PRON', 'test')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77) VALUES "
                "('GEN 1:1', 'GEN', 1, 1, 'Pasian in leitung a piangsak hi.'), "
                "('JHN 1:1', 'JHN', 1, 1, 'Pasian in gam a piangsak hi.')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO word_usage "
                "(word, book, total_freq, meaning_shifts, co_occurring_words) VALUES "
                "('pasian', 'GEN', 40, '[]', '[]'), "
                "('gam', 'JHN', 25, '[]', '[]'), "
                "('piangsak', 'JHN', 18, '[]', '[]')"
            )
        )

    yield manager.engine
    manager.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def test_pos_discovery_runs(db_engine: Engine) -> None:
    """Test that POS discovery runs without error (small limit)."""
    discovery = POSDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    assert "words_processed" in result
    assert "hypotheses_written" in result
    assert "status_counts" in result
    assert result["hypotheses_written"] >= 0
    assert result["words_processed"] >= 0


def test_pos_hypotheses_written_have_evidence(db_engine: Engine) -> None:
    """Test that written POS hypotheses have evidence_ids and correct status."""
    discovery = POSDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    # Full-table scan: every persisted POS hypothesis must be well-formed.
    # This failed when 43 promotion-fixture rows (NULL confidence, PID-suffixed
    # subjects) leaked into the shared DB -- that pollution was real and also
    # showed up in the admin review UI, so the test was right to fail.
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, subject, predicate, evidence_ids, status, "
                "confidence FROM hypotheses WHERE kind = 'pos'"
            )
        ).fetchall()

    assert rows, "no POS hypotheses to check -- fixture rows missing?"

    for row in rows:
        assert row.kind == "pos"
        assert row.subject.startswith("word:")
        assert row.predicate.startswith("pos:")
        assert row.evidence_ids is not None
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0


def test_pos_confidence_is_two_dp(db_engine: Engine) -> None:
    """Test that confidence is rounded to 2 decimal places."""
    discovery = POSDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT confidence FROM hypotheses WHERE kind = 'pos'")
        ).fetchall()

    for row in rows:
        if row.confidence is not None:
            conf = float(row.confidence)
            # Check 2 decimal places
            assert round(conf, 2) == conf


def test_pos_evidence_rows_created(db_engine: Engine) -> None:
    """Test that foundation_evidence rows are created for POS hypotheses."""
    discovery = POSDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    # Check evidence table
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, fact_type, fact_key, tier, source, method, "
                "extractor FROM foundation_evidence WHERE fact_type = 'word'"
            )
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.fact_type in ("word", "grammar")
        assert row.fact_key.startswith("word:")
        assert row.tier in (1, 2, 3, 4, 5)
        assert row.source
        assert row.method
        # Each evidence row records which source produced it; the extractor is
        # that source (there is no generic ``discovery`` extractor).
        assert row.extractor in _POS_EXTRACTORS
