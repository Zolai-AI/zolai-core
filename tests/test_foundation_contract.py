"""W1 contract hardening — the 11 typed ``/api/v1/foundation/*`` POST endpoints.

Before this wave each of these routes took a bare ``request: dict`` and read its
keys by hand, so:

* OpenAPI published ``{"additionalProperties": true, "type": "object",
  "title": "Request"}`` — an opaque body, so an MCP tool had no schema to
  generate and a client could not discover a single field;
* a missing or misspelled key silently fell back to a hard-coded default.

What is asserted here, per the wave's done-when:

- every one of the 11 routes declares a real object schema with named,
  described properties (**the regression guard** — it fails on the pre-change
  source; the throwaway proof run recorded 11/11 opaque);
- a request missing the required field is a **422**, not a silent default;
- a **valid** request still reaches the handler (no regression), the empty
  string still raises the original **400** where the handler had that guard, and
  the optional keys keep their exact defaults;
- :data:`FOUNDATION_TOOL_CONTRACTS` covers all 11, and each declared
  method/path matches a real ``@router.post(...)`` route, carries a
  frozen-vocabulary scope, and agrees with ``rbac.classify_route``.

Deliberately DB-free: a minimal app carrying only this router is mounted, so
nothing here needs the canonical database (which is 0 bytes on a dev box — see
.pcore/findings-2026-10-18.md). "Reaches the handler" is proved by replacing
the engines with a sentinel that records the call, so the test cannot be
satisfied by a status code that a stub could fake.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from zolai.api import rbac
from zolai.api.auth import VALID_ACTIONS
from zolai.api.foundation_router import (
    FOUNDATION_TOOL_CONTRACTS,
    PUBLIC_SCOPE,
    AdaptiveDifficultyRequest,
    CorpusAnalysisRequest,
    CrossLingualSearchRequest,
    DisambiguationRequest,
    EnhancedTranslationRequest,
    ErrorCategorizationRequest,
    FoundationToolContract,
    MorphologyAnalysisRequest,
    PhonologyAnalysisRequest,
    RankedSearchRequest,
    StructureValidationRequest,
    TopicalSearchRequest,
)
from zolai.api.foundation_router import (
    router as foundation_router,
)

#: The 11 routes this wave typed: router-relative path -> (request model, the
#: field the handler used to default to "" and then reject with a 400).
TYPED_ROUTES: dict[str, tuple[type[BaseModel], str]] = {
    "/analyze/corpus": (CorpusAnalysisRequest, "text"),
    "/analyze/phonology": (PhonologyAnalysisRequest, "word"),
    "/analyze/morphology": (MorphologyAnalysisRequest, "word"),
    "/translate/enhanced": (EnhancedTranslationRequest, "text"),
    "/progress/adaptive-difficulty": (AdaptiveDifficultyRequest, "user_id"),
    "/search/ranked": (RankedSearchRequest, "query"),
    "/search/cross-lingual": (CrossLingualSearchRequest, "query"),
    "/search/topical": (TopicalSearchRequest, "theme"),
    "/grammar/validate-structure": (StructureValidationRequest, "sentence"),
    "/translation/disambiguate": (DisambiguationRequest, "word"),
    "/progress/error-categorize": (ErrorCategorizationRequest, "original"),
}

#: Minimal valid body per route. ``/progress/adaptive-difficulty`` is the one
#: endpoint with **no** required key — ``user_id`` has always defaulted to
#: "default" — so an empty body is valid there and it is excluded from the 422
#: check below.
VALID_BODIES: dict[str, dict[str, Any]] = {
    "/analyze/corpus": {"text": "a b c"},
    "/analyze/phonology": {"word": "pasian"},
    "/analyze/morphology": {"word": "pasian"},
    "/translate/enhanced": {"text": "pasian"},
    "/progress/adaptive-difficulty": {},
    "/search/ranked": {"query": "pasian"},
    "/search/cross-lingual": {"query": "god"},
    "/search/topical": {"theme": "love"},
    "/grammar/validate-structure": {"sentence": "Ka pai hi."},
    "/translation/disambiguate": {"word": "dam"},
    "/progress/error-categorize": {"original": "pathian", "corrected": "pasian"},
}

#: Routes whose handler rejected an explicitly-empty string with 400. That guard
#: is kept on purpose, so "" must still be 400 (not 422) after typing.
EMPTY_STRING_STILL_400 = (
    "/analyze/corpus",
    "/analyze/phonology",
    "/analyze/morphology",
    "/translate/enhanced",
    "/search/ranked",
    "/search/cross-lingual",
    "/search/topical",
    "/grammar/validate-structure",
    "/translation/disambiguate",
    "/progress/error-categorize",
)


def full_path(rel: str) -> str:
    """Router-relative path -> the path ``server.py`` actually serves."""
    return f"/api/v1/foundation{rel}"


def _body_schema(spec: dict[str, Any], rel: str) -> dict[str, Any]:
    return spec["paths"][full_path(rel)]["post"]["requestBody"]["content"]["application/json"]["schema"]


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """Minimal app carrying only the foundation router (no DB, no lifespan)."""
    application = FastAPI()
    application.include_router(foundation_router, prefix="/api/v1")
    return application


@pytest.fixture(scope="module")
def spec(app: FastAPI) -> dict[str, Any]:
    return app.openapi()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    # Deliberately NOT used as a context manager: entering it would run the app
    # lifespan, i.e. run_all_migrations() against the canonical database.
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. The regression guard — real object schema with described properties
# ---------------------------------------------------------------------------

def test_every_typed_route_declares_a_real_object_schema(spec: dict[str, Any]) -> None:
    """Fails on the pre-change source, where all 11 bodies were opaque ``Request``."""
    schemas = spec["components"]["schemas"]
    opaque: list[str] = []
    wrong: list[str] = []

    for rel, (model, _required) in TYPED_ROUTES.items():
        ref = _body_schema(spec, rel).get("$ref")
        if not ref:
            opaque.append(rel)
            continue
        name = ref.rsplit("/", 1)[-1]
        if name != model.__name__:
            wrong.append(f"{rel}: body is {name}, expected {model.__name__}")
            continue
        published = schemas[name]
        if published.get("type") != "object":
            wrong.append(f"{rel}: {name} is not type=object")
            continue
        props = published.get("properties") or {}
        expected = set(model.model_fields)
        if not props:
            opaque.append(rel)
        elif set(props) != expected:
            wrong.append(f"{rel}: publishes {sorted(props)}, model declares {sorted(expected)}")

    assert not opaque, f"{len(opaque)}/11 routes publish an opaque body (no properties): {opaque}"
    assert not wrong, "\n".join(wrong)


def test_opaque_request_body_is_gone(spec: dict[str, Any]) -> None:
    """The old inline body was literally titled ``Request`` — none may remain."""
    still_opaque = [rel for rel in TYPED_ROUTES if _body_schema(spec, rel).get("title") == "Request"]
    assert not still_opaque, f"still publishing the opaque 'Request' body: {still_opaque}"


@pytest.mark.parametrize("rel", sorted(TYPED_ROUTES))
def test_every_field_has_a_genuine_description(spec: dict[str, Any], rel: str) -> None:
    """Descriptions become MCP tool parameter docs, so none may be filler."""
    ref = _body_schema(spec, rel)["$ref"]
    props = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]
    for name, prop in props.items():
        description = (prop.get("description") or "").strip()
        assert len(description) >= 25, f"{rel}.{name}: description is filler ({description!r})"
        assert description.lower() != name.lower(), f"{rel}.{name}: description just repeats the name"
    # The model itself must carry them too, not just the published schema.
    for name, field in TYPED_ROUTES[rel][0].model_fields.items():
        assert (field.description or "").strip(), f"{rel}.{name} has no description on the model"


# ---------------------------------------------------------------------------
# 2. Missing required field -> 422
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("rel", "omit"),
    [(rel, next(iter(VALID_BODIES[rel]))) for rel in sorted(VALID_BODIES) if VALID_BODIES[rel]],
)
def test_missing_required_field_is_422(client: TestClient, rel: str, omit: str) -> None:
    body = {k: v for k, v in VALID_BODIES[rel].items() if k != omit}
    response = client.post(full_path(rel), json=body)
    assert response.status_code == 422, f"{rel} without {omit!r}: {response.status_code} {response.text}"
    # The 422 must name the field, so a caller can act on it.
    assert any(omit in str(err.get("loc", "")) for err in response.json()["detail"]), response.text


def test_optional_only_endpoint_accepts_an_empty_body(client: TestClient) -> None:
    """``/progress/adaptive-difficulty`` never had a required key — still none."""
    assert set(AdaptiveDifficultyRequest.model_fields) == {"user_id"}
    assert not _required_fields(AdaptiveDifficultyRequest)
    assert client.post(full_path("/progress/adaptive-difficulty"), json={}).status_code != 422


@pytest.mark.parametrize("rel", sorted(EMPTY_STRING_STILL_400))
def test_explicit_empty_string_keeps_its_400(client: TestClient, rel: str) -> None:
    """The handler's own falsy guard is kept, so "" is still 400, not 422."""
    required = TYPED_ROUTES[rel][1]
    # Send the full valid body with just that one field emptied, so validation
    # passes and the *handler's* guard is what answers (error-categorize needs
    # both of its keys present to reach the guard).
    body = {**VALID_BODIES[rel], required: ""}
    response = client.post(full_path(rel), json=body)
    assert response.status_code == 400, f"{rel} with empty {required!r}: {response.status_code} {response.text}"


def _required_fields(model: type[BaseModel]) -> set[str]:
    return {name for name, field in model.model_fields.items() if field.is_required()}


@pytest.mark.parametrize("rel", sorted(VALID_BODIES))
def test_required_field_matches_the_old_falsy_guard(rel: str) -> None:
    """Exactly the keys the handler defaulted to "" and 400'd on are required now."""
    model, required = TYPED_ROUTES[rel]
    is_required = _required_fields(model)
    if VALID_BODIES[rel]:
        assert required in is_required, f"{rel}: {required!r} should be required"
    else:
        assert not is_required, f"{rel}: {rel} was never 400'd on a missing key, so nothing is required"
        assert required not in is_required


# ---------------------------------------------------------------------------
# 3. A valid request still reaches the handler (no regression)
# ---------------------------------------------------------------------------

_STUBBED_MODULES = (
    "zolai.foundation.corpus",
    "zolai.foundation.phonology",
    "zolai.foundation.morphology",
    "zolai.learning.translation",
    "zolai.learning.online_search",
    "zolai.learning.grammar_editor",
    "zolai.learning.progress",
)


def _install_stubs(seen: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Replace the engines with sentinels that record what the handler passed.

    Returns the modules that were in ``sys.modules`` *before* the swap, so the
    fixture can restore them.  Taking the snapshot after installing (the old
    behaviour) returned the stubs themselves and left every later test in the
    session importing ``ProgressTracker`` as a sentinel that answers
    ``{"category": "orthographic"}`` — the full suite failed 24 learning-engine
    and 29 online-search cases that pass in isolation.
    """
    saved = {name: sys.modules.get(name) for name in _STUBBED_MODULES}

    class _Result:
        def __init__(self) -> None:
            self.__dict__.update(
                ngrams={}, collocations=[], freq_distribution={}, register="common",
                total_words_analyzed=3, syllable_structures=[], tone_patterns=[],
                sandhi_applied=[], phonotactic_valid=True, stress_pattern=[], violations=[],
                segments=["pa", "sian"], directional=None, stem="pasian", aspect=None,
                particle=None, is_valid=True, compound_parts=[], prefix=None, suffix=None,
            )

    class _Corpus:
        def analyze(self, text: str) -> _Result:
            seen["/analyze/corpus"] = {"text": text}
            return _Result()

    class _Phonology:
        def analyze(self, word: str) -> _Result:
            seen["/analyze/phonology"] = {"word": word}
            return _Result()

    class _Morphology:
        def decompose(self, word: str) -> _Result:
            seen["/analyze/morphology"] = {"word": word}
            return _Result()

    class _Translation:
        def translate(self, text: str, direction: str = "auto", context: str | None = None) -> dict:
            seen["/translate/enhanced"] = {"text": text, "direction": direction, "context": context}
            return {"translation": "God", "confidence": 0.95, "tier": "dictionary"}

        def _disambiguate_polysemy(self, word: str, context: str | None = None,
                                   direction: str = "zo-en") -> dict:
            seen["/translation/disambiguate"] = {"word": word, "context": context, "direction": direction}
            return {"word": word, "candidates": [{"meaning": "well"}]}

    class _Search:
        def search_ranked(self, query: str, category: str | None = None, limit: int = 10) -> list:
            seen["/search/ranked"] = {"query": query, "category": category, "limit": limit}
            return [{"word": "pasian"}]

        def search_cross_lingual(self, query: str, source_lang: str = "en",
                                 target_lang: str = "zo", limit: int = 10) -> list:
            seen["/search/cross-lingual"] = {
                "query": query, "source_lang": source_lang, "target_lang": target_lang, "limit": limit,
            }
            return [{"zolai": "pasian"}]

        def search_topical(self, theme: str, limit: int = 10) -> list:
            seen["/search/topical"] = {"theme": theme, "limit": limit}
            return [{"reference": "GEN 1:1"}]

    class _Editor:
        def validate_sentence_structure(self, sentence: str) -> dict:
            seen["/grammar/validate-structure"] = {"sentence": sentence}
            return {"valid": True, "issues": []}

    class _Tracker:
        def __init__(self, user_id: str | None = None) -> None:
            seen["/progress/adaptive-difficulty"] = {"ctor_user_id": user_id}

        def get_adaptive_difficulty(self, user_id: str | None = None) -> dict:
            seen["/progress/adaptive-difficulty"]["difficulty_user_id"] = user_id
            return {"difficulty": 0.5}

        @staticmethod
        def categorize_correction(original: str, corrected: str) -> dict:
            seen["/progress/error-categorize"] = {"original": original, "corrected": corrected}
            return {"category": "orthographic"}

    stubs = {
        "zolai.foundation.corpus": {"get_corpus_analyzer": lambda: _Corpus()},
        "zolai.foundation.phonology": {"get_phonological_analyzer": lambda: _Phonology()},
        "zolai.foundation.morphology": {"get_enhanced_morphology": lambda: _Morphology()},
        "zolai.learning.translation": {"TranslationEngine": _Translation},
        "zolai.learning.online_search": {"get_online_search": lambda: _Search()},
        "zolai.learning.grammar_editor": {"GrammarEditor": _Editor},
        "zolai.learning.progress": {"ProgressTracker": _Tracker},
    }
    for name, attrs in stubs.items():
        module = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        sys.modules[name] = module
    return saved


@pytest.fixture()
def stubbed_engines():
    """Install sentinel engines for the duration of one test."""
    seen: dict[str, dict[str, Any]] = {}
    saved = _install_stubs(seen)
    yield seen
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_valid_request_reaches_every_handler(
    client: TestClient, stubbed_engines: dict[str, dict[str, Any]]
) -> None:
    """All 11 still run on a valid body, and forward the values they used to."""
    seen = stubbed_engines
    for rel, body in VALID_BODIES.items():
        response = client.post(full_path(rel), json=body)
        assert response.status_code == 200, f"{rel} valid body regressed: {response.status_code} {response.text}"

    assert sorted(seen) == sorted(TYPED_ROUTES), f"handler not reached for: {sorted(set(TYPED_ROUTES) - set(seen))}"
    assert seen["/analyze/morphology"]["word"] == "pasian"
    assert seen["/analyze/phonology"]["word"] == "pasian"
    assert seen["/grammar/validate-structure"]["sentence"] == "Ka pai hi."
    assert seen["/search/cross-lingual"]["source_lang"] == "en"
    assert seen["/search/ranked"]["limit"] == 10, "the default must survive typing"
    assert seen["/search/topical"]["limit"] == 10
    assert seen["/progress/error-categorize"] == {"original": "pathian", "corrected": "pasian"}
    assert seen["/progress/adaptive-difficulty"]["ctor_user_id"] == "default"
    assert seen["/translate/enhanced"]["direction"] == "auto"
    assert seen["/translation/disambiguate"]["direction"] == "zo-en"


def test_optional_values_are_forwarded_when_supplied(
    client: TestClient, stubbed_engines: dict[str, dict[str, Any]]
) -> None:
    """An explicitly-supplied optional key still reaches the engine."""
    response = client.post(full_path("/search/ranked"), json={"query": "pasian", "category": "bible", "limit": 3})
    assert response.status_code == 200, response.text
    assert stubbed_engines["/search/ranked"] == {"query": "pasian", "category": "bible", "limit": 3}


def test_unknown_keys_are_still_ignored(
    client: TestClient, stubbed_engines: dict[str, dict[str, Any]]
) -> None:
    """Extra keys used to be accepted-and-ignored by ``request: dict``; still are."""
    response = client.post(full_path("/analyze/morphology"), json={"word": "pasian", "bogus": [1, 2]})
    assert response.status_code == 200, f"typing must not reject keys the old dict body accepted: {response.text}"
    assert stubbed_engines["/analyze/morphology"] == {"word": "pasian"}


def test_defaults_survive_typing() -> None:
    """Optional keys keep the exact defaults the handler used to hard-code."""
    translated = EnhancedTranslationRequest(text="x")
    assert (translated.direction, translated.context) == ("auto", None)
    instance = CrossLingualSearchRequest(query="god")
    assert (instance.source_lang, instance.target_lang, instance.limit) == ("en", "zo", 10)
    instance = CorpusAnalysisRequest(text="a")
    assert (instance.ngram_size, instance.top_k) == (2, 100)
    assert PhonologyAnalysisRequest(word="pasian").apply_sandhi is True
    assert AdaptiveDifficultyRequest().user_id == "default"
    assert AdaptiveDifficultyRequest(user_id=None).user_id is None
    assert RankedSearchRequest(query="q").category is None
    assert DisambiguationRequest(word="dam").context is None
    assert TopicalSearchRequest(theme="love").limit == 10
    assert StructureValidationRequest(sentence="Ka pai hi.").sentence == "Ka pai hi."


# ---------------------------------------------------------------------------
# 4. The registry: covers all 11 and matches the real routes
# ---------------------------------------------------------------------------

def test_registry_covers_all_eleven_typed_routes() -> None:
    assert len(FOUNDATION_TOOL_CONTRACTS) == 11
    assert {c.path for c in FOUNDATION_TOOL_CONTRACTS} == set(TYPED_ROUTES)
    assert len({c.operation_id for c in FOUNDATION_TOOL_CONTRACTS}) == 11, "operation ids must be unique"


def test_registry_records_are_well_formed() -> None:
    for contract in FOUNDATION_TOOL_CONTRACTS:
        assert isinstance(contract, FoundationToolContract)
        assert contract.method == "POST"
        assert contract.path.startswith("/") and not contract.path.startswith("/api")
        assert contract.operation_id and contract.operation_id.islower()
        assert (contract.description or "").strip(), contract.operation_id
        # Non-empty scope, or the explicit public marker ("").
        assert contract.required_scope in VALID_ACTIONS, contract.operation_id


def _decorator_paths() -> dict[tuple[str, str], str]:
    """``(method, router-relative path)`` -> handler name, for the real routes.

    ``APIRouter`` prepends its own ``prefix`` to every ``route.path``, so the
    prefix is stripped to recover the string exactly as written in the
    ``@router.post(...)`` decorator — which is what the registry declares.
    """
    prefix = foundation_router.prefix
    stripped = {
        (method, route.path[len(prefix):] or "/"): route.endpoint.__name__
        for route in foundation_router.routes
        if route.path.startswith(prefix)
        for method in (getattr(route, "methods", None) or set())
    }
    assert len(stripped) == len(foundation_router.routes), (
        f"router prefix {prefix!r} does not lead every route path"
    )
    return stripped


def test_registry_paths_match_real_routes() -> None:
    """Each declared method/path must equal a real ``@router.post(...)`` route."""
    real = set(_decorator_paths())
    for contract in FOUNDATION_TOOL_CONTRACTS:
        assert (contract.method, contract.path) in real, (
            f"{contract.operation_id} declares {contract.method} {contract.path}, "
            f"which is not a route on this router"
        )


def test_registry_operation_ids_match_handler_names() -> None:
    handlers = {
        path: name for (method, path), name in _decorator_paths().items() if method == "POST"
    }
    typed = {name for (method, path), name in _decorator_paths().items() if path in TYPED_ROUTES}
    assert len(typed) == 11, f"expected 11 typed route handlers, found {sorted(typed)}"
    for contract in FOUNDATION_TOOL_CONTRACTS:
        assert contract.operation_id in typed, contract.operation_id
        assert handlers[contract.path] == contract.operation_id


def test_registry_scopes_agree_with_the_enforced_rbac_posture() -> None:
    """A declared scope must not contradict what the middleware actually enforces."""
    for contract in FOUNDATION_TOOL_CONTRACTS:
        route_class = rbac.classify_route(contract.method, full_path(contract.path))
        if contract.required_scope == PUBLIC_SCOPE:
            assert route_class == "public", f"{contract.path} declared public but is {route_class}"
        else:
            assert route_class == "member", f"{contract.path} declares a scope but is {route_class}"


def test_public_marker_is_the_empty_string() -> None:
    assert PUBLIC_SCOPE == ""
