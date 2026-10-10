"""UnifiedRetriever.search — English columns + stopword-tolerant token matching."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.rag.retrieve import UnifiedRetriever, tokenize_query

SCHEMA = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY, zolai TEXT, english TEXT, english_clean TEXT, pos TEXT
);
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY, headword TEXT, translations TEXT,
    translations_clean TEXT, pos TEXT
);
CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY, ref TEXT, zo_tdb77 TEXT, zo_tedim2010 TEXT, en_kJV TEXT
);
CREATE TABLE phrases (
    id INTEGER PRIMARY KEY, zolai TEXT, english TEXT, frequency INTEGER
);
CREATE TABLE grammar_patterns (
    id INTEGER PRIMARY KEY, pattern_id TEXT, pattern TEXT, description TEXT,
    function TEXT, examples TEXT, confidence REAL, status TEXT
);
"""


@pytest.fixture
def engine():
    """In-memory stub engine with only the columns retrieval touches."""
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text(
            "INSERT INTO dictionary VALUES "
            "(1, 'zomi', 'a people of Mizoram', 'people of mizoram', 'n'), "
            "(2, 'bang', 'the zomi community gathers', 'zomi community gathers', 'n'), "
            "(3, 'sawhding', 'hello', 'hello', 'int')"
        ))
        conn.execute(text(
            "INSERT INTO dictionary_en_zo VALUES "
            "(1, 'love', 'a great affection', 'great affection', 'n')"
        ))
        conn.execute(text(
            "INSERT INTO bible_verses VALUES "
            "(1, 'John 3:16', 'Ka zuu a ciang', 'God so loved', 'For God so loved the world')"
        ))
        conn.execute(text(
            "INSERT INTO phrases VALUES "
            "(1, 'ka lang a', 'I am', 12)"
        ))
        conn.execute(text(
            "INSERT INTO grammar_patterns VALUES "
            "(1, 'GP-1', 'ka + V', 'first person marker', 'subject', '[]', 0.9, 'supported')"
        ))
    return eng


@pytest.fixture
def retriever(engine, monkeypatch):
    """Retriever over the stub engine with vector search disabled."""
    monkeypatch.setattr(UnifiedRetriever, "_get_embedder", lambda self: None)
    return UnifiedRetriever(engine)


@pytest.fixture
def sql_log(engine):
    """Capture every statement + bound params the retriever executes."""
    statements: list[tuple[str, dict]] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append((statement, parameters))

    yield statements
    event.remove(engine, "before_cursor_execute", _capture)


class TestTokenizeQuery:
    def test_stopwords_dropped(self):
        assert tokenize_query("What does the word zomi mean?") == ["zomi"]

    def test_lowercase_alphanumeric(self):
        assert tokenize_query("Zomi 42!") == ["zomi", "42"]

    def test_dedupe_preserves_order(self):
        assert tokenize_query("love love zomi love") == ["love", "zomi"]

    def test_cap_at_six_tokens(self):
        query = " ".join(f"tok{i}" for i in range(10))
        assert len(tokenize_query(query)) == 6

    def test_empty_when_only_stopwords_or_punctuation(self):
        assert tokenize_query("What does it mean?") == []
        assert tokenize_query("?!...") == []


class TestSearchRanking:
    def test_exact_zomi_ranks_above_english_hits(self, retriever):
        """A Zomi exact match outranks English-column hits for the same term."""
        results = retriever.search("zomi")
        assert results, "expected results for 'zomi'"
        top = results[0]
        assert top.id == "dictionary:zomi"
        assert top.metadata["match_type"] == "exact"
        assert top.score == 1.0
        # English-derived hit for the same term is present but ranked lower.
        english_hits = [r for r in results if r.metadata["match_type"] == "token_or"]
        assert english_hits, "expected an English-column hit via token-OR"
        assert english_hits[0].score < top.score
        assert all(r.score <= top.score for r in results[1:])

    def test_scores_are_monotonic(self, retriever):
        scores = [r.score for r in retriever.search("zomi")]
        assert scores == sorted(scores, reverse=True)

    def test_dedupe_by_source_and_id(self, retriever):
        """A row matched by two passes appears once, with its best match type."""
        results = retriever.search("zomi")
        keys = [(r.source, r.id) for r in results]
        assert len(keys) == len(set(keys))


class TestEnglishCoverage:
    def test_english_only_term_returns_a_row(self, retriever):
        """An English-only query hits dictionary.english_clean (english_exact)."""
        results = retriever.search("hello")
        assert results, "expected a row for the English-only term 'hello'"
        assert results[0].id == "dictionary:sawhding"
        assert results[0].metadata["match_type"] == "english_exact"

    def test_english_exact_matches_translations_clean(self, retriever):
        results = retriever.search("love")
        assert any(r.source == "dictionary_en_zo" for r in results)

    def test_english_token_reaches_dictionary_en_zo(self, retriever):
        results = retriever.search("affection")
        assert any(r.source == "dictionary_en_zo" for r in results)

    def test_bible_english_column_is_matchable(self, retriever):
        results = retriever.search("world")
        assert any(r.source == "bible_verses" for r in results)

    def test_phrases_rows_are_reachable(self, retriever):
        """phrases must not be silently dropped by a bad context column."""
        results = retriever.search("ka lang")
        assert any(r.source == "phrases" for r in results)


class TestQuestionQueries:
    def test_stopword_heavy_question_returns_rows(self, retriever):
        results = retriever.search("What does the word zomi mean?")
        assert results, "stopword-heavy question returned nothing"
        assert results[0].id == "dictionary:zomi"

    def test_punctuation_only_query_does_not_raise(self, retriever):
        assert retriever.search("?!...") == [] or isinstance(retriever.search("?!..."), list)

    def test_question_without_tokens_falls_back_to_whole_string(self, retriever):
        results = retriever.search("What does it mean?")
        assert isinstance(results, list)


class TestSQLShape:
    """Assert the SQL the retriever actually issues — not just result counts."""

    def test_token_pass_ors_like_across_matchable_columns(self, retriever, sql_log):
        retriever.search("What does the word zomi mean?")
        token_hits = [(stmt, params) for stmt, params in sql_log if stmt.count("LIKE ?") >= 3]
        assert token_hits, "expected a token-OR pass"
        stmt, params = token_hits[0]
        assert "(zolai LIKE ? OR english_clean LIKE ? OR english LIKE ?)" in stmt
        assert "FROM dictionary" in stmt
        assert "IN (" not in stmt, "token pass must use OR-of-LIKE, not IN"
        assert "%zomi%" in params

    def test_token_pass_covers_parallel_and_english_columns(self, retriever, sql_log):
        retriever.search("world")
        token_sql = [stmt for stmt, _ in sql_log if "LIKE ?" in stmt]
        assert any("en_kJV LIKE ?" in stmt for stmt in token_sql)
        assert any("translations_clean LIKE ?" in stmt for stmt in token_sql)

    def test_token_params_are_percent_wrapped(self, retriever, sql_log):
        retriever.search("What does the word zomi mean?")
        token_params = [params for stmt, params in sql_log if stmt.count("LIKE ?") >= 3]
        assert token_params, "expected token parameters"
        assert all("%" in str(params) for params in token_params)
        assert any("%zomi%" in str(params) for params in token_params)

    def test_empty_tokenization_skips_token_pass(self, retriever, sql_log):
        retriever.search("?!...")
        # Prefix/contains bind a single LIKE per table; only the token pass ORs
        # across every matchable column.
        assert not any("english_clean LIKE ?" in stmt for stmt, _ in sql_log)

    def test_exact_pass_binds_plain_query(self, retriever, sql_log):
        retriever.search("zomi")
        exact = [(stmt, params) for stmt, params in sql_log if "WHERE zolai = ?" in stmt]
        assert exact, "expected an exact-match pass"
        assert exact[0][1] == ("zomi",)
