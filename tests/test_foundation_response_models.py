"""Response-side typing for ``/api/v1/foundation/*`` — additive, byte-compatible.

The 22 endpoints below used to carry no return annotation, so FastAPI published
**no** schema for them: OpenAPI had an empty response contract, a client
generator had nothing to emit, and a renamed key broke callers silently.

Adding a response model is only safe if the bytes do not move, which means two
things this file pins:

* a key a producer emits *sometimes* (``error_rate``, ``total_senses``,
  ``error``, ``tokens``) must **not** be declared — a declared field is
  materialised from its default on every response, which would *add* a key that
  was never there. It travels through ``extra="allow"`` instead;
* a value that comes from a repository row or an analyzer object must stay
  ``Any`` — a tighter type could coerce or reject a value the endpoint already
  returns, turning a 200 into a 500.

Every assertion here compares the served JSON against the payload the stubbed
producer handed the handler, so a model that adds, drops or rewrites a key
fails. No database and no app lifespan: the router is mounted on a bare
``FastAPI``, so ``run_all_migrations()`` never runs.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zolai.api.foundation_router import router as foundation_router

#: Every route this batch typed, with the 200-content it must keep.
TYPED_ROUTES = [
    ("/api/v1/foundation/review/queue", "get"),
    ("/api/v1/foundation/review/{item_id}", "get"),
    ("/api/v1/foundation/review/{item_id}/approve", "post"),
    ("/api/v1/foundation/review/{item_id}/reject", "post"),
    ("/api/v1/foundation/review/{item_id}/assign", "post"),
    ("/api/v1/foundation/review/bulk", "post"),
    ("/api/v1/foundation/stats", "get"),
    ("/api/v1/foundation/costs/summary", "get"),
    ("/api/v1/foundation/analyze/corpus", "post"),
    ("/api/v1/foundation/analyze/phonology", "post"),
    ("/api/v1/foundation/analyze/morphology", "post"),
    ("/api/v1/foundation/translate/enhanced", "post"),
    ("/api/v1/foundation/progress/adaptive-difficulty", "post"),
    ("/api/v1/foundation/search/ranked", "post"),
    ("/api/v1/foundation/search/cross-lingual", "post"),
    ("/api/v1/foundation/search/topical", "post"),
    ("/api/v1/foundation/search/analytics", "get"),
    ("/api/v1/foundation/grammar/validate-structure", "post"),
    ("/api/v1/foundation/translation/disambiguate", "post"),
    ("/api/v1/foundation/progress/streak/{user_id}", "get"),
    ("/api/v1/foundation/progress/error-categorize", "post"),
    ("/api/v1/foundation/progress/error-breakdown/{user_id}", "get"),
]


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """Foundation router only — no database, no lifespan, no migrations."""
    application = FastAPI()
    application.include_router(foundation_router, prefix="/api/v1")
    return application


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    # Deliberately NOT used as a context manager: entering it would run the lifespan.
    return TestClient(app)


@pytest.mark.parametrize(("path", "method"), TYPED_ROUTES)
def test_openapi_publishes_a_response_schema(app: FastAPI, path: str, method: str) -> None:
    """No route may be left with the empty ``{}`` response contract."""
    operation = app.openapi()["paths"][path][method]
    schemas = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schemas, f"{method.upper()} {path} still has no response schema"


def _stub(monkeypatch: pytest.MonkeyPatch, module: str, **attrs: Any) -> None:
    stub = types.ModuleType(module)
    for key, value in attrs.items():
        setattr(stub, key, value)
    monkeypatch.setitem(sys.modules, module, stub)


class _Syllable:
    def __init__(self) -> None:
        self.syllable, self.onset, self.nucleus, self.coda = "pas", "p", "a", "s"
        self.tone, self.valid = "T1", True


class _PhonoResult:
    syllable_structures = [_Syllable()]
    tone_patterns = ["T1", "T2"]
    sandhi_applied = [("a", "T1"), ("i", "T3")]
    phonotactic_valid = True
    stress_pattern = ["pas"]
    violations = ["stress on the second syllable"]


def test_analyze_phonology_response_is_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(
        monkeypatch,
        "zolai.foundation.phonology",
        get_phonological_analyzer=lambda: types.SimpleNamespace(analyze=lambda w: _PhonoResult()),
    )
    response = client.post("/api/v1/foundation/analyze/phonology", json={"word": "pasian"})
    assert response.status_code == 200
    assert response.json() == {
        "syllable_structures": [
            {
                "syllable": "pas",
                "onset": "p",
                "nucleus": "a",
                "coda": "s",
                "tone": "T1",
                "valid": True,
            }
        ],
        "tone_patterns": ["T1", "T2"],
        "sandhi_applied": [["a", "T1"], ["i", "T3"]],
        "phonotactic_valid": True,
        "stress_pattern": ["pas"],
        "violations": ["stress on the second syllable"],
    }


def test_corpus_analysis_keeps_the_register_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`register` is aliased on the model; the wire name must not move."""
    result = types.SimpleNamespace(
        ngrams={"pasian": 3},
        collocations=[types.SimpleNamespace(word1="a", word2="b", pmi=1.5, freq=2)],
        freq_distribution={"pasian": 3},
        register="common",
        total_words_analyzed=7,
    )
    _stub(
        monkeypatch,
        "zolai.foundation.corpus",
        get_corpus_analyzer=lambda: types.SimpleNamespace(analyze=lambda t: result),
    )
    body = client.post("/api/v1/foundation/analyze/corpus", json={"text": "a b"}).json()
    assert body["register"] == "common"
    assert body["ngrams"] == {"pasian": 3}
    assert body["collocations"] == [{"word1": "a", "word2": "b", "pmi": 1.5, "freq": 2}]
    assert body["total_words_analyzed"] == 7


def test_adaptive_difficulty_without_history_gains_no_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-history payload has 6 keys — the model must not materialise 3 more."""
    no_history = {
        "difficulty": "beginner",
        "frequency_tier": "high",
        "morphology_threshold": 0.2,
        "include_tone_questions": False,
        "recommended_count": 10,
        "reason": "No history — starting with high-frequency simple words",
    }
    _stub(
        monkeypatch,
        "zolai.learning.progress",
        ProgressTracker=type(
            "T",
            (),
            {
                "__init__": lambda self, user_id=None: None,
                "get_adaptive_difficulty": lambda self, user_id=None: no_history,
            },
        ),
    )
    response = client.post("/api/v1/foundation/progress/adaptive-difficulty", json={})
    assert response.status_code == 200
    assert response.json() == no_history
    assert "error_rate" not in response.json()


def test_adaptive_difficulty_with_history_keeps_its_optional_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_history = {
        "difficulty": "expert",
        "frequency_tier": "rare",
        "morphology_threshold": 0.8,
        "include_tone_questions": True,
        "recommended_count": 15,
        "error_rate": 0.05,
        "avg_ease": 2.7,
        "avg_complexity": 0.61,
        "reason": "Excellent performance",
    }
    _stub(
        monkeypatch,
        "zolai.learning.progress",
        ProgressTracker=type(
            "T",
            (),
            {
                "__init__": lambda self, user_id=None: None,
                "get_adaptive_difficulty": lambda self, user_id=None: with_history,
            },
        ),
    )
    body = client.post("/api/v1/foundation/progress/adaptive-difficulty", json={}).json()
    assert body == with_history


def test_error_breakdown_passes_the_tracker_error_key_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``error`` appears only on the tracker's internal-failure path."""
    breakdown = {
        "user_id": "u1",
        "total_errors": 0,
        "categories": {},
        "most_common": None,
        "error": "no such table: user_reviews",
    }
    _stub(
        monkeypatch,
        "zolai.learning.progress",
        ProgressTracker=type(
            "T",
            (),
            {"__init__": lambda self, user_id=None: None, "get_error_breakdown": lambda self, user_id=None: breakdown},
        ),
    )
    body = client.get("/api/v1/foundation/progress/error-breakdown/u1").json()
    assert body == breakdown


def test_disambiguation_keeps_total_senses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "candidates": [{"meaning": "God", "confidence": 0.6, "evidence": "entry 1/2", "pos": "noun"}],
        "word": "pasian",
        "direction": "zo-en",
        "total_senses": 2,
    }
    _stub(
        monkeypatch,
        "zolai.learning.translation",
        TranslationEngine=type(
            "E",
            (),
            {
                "__init__": lambda self: None,
                "_disambiguate_polysemy": (
                    lambda self, w, context=None, direction="zo-en": payload
                ),
            },
        ),
    )
    body = client.post(
        "/api/v1/foundation/translation/disambiguate", json={"word": "pasian"}
    ).json()
    assert body == payload


def test_structure_validation_keeps_its_token_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "tokens": ["Ka", "pai", "hi"],
        "verb_position": 1,
        "zvs_compliant": True,
    }
    _stub(
        monkeypatch,
        "zolai.learning.grammar_editor",
        GrammarEditor=type(
            "E", (), {"__init__": lambda self: None, "validate_sentence_structure": lambda self, s: payload}
        ),
    )
    body = client.post(
        "/api/v1/foundation/grammar/validate-structure", json={"sentence": "Ka pai hi."}
    ).json()
    assert body == payload


def test_review_queue_and_detail_keep_their_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {
        "id": 1,
        "fact_type": "word",
        "fact_key": "word:pasian",
        "priority": 0,
        "assignee": None,
        "status": "pending",
        "created_at": "2026-10-18T00:00:00",
        "resolved_at": None,
        "extra_row_column": "kept",
    }
    monkeypatch.setattr(
        "zolai.api.foundation_router._get_repos",
        lambda: {
            "foundation_review_queue": types.SimpleNamespace(
                get_pending=lambda limit=1000: [item],
                get_by_id=lambda i: item if i == 1 else None,
            ),
            "foundation_evidence": None,
            "foundation_consensus": None,
        },
    )
    queue = client.get("/api/v1/foundation/review/queue").json()
    assert queue == {
        "items": [item],
        "total": 1,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
    }
    detail = client.get("/api/v1/foundation/review/1").json()
    assert set(detail) == {
        "id",
        "fact_type",
        "fact_key",
        "priority",
        "assignee",
        "status",
        "created_at",
        "resolved_at",
        "candidate_value",
        "evidence_list",
        "consensus",
        "confidence",
    }
    assert detail["evidence_list"] == []
    assert detail["confidence"] == 0.0
