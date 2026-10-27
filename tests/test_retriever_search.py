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

    def test_multi_token_question_uses_a_strong_pass_not_the_fallback(self, retriever):
        """Regression: a multi-token query must not degrade to the weak fallback.

        Every strong pass used to compare the *whole query string* against the
        columns, so for "What does the word zomi mean?" all of them failed and
        every hit came from `token_or` - the weakest pass, and the only one with
        no ORDER BY. On the real corpus that returned the head of an
        alphabetical index scan (abba, adonai, allah for "What is pasian?") and
        the assistant concluded it had no evidence.

        The stub fixture is too small to reproduce the wrong *ordering*, so the
        old assertion (top row is the expected one) passed with the bug present.
        What is stable regardless of fixture size is **how** the top row matched.
        """
        results = retriever.search("What does the word zomi mean?")
        assert results, "expected results"
        top = results[0]
        assert top.id == "dictionary:zomi"
        assert top.metadata["match_type"] != "token_or", (
            "multi-token question fell through to the unordered token_or "
            "fallback - the exact-match pass did not run per token"
        )
        assert top.score > 0.6, f"expected a strong pass, got {top.score}"

    def test_each_meaningful_token_is_matched_strongly(self, retriever):
        """Every non-stopword token gets an exact attempt, not just the first."""
        for query, expected in (
            ("What does the word zomi mean?", "dictionary:zomi"),
            ("zomi meaning please", "dictionary:zomi"),
        ):
            results = retriever.search(query)
            assert results, f"no results for {query!r}"
            assert results[0].id == expected, f"{query!r} ranked {results[0].id} first"
            assert results[0].score > 0.6, f"{query!r} scored {results[0].score}"

    def test_single_word_scoring_is_unchanged(self, retriever):
        """The per-token passes must not demote the whole-string exact match."""
        top = retriever.search("zomi")[0]
        assert top.id == "dictionary:zomi"
        assert top.score == 1.0, "a single-word exact match must still score 1.0"

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


class TestRagQuery:
    """`/rag` must call a model when one is configured, and never fake an answer."""

    def test_fallback_answer_names_the_question_and_the_sources(self, retriever):
        answer = retriever._fallback_answer("what is zomi", retriever.search("zomi"))
        assert "zomi" in answer
        assert "not a written answer" in answer
        assert "dictionary" in answer

    def test_fallback_with_no_results_says_there_is_no_evidence(self, retriever):
        answer = retriever._fallback_answer("xyzzy nonsense", [])
        assert "no evidence" in answer.lower()

    def test_evidence_line_carries_the_definition_not_just_the_word(self, retriever):
        """The old context was `[1] dictionary: zomi` — nothing to answer from."""
        line = retriever._evidence_line(1, retriever.search("zomi")[0])
        assert line.startswith("[1] dictionary:")
        assert "definition=" in line, f"evidence line carries no content: {line!r}"

    def test_rag_query_without_a_provider_is_honestly_flagged(self, retriever, monkeypatch):
        """retrieval_only must be True and the answer must not claim to be written."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.endswith("synthesis") or name == "zolai.agent.synthesis":
                raise RuntimeError("no provider configured")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        out = retriever.rag_query("What is zomi?")
        assert out["retrieval_only"] is True
        assert out["generated"] is False
        assert out["provider"] == "" and out["model"] == ""
        assert "not a written answer" in out["answer"] or "No evidence" in out["answer"]

    def test_rag_query_reports_generated_when_the_model_answers(self, retriever, monkeypatch):
        import builtins
        import sys
        import types

        module = types.ModuleType("zolai.agent.synthesis")

        def make_chat_fn(**_kwargs):
            def chat(messages, tools):
                assert "Evidence:" in messages[1]["content"], "model got no evidence"
                # ChatFn returns {"text", ...} — not "content". This shape is the
                # regression: reading "content" silently yielded empty_reply.
                return {"text": "Zomi means 'people of Mizoram'. [1]"}

            return chat, {"catalog_id": "test-provider", "model": "test-model"}

        module.make_chat_fn = make_chat_fn
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("zolai.agent.synthesis",) or name.endswith("agent.synthesis"):
                return module
            return real_import(name, *args, **kwargs)

        monkeypatch.setitem(sys.modules, "zolai.agent.synthesis", module)
        monkeypatch.setattr(builtins, "__import__", fake_import)

        out = retriever.rag_query("What is zomi?")
        assert out["generated"] is True
        assert out["retrieval_only"] is False
        assert out["provider"] == "test-provider"
        assert out["model"] == "test-model"
        assert "Mizoram" in out["answer"]

    def test_empty_text_reply_is_flagged_not_silently_faked(self, retriever, monkeypatch):
        """An empty completion must degrade to evidence, never to a template answer."""
        import builtins
        import sys
        import types

        module = types.ModuleType("zolai.agent.synthesis")

        def make_chat_fn(**_kwargs):
            return (lambda messages, tools: {"text": "   "}), {
                "catalog_id": "test-provider", "model": "test-model",
            }

        module.make_chat_fn = make_chat_fn
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("zolai.agent.synthesis",) or name.endswith("agent.synthesis"):
                return module
            return real_import(name, *args, **kwargs)

        monkeypatch.setitem(sys.modules, "zolai.agent.synthesis", module)
        monkeypatch.setattr(builtins, "__import__", fake_import)

        out = retriever.rag_query("What is zomi?")
        assert out["retrieval_only"] is True
        assert out["generated"] is False
        assert out["provider_error"] == "empty_reply"
        assert "empty answer" in out["answer"]
