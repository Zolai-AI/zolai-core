"""The ``foundation_*`` tool family — the typed ``/api/v1/foundation/*` twins.

Why these exist as a *generated* family rather than eleven hand-written specs:

- ``zolai/api/foundation_router.py`` already declares one
  :class:`~zolai.api.foundation_router.FoundationToolContract` per typed
  endpoint — the machine-readable tool contract the MCP manifest serves.
- Each of those endpoints takes exactly one **Pydantic request model**, which is
  the same contract a tool input schema needs.

So every spec here is built by *pairing* those two facts: the name is
``foundation_<operation_id>``, the description is the contract's own
description, and the input schema is the route's ``model_json_schema()``.  A
tool can therefore never disagree with the REST route it wraps, and the pairing
— declaration ⇄ input model ⇄ handler — is asserted **both ways** by
:func:`~zolai.agent.tools.family.build_family` at import, so adding a contract
to the router without giving it a handler (or writing a handler for a contract
that was never declared) fails at start-up instead of silently publishing a
tool that cannot run.

Naming: the family is **prefixed**, so it can never collide with the bare agent
tools (``grammar_check`` vs ``foundation_validate_structure``) — a message may
list both, and ``tests/test_mcp_agent_namespace.py`` fails if a bare name ever
appears in this family or vice versa.

Authority: these are ``member``-tier.  Nothing here writes (every contract is a
read) and nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from ...api.foundation_router import (
    FOUNDATION_TOOL_CONTRACTS,
    AdaptiveDifficultyRequest,
    CorpusAnalysisRequest,
    CrossLingualSearchRequest,
    DisambiguationRequest,
    EnhancedTranslationRequest,
    ErrorCategorizationRequest,
    MorphologyAnalysisRequest,
    PhonologyAnalysisRequest,
    RankedSearchRequest,
    StructureValidationRequest,
    TopicalSearchRequest,
)
from .family import ToolFamily, build_family
from .types import ToolError, ToolSpec

#: ``operation_id`` → the request model of the REST route it wraps.  Every value
#: is a read-only analysis/search body, so no contract carries a write scope.
_OPERATION_MODELS: dict[str, type[BaseModel]] = {
    "analyze_corpus": CorpusAnalysisRequest,
    "analyze_phonology": PhonologyAnalysisRequest,
    "analyze_morphology": MorphologyAnalysisRequest,
    "translate_enhanced": EnhancedTranslationRequest,
    "get_adaptive_difficulty": AdaptiveDifficultyRequest,
    "search_ranked": RankedSearchRequest,
    "search_cross_lingual": CrossLingualSearchRequest,
    "search_topical": TopicalSearchRequest,
    "validate_structure": StructureValidationRequest,
    "disambiguate_translation": DisambiguationRequest,
    "categorize_error": ErrorCategorizationRequest,
}

#: Tool-name prefix.  Frozen: renaming it breaks a client's cached tool list.
PREFIX = "foundation_"


def tool_name(operation_id: str) -> str:
    """The registered tool name for a contract's ``operation_id``."""
    return f"{PREFIX}{operation_id}"


def _validated(model: type[BaseModel], inp: dict[str, Any]) -> Any:
    """Validate one tool call against the route's own request model.

    Extra keys are ignored (Pydantic's default, which is also FastAPI's
    behaviour for these bodies) so a model that invents a field is not punished;
    a missing or ill-typed one becomes a readable :class:`ToolError` instead of
    a Pydantic traceback.
    """
    try:
        return model(**inp)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ())) or "arguments"
        raise ToolError(f"{model.__name__}: {loc}: {first.get('msg')}") from exc


# ── Handlers ─────────────────────────────────────────────────────────────────
#
# Each mirrors the singleton call its REST route makes (same in-process
# services, same returned dict) — nothing is re-implemented, and no handler
# performs a write.


def _analyze_corpus(inp: dict[str, Any]) -> Any:
    from ...foundation.corpus import get_corpus_analyzer

    request = _validated(CorpusAnalysisRequest, inp)
    if not request.text:
        raise ToolError("Text is required")
    result = get_corpus_analyzer().analyze(request.text)
    return {
        "ngrams": {str(k): v for k, v in result.ngrams.items()},
        "collocations": [
            {"word1": c.word1, "word2": c.word2, "pmi": c.pmi, "freq": c.freq}
            for c in result.collocations
        ],
        "freq_distribution": result.freq_distribution,
        "register": result.register,
        "total_words_analyzed": result.total_words_analyzed,
    }


def _analyze_phonology(inp: dict[str, Any]) -> Any:
    from ...foundation.phonology import get_phonological_analyzer

    request = _validated(PhonologyAnalysisRequest, inp)
    if not request.word:
        raise ToolError("Word is required")
    result = get_phonological_analyzer().analyze(request.word)
    return {
        "syllable_structures": [
            {
                "syllable": s.syllable,
                "onset": s.onset,
                "nucleus": s.nucleus,
                "coda": s.coda,
                "tone": s.tone,
                "valid": s.valid,
            }
            for s in result.syllable_structures
        ],
        "tone_patterns": list(result.tone_patterns),
        "sandhi_applied": [list(pair) for pair in result.sandhi_applied],
        "phonotactic_valid": result.phonotactic_valid,
        "stress_pattern": list(result.stress_pattern),
        "violations": list(result.violations),
    }


def _analyze_morphology(inp: dict[str, Any]) -> Any:
    from ...foundation.morphology import get_enhanced_morphology

    request = _validated(MorphologyAnalysisRequest, inp)
    if not request.word:
        raise ToolError("Word is required")
    result = get_enhanced_morphology().decompose(request.word)
    return {
        "segments": list(result.segments),
        "directional": result.directional,
        "stem": result.stem,
        "aspect": result.aspect,
        "particle": result.particle,
        "is_valid": result.is_valid,
        "violations": list(result.violations),
        "compound_parts": list(result.compound_parts),
        "prefix": result.prefix,
        "suffix": result.suffix,
    }


def _translate_enhanced(inp: dict[str, Any]) -> Any:
    from ...learning.translation import TranslationEngine

    request = _validated(EnhancedTranslationRequest, inp)
    if not request.text:
        raise ToolError("Text is required")
    result = TranslationEngine().translate(
        request.text, direction=request.direction, context=request.context
    )
    return {
        "translation": result.get("translation", ""),
        "confidence": result.get("confidence", 0),
        "tier": result.get("tier", "none"),
        "sources": result.get("sources", []),
        "evidence_chain": result.get("evidence_chain", []),
        "zolai": result.get("zolai"),
        "english": result.get("english"),
        "pos": result.get("pos"),
        "note": result.get("note"),
        "morphology_breakdown": result.get("morphology_breakdown"),
    }


def _get_adaptive_difficulty(inp: dict[str, Any]) -> Any:
    from ...learning.progress import ProgressTracker

    request = _validated(AdaptiveDifficultyRequest, inp)
    user_id = request.user_id
    return ProgressTracker(user_id=user_id).get_adaptive_difficulty(user_id=user_id)


def _search_ranked(inp: dict[str, Any]) -> Any:
    from ...learning.online_search import get_online_search

    request = _validated(RankedSearchRequest, inp)
    if not request.query:
        raise ToolError("Query is required")
    results = get_online_search().search_ranked(
        request.query, category=request.category, limit=request.limit
    )
    return {"query": request.query, "results": results, "total": len(results)}


def _search_cross_lingual(inp: dict[str, Any]) -> Any:
    from ...learning.online_search import get_online_search

    request = _validated(CrossLingualSearchRequest, inp)
    if not request.query:
        raise ToolError("Query is required")
    if request.source_lang not in ("en", "zo"):
        raise ToolError("source_lang must be 'en' or 'zo'")
    results = get_online_search().search_cross_lingual(
        request.query,
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        limit=request.limit,
    )
    return {
        "query": request.query,
        "source_lang": request.source_lang,
        "target_lang": request.target_lang,
        "results": results,
        "total": len(results),
    }


def _search_topical(inp: dict[str, Any]) -> Any:
    from ...learning.online_search import get_online_search

    request = _validated(TopicalSearchRequest, inp)
    if not request.theme:
        raise ToolError("Theme is required")
    results = get_online_search().search_topical(request.theme, limit=request.limit)
    return {"theme": request.theme, "results": results, "total": len(results)}


def _validate_structure(inp: dict[str, Any]) -> Any:
    from ...learning.grammar_editor import GrammarEditor

    request = _validated(StructureValidationRequest, inp)
    if not request.sentence:
        raise ToolError("Sentence is required")
    return GrammarEditor().validate_sentence_structure(request.sentence)


def _disambiguate_translation(inp: dict[str, Any]) -> Any:
    from ...learning.translation import TranslationEngine

    request = _validated(DisambiguationRequest, inp)
    if not request.word:
        raise ToolError("Word is required")
    return TranslationEngine()._disambiguate_polysemy(
        request.word, context=request.context, direction=request.direction
    )


def _categorize_error(inp: dict[str, Any]) -> Any:
    from ...learning.progress import ProgressTracker

    request = _validated(ErrorCategorizationRequest, inp)
    if not request.original or not request.corrected:
        raise ToolError("Both original and corrected are required")
    return ProgressTracker.categorize_correction(request.original, request.corrected)


_OPERATION_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "analyze_corpus": _analyze_corpus,
    "analyze_phonology": _analyze_phonology,
    "analyze_morphology": _analyze_morphology,
    "translate_enhanced": _translate_enhanced,
    "get_adaptive_difficulty": _get_adaptive_difficulty,
    "search_ranked": _search_ranked,
    "search_cross_lingual": _search_cross_lingual,
    "search_topical": _search_topical,
    "validate_structure": _validate_structure,
    "disambiguate_translation": _disambiguate_translation,
    "categorize_error": _categorize_error,
}


_CONTRACTS: dict[str, Any] = {
    contract.operation_id: contract for contract in FOUNDATION_TOOL_CONTRACTS
}


def _describe(operation_id: str) -> str:
    """``Foundation twin of …`` description for one contract."""
    contract = _CONTRACTS[operation_id]
    return (
        f"Foundation twin of {contract.method} /api/v1/foundation{contract.path}. "
        f"{contract.description}"
    )


def _spec_of(operation_id: str, model: type[BaseModel], tname: str) -> ToolSpec:
    """One :class:`ToolSpec` for one contract (its route body = input schema)."""
    return ToolSpec(
        name=tname,
        description=_describe(operation_id),
        # The REST route's own body model IS the tool's input schema.
        parameters=model.model_json_schema(),
        title=f"Foundation: {operation_id.replace('_', ' ')}",
        # Every contract is a read: nothing in this family writes, so nothing
        # in it is destructive.  Declared explicitly like every published spec.
        destructive=False,
    )


#: Built once, at import: the coverage assertion inside ``build_family`` (every
#: declared contract has an input model **and** a handler, and neither table
#: carries an entry for a contract that does not exist) runs here, so an
#: incomplete family fails at start-up instead of publishing a tool that errors
#: when called.
FAMILY: ToolFamily = build_family(
    family="foundation",
    declared=tuple(contract.operation_id for contract in FOUNDATION_TOOL_CONTRACTS),
    models=_OPERATION_MODELS,
    handlers=_OPERATION_HANDLERS,
    name_of=tool_name,
    spec_of=_spec_of,
)

#: The family's specs (declaration order of the contracts registry).
FOUNDATION_SPECS: dict[str, ToolSpec] = FAMILY.specs

#: The family's handlers, keyed by tool name.
FOUNDATION_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = FAMILY.handlers

#: The family's tool names, in declaration order.
FOUNDATION_TOOL_NAMES: tuple[str, ...] = FAMILY.names

__all__ = [
    "FOUNDATION_HANDLERS",
    "FOUNDATION_SPECS",
    "FOUNDATION_TOOL_NAMES",
    "FAMILY",
    "PREFIX",
    "tool_name",
]
