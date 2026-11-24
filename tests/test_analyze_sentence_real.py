"""core#26 — ``/analyze/sentence`` is backed by the real FoundationAnalyzer.

The previous retriever method returned a string ``"placeholder - full analysis
requires discovery pipeline"`` and no POS/grammar. These tests pin the frozen
response contract, the additive provenance keys, and the API pass-through for
the exact sentence named in the issue.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.rag_router import AnalyzeSentenceResponse
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table
from zolai.foundation import FoundationAnalyzer
from zolai.rag import retrieve
from zolai.rag.retrieve import UnifiedRetriever

SENTENCE = "Pasian in leitung a piangsak hi."
FROZEN_KEYS = ("text", "tokens", "pos", "grammar", "entities")
GRAMMAR_KEYS = {
    "sov_valid",
    "ergative_present",
    "negation_type",
    "question_type",
    "tense",
    "grammar_patterns_matched",
    "zvs_compliant",
    "zvs_violations",
}


@pytest.fixture()
def retriever() -> Iterator[UnifiedRetriever]:
    engine: Engine = create_engine("sqlite://")
    yield UnifiedRetriever(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'analyze_sentence_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


def test_analyze_sentence_delegates_to_foundation_analyzer(
    retriever: UnifiedRetriever, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retriever must call FoundationAnalyzer, not reimplement tokenization."""
    seen: list[str] = []
    original = FoundationAnalyzer.analyze_sentence

    def spy(self: FoundationAnalyzer, sentence: str):  # noqa: ANN001, ANN201
        seen.append(sentence)
        return original(self, sentence)

    monkeypatch.setattr(FoundationAnalyzer, "analyze_sentence", spy)
    result = retriever.analyze_sentence(SENTENCE)

    assert seen == [SENTENCE]
    assert "placeholder" not in str(result).lower()


def test_frozen_keys_types_and_grammar_flags(retriever: UnifiedRetriever) -> None:
    result = retriever.analyze_sentence(SENTENCE)

    assert all(key in result for key in FROZEN_KEYS)
    assert isinstance(result["text"], str)
    assert isinstance(result["tokens"], list)
    assert all(isinstance(token, str) for token in result["tokens"])
    assert isinstance(result["pos"], list)
    assert all(isinstance(tag, str) for tag in result["pos"])
    assert isinstance(result["grammar"], list)
    assert all(isinstance(item, dict) for item in result["grammar"])
    assert isinstance(result["entities"], list)
    assert all(isinstance(entity, dict) for entity in result["entities"])

    assert result["tokens"], "expected real tokens"
    assert result["pos"], "expected real POS tags"
    grammar = result["grammar"][0]
    assert GRAMMAR_KEYS <= grammar.keys()
    assert grammar["sov_valid"] is True
    assert grammar["ergative_present"] is True
    assert grammar["grammar_patterns_matched"], "expected a matched SOV/ergative pattern"
    assert "placeholder" not in str(result).lower()


def test_entities_are_empty_with_explicit_provenance(retriever: UnifiedRetriever) -> None:
    result = retriever.analyze_sentence(SENTENCE)

    assert result["entities"] == []
    assert result["entities_source"] == "unavailable"
    assert result["analysis_source"] == "foundation_analyzer"
    assert result["analysis"]["source"] == "foundation_analyzer"
    assert result["token_count"] == len(result["tokens"])
    assert result["language"] == "zolai"


def test_analyzer_is_a_lazy_process_singleton() -> None:
    assert retrieve._get_sentence_analyzer() is retrieve._get_sentence_analyzer()


def test_empty_input_stays_tolerant(retriever: UnifiedRetriever) -> None:
    result = retriever.analyze_sentence("   ")

    assert result["tokens"] == []
    assert result["pos"] == []
    assert result["grammar"] == []
    assert result["entities"] == []
    assert result["entities_source"] == "unavailable"
    assert "placeholder" not in str(result).lower()


def test_api_response_has_no_placeholder_and_passes_provenance(
    client: TestClient, auth_db: DatabaseManager
) -> None:
    response = client.post("/api/v1/analyze/sentence", json={"text": SENTENCE})

    assert response.status_code == 200, response.text
    assert "placeholder" not in response.text.lower()
    body = response.json()
    assert body["text"] == SENTENCE
    assert body["entities"] == []
    assert body["entities_source"] == "unavailable"
    assert body["analysis_source"] == "foundation_analyzer"
    assert body["analysis"]["status"] == "analyzed"
    assert body["token_count"] == len(body["tokens"])
    assert body["language"] == "zolai"
    assert body["grammar"][0]["sov_valid"] is True
    assert body["grammar"][0]["ergative_present"] is True
    assert body["grammar"][0]["grammar_patterns_matched"]

    # Frozen names/types are re-checked by the response model itself.
    model = AnalyzeSentenceResponse(**body)
    assert model.tokens == body["tokens"]
    assert model.grammar == body["grammar"]
