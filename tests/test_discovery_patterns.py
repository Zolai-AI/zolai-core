"""Tests for Sentence Pattern and Grammar Discovery.

These run against a throwaway database. The first version called
``get_engine()``, so every suite run wrote 10 ``disc_g_*`` rows (plus their
``foundation_evidence`` rows) into the canonical corpus — the source of the
22 copies of each pattern found on 2026-10-29. The ``no_canonical_db_writes``
fence now fails that, and the ids are stable, so a re-run is a no-op.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.learning.discovery.grammar import GrammarDiscovery
from zolai.learning.discovery.sentence_patterns import SentencePatternDiscovery


@pytest.fixture(scope="module")
def db_engine() -> Engine:
    """A fresh schema with just enough corpus to discover against."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name

    manager = DatabaseManager(f"sqlite:///{path}")
    manager.init_db()
    with manager.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77, en_kJV) VALUES "
                "('JHN 1:1', 'JHN', 1, 1, 'Pasian in leitung a bawl hi.', 'God created.'), "
                "('JHN 1:2', 'JHN', 1, 2, 'Topa in kei hong huh hi.', 'The Lord helped me.')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO translations (source, target, direction, reference, confidence) VALUES "
                "('Pasian in leitung a bawl hi.', 'God created.', 'zo_en', 'JHN 1:1', 1.0), "
                "('Topa in kei hong huh hi.', 'The Lord helped me.', 'zo_en', 'JHN 1:2', 1.0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO grammar_patterns (pattern_id, pattern, function, examples, "
                "frequency, normalized, components, sources, evidence_ids, status) VALUES "
                "('pat_legacy_1', 'legacy', 'legacy', '[]', 1, 'legacy', '{}', '[]', '[]', 'OBSERVED')"
            )
        )

    yield manager.engine
    manager.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def test_sentence_pattern_discovery_runs(db_engine: Engine) -> None:
    """Test that sentence pattern discovery runs without error."""
    discovery = SentencePatternDiscovery(db_engine, limit=10)
    result = discovery.run()

    assert "patterns_written" in result
    assert "status_counts" in result
    assert "pattern_types" in result
    assert result["patterns_written"] >= 0


def test_sentence_patterns_use_disc_sp_namespace(db_engine: Engine) -> None:
    """Test that sentence patterns use disc_sp_* namespace."""
    discovery = SentencePatternDiscovery(db_engine, limit=10)
    discovery.run()

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT pattern_id, pattern, function, normalized, status, confidence, "
                "evidence_ids FROM grammar_patterns WHERE pattern_id LIKE 'disc_sp_%'"
            )
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.pattern_id.startswith("disc_sp_")
        assert row.pattern
        assert row.normalized
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0
        assert json.loads(row.evidence_ids), row.pattern_id

    # One (type, structure) maps to one row, however many verses or paths saw it.
    assert len(rows) == len({(row.function, row.pattern) for row in rows})


def test_grammar_discovery_runs(db_engine: Engine) -> None:
    """Test that grammar phenomena discovery runs without error."""
    discovery = GrammarDiscovery(db_engine, limit=10)
    result = discovery.run()

    assert "phenomena_found" in result
    assert "patterns_written" in result
    assert "status_counts" in result
    assert result["patterns_written"] >= 0


def test_grammar_patterns_use_disc_g_namespace(db_engine: Engine) -> None:
    """Test that grammar patterns use disc_g_* namespace."""
    discovery = GrammarDiscovery(db_engine, limit=10)
    discovery.run()

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT pattern_id, pattern, function, status, confidence, "
                "evidence_ids FROM grammar_patterns WHERE pattern_id LIKE 'disc_g_%'"
            )
        ).fetchall()

    assert len(rows) > 0
    for row in rows:
        assert row.pattern_id.startswith("disc_g_")
        assert row.pattern
        assert row.function
        assert row.status in ("OBSERVED", "CANDIDATE")
        assert row.confidence is not None
        assert 0.0 <= float(row.confidence) <= 1.0
        assert json.loads(row.evidence_ids), row.pattern_id


def test_baseline_grammar_patterns_unchanged(db_engine: Engine) -> None:
    """Discovery never rewrites rows outside its own namespace."""
    with db_engine.connect() as conn:
        before = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE pattern_id NOT LIKE 'disc_%'")
        ).scalar()

    discovery = GrammarDiscovery(db_engine, limit=10)
    discovery.run()

    with db_engine.connect() as conn:
        after = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE pattern_id NOT LIKE 'disc_%'")
        ).scalar()

    assert after == before == 1


def test_a_second_grammar_run_writes_no_new_patterns(db_engine: Engine) -> None:
    """Stable ids make discovery idempotent across runs.

    The ids used to carry ``abs(hash(text)) % 100000``, which is salted per
    process, so the existence check never matched and every run inserted
    another copy of the same pattern into the canonical corpus.
    """
    discovery = GrammarDiscovery(db_engine, limit=10)
    first_run = discovery.run()
    with db_engine.connect() as conn:
        first = conn.execute(text("SELECT COUNT(*) FROM grammar_patterns")).scalar()
        first_ids = {
            row[0]
            for row in conn.execute(
                text("SELECT pattern_id FROM grammar_patterns WHERE pattern_id LIKE 'disc_g_%'")
            )
        }

    second_run = discovery.run()
    with db_engine.connect() as conn:
        second = conn.execute(text("SELECT COUNT(*) FROM grammar_patterns")).scalar()
        second_ids = {
            row[0]
            for row in conn.execute(
                text("SELECT pattern_id FROM grammar_patterns WHERE pattern_id LIKE 'disc_g_%'")
            )
        }

    assert second == first
    assert second_ids == first_ids
    assert second_run["patterns_written"] == 0
    assert second_run["already_present"] == (
        first_run["patterns_written"] + first_run["already_present"]
    )


def test_a_second_sentence_pattern_run_writes_no_new_patterns(db_engine: Engine) -> None:
    """The translation path keys its id on structure, not on a hash of the text."""
    discovery = SentencePatternDiscovery(db_engine, limit=10)
    discovery.run()
    with db_engine.connect() as conn:
        first = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE pattern_id LIKE 'disc_sp_%'")
        ).scalar()

    second_run = discovery.run()
    with db_engine.connect() as conn:
        second = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE pattern_id LIKE 'disc_sp_%'")
        ).scalar()

    assert second == first
    assert second_run["patterns_written"] == 0
    assert second_run["already_present"] > 0
