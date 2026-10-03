"""Engine contract registry + AI-optional engine mode (P2 / founder directive D2).

This module is the single place where the toolkit declares **what an engine
is** and **which execution path an engine may take**.

Registry (:data:`ENGINES`)
    ~16 entries — dictionary lookup, rule translation fallback, syllable,
    morphology, phonology/tone, POS normalization, rule POS tagger, ZVS
    validation, grammar rules, corpus n-gram, prediction n-gram, polysemy,
    attestation, online search, tokenizer, context validator.  Every entry is
    a lazy ``module:attr`` reference (nothing is imported until
    :meth:`EngineSpec.load` is called) plus a capability triple and a short
    docstring.  ``tests/test_engine_contract.py`` parametrizes over this
    registry: lazy import, invariant flags, determinism (same input → same
    output across two calls) and — for ``network=False`` entries — a run under
    a socket guard proving **no outbound network**.

Mode (:func:`engine_mode`, founder directive D2)
    ``ZOLAI_ENGINE_MODE=rule|hybrid|ai`` (default ``rule``):

    - ``rule``     — offline, deterministic, **zero outbound network to LLM
      providers**, ever.
    - ``hybrid``   — local rule path first; AI enrichment only when a provider
      key is present.
    - ``ai``       — LLM path when a key is present; **without a key it must
      degrade gracefully to the rule path** (same public contract, never a
      500).  :func:`resolve_engine_path` is the registry path resolver
      (consumers wired in C2).

    The gate every LLM call site consults is :func:`llm_allowed` — currently
    read by :class:`zolai.llm.fallback.FallbackChain` (the provider-chain path
    chooser) and exposed on ``GET /api/v1/predictions/health`` as ``mode``.
    Any future AI enrichment in the prediction/translation routes must call
    ``llm_allowed()`` before opening a socket; the contract tests keep the
    rule path honest.

No hardcoded lexicon data lives here (D2): the registry only names modules —
words/tags/patterns come from DB tables and versioned artifacts.  The repo-wide
sweep of literals that still *do* live in engine paths is
``docs/linguistics/ENGINE_HARDCODE_INVENTORY.md``.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Iterator

from .config import config

#: Environment variable holding the engine mode (read live, see
#: :func:`engine_mode` — same pattern as ``ZOLAI_API_AUTH``).
ENGINE_MODE_ENV = "ZOLAI_ENGINE_MODE"

#: Valid modes, in increasing AI reliance.
ENGINE_MODES: tuple[str, ...] = ("rule", "hybrid", "ai")

#: Offline deterministic default — engines never surprise an operator with a
#: network call because a flag was forgotten.
DEFAULT_ENGINE_MODE = "rule"

#: Env vars whose presence marks an LLM provider as credentialed.  Used only
#: to decide whether ``hybrid``/``ai`` may take the AI path; ``rule`` mode
#: ignores keys entirely.
AI_KEY_ENV_VARS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)


# ── Mode plumbing (D2) ───────────────────────────────────────────────────────


def engine_mode() -> str:
    """Active engine mode: ``rule`` (default) | ``hybrid`` | ``ai``.

    Read live from the environment so ops can flip the window without a
    restart (mirrors :func:`zolai.api.auth.api_auth_mode`).  An unset or
    unknown value falls back to :data:`DEFAULT_ENGINE_MODE` (``rule``), never
    to ``ai`` — a typo must not open the network.
    """
    raw = os.environ.get(ENGINE_MODE_ENV)
    if raw is None or not raw.strip():
        raw = str(getattr(config, "engine_mode", DEFAULT_ENGINE_MODE))
    mode = str(raw).strip().lower()
    return mode if mode in ENGINE_MODES else DEFAULT_ENGINE_MODE


def ai_key_present() -> bool:
    """True when at least one LLM provider key is configured (any mode)."""
    return any(os.environ.get(var, "").strip() for var in AI_KEY_ENV_VARS)


def llm_allowed() -> bool:
    """May the current mode open an outbound connection to an LLM provider?

    ``rule`` → always ``False`` (zero network, deterministic).  ``hybrid`` /
    ``ai`` → only with a key present; without one the caller degrades to the
    rule path (never an exception, never a 500).
    """
    if engine_mode() == DEFAULT_ENGINE_MODE:
        return False
    return ai_key_present()


def resolve_engine_path(mode: str | None = None) -> str:
    """Effective execution path for ``mode`` (default: :func:`engine_mode`).

    Returns the *requested* mode when it can be honored, otherwise ``"rule"``
    — the graceful-degradation contract: ``ai`` without a key still serves the
    same public API from the deterministic local path.
    """
    requested = mode if mode is not None else engine_mode()
    if requested not in ENGINE_MODES:
        requested = DEFAULT_ENGINE_MODE
    if requested == DEFAULT_ENGINE_MODE:
        return "rule"
    return requested if ai_key_present() else "rule"


# ── Registry ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Capabilities:
    """Capability flags enforced as invariants by the contract tests.

    - ``network`` — the engine opens outbound sockets in its normal path
      (``False`` engines must pass a contract run under a socket guard).
    - ``deterministic`` — same input → same output across two calls (ZVS
      determinism).
    - ``writes`` — the engine's surface can mutate persistent state (DB rows /
      artifact files); read-only API layers must not mount these without an
      audit path.
    """

    network: bool = False
    deterministic: bool = True
    writes: bool = False


@dataclass(frozen=True)
class EngineSpec:
    """One registered engine: lazy reference + capabilities + short docstring."""

    name: str
    target: str
    """``"package.module:attr"`` — imported only when :meth:`load` is called."""

    capabilities: Capabilities
    summary: str

    def load(self) -> Any:
        """Import the target module and return the referenced attribute.

        Raises ``ImportError``/``AttributeError`` loudly — the contract test
        treats a broken lazy reference as a registry defect, not a soft skip.
        """
        module_name, _, attr = self.target.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, attr) if attr else module


#: The registry — order is documentation, not precedence.
ENGINES: tuple[EngineSpec, ...] = (
    EngineSpec(
        name="dictionary_lookup",
        target="zolai.data.repositories.dictionary:DictionaryRepository",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Exact/prefix bilingual headword lookup over the dictionary tables (DB-first).",
    ),
    EngineSpec(
        name="translation_fallback",
        target="zolai.offline.rule_engine:RuleEngine",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="LLM-less translation fallback: dictionary + ZVS + SOV rules (deprioritized path).",
    ),
    EngineSpec(
        name="syllable",
        target="zolai.syllable:segment",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Rule-based syllable segmentation (phonotactic rules, no DB write).",
    ),
    EngineSpec(
        name="morphology",
        target="zolai.foundation.morphology:EnhancedMorphologyAnalyzer",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Agglutinative decomposition: directional + stem + aspect + particle, ZVS-validated.",
    ),
    EngineSpec(
        name="phonology_tone",
        target="zolai.foundation.phonology:PhonologicalAnalyzer",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Syllable structure, tone-sandhi rules and phonotactic checks.",
    ),
    EngineSpec(
        name="pos_normalize",
        target="zolai.data.pos_normalize:normalize_legacy_pos",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Legacy POS label → canonical UPOS allowlist normalization (pure function).",
    ),
    EngineSpec(
        name="rule_tagger",
        target="zolai.pos_tagger:ZolaiPOSTagger",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Rule/lexicon POS tagger: closed classes + dictionary POS + suffix heuristics.",
    ),
    EngineSpec(
        name="zvs_validate",
        target="zolai.zvs:validate",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="ZVS 2018 orthography validation — versioned rules, report-only (no writes).",
    ),
    EngineSpec(
        name="grammar_rules",
        target="zolai.rules.zolai_rules_reference:ZolaiRules",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Grammar/SOV/negation rule reference incl. the versioned ZVS forbidden-forms table.",
    ),
    EngineSpec(
        name="corpus_ngram",
        target="zolai.foundation.corpus:CorpusAnalyzer",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Corpus n-gram extraction + PMI collocations from DB tables (read-only).",
    ),
    EngineSpec(
        name="prediction_ngram",
        target="zolai.knowledge.ngram:predict_next",
        capabilities=Capabilities(network=False, deterministic=True, writes=True),
        summary="Next-word prediction over the ngram table; its build path persists tables.",
    ),
    EngineSpec(
        name="polysemy",
        target="zolai.learning.translation:TranslationEngine",
        capabilities=Capabilities(network=False, deterministic=True, writes=True),
        summary="Context-aware translation + per-book polysemy disambiguation; logs corrections to audit.",
    ),
    EngineSpec(
        name="attestation",
        target="zolai.learning.word_attestation:WordAttestation",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Multi-source word attestation (Bible/dictionary/corpus) with suggestion fallback.",
    ),
    EngineSpec(
        name="online_search",
        target="zolai.learning.online_search:OnlineSearch",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="DB-backed search over vocabulary/grammar/Bible (historically named 'online'; no network I/O).",
    ),
    EngineSpec(
        name="tokenizer",
        target="zolai.tokenizer.zolai_tokenizer:ZolaiTokenizer",
        capabilities=Capabilities(network=False, deterministic=True, writes=True),
        summary="SentencePiece tokenizer wrapper — encode/decode are read-only, train writes artifacts.",
    ),
    EngineSpec(
        name="context_validator",
        target="zolai.learning.context_validator:ContextValidator",
        capabilities=Capabilities(network=False, deterministic=True, writes=False),
        summary="Sentence-pair plausibility against Bible/conversation context (DB-backed, read-only).",
    ),
    EngineSpec(
        name="observation",
        target="zolai.foundation.observation.pipeline:ObservationPipeline",
        capabilities=Capabilities(network=False, deterministic=True, writes=True),
        summary="Observation build: tokenize → normalize → freq/contexts/PMI/attestation "
        "into rebuildable derived tables.",
    ),
)

#: Name → spec, for O(1) lookup in tests and call sites.
ENGINE_BY_NAME: dict[str, EngineSpec] = {spec.name: spec for spec in ENGINES}


def get_engine(name: str) -> EngineSpec:
    """Return the registered :class:`EngineSpec` for ``name``.

    Raises ``KeyError`` for unknown names — the registry is closed by design
    (new engines must declare capabilities to join it).
    """
    try:
        return ENGINE_BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown engine {name!r}; registered: {sorted(ENGINE_BY_NAME)}") from None


def iter_engines() -> Iterator[EngineSpec]:
    """Iterate the registry in declaration order."""
    return iter(ENGINES)


def engine_names() -> list[str]:
    """Registered engine names, in declaration order."""
    return [spec.name for spec in ENGINES]
