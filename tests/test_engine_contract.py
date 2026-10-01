"""Engine contract harness + AI-optional mode tests (P2 / founder directive D2).

Parametrized over ``zolai.engines.ENGINES``:

- **lazy import** — every registry target resolves via ``EngineSpec.load()``;
- **invariants** — capability flags well-formed; offline engines are
  deterministic; the probe table and the registry cannot drift apart;
- **determinism** — same input → same output across two contract runs
  (ZVS determinism), for every ``deterministic=True`` engine;
- **offline** — every ``network=False`` engine runs its contract probe under a
  socket guard: zero outbound network attempts.

Mode plumbing (``ZOLAI_ENGINE_MODE=rule|hybrid|ai``, default ``rule``):

- ``rule`` blocks the LLM fallback chain **even with a provider key present**
  (the provider registry is never consulted, zero sockets);
- ``ai``/``hybrid`` **without a key** degrade to the rule path — same public
  contract, never a 500 — asserted at the chain level *and* over HTTP;
- ``ai`` **with** a key opens the gate (fake in-process provider, no network);
- ``GET /api/v1/predictions/health`` reports the active mode (additive key).

Known engine defects are marked ``xfail(reason=...)`` and documented in
``docs/linguistics/ENGINE_FINDINGS.md`` — NO behavior fixes in P2.
"""

from __future__ import annotations

import asyncio
import dataclasses
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zolai.api.server import create_app
from zolai.config import config
from zolai.engines import (
    AI_KEY_ENV_VARS,
    DEFAULT_ENGINE_MODE,
    ENGINE_BY_NAME,
    ENGINE_MODE_ENV,
    ENGINE_MODES,
    ENGINES,
    Capabilities,
    EngineSpec,
    ai_key_present,
    engine_mode,
    engine_names,
    iter_engines,
    llm_allowed,
    resolve_engine_path,
)
from zolai.engines import (
    get_engine as get_engine_spec,
)
from zolai.llm import fallback as fallback_mod
from zolai.llm.providers.base import LLMProvider, ProviderRegistry

# ── Deterministic n-gram tables for the HTTP mode tests ──────────────────────

TINY_TABLES: dict[str, Any] = {
    "unigrams": {"khi": 100, "le": 200},
    "bigrams": {("khi", "a"): 50, ("khi", "b"): 30, ("le", "a"): 100},
}

_NGRAM_PATCH = "zolai.api.prediction_api.load_ngram_tables"


# ── Socket guard ─────────────────────────────────────────────────────────────


class NetworkBlocked(AssertionError):
    """Raised by the guard when an engine attempts outbound network."""


@contextmanager
def network_blocked() -> Iterator[list[str]]:
    """Fail (and record) any outbound network attempt while active.

    Blocks the connect + DNS primitives an engine could use; every attempt is
    recorded so tests can assert *zero* calls, not merely zero successes.
    """
    attempts: list[str] = []

    def _block(label: str) -> Callable[..., Any]:
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            attempts.append(label)
            raise NetworkBlocked(f"outbound network attempted: {label}")

        return _blocked

    originals = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
    }
    socket.socket.connect = _block("socket.connect")  # type: ignore[method-assign]
    socket.socket.connect_ex = _block("socket.connect_ex")  # type: ignore[method-assign]
    socket.create_connection = _block("socket.create_connection")
    socket.getaddrinfo = _block("socket.getaddrinfo")
    try:
        yield attempts
    finally:
        socket.socket.connect = originals["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = originals["connect_ex"]  # type: ignore[method-assign]
        socket.create_connection = originals["create_connection"]
        socket.getaddrinfo = originals["getaddrinfo"]


def _snap(value: Any, depth: int = 0) -> Any:
    """Plain-data snapshot of a probe result (for run-vs-run comparison)."""
    if depth > 8:
        return repr(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            fields = dataclasses.asdict(value)
        except (TypeError, ValueError):
            return repr(value)
        return {"__dc__": type(value).__name__, **{k: _snap(v, depth + 1) for k, v in fields.items()}}
    if isinstance(value, dict):
        return {str(k): _snap(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_snap(v, depth + 1) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


# ── Contract probes (one per registry entry) ─────────────────────────────────
# Each factory runs setup once (inside the guard) and returns a zero-arg
# callable that performs the engine's representative read-only run.


def _probe_dictionary() -> Callable[[], Any]:
    from zolai.data.repositories import get_engine
    from zolai.data.repositories.dictionary import DictionaryRepository

    repo = DictionaryRepository(get_engine())

    def run() -> Any:
        return {"count": repo.count(), "row": repo.find_one({"zolai": "pasian"})}

    return run


def _probe_translation_fallback() -> Callable[[], Any]:
    from zolai.offline.rule_engine import RuleEngine

    engine = RuleEngine()

    def run() -> Any:
        return {
            "translate": engine.translate("God"),
            "sov": engine.validate_sov("Pasian in gam a piangsak hi."),
        }

    return run


def _probe_syllable() -> Callable[[], Any]:
    from zolai.syllable import segment

    return lambda: segment("nisuahna")


def _probe_morphology() -> Callable[[], Any]:
    from zolai.foundation.morphology import EnhancedMorphologyAnalyzer

    analyzer = EnhancedMorphologyAnalyzer()
    return lambda: analyzer.decompose("piangsak")


def _probe_phonology_tone() -> Callable[[], Any]:
    from zolai.foundation.phonology import PhonologicalAnalyzer

    analyzer = PhonologicalAnalyzer()
    return lambda: analyzer.analyze("pasian")


def _probe_pos_normalize() -> Callable[[], Any]:
    from zolai.data.pos_normalize import normalize_legacy_pos

    return lambda: normalize_legacy_pos("ver")


def _probe_rule_tagger() -> Callable[[], Any]:
    from zolai.pos_tagger import get_pos_tagger

    tagger = get_pos_tagger()
    return lambda: tagger.tag("Pasian in gam a piangsak hi.")


def _probe_zvs_validate() -> Callable[[], Any]:
    from zolai.zvs import validate

    return lambda: validate("Pasian in gam a piangsak hi.")


def _probe_grammar_rules() -> Callable[[], Any]:
    from zolai.rules.zolai_rules_reference import ZolaiRules

    rules = ZolaiRules()
    return lambda: rules.check_forbidden("Pathian in ram a ser hi.")


def _probe_corpus_ngram() -> Callable[[], Any]:
    from zolai.foundation.corpus import CorpusAnalyzer

    corpus = CorpusAnalyzer()
    return lambda: corpus.extract_ngrams("Pasian in vantung leh leitung a piangsak hi.", n=2)


def _probe_prediction_ngram() -> Callable[[], Any]:
    from zolai.knowledge.ngram import predict_next

    tables = TINY_TABLES
    return lambda: predict_next("khi", top_k=3, tables=tables)


def _probe_polysemy() -> Callable[[], Any]:
    from zolai.learning.translation import TranslationEngine

    engine = TranslationEngine()
    return lambda: engine.translate("earth")


def _probe_attestation() -> Callable[[], Any]:
    from zolai.learning.word_attestation import WordAttestation

    attestation = WordAttestation()
    return lambda: attestation.attest_word("pasian")


def _probe_online_search() -> Callable[[], Any]:
    """Construct-only: ``OnlineSearch`` is DB-backed (sqlite3 reads) — no network I/O."""
    from zolai.learning.online_search import OnlineSearch

    instance = OnlineSearch()
    return lambda: type(instance).__name__


def _probe_tokenizer() -> Callable[[], Any]:
    """Construct-only until P3 ships a trained artifact (finding F3)."""
    from zolai.tokenizer.zolai_tokenizer import ZolaiTokenizer

    tokenizer = ZolaiTokenizer()
    return lambda: type(tokenizer).__name__


def _probe_context_validator() -> Callable[[], Any]:
    from zolai.learning.context_validator import ContextValidator

    validator = ContextValidator()
    return lambda: validator.validate_context("Pasian in vantung a piangsak hi.")


PROBES: dict[str, Callable[[], Callable[[], Any]]] = {
    "dictionary_lookup": _probe_dictionary,
    "translation_fallback": _probe_translation_fallback,
    "syllable": _probe_syllable,
    "morphology": _probe_morphology,
    "phonology_tone": _probe_phonology_tone,
    "pos_normalize": _probe_pos_normalize,
    "rule_tagger": _probe_rule_tagger,
    "zvs_validate": _probe_zvs_validate,
    "grammar_rules": _probe_grammar_rules,
    "corpus_ngram": _probe_corpus_ngram,
    "prediction_ngram": _probe_prediction_ngram,
    "polysemy": _probe_polysemy,
    "attestation": _probe_attestation,
    "online_search": _probe_online_search,
    "tokenizer": _probe_tokenizer,
    "context_validator": _probe_context_validator,
}


# ── Registry invariants ──────────────────────────────────────────────────────


class TestRegistry:
    def test_registry_has_at_least_fourteen_engines(self) -> None:
        assert len(ENGINES) >= 14

    def test_names_unique_and_indexed(self) -> None:
        names = engine_names()
        assert len(names) == len(set(names))
        assert set(ENGINE_BY_NAME) == set(names)
        assert list(iter_engines()) == list(ENGINES)

    def test_spec_metadata_shape(self) -> None:
        for spec in ENGINES:
            assert isinstance(spec, EngineSpec)
            assert isinstance(spec.capabilities, Capabilities)
            module_name, sep, attr = spec.target.partition(":")
            assert sep == ":", f"{spec.name}: target must be 'module:attr'"
            assert module_name.startswith("zolai."), f"{spec.name}: {module_name}"
            assert attr, f"{spec.name}: target must reference an attribute"
            assert spec.summary.strip(), f"{spec.name}: empty summary"

    def test_offline_engines_are_deterministic(self) -> None:
        """Rule-path engines (network=False) must be deterministic (D2)."""
        for spec in ENGINES:
            if not spec.capabilities.network:
                assert spec.capabilities.deterministic, f"{spec.name}: offline but non-deterministic"

    def test_get_spec_unknown_name_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown engine"):
            get_engine_spec("definitely_not_registered")

    def test_probe_table_matches_registry(self) -> None:
        """Registry and probe table cannot drift (new engine ⇒ new probe)."""
        assert set(PROBES) == set(engine_names())


# ── Lazy import ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", ENGINES, ids=lambda s: s.name)
def test_lazy_import_resolves(spec: EngineSpec) -> None:
    target = spec.load()
    assert target is not None, f"{spec.name}: target resolved to None"


# ── Contract runs: determinism + no network ──────────────────────────────────

_OFFLINE = [s for s in ENGINES if not s.capabilities.network]


@pytest.mark.parametrize("spec", _OFFLINE, ids=lambda s: s.name)
def test_offline_engine_contract(spec: EngineSpec) -> None:
    """Run twice under a socket guard: no network, same output (ZVS determinism)."""
    with network_blocked() as attempts:
        run = PROBES[spec.name]()
        first = _snap(run())
        if spec.capabilities.deterministic:
            second = _snap(run())
            assert first == second, f"{spec.name}: non-deterministic across two runs"
    assert attempts == [], f"{spec.name}: outbound network attempted {attempts}"


def test_network_engine_probe_runs_unguarded() -> None:
    """Run unguarded probes for every ``network=True`` engine.

    Historically ``online_search`` was the only flagged network engine — that flag
    was wrong (it is a DB-only sqlite3 reader; see F2 in ENGINE_FINDINGS.md, now
    REJECTED as naming confusion) and it is now ``network=False``, i.e. part of the
    guarded offline set. The registry currently registers **no** network engines, so
    this test skips; if a genuinely network-backed engine ever joins, its probe runs
    unguarded here (it is excluded from the socket-guarded offline contract).
    """
    network_engines = [s for s in ENGINES if s.capabilities.network]
    if not network_engines:
        pytest.skip("no network engines registered — all engines are offline/deterministic")
    for spec in network_engines:
        run = PROBES[spec.name]()
        assert run() is not None, f"{spec.name}: probe returned None"


def test_online_search_is_db_only() -> None:
    """Regression guard: ``online_search`` must stay network-free and deterministic.

    It reads only the local SQLite DB (vocabulary/grammar/Bible), so it belongs in the
    socket-guarded offline contract set — never flagged as a network engine again.
    """
    spec = get_engine_spec("online_search")
    assert spec.capabilities.network is False
    assert spec.capabilities.deterministic is True
    run = PROBES["online_search"]()
    assert run() == "OnlineSearch"


# ── Mode plumbing (D2) ───────────────────────────────────────────────────────


class _ExplodingRegistry:
    """Fails the test if the chain consults providers despite the mode gate."""

    def get_available_providers(self) -> Any:
        raise AssertionError("provider registry consulted despite mode gate")

    def get_provider(self, name: str) -> Any:
        raise AssertionError("provider registry consulted despite mode gate")


class _FakeProvider(LLMProvider):
    """In-process provider proving the gate opens in ``hybrid``/``ai`` + key."""

    def __init__(self) -> None:
        super().__init__(name="fake_contract", priority=1)
        self.calls = 0

    async def generate(
        self, messages: list[dict[str, str]], model: str | None = None, **kwargs: Any
    ) -> str:
        self.calls += 1
        return "FAKE_CONTRACT_RESPONSE"

    def list_models(self) -> list[str]:
        return ["fake-model"]


def _run_chain(chain: fallback_mod.FallbackChain) -> dict[str, Any]:
    return asyncio.run(chain.generate([{"role": "user", "content": "hello"}]))


class TestEngineMode:
    @pytest.fixture(autouse=True)
    def _isolated_mode(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Default world: ``rule`` mode, no AI keys, warn auth, no rate state."""
        monkeypatch.delenv(ENGINE_MODE_ENV, raising=False)
        for var in AI_KEY_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(config, "engine_mode", DEFAULT_ENGINE_MODE)
        monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
        yield

    # -- mode resolution ----------------------------------------------------

    def test_default_mode_is_rule(self) -> None:
        assert engine_mode() == "rule"
        assert resolve_engine_path() == "rule"
        assert llm_allowed() is False

    def test_unknown_mode_falls_back_to_rule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, "banana")
        assert engine_mode() == "rule"
        assert resolve_engine_path() == "rule"
        assert llm_allowed() is False

    # -- rule mode: zero outbound LLM network -------------------------------

    def test_rule_mode_blocks_chain_even_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, "rule")
        monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
        assert ai_key_present() is True, "precondition: key configured"
        assert llm_allowed() is False, "rule mode must ignore keys"

        chain = fallback_mod.FallbackChain()
        monkeypatch.setattr(chain, "_registry", _ExplodingRegistry())
        with network_blocked() as attempts:
            result = _run_chain(chain)

        assert result["provider"] == "rule_based"
        assert result["fallback_used"] is True
        assert attempts == [], f"rule mode attempted network: {attempts}"

    # -- ai mode without a key: graceful degradation, never 500 -------------

    def test_ai_mode_without_key_degrades_to_rule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, "ai")
        assert ai_key_present() is False, "precondition: no key"
        assert resolve_engine_path() == "rule", "ai without key must degrade to rule"
        assert llm_allowed() is False

        chain = fallback_mod.FallbackChain()
        monkeypatch.setattr(chain, "_registry", _ExplodingRegistry())
        with network_blocked() as attempts:
            result = _run_chain(chain)

        assert result["provider"] == "rule_based"
        assert result["fallback_used"] is True
        assert attempts == [], f"ai-without-key attempted network: {attempts}"

    def test_ai_mode_without_key_public_contract_serves_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same public contract in every mode — degraded, but serving."""
        with patch(_NGRAM_PATCH, return_value=TINY_TABLES):
            monkeypatch.setenv(ENGINE_MODE_ENV, "rule")
            client = TestClient(create_app())
            rule_health = client.get("/api/v1/predictions/health")
            rule_next = client.get("/api/v1/predictions/next", params={"word": "khi"})

            monkeypatch.setenv(ENGINE_MODE_ENV, "ai")
            ai_health = client.get("/api/v1/predictions/health")
            ai_next = client.get("/api/v1/predictions/next", params={"word": "khi"})

        assert ai_health.status_code == 200, ai_health.text
        assert ai_next.status_code == 200, ai_next.text
        assert ai_health.json()["mode"] == "ai"
        assert resolve_engine_path() == "rule"

        # Contract identical apart from the additive ``mode`` key.
        rule_body = {k: v for k, v in rule_health.json().items() if k != "mode"}
        ai_body = {k: v for k, v in ai_health.json().items() if k != "mode"}
        assert rule_body == ai_body
        assert rule_next.json() == ai_next.json()

    # -- hybrid / ai with a key: gate opens (no real network) ---------------

    def test_hybrid_mode_is_rule_first_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, "hybrid")
        assert resolve_engine_path() == "rule"
        assert llm_allowed() is False
        monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
        assert llm_allowed() is True, "hybrid + key must allow AI enrichment"

    def test_ai_mode_with_key_opens_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, "ai")
        monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
        assert llm_allowed() is True

        registry = ProviderRegistry()
        fake = _FakeProvider()
        registry.register(fake)
        monkeypatch.setattr(fallback_mod, "get_provider_registry", lambda: registry)

        chain = fallback_mod.FallbackChain()
        result = _run_chain(chain)
        assert result["provider"] == "fake_contract"
        assert result["fallback_used"] is False
        assert fake.calls == 1

    # -- health reports the active mode (additive key only) -----------------

    @pytest.mark.parametrize("mode", ["rule", "hybrid", "ai"])
    def test_predictions_health_reports_mode(
        self, mode: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, mode)
        with patch(_NGRAM_PATCH, return_value=TINY_TABLES):
            client = TestClient(create_app())
            resp = client.get("/api/v1/predictions/health")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == mode
        assert body["mode"] in ENGINE_MODES
        # Pre-existing keys unchanged (additive-only contract).
        assert set(body) == {"status", "tables_loaded", "unigram_count", "bigram_count", "mode"}
        assert body["status"] == "ok"
        assert body["tables_loaded"] is True

    # -- known defect: legacy chat endpoints bypass the mode gate (F1) ------

    @pytest.mark.xfail(
        reason="F1: legacy /chat/* endpoints bypass ZOLAI_ENGINE_MODE (see docs/linguistics/ENGINE_FINDINGS.md)"
    )
    def test_rule_mode_legacy_chat_endpoint_stays_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENGINE_MODE_ENV, "rule")
        client = TestClient(create_app(), raise_server_exceptions=False)
        with network_blocked() as attempts:
            resp = client.get("/chat", params={"q": "hello"})
        assert resp.status_code == 200, resp.status_code
        assert attempts == [], f"rule mode attempted network: {attempts}"
