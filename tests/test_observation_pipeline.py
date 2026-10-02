"""Observation build pipeline tests (Phase 2 capability integration).

Fixture corpus (hand-computed expectations — all assertions are derived by
hand, never re-executed through the pipeline):

====================  ==================================================
bible_verses:1  GEN   ``Pasian in vantung leh leitung a piangsak hi.`` (8 tok)
bible_verses:2  TEST  ``Gam ka lak hi.`` (4 tok)
bible_verses:3  EXO   tedim only: ``Pathian a pai ta hi.`` (5 tok, ZVS)
translations:1  zo→en ``Gam ka mu hi.`` (4 tok)
translations:2  en→zo ``Gam ka mu hi.`` (4 tok)
translations:3  en→my Myanmar target — **must be skipped** (§19 Zolai-only)
phrases:1             ``vanlai in leitung a piangsak hi`` (6 tok)
====================  ==================================================

Totals: 6 observations · 31 tokens · 6 documents · 15 distinct words ·
44 within-window pair occurrences (window ±2, no self-pairs).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_observation_tables
from zolai.data.models import BibleVerse, PhraseEntry, TranslationPair
from zolai.data.repositories.observation import WordStatsRepository
from zolai.foundation.observation import (
    PIPELINE_VERSION,
    ObservationPipeline,
    build_observations,
)
from zolai.foundation.observation.store import ensure_tables

EXPECTED_SOURCE_REFS = {
    "bible_verses:zo_tdb77:1",
    "bible_verses:zo_tdb77:2",
    "bible_verses:zo_tedim2010:3",
    "translations:zolai:1",
    "translations:zolai:2",
    "phrases:zolai:1",
}


def _seed_sources(engine: Engine) -> None:
    """Load the fixture corpus (identical key sets — executemany needs them)."""
    with engine.begin() as conn:
        conn.execute(
            BibleVerse.__table__.insert(),
            [
                {
                    "id": 1,
                    "ref": "GEN 1:1",
                    "book": "GEN",
                    "chapter": 1,
                    "verse": 1,
                    "zo_tdb77": "Pasian in vantung leh leitung a piangsak hi.",
                    "zo_tedim2010": None,
                },
                {
                    # Synthetic verse — test seed only, not a real Bible ref.
                    "id": 2,
                    "ref": "TEST 1:2",
                    "book": "TEST",
                    "chapter": 1,
                    "verse": 2,
                    "zo_tdb77": "Gam ka lak hi.",
                    "zo_tedim2010": None,
                },
                {
                    "id": 3,
                    "ref": "EXO 1:1",
                    "book": "EXO",
                    "chapter": 1,
                    "verse": 1,
                    "zo_tdb77": None,
                    "zo_tedim2010": "Pathian a pai ta hi.",
                },
            ],
        )
        conn.execute(
            TranslationPair.__table__.insert(),
            [
                {
                    "id": 1,
                    "direction": "zo_to_en",
                    "source": "Gam ka mu hi.",
                    "target": "I see the land.",
                    "reference": "gen:1",
                    "confidence": 1.0,
                },
                {
                    "id": 2,
                    "direction": "en_to_zo",
                    "source": "I see the land.",
                    "target": "Gam ka mu hi.",
                    "reference": "gen:1",
                    "confidence": 1.0,
                },
                {
                    # §19: English→Myanmar carries no Zolai — must be skipped.
                    "id": 3,
                    "direction": "en_to_my",
                    "source": "I see the land.",
                    "target": "မြင်သည်။",
                    "reference": "gen:1",
                    "confidence": 1.0,
                },
            ],
        )
        conn.execute(
            PhraseEntry.__table__.insert(),
            [
                {
                    "id": 1,
                    "zolai": "vanlai in leitung a piangsak hi",
                    "english": "the word created the earth",
                    "myanmar": None,
                    "frequency": 1,
                }
            ],
        )


@pytest.fixture()
def obs_db(tmp_path: Path) -> DatabaseManager:
    """Temporary DB with the ORM schema, Phase 2 tables and the fixture corpus."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'observations.db'}")
    mgr.init_db()
    create_observation_tables(mgr)
    _seed_sources(mgr.engine)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def summary(obs_db: DatabaseManager) -> dict:
    return ObservationPipeline(obs_db.engine).build()


# ---------------------------------------------------------------------------
# Build surface
# ---------------------------------------------------------------------------


def test_build_summary_shape(summary: dict) -> None:
    assert summary["pipeline_version"] == PIPELINE_VERSION == "phase2-v1"
    assert summary["sources"] == [
        "bible_tdb77",
        "bible_tedim2010",
        "translations_zo",
        "phrases",
    ]
    assert summary["sources_skipped"] == []
    assert summary["migration"]["errors"] == []
    assert summary["sentences"] == 6
    assert summary["observations"] == 6
    assert summary["observations_inserted"] == 6
    assert summary["tokens"] == 31
    assert summary["documents"] == 6
    assert summary["words"] == 15
    assert summary["stats_rows"] == 15
    assert summary["pair_occurrences"] == 44
    assert summary["limit"] is None
    for key in ("started_at", "finished_at", "elapsed_seconds"):
        assert key in summary


def test_build_writes_expected_observations(obs_db: DatabaseManager, summary: dict) -> None:
    with obs_db.engine.connect() as conn:
        refs = {
            row[0]
            for row in conn.execute(text("SELECT source_ref FROM observations"))
        }
        assert refs == EXPECTED_SOURCE_REFS
        count = conn.execute(text("SELECT COUNT(*) FROM observations")).scalar_one()
    assert count == 6 == summary["observations_inserted"]


def test_en_to_my_direction_excluded(obs_db: DatabaseManager) -> None:
    """§19 Zolai-only: the en→my direction never becomes an observation."""
    ObservationPipeline(obs_db.engine).build()
    with obs_db.engine.connect() as conn:
        myanmar = conn.execute(
            text("SELECT COUNT(*) FROM observations WHERE document_id = 'en_to_my'")
        ).scalar_one()
        translations = conn.execute(
            text("SELECT COUNT(*) FROM observations WHERE source_id = 'translations:zolai'")
        ).scalar_one()
    assert myanmar == 0
    assert translations == 2  # zo_to_en + en_to_zo only


def test_row_fields_tokens_context_metadata(obs_db: DatabaseManager) -> None:
    ObservationPipeline(obs_db.engine).build()
    with obs_db.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT tokens, context, source_id, document_id, sentence_id, "
                "method, extractor, metadata FROM observations "
                "WHERE source_ref = 'bible_verses:zo_tdb77:1'"
            )
        ).mappings().one()
        zvs_row = conn.execute(
            text("SELECT metadata FROM observations WHERE source_ref = 'bible_verses:zo_tedim2010:3'")
        ).scalar_one()

    assert json.loads(row["tokens"]) == [
        "pasian",
        "in",
        "vantung",
        "leh",
        "leitung",
        "a",
        "piangsak",
        "hi",
    ]
    assert row["context"] == "bible_tdb77:GEN"
    assert row["source_id"] == "bible_verses:zo_tdb77"
    assert row["document_id"] == "GEN"
    assert row["sentence_id"] == "GEN 1:1"
    assert row["method"] == "pre_segmented"
    assert row["extractor"] == "observation-pipeline"

    meta = json.loads(row["metadata"])
    assert meta["token_count"] == 8
    assert meta["zvs_corrected"] is False
    assert meta["pipeline_version"] == "phase2-v1"
    # ``Pathian`` → ``pasian``: the ZVS correction is flagged on the row.
    assert json.loads(zvs_row)["zvs_corrected"] is True


def test_build_is_idempotent(obs_db: DatabaseManager, summary: dict) -> None:
    """Re-running inserts nothing (ux_obs_source_ref) and refreshes stats."""
    second = build_observations(obs_db.engine)
    assert second["observations"] == 6
    assert second["observations_inserted"] == 0
    assert second["stats_rows"] == summary["stats_rows"]
    with obs_db.engine.connect() as conn:
        rows = conn.execute(text("SELECT COUNT(*) FROM observations")).scalar_one()
    assert rows == 6


# ---------------------------------------------------------------------------
# Stats + normalization
# ---------------------------------------------------------------------------


def test_stats_scalars_hand_computed(obs_db: DatabaseManager) -> None:
    ObservationPipeline(obs_db.engine).build()
    with obs_db.engine.connect() as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                text(
                    "SELECT normalized_form, frequency, doc_freq, sent_freq, "
                    "source_count, diversity FROM word_observation_stats "
                    "WHERE normalized_form IN ('gam', 'pasian', 'hi')"
                )
            )
        }
    # gam: bible TEST 1:2 + both translation directions → 3/3/3, docs TEST +
    # zo_to_en + en_to_zo (3 of 6), sources bible + translations (2 of 4).
    assert rows["gam"] == (3, 3, 3, 2, 0.5)
    # pasian: GEN (surface 'pasian') + EXO (surface 'pathian'), docs GEN/EXO
    # (2 of 6 → diversity rounded to 4 dp).
    assert rows["pasian"] == (2, 2, 2, 2, 0.3333)
    # hi: every sentence → 6 occurrences, 6 documents, all 4 sources.
    assert rows["hi"] == (6, 6, 6, 4, 1.0)


def test_surface_forms_carry_zvs_variants(obs_db: DatabaseManager) -> None:
    ObservationPipeline(obs_db.engine).build()
    with obs_db.engine.connect() as conn:
        raw = conn.execute(
            text(
                "SELECT surface_forms FROM word_observation_stats "
                "WHERE normalized_form = 'pasian'"
            )
        ).scalar_one()
    surfaces = json.loads(raw)
    assert surfaces == [
        {"surface": "pasian", "count": 1},
        {"surface": "pathian", "count": 1},
    ]


def test_contexts_and_neighbors_are_json(obs_db: DatabaseManager) -> None:
    ObservationPipeline(obs_db.engine).build()
    with obs_db.engine.connect() as conn:
        raw = conn.execute(
            text(
                "SELECT contexts, neighbors, collocations FROM "
                "word_observation_stats WHERE normalized_form = 'hi'"
            )
        ).one()
    contexts = json.loads(raw[0])
    assert contexts["snippet"] == "Pasian in vantung leh leitung a piangsak hi."
    assert {c["token"] for c in contexts["right"]} == set()
    # Window-2 left neighbours of sentence-final ``hi`` (count desc, token asc).
    assert contexts["left"][0] == {"token": "ka", "count": 3}
    neighbors = json.loads(raw[1])
    assert [n["count"] for n in neighbors] == sorted(
        (n["count"] for n in neighbors), reverse=True
    )
    # Default min_freq=5 keeps this fixture's collocation list empty — the
    # assertion pins that the column always parses as a JSON array.
    assert isinstance(json.loads(raw[2]), list)


# ---------------------------------------------------------------------------
# Hooks, selection, guards
# ---------------------------------------------------------------------------


def test_attestor_hook_fills_column(obs_db: DatabaseManager) -> None:
    def fake_attestor(word: str) -> dict:
        return {"word": word, "attested": word in {"gam", "pasian"}}

    ObservationPipeline(obs_db.engine, attestor=fake_attestor).build()
    with obs_db.engine.connect() as conn:
        pasian = json.loads(
            conn.execute(
                text(
                    "SELECT attestation FROM word_observation_stats "
                    "WHERE normalized_form = 'pasian'"
                )
            ).scalar_one()
        )
        lak = json.loads(
            conn.execute(
                text(
                    "SELECT attestation FROM word_observation_stats "
                    "WHERE normalized_form = 'lak'"
                )
            ).scalar_one()
        )
    assert pasian == {"word": "pasian", "attested": True}
    assert lak == {"word": "lak", "attested": False}
    # No attestor wired → empty JSON object (default build path).
    ObservationPipeline(obs_db.engine).build()
    with obs_db.engine.connect() as conn:
        empty = conn.execute(
            text(
                "SELECT attestation FROM word_observation_stats "
                "WHERE normalized_form = 'pasian'"
            )
        ).scalar_one()
    assert json.loads(empty) == {}


def test_limit_and_source_selection(obs_db: DatabaseManager) -> None:
    summary = ObservationPipeline(
        obs_db.engine, sources=["bible_tdb77"], limit=1
    ).build()
    assert summary["sources"] == ["bible_tdb77"]
    assert summary["observations"] == 1
    with obs_db.engine.connect() as conn:
        refs = [
            row[0]
            for row in conn.execute(text("SELECT source_ref FROM observations"))
        ]
    assert refs == ["bible_verses:zo_tdb77:1"]


def test_unknown_source_raises(obs_db: DatabaseManager) -> None:
    with pytest.raises(ValueError, match="unknown source"):
        ObservationPipeline(obs_db.engine, sources=["nope"]).build()


def test_no_source_tables_raises(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert ensure_tables(engine)["errors"] == []
    with pytest.raises(ValueError, match="no observation source tables"):
        ObservationPipeline(engine).build()
    engine.dispose()


def test_bulk_write_skips_per_row_audit(obs_db: DatabaseManager, summary: dict) -> None:
    """Plan deviation 5: derived rows never flood ``data_audit_log``."""
    assert summary["observations_inserted"] == 6
    with obs_db.engine.connect() as conn:
        audit = conn.execute(text("SELECT COUNT(*) FROM data_audit_log")).scalar_one()
    assert audit == 0


def test_ensure_tables_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    first = ensure_tables(engine)
    assert first["errors"] == []
    assert first["created"][:3] == [
        "observations",
        "word_observation_stats",
        "attestation_index",
    ]
    assert set(first["created"][3:]) == {
        "ux_obs_source_ref",
        "ix_obs_sentence",
        "ix_obs_document",
        "ix_attestation_source",
    }
    second = ensure_tables(engine)
    assert second["created"] == []
    assert second["errors"] == []
    assert len(second["skipped"]) == 7
    engine.dispose()


def test_stats_upsert_merges_seen_timestamps(obs_db: DatabaseManager) -> None:
    """``first_seen`` survives a later rebuild; ``last_seen`` moves forward."""
    repo = WordStatsRepository(obs_db.engine)
    older = "2020-01-01T00:00:00+00:00"
    newer = "2026-10-02T00:00:00+00:00"
    base = {
        "normalized_form": "gam",
        "frequency": 1,
        "doc_freq": 1,
        "sent_freq": 1,
        "source_count": 1,
        "diversity": 0.1,
        "surface_forms": "[]",
        "contexts": "{}",
        "neighbors": "[]",
        "collocations": "[]",
        "attestation": "{}",
        "first_seen": older,
        "last_seen": older,
        "pipeline_version": "phase2-v1",
        "updated_at": older,
    }
    assert repo.upsert_many([base]) == 1
    merged = {**base, "frequency": 9, "first_seen": newer, "last_seen": newer}
    assert repo.upsert_many([merged]) == 1
    with obs_db.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT frequency, first_seen, last_seen FROM "
                "word_observation_stats WHERE normalized_form = 'gam'"
            )
        ).one()
    # Scalars are last-writer-wins; timestamps merge across runs.
    assert row[0] == 9
    assert row[1] == older
    assert row[2] == newer
