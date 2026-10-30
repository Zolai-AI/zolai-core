"""Tests for Discovery Pipeline.

Runs on a throwaway database, never the corpus: the pipeline is a write
surface (upserts into ``hypotheses`` / ``foundation_evidence``), and this module
used to take ``get_engine()`` — the 2.4 GB canonical DB — so every run of the
suite committed discovery rows into the production corpus.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.learning.discovery.pipeline import DiscoveryPipeline, build_discovery

#: Words with a canonical POS so the POS/morphology builders have input.
_SEED_DICTIONARY = (
    # zolai, english, pos_canonical, pos, pos_evidence, confidence
    ("pasian", "God", "NOUN", "n", "lexicon", 0.9),
    ("piangsak", "to create", "VERB", "v", "lexicon", 0.9),
    ("gam", "earth, land", "NOUN", "n", "lexicon", 0.8),
    ("leh", "and, then", "PARTICLE", "particle", "corpus", 0.7),
    ("in", "we (inclusive)", "PRON", "pron", "corpus", 0.6),
    ("zomi", "a people of Mizoram", "NOUN", "n", "lexicon", 0.9),
)

_SEED_BIBLE = (
    # ref, book, chapter, verse, zo_tdb77
    ("GEN 1:1", "Genesis", 1, 1, "Pasian in leitung a piangsak hi"),
    ("JHN 1:1", "John", 1, 1, "Piangsak hi leitung gam zolo in a hi"),
)


@pytest.fixture()
def db_engine(tmp_path) -> Iterator[Engine]:
    """Full canonical schema on a throwaway file, seeded with a tiny lexicon."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'discovery.db'}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dictionary (zolai, english, source, pos, pos_canonical,"
                " pos_evidence, confidence) VALUES (:zolai, :english, 'test-seed', :pos,"
                " :pos_canonical, :pos_evidence, :confidence)"
            ),
            [
                {
                    "zolai": w,
                    "english": en,
                    "pos_canonical": posc,
                    "pos": pos,
                    "pos_evidence": ev,
                    "confidence": conf,
                }
                for w, en, posc, pos, ev, conf in _SEED_DICTIONARY
            ],
        )
        conn.execute(
            text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77)"
                " VALUES (:ref, :book, :chapter, :verse, :zo)"
            ),
            [
                {"ref": ref, "book": book, "chapter": ch, "verse": v, "zo": zo}
                for ref, book, ch, v, zo in _SEED_BIBLE
            ],
        )
    yield mgr.engine
    mgr.dispose()


def test_discovery_pipeline_runs(db_engine: Engine) -> None:
    """Test that the full discovery pipeline runs (small limit)."""
    pipeline = DiscoveryPipeline(db_engine, capabilities=["pos", "morphology"], limit=5)
    result = pipeline.build()

    assert result["pipeline_version"] == "phase3-v1"
    assert "capabilities_run" in result
    assert "pos" in result["capabilities_run"]
    assert "morphology" in result["capabilities_run"]
    assert "pos" in result
    assert "morphology" in result


def test_discovery_pipeline_dry_run(db_engine: Engine) -> None:
    """Dry-run analyses the capability but must not persist anything."""
    with db_engine.connect() as conn:
        before = conn.execute(
            text("SELECT COUNT(*) FROM hypotheses WHERE kind = 'pos'")
        ).scalar_one()

    pipeline = DiscoveryPipeline(db_engine, capabilities=["pos"], limit=5, dry_run=True)
    result = pipeline.build()

    with db_engine.connect() as conn:
        after = conn.execute(
            text("SELECT COUNT(*) FROM hypotheses WHERE kind = 'pos'")
        ).scalar_one()

    assert result["dry_run"] is True
    # The builder still runs -- it reports what it *would* write -- so the
    # capability is listed. Asserting [] here would only prove the summary is
    # empty; what matters is that no row reached the database.
    assert result["capabilities_run"] == ["pos"]
    assert after == before, "dry-run wrote rows"
    assert result["pos"], "dry-run produced no analysis to report"


def test_build_discovery_convenience(db_engine: Engine) -> None:
    """Test the module-level build_discovery wrapper."""
    result = build_discovery(db_engine, capabilities=["collocation"], limit=5)

    assert result["pipeline_version"] == "phase3-v1"
    assert "collocation" in result["capabilities_run"]


def _row_count(db_engine: Engine, table: str) -> int:
    with db_engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def test_discovery_idempotent(db_engine: Engine) -> None:
    """Re-running discovery must not append duplicate hypotheses or audit rows."""
    pipeline = DiscoveryPipeline(db_engine, capabilities=["pos"], limit=5)
    result1 = pipeline.build()
    assert "error" not in result1["pos"], result1["pos"]
    assert result1["pos"]["hypotheses_new"] > 0, "first run wrote no hypotheses"

    hypotheses = _row_count(db_engine, "hypotheses")
    audit = _row_count(db_engine, "data_audit_log")

    result2 = pipeline.build()
    assert "error" not in result2["pos"], result2["pos"]

    # Same corpus, same claims: the second run refreshes what already exists.
    assert result2["pos"]["hypotheses_new"] == 0
    assert _row_count(db_engine, "hypotheses") == hypotheses
    assert _row_count(db_engine, "data_audit_log") == audit


def test_hypotheses_status_only_observed_candidate(db_engine: Engine) -> None:
    """Test that all hypotheses have status OBSERVED or CANDIDATE only."""
    pipeline = DiscoveryPipeline(
        db_engine, capabilities=["pos", "morphology", "collocation"], limit=5
    )
    pipeline.build()

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT status FROM hypotheses WHERE kind IN ('pos', 'morph_relation', 'collocation')")
        ).fetchall()

    statuses = {row.status for row in rows}
    assert statuses.issubset({"OBSERVED", "CANDIDATE"})
    assert "SUPPORTED" not in statuses
    assert "VERIFIED" not in statuses


def test_foundation_evidence_has_method_extractor(db_engine: Engine) -> None:
    """Every evidence row written by discovery carries provenance (method + extractor).

    ``extractor`` names the tool that produced the row (``ZolaiPOSTagger``,
    ``EnhancedMorphologyAnalyzer``, …), so the filter here is "the database
    contains nothing but discovery output" — true because this test runs
    against a throwaway schema.
    """
    pipeline = DiscoveryPipeline(
        db_engine, capabilities=["pos", "morphology", "collocation"], limit=5
    )
    pipeline.build()

    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT method, extractor FROM foundation_evidence")
        ).fetchall()

    assert len(rows) > 0, "discovery wrote no evidence"
    missing = [
        row for row in rows
        if not (row.method or "").strip() or not (row.extractor or "").strip()
    ]
    assert not missing, f"{len(missing)} evidence rows without method/extractor"


def test_capability_failure_is_named_and_isolated(db_engine: Engine, monkeypatch) -> None:
    """One builder raising must not hide behind an OK summary."""
    import zolai.learning.discovery.pipeline as pipeline_mod

    def boom(engine, **kwargs):
        raise RuntimeError("cannot open /srv/zolai/private.db with api_key=sk-test-abc123")

    monkeypatch.setattr(pipeline_mod, "build_pos_hypotheses", boom)

    result = build_discovery(db_engine, capabilities=["pos", "collocation"], limit=5)

    assert result["ok"] is False
    assert result["failed_capabilities"] == ["pos"]
    assert result["pos"]["ok"] is False
    assert result["pos"]["error_class"] == "RuntimeError"
    # The safe message is stored, never the raw exception text.
    assert "sk-test-abc123" not in result["pos"]["error"]
    # The other capability still ran and is not flagged.
    assert result["collocation"].get("ok", True) is not False
