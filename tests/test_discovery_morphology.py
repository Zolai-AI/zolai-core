"""Tests for Morphology Discovery.

These run against a throwaway database: discovery writes ``foundation_evidence``
rows, and doing that through ``get_engine()`` mutated the canonical corpus on
every suite run (the ``no_canonical_db_writes`` fence now fails it).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.learning.discovery.morphology import MorphologyDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    """A fresh schema seeded with words that actually decompose."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name

    manager = DatabaseManager(f"sqlite:///{path}")
    manager.init_db()
    with manager.engine.begin() as conn:
        # hongpai = directional "hong" + stem; paita = stem + aspect "ta"
        conn.execute(
            text(
                "INSERT INTO vocabulary "
                "(headword, english, frequency, books, examples) VALUES "
                "('hongpai', 'went', 40, '[]', '[]'), "
                "('paita', 'went already', 30, '[]', '[]'), "
                "('pasian', 'God', 25, '[]', '[]'), "
                "('leitung', 'earth', 12, '[]', '[]')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO word_usage "
                "(word, book, total_freq, meaning_shifts, co_occurring_words) VALUES "
                "('hongpai', 'JHN', 40, '[]', '[]'), "
                "('paita', 'JHN', 30, '[]', '[]'), "
                "('pasian', 'JHN', 25, '[]', '[]')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77) VALUES "
                "('JHN 1:1', 'JHN', 1, 1, 'Pasian in leitung a bawl hi.'), "
                "('JHN 1:2', 'JHN', 1, 2, 'Topa in hongpai hi.')"
            )
        )

    yield manager.engine
    manager.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def test_morphology_discovery_runs(db_engine: Engine) -> None:
    """Test that morphology discovery runs without error (small limit)."""
    discovery = MorphologyDiscovery(db_engine, limit=10)
    with db_engine.connect() as conn:
        result = discovery.run(conn)

    # The builder has always returned "words_processed" (as pos.py does);
    # "words_analyzed" was written aspirationally and never matched.
    assert "words_processed" in result
    assert "relations_written" in result
    assert "status_counts" in result
    assert result["relations_written"] >= 0
    assert result["words_processed"] >= 0


def test_morphology_hypotheses_written_have_evidence(db_engine: Engine) -> None:
    """Test that written morphology relations have evidence_ids and correct status."""
    discovery = MorphologyDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, subject, predicate, object, evidence_ids, "
                "status, confidence FROM hypotheses WHERE kind = 'morph_relation'"
            )
        ).fetchall()

    for row in rows:
        assert row.kind == "morph_relation"
        assert row.subject.startswith("word:")
        assert row.predicate.startswith("morph:")
        assert row.evidence_ids is not None
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0


def test_morphology_confidence_is_two_dp(db_engine: Engine) -> None:
    """Test that confidence is rounded to 2 decimal places."""
    discovery = MorphologyDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT confidence FROM hypotheses WHERE kind = 'morph_relation'")
        ).fetchall()

    for row in rows:
        if row.confidence is not None:
            conf = float(row.confidence)
            assert round(conf, 2) == conf


def test_morphology_evidence_tiers(db_engine: Engine) -> None:
    """Test that evidence uses correct tiers (DICTIONARY=2, BIBLE=1, CORPUS=4)."""
    discovery = MorphologyDiscovery(db_engine, limit=5)
    with db_engine.connect() as conn:
        discovery.run(conn)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tier FROM foundation_evidence WHERE extractor = 'discovery' AND fact_type = 'word'")
        ).fetchall()

    for row in rows:
        # Morphology should use tier 1 (BIBLE), 2 (DICTIONARY), or 4 (CORPUS)
        assert row.tier in (1, 2, 4)
