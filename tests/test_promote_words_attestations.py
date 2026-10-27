"""Tests for attestation-driven promotion into ``canonical_words``."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.pipeline.foundation import FoundationETL


@pytest.fixture()
def etl():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    from zolai.data.migrations import (
        apply_foundation_constraints,
        apply_foundation_indexes,
        create_foundation_tables,
    )

    create_foundation_tables(mgr)
    apply_foundation_constraints(mgr)
    apply_foundation_indexes(mgr)

    with mgr.engine.begin() as conn:
        columns = {r[1] for r in conn.execute(text("PRAGMA table_info(vocabulary)"))}
        for column, ddl in (
            ("syllables", "TEXT"),
            ("syllable_count", "INTEGER"),
            ("pos", "TEXT"),
        ):
            if column not in columns:
                conn.execute(text(f"ALTER TABLE vocabulary ADD COLUMN {column} {ddl}"))

        conn.execute(text(
            "INSERT INTO attestation_index (word, source) VALUES "
            "('pasian','bible'),('pasian','dict'),"
            "('tua','bible'),('tua','corpus'),"
            "('gaml','dict'),('gaml','corpus'),"
            "('solo','dict')"
        ))
        conn.execute(text(
            "INSERT INTO vocabulary "
            "(headword, english, frequency, syllables, syllable_count, books, examples, status) VALUES "
            "('pasian','God',3750,'[\"pa\", \"sian\"]',2,'[]','[]','OBSERVED'),"
            "('tua','that',12299,'[\"tua\"]',1,'[]','[]','OBSERVED'),"
            "('gaml','gold',12,'[\"gaml\"]',1,'[]','[]','OBSERVED')"
        ))
        conn.execute(text(
            "INSERT INTO dictionary (zolai, english_clean, english, source, pos) VALUES "
            "('pasian','God (P-ZVS)','God (P-ZVS)','seed','NOUN'),"
            "('tua','that those','that those','seed','ADJ'),"
            "('gaml','gold','gold','seed','NOUN')"
        ))

    yield FoundationETL(mgr=mgr)
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


def _forms(mgr) -> dict[str, dict]:
    with mgr.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT form, syllables, syllable_count, pos, meanings, evidence_ids "
            "FROM canonical_words"
        )).fetchall()
    return {r[0]: r._mapping for r in rows}


def test_two_tier_words_are_promoted(etl):
    stats = etl.promote_words_from_attestations(threshold=0.85)

    assert stats.records_promoted == 2
    assert stats.records_queued_for_review == 1
    forms = _forms(etl._get_manager())
    assert set(forms) == {"pasian", "tua"}

    pasian = forms["pasian"]
    assert json.loads(pasian["syllables"]) == ["pa", "sian"]
    assert pasian["syllable_count"] == 2
    assert "God (P-ZVS)" in json.loads(pasian["meanings"])
    assert len(json.loads(pasian["evidence_ids"])) == 2


def test_single_tier_word_is_not_promoted(etl):
    etl.promote_words_from_attestations(threshold=0.85)

    forms = _forms(etl._get_manager())
    assert "solo" not in forms
    with etl._get_manager().engine.connect() as conn:
        queued = {r[0] for r in conn.execute(text(
            "SELECT fact_key FROM foundation_review_queue WHERE fact_type='word'"
        ))}
    assert "word:solo" not in queued


def test_gated_word_below_threshold_is_queued(etl):
    etl.promote_words_from_attestations(threshold=0.85)

    forms = _forms(etl._get_manager())
    assert "gaml" not in forms
    with etl._get_manager().engine.connect() as conn:
        queued = {r[0] for r in conn.execute(text(
            "SELECT fact_key FROM foundation_review_queue WHERE fact_type='word'"
        ))}
    assert "word:gaml" in queued


def test_rerun_is_idempotent(etl):
    etl.promote_words_from_attestations(threshold=0.85)
    second = etl.promote_words_from_attestations(threshold=0.85)

    assert second.records_promoted == 0
    assert second.records_queued_for_review == 0
    assert len(_forms(etl._get_manager())) == 2


def test_evidence_and_consensus_rows_are_recorded(etl):
    etl.promote_words_from_attestations(threshold=0.85)

    with etl._get_manager().engine.connect() as conn:
        evidence = conn.execute(text(
            "SELECT source, tier FROM foundation_evidence WHERE fact_key='word:pasian'"
        )).fetchall()
        consensus = conn.execute(text(
            "SELECT method, confidence FROM foundation_consensus WHERE fact_key='word:pasian'"
        )).fetchone()

    assert {(r[0], r[1]) for r in evidence} == {
        ("attestation_index:bible", 1),
        ("attestation_index:dict", 2),
    }
    assert consensus[0] == "weighted_evidence"
    assert consensus[1] >= 0.85
