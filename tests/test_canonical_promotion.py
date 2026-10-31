"""Canonical sentence/paragraph promotion from parallel Bible witnesses."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.pipeline.foundation import FoundationETL

ZOLAI_WORDS = (
    "Pasian in leitung a bawl hi. "
    "Ama'n vantung leh leitung a bawl hi. "
    "Tua ciangin khuapi sungah thu a piang hi."
)


@pytest.fixture()
def etl_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    etl = FoundationETL(mgr=mgr, db_path=f"sqlite:///{db_path}")
    yield mgr, etl
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


def _add_vocabulary(etl_db, words: tuple[str, ...] = ()) -> None:
    mgr, _ = etl_db
    vocab = words or tuple(
        {
            token
            for token in ZOLAI_WORDS.lower().replace(".", "").split()
            if token
        }
    )
    with mgr.engine.begin() as conn:
        for word in sorted(vocab):
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO vocabulary "
                    "(headword, english, frequency, books, examples) "
                    "VALUES (:headword, '', 1, '[]', '[]')"
                ),
                {"headword": word},
            )


def _add_verse(mgr, ref: str, zo_tdb77: str, zo_tedim2010: str, english: str = "") -> None:
    book, chapter_verse = ref.split(" ", 1)
    chapter, verse = chapter_verse.split(":")
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO bible_verses
                   (ref, book, chapter, verse, zo_tdb77, zo_tedim2010, en_kJV)
                   VALUES (:ref, :book, :chapter, :verse, :tdb77, :tedim, :en)"""
            ),
            {
                "ref": ref,
                "book": book,
                "chapter": int(chapter),
                "verse": int(verse),
                "tdb77": zo_tdb77,
                "tedim": zo_tedim2010,
                "en": english,
            },
        )


def _scalar(mgr, sql: str) -> int:
    with mgr.engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar() or 0)


def test_a_verse_witnessed_by_two_translations_is_promoted(etl_db):
    mgr, etl = etl_db
    _add_vocabulary(etl_db)
    _add_verse(
        mgr,
        "GEN 1:1",
        "Pasian in leitung a bawl hi.",
        "Pasian in leitung a bawl hi.",
        "In the beginning God created the heaven and the earth.",
    )

    stats = etl.promote_bible_sentences(threshold=0.9)

    assert stats.records_promoted == 1
    assert _scalar(mgr, "SELECT COUNT(*) FROM canonical_sentences") == 1
    with mgr.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT text, tokens, pos_tags, translation, grammar, "
                "source_hash, verified_by, evidence_ids FROM canonical_sentences"
            )
        ).one()
        evidence = conn.execute(
            text(
                "SELECT tier, source, method FROM foundation_evidence "
                "WHERE fact_type = 'sentence' ORDER BY tier"
            )
        ).fetchall()
        consensus = conn.execute(
            text(
                "SELECT method, confidence FROM foundation_consensus "
                "WHERE fact_type = 'sentence'"
            )
        ).one()

    assert row.text == "Pasian in leitung a bawl hi."
    assert json.loads(row.tokens) == ["pasian", "in", "leitung", "a", "bawl", "hi"]
    assert json.loads(row.pos_tags) == []
    assert row.translation.startswith("In the beginning")
    grammar = json.loads(row.grammar)
    assert grammar["ref"] == "GEN 1:1"
    assert grammar["clause"]
    assert grammar["structure"]
    assert row.verified_by.startswith("consensus:")
    assert len(json.loads(row.evidence_ids)) == 2
    assert [(e.tier, e.method) for e in evidence] == [
        (1, "bible_parallel"),
        (2, "bible_parallel"),
    ]
    assert consensus.method == "threshold_gate"
    assert consensus.confidence >= 0.9


def test_a_verse_read_by_one_translation_is_not_promoted(etl_db):
    mgr, etl = etl_db
    _add_vocabulary(etl_db)
    _add_verse(mgr, "GEN 1:2", "Pasian in leitung a bawl hi.", "")

    stats = etl.promote_bible_sentences(threshold=0.9)

    assert stats.records_promoted == 0
    assert _scalar(mgr, "SELECT COUNT(*) FROM canonical_sentences") == 0


def test_unknown_vocabulary_queues_instead_of_promoting(etl_db):
    mgr, etl = etl_db
    # No vocabulary rows: the words carry no glossary entry.
    _add_verse(
        mgr,
        "GEN 1:3",
        "Pasian in leitung a bawl hi.",
        "Pasian in leitung a bawl hi.",
    )

    stats = etl.promote_bible_sentences(threshold=0.9)

    assert stats.records_promoted == 0
    assert stats.records_queued_for_review == 1
    assert _scalar(mgr, "SELECT COUNT(*) FROM canonical_sentences") == 0
    assert (
        _scalar(
            mgr,
            "SELECT COUNT(*) FROM foundation_review_queue "
            "WHERE fact_type = 'sentence'",
        )
        == 1
    )


def test_rerun_does_not_recount_pending_review_decisions(etl_db):
    mgr, etl = etl_db
    _add_verse(
        mgr,
        "GEN 1:3",
        "Pasian in leitung a bawl hi.",
        "Pasian in leitung a bawl hi.",
    )

    first = etl.promote_bible_sentences(threshold=0.9)
    second = etl.promote_bible_sentences(threshold=0.9)

    assert first.records_queued_for_review == 1
    assert second.records_queued_for_review == 0
    assert (
        _scalar(
            mgr,
            "SELECT COUNT(*) FROM foundation_review_queue "
            "WHERE fact_type = 'sentence'",
        )
        == 1
    )


def test_sentence_promotion_is_idempotent(etl_db):
    mgr, etl = etl_db
    _add_vocabulary(etl_db)
    _add_verse(
        mgr,
        "GEN 1:1",
        "Pasian in leitung a bawl hi.",
        "Pasian in leitung a bawl hi.",
    )

    first = etl.promote_bible_sentences(threshold=0.9)
    second = etl.promote_bible_sentences(threshold=0.9)

    assert first.records_promoted == 1
    assert second.records_promoted == 0
    assert _scalar(mgr, "SELECT COUNT(*) FROM canonical_sentences") == 1
    assert _scalar(mgr, "SELECT COUNT(*) FROM foundation_evidence") == 2


def test_a_parallel_paragraph_is_promoted_from_the_learned_units(etl_db):
    mgr, etl = etl_db
    _add_vocabulary(etl_db)
    _add_verse(
        mgr,
        "GEN 1:1",
        "Pasian in leitung a bawl hi.",
        "Pasian in leitung a bawl hi.",
    )
    _add_verse(
        mgr,
        "GEN 1:2",
        "Ama'n vantung leh leitung a bawl hi.",
        "Ama in vantung leh leitung a bawl hi.",
    )

    stats = etl.promote_bible_paragraphs(threshold=0.9)

    assert stats.records_promoted == 1
    with mgr.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT text, sentences, style_profile, source_hash, "
                "evidence_ids FROM canonical_paragraphs"
            )
        ).one()
        evidence = conn.execute(
            text(
                "SELECT tier FROM foundation_evidence "
                "WHERE fact_type = 'paragraph' ORDER BY tier"
            )
        ).fetchall()

    assert "Pasian in leitung a bawl hi." in row.text
    assert len(json.loads(row.sentences)) >= 1
    profile = json.loads(row.style_profile)
    assert profile["lines"] == 2
    assert profile["tokens"] > 0
    assert len(json.loads(row.evidence_ids)) == 2
    assert [e.tier for e in evidence] == [1, 2]


def test_paragraph_promotion_is_idempotent(etl_db):
    mgr, etl = etl_db
    _add_vocabulary(etl_db)
    _add_verse(
        mgr,
        "GEN 1:1",
        "Pasian in leitung a bawl hi.",
        "Pasian in leitung a bawl hi.",
    )
    _add_verse(
        mgr,
        "GEN 1:2",
        "Ama'n vantung leh leitung a bawl hi.",
        "Ama in vantung leh leitung a bawl hi.",
    )

    first = etl.promote_bible_paragraphs(threshold=0.9)
    second = etl.promote_bible_paragraphs(threshold=0.9)

    assert first.records_promoted == 1
    assert second.records_promoted == 0
    assert _scalar(mgr, "SELECT COUNT(*) FROM canonical_paragraphs") == 1
