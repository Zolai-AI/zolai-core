"""The wiki FTS5 pass must keep its BM25 ranking.

Every FTS5 row used to be scored a constant 0.95, and the final stable sort
then kept rowid order — so "best match first" was really "insertion order
first", and every FTS hit tied the exact-match passes.  ``bm25()`` is now the
ORDER BY and the score is derived from it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from zolai.rag.retrieve import FTS_SCORE_FLOOR, FTS_SCORE_SPAN, UnifiedRetriever


@pytest.fixture()
def engine(tmp_path, monkeypatch) -> Engine:
    monkeypatch.setenv("ZOLAI_VECTOR_SEARCH", "0")
    eng = create_engine(f"sqlite:///{tmp_path / 'rag.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE wiki_content (id INTEGER PRIMARY KEY, title TEXT, content TEXT)"))
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE wiki_content_fts USING fts5("
                "title, content, content='wiki_content', content_rowid='id')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO wiki_content (id, title, content) VALUES "
                "(1, 'Pasians', 'pasian pasian pasian leitung'), "
                "(2, 'Pasians', 'pasian leitung'), "
                "(3, 'Gam', 'gam leitung')"
            )
        )
        conn.execute(text("INSERT INTO wiki_content_fts(wiki_content_fts) VALUES('rebuild')"))
    yield eng
    eng.dispose()


def test_fts_hits_are_ordered_and_scored_by_bm25(engine: Engine) -> None:
    wiki = [r for r in UnifiedRetriever(engine).search("pasian", limit=5) if r.source == "wiki"]

    assert [r.id for r in wiki] == ["wiki:1", "wiki:2"]
    assert wiki[0].score > wiki[1].score, "the denser document must rank first"
    assert all(r.score < 1.0 for r in wiki), "an FTS hit must not tie an exact match"
    assert FTS_SCORE_FLOOR <= wiki[-1].score <= FTS_SCORE_FLOOR + FTS_SCORE_SPAN
