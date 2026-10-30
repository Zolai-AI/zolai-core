"""Tests for the hierarchical corpus learner (line → paragraph → document → corpus).

Runs against throwaway databases. The autouse ``no_canonical_db_writes`` fence
in ``tests/conftest.py`` fails the suite if anything touches the canonical DB.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager
from zolai.learning.bible_pattern_learner import classify_clause
from zolai.learning.hierarchy import Line, learn_layers, segment_lines

# ── fixtures ────────────────────────────────────────────────────────────────


def _tmp_engine() -> tuple[DatabaseManager, str]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    manager = DatabaseManager(f"sqlite:///{path}")
    manager.init_db()
    return manager, path


def _cleanup(manager: DatabaseManager, path: str) -> None:
    manager.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


@pytest.fixture()
def bible_engine() -> Engine:
    """Two chapters of one book, enough tokens per chapter to be kept."""
    manager, path = _tmp_engine()
    chapter_one = [
        ("Pasian in leitung a bawl hi.", "God created the world."),
        ("Topa in kei hong huh hi.", "The Lord helped me."),
        ("Ama in ka inn a khempeuh a phat hi.", "He blessed all my house."),
        ("Ka pa in lo a kho hi.", "My father farmed the field."),
        ("Naute in la a sa hi.", "The children sang a song."),
        ("Zinei in an a nek hi.", "The woman ate rice."),
        ("Tapa in sial a khawh hi.", "The son herded the mithun."),
        ("Sanginn in kong a hawn hi.", "The school opened its door."),
        ("Khuapi in mei a khu hi.", "The town burned with fire."),
        ("Gam lai in gamtang a nei hi.", "The forest has animals."),
        ("Tui in lei a liah hi.", "Water covered the ground."),
        ("Ni in khua a vak hi.", "The sun shone on the village."),
    ]
    chapter_two = [
        ("Mipa in sial a go hi.", "The man killed a mithun."),
        ("Zinei in tui a tawi hi.", "The woman drew water."),
        ("Naupang in la a ngai hi.", "The child listened to the song."),
        ("Ka nu in an a huan hi.", "My mother cooked rice."),
        ("Topa in khuapi a kem hi.", "The Lord guards the town."),
        ("Pasian in nuntakna a pia hi.", "God gives life."),
        ("Sanginn in laibu a pia hi.", "The school gave books."),
        ("Gam lai in a liang a nei hi.", "The forest has shade."),
        ("Ni in lei a khu hi.", "The sun warmed the ground."),
        ("Tui in gam a luang hi.", "Water flows through the land."),
        ("Ka pa in inn a lam hi.", "My father built a house."),
        ("Zinei in puan a si hi.", "The woman wove cloth."),
    ]
    with manager.engine.begin() as conn:
        for chapter, rows in ((1, chapter_one), (2, chapter_two)):
            for verse, (zo, en) in enumerate(rows, start=1):
                conn.execute(
                    text(
                        "INSERT INTO bible_verses "
                        "(ref, book, chapter, verse, zo_tdb77, en_kJV) "
                        "VALUES (:ref, 'JHN', :ch, :v, :zo, :en)"
                    ),
                    {
                        "ref": f"JHN {chapter}:{verse}",
                        "ch": chapter,
                        "v": verse,
                        "zo": zo,
                        "en": en,
                    },
                )
    yield manager.engine
    _cleanup(manager, path)


def _create_articles(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS articles ("
                "id INTEGER PRIMARY KEY, namespace TEXT, language TEXT, "
                "title TEXT, content TEXT, link TEXT)"
            )
        )


def _insert_news(engine: Engine, count: int) -> None:
    opener = "Tua ciangin"
    with engine.begin() as conn:
        for n in range(1, count + 1):
            content = "".join(
                f"<p>{opener} khuapi sungah thu a piang hi. "
                f"Pasian in mite a huh hi. Ama in lungdamna a pia hi.</p>"
                for _ in range(3)
            )
            conn.execute(
                text(
                    "INSERT INTO articles "
                    "(namespace, language, title, content, link) "
                    "VALUES ('news', 'zolai', :title, :content, :link)"
                ),
                {
                    "title": f"Thu {n}",
                    "content": content,
                    "link": f"https://zomidaily.example/news/{n}",
                },
            )


@pytest.fixture()
def news_engine() -> Engine:
    manager, path = _tmp_engine()
    _create_articles(manager.engine)
    _insert_news(manager.engine, count=12)
    yield manager.engine
    _cleanup(manager, path)


# ── segmentation ────────────────────────────────────────────────────────────


def test_segment_lines_is_deterministic_and_covers_all_lines() -> None:
    lines = [
        Line(ref=f"x:{i}", text=text)
        for i, text in enumerate(
            [
                "Pasian in leitung a bawl hi.",
                "Topa in kei hong huh hi.",
                "Ka pa in lo a kho hi.",
                "Ni in khua a vak hi.",
                "Tui in lei a liah hi.",
                "Sanginn in kong a hawn hi.",
                "Zinei in an a nek hi.",
                "Tapa in sial a khawh hi.",
            ],
            start=1,
        )
    ]
    first = segment_lines(lines, {})
    second = segment_lines(lines, {})

    assert first == second
    assert first[0][0] == 0
    assert first[-1][1] == len(lines) - 1
    for (_, end), (start, _) in zip(first, first[1:]):
        assert start == end + 1, "paragraph ranges must be contiguous"
    for start, end in first:
        assert end >= start
        assert end - start + 1 >= 2, "a paragraph needs at least two lines"


# ── layer construction ──────────────────────────────────────────────────────


def test_bible_learns_all_four_levels(bible_engine: Engine) -> None:
    result = learn_layers(bible_engine, sources=["bible"])

    with bible_engine.connect() as conn:
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "SELECT level, COUNT(*) FROM corpus_learning_layers "
                    "WHERE source = 'bible' GROUP BY level"
                )
            )
        }

    assert counts.get("line") == 24
    assert counts.get("paragraph", 0) >= 2
    assert counts.get("document") == 1, "both chapters belong to one book document"
    assert counts.get("corpus") == 1
    assert result["per_source"]["bible"]["layers"] == sum(counts.values())


def test_document_row_aggregates_chapters(bible_engine: Engine) -> None:
    learn_layers(bible_engine, sources=["bible"], levels=["document"])

    with bible_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT doc_id, features FROM corpus_learning_layers "
                "WHERE source = 'bible' AND level = 'document'"
            )
        ).fetchone()

    assert row is not None
    assert row[0] == "JHN"
    features = json.loads(row[1])
    assert features["chapters"] == 2
    assert features["lines"] == 24


def test_split_chapter_import_stays_one_document() -> None:
    """A chapter imported in two runs must not collide on unit ids.

    ``bible_verses`` holds 1CH 1:1–53 near the start and 1CH 1:54 at the tail;
    grouping by contiguous runs produced two DocUnits with the same keys and
    the upsert silently dropped the second half's rows.
    """
    manager, path = _tmp_engine()
    try:
        with manager.engine.begin() as conn:
            for ref, verse in (("JHN 1:1", 1), ("JHN 1:2", 2), ("JHN 1:3", 3)):
                conn.execute(
                    text(
                        "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77) "
                        "VALUES (:ref, 'JHN', 1, :v, 'Pasian in leitung a bawl hi.')"
                    ),
                    {"ref": ref, "v": verse},
                )
            conn.execute(
                text(
                    "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77) "
                    "VALUES ('JHN 2:1', 'JHN', 2, 1, 'Topa in kei hong huh hi.')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77) "
                    "VALUES ('JHN 1:4', 'JHN', 1, 4, 'Ama in ka inn a phat hi.')"
                )
            )

        learn_layers(manager.engine, sources=["bible"])
        with manager.engine.connect() as conn:
            lines = conn.execute(
                text(
                    "SELECT COUNT(*) FROM corpus_learning_layers "
                    "WHERE source='bible' AND level='line'"
                )
            ).scalar()
            features = conn.execute(
                text(
                    "SELECT features FROM corpus_learning_layers "
                    "WHERE source='bible' AND level='document' AND unit_id='JHN'"
                )
            ).scalar()
    finally:
        _cleanup(manager, path)

    # Chapter 2 holds one 6-token verse, below MIN_DOCUMENT_TOKENS, so only
    # chapter 1 is stored — and it must keep all four verses, including the
    # one whose row id lands after chapter 2.
    assert lines == 4, "chapter 1 must keep all four verses in one document"
    assert json.loads(features)["lines"] == 4


def test_site_chrome_paragraphs_are_dropped() -> None:
    from zolai.learning.hierarchy import _html_paragraphs

    blocks = _html_paragraphs(
        "<p>Tua ciangin khuapi sungah thu a piang hi.</p>"
        "<p>More: Lei ah daitak in kivui ~ Sia Kam Thang</p>"
        "<p>Read more</p>"
        "<p>Pasian in mite a huh hi.</p>"
    )

    assert len(blocks) == 2
    assert all(not block.lower().startswith(("more:", "read more")) for block in blocks)


def test_news_paragraph_openers_carry_evidence(news_engine: Engine) -> None:
    result = learn_layers(news_engine, sources=["zomidaily"])

    with news_engine.connect() as conn:
        patterns = conn.execute(
            text(
                "SELECT pattern_id, frequency, evidence_ids FROM grammar_patterns "
                "WHERE book = 'PARAGRAPH_OPENER' "
                "AND pattern_id LIKE 'disc_p_zomidaily_%'"
            )
        ).fetchall()
        evidence = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT fact_key FROM foundation_evidence "
                    "WHERE fact_key LIKE 'grammar:pattern:disc_p_zomidaily_%'"
                )
            )
        }

    assert patterns, "the repeated opener should be learned"
    assert result["openers_total"] >= 1
    for pattern_id, frequency, evidence_ids in patterns:
        assert frequency >= 5
        ids = json.loads(evidence_ids)
        assert ids, f"{pattern_id} has no evidence row"
        assert f"grammar:pattern:{pattern_id}" in evidence


def test_opener_slug_collision_keeps_one_row_and_reports_it() -> None:
    """``tua-ciangin`` and ``tua ciangin`` normalize to the same pattern id.

    The hyphen is interior, so the tokenizer keeps it as one unigram while the
    two-word form is a bigram; both slug to ``tua_ciangin``. The writer must
    keep one row (the stronger marker) and count the drop instead of letting
    the later upsert overwrite the earlier one silently.
    """
    manager, path = _tmp_engine()
    try:
        _create_articles(manager.engine)
        spaced = "Tua ciangin khuapi sungah thu a piang hi. Pasian in mite a huh hi."
        content = (
            "<p>Tua-ciangin khuapi sungah thu a piang hi. "
            "Pasian in mite a huh hi. Ama in lungdamna a pia hi.</p>"
            f"<p>{spaced}</p><p>{spaced}</p>"
        )
        with manager.engine.begin() as conn:
            for n in range(1, 13):
                conn.execute(
                    text(
                        "INSERT INTO articles "
                        "(namespace, language, title, content, link) "
                        "VALUES ('news', 'zolai', :title, :content, :link)"
                    ),
                    {
                        "title": f"Thu {n}",
                        "content": content,
                        "link": f"https://zomidaily.example/news/{n}",
                    },
                )

        result = learn_layers(manager.engine, sources=["zomidaily"])
        stats = result["per_source"]["zomidaily"]
        assert stats["collisions"] == 1

        with manager.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT pattern, frequency FROM grammar_patterns "
                    "WHERE book = 'PARAGRAPH_OPENER' "
                    "AND pattern_id = 'disc_p_zomidaily_tua_ciangin'"
                )
            ).fetchall()

        assert len(rows) == 1, "the slug collision must not create two rows"
        assert rows[0][0] == "tua ciangin", "the stronger bigram was kept"
        assert rows[0][1] == 24
    finally:
        _cleanup(manager, path)


def test_dry_run_writes_nothing(bible_engine: Engine) -> None:
    with bible_engine.connect() as conn:
        before = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns")
        ).scalar()

    result = learn_layers(bible_engine, sources=["bible"], dry_run=True)
    assert result["layers_total"] > 0
    assert result["per_source"]["bible"]["layers_written"] == 0

    with bible_engine.connect() as conn:
        table = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='corpus_learning_layers'"
            )
        ).first()
        after = conn.execute(text("SELECT COUNT(*) FROM grammar_patterns")).scalar()

    assert table is None, "dry-run must not create the layers table"
    assert after == before
    assert result["per_source"]["bible"]["patterns_written"] == 0


def test_rerun_is_idempotent(news_engine: Engine) -> None:
    first = learn_layers(news_engine, sources=["zomidaily"])
    with news_engine.connect() as conn:
        layers_first = conn.execute(
            text("SELECT COUNT(*) FROM corpus_learning_layers")
        ).scalar()
        patterns_first = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE book = 'PARAGRAPH_OPENER'")
        ).scalar()
        evidence_first = conn.execute(
            text(
                "SELECT COUNT(*) FROM foundation_evidence "
                "WHERE method = 'paragraph_discourse_learning'"
            )
        ).scalar()

    second = learn_layers(news_engine, sources=["zomidaily"])
    with news_engine.connect() as conn:
        layers_second = conn.execute(
            text("SELECT COUNT(*) FROM corpus_learning_layers")
        ).scalar()
        patterns_second = conn.execute(
            text("SELECT COUNT(*) FROM grammar_patterns WHERE book = 'PARAGRAPH_OPENER'")
        ).scalar()
        evidence_second = conn.execute(
            text(
                "SELECT COUNT(*) FROM foundation_evidence "
                "WHERE method = 'paragraph_discourse_learning'"
            )
        ).scalar()

    assert layers_second == layers_first
    assert patterns_second == patterns_first
    assert evidence_second == evidence_first
    assert first["per_source"]["zomidaily"]["patterns_written"] >= 1
    assert second["per_source"]["zomidaily"]["patterns_written"] == 0
    assert second["per_source"]["zomidaily"]["patterns_updated"] >= 1


def test_wiki_run_does_not_delete_other_source_openers(news_engine: Engine) -> None:
    """Stale cleanup is scoped to its own ``disc_p_<source>_*`` namespace."""
    learn_layers(news_engine, sources=["zomidaily"])
    with news_engine.connect() as conn:
        keep = conn.execute(
            text(
                "SELECT pattern_id FROM grammar_patterns "
                "WHERE pattern_id LIKE 'disc_p_zomidaily_%'"
            )
        ).fetchall()
    assert keep

    with news_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE wiki_content (id INTEGER PRIMARY KEY, title TEXT, "
                "content TEXT, source_path TEXT, wiki_category TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO wiki_content (title, content, source_path, wiki_category) "
                "VALUES ('Zolai', :content, 'wiki/zolai.md', 'language')"
            ),
            {
                "content": (
                    "# Zolai\n\n"
                    "Zolai pen Tedim Lai a kizang hi. Pasian in mite a it hi. "
                    "Tua pen thupha ahi hi.\n\n"
                    "Khamtung gamah Zolai a kizang hi. Mipite in la a sa hi. "
                    "Ni in gam a vak hi.\n\n"
                    "Lai siangtho pen Zolai-in a kigelh hi. Topa in a kem hi. "
                    "Tui in gam a luang hi.\n\n"
                    "Sanginn in Zolai a hilh hi. Naupang in la a ngai hi. "
                    "Khuapi in mei a khu hi.\n"
                )
            },
        )

    learn_layers(news_engine, sources=["wiki"])

    with news_engine.connect() as conn:
        after = conn.execute(
            text(
                "SELECT COUNT(*) FROM grammar_patterns "
                "WHERE pattern_id LIKE 'disc_p_zomidaily_%'"
            )
        ).scalar()

    assert after == len(keep)


# ── tokenizer ───────────────────────────────────────────────────────────────


def test_tokenizer_keeps_accented_zolai_words() -> None:
    from zolai.shared.text import tokenize_words

    tokens = tokenize_words("Gal Ngam masà a, diangkhià ngeingai; 'Zolai-in' a kigelh hi.")
    assert "masà" in tokens
    assert "diangkhià" in tokens
    assert "zolai-in" in tokens


# ── clause classifier regressions ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Pasian in leitung a bawl hi.", "ergative"),
        # ``Nading`` fuses the marker into a longer word — not future.
        ("Nading khatvei a hong pai hi.", "sov"),
        # ``keimah`` contains "kei" but it is not a negation.
        ("Keimah in ka sem hi.", "ergative"),
        # Question wins over the ergative form.
        ("Pasian in leitung a bawl hiam?", "question"),
        # ``lo ding`` is the negated future.
        ("Ka pai lo ding hi.", "negation"),
        # Case-insensitive quotative.
        ("A pa in, 'Ka pai ding hi,' Ci Hi.", "quotative"),
        # Attached completive suffix.
        ("Ama in ka hmuksak hi.", "past_completed"),
        # Hyphenated clitic still counts as a boundary.
        ("A hong pai ding-in ka ngak hi.", "future"),
    ],
)
def test_classify_clause_word_boundaries(text: str, expected: str) -> None:
    assert classify_clause(text) == expected
