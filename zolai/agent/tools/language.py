"""The language tool family — B1a's five bilingual/linguistic reads.

Every spec here wraps a capability that **already exists in-process**; nothing
is re-implemented and nothing shells out to HTTP (``.pcore/specs/registry-extension.md``
§2).  The handler for each tool is one call into the very function the REST
twin routes, or into the same pure capability those routes call:

| tool | calls (verified in source) | REST twin |
|---|---|---|
| ``lex_search`` | ``zolai.api.lexicon_router.lexicon_search`` | ``GET /api/v1/lexicon/search`` |
| ``word_detail`` | ``UnifiedRetriever.query_word`` → ``WordDetailResponse`` | ``GET /api/v1/word/{word}`` |
| ``syllable_segment`` | ``zolai.syllable.segment`` | ``GET /api/v1/linguistics/syllable`` |
| ``pos_tag`` | ``zolai.pos_tagger.get_pos_tagger().tag`` | ``GET /api/v1/linguistics/pos`` |
| ``zvs_validate`` | ``zolai.zvs.validate`` — the **strict** validator | tool-only |

**Not here — the sixth tool of the batch.**  A grammar/SOV structure check is
already published under two names: ``foundation_validate_structure`` *is*
``GrammarEditor().validate_sentence_structure`` (foundation.py ``_validate_structure``)
and ``grammar_check`` *is* ``GrammarEditor().validate_text``, which runs that
same per-sentence structure validator over every sentence and returns its
``tokens`` / ``verb_position`` / ``zvs_compliant``.  Adding a third name for one
capability is the defect the registry exists to prevent, so the batch skipped it.

Authority: **member tier** — the anonymous set is a founder decision
(registry-extension §5.7) and was not widened here.  Every tool is a read
(``writes=False``, ``destructive=False``); none takes a credential field, which
is the open-proxy risk the catalogue's M3 note describes.

Scopes: ``scope`` is set from the frozen 33-action vocabulary **only where the
backing route declares ``require_scope``** — ``lex_search``/``word_detail``/
``syllable_segment`` inherit their routes' ``dataset:read``, ``pos_tag`` the
route's ``pos:read``.  ``zvs_validate`` has no route, so it carries none: a
scope with no route to be consistent with would be an invented permission.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .family import assert_handlers_cover_specs
from .types import ToolError, ToolErrorCode, ToolSpec

_OBJ = "object"


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": _OBJ, "properties": properties, "required": required or []}


#: Honest-empty states are additive keys: the REST twin's own payload keys are
#: never removed, renamed or reordered, so a client that only reads
#: ``items`` / ``word`` / ``tokens`` keeps working unchanged.
_NO_MATCH_REASON = (
    "No dictionary entry matched this query text. That is a query miss on a "
    "populated dictionary — it is not an empty or unavailable dictionary."
)
_NO_EVIDENCE_REASON = (
    "No evidence was assembled for this headword in the indexed corpus. That is "
    "'nothing recorded', not 'this word does not exist' and not 'this word is "
    "never used': an all-zero frequency and empty lists are a backfill/index gap, "
    "not a measurement of absence."
)

_LEX_SEARCH_SCHEMA = _obj(
    {
        "q": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Free text matched case-insensitively against zolai/english (the ZO side) "
                "and headword/translations (the EN side). LIKE wildcards are escaped, so "
                "'%' and '_' are literal characters."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "Page size (default 50, capped at 200).",
        },
        "cursor": {
            "type": "string",
            "description": (
                "Opaque \"<id>:<table>\" value copied verbatim from the previous page's "
                "next_cursor. A bare integer or an unknown table is rejected, never "
                "silently degraded."
            ),
        },
    },
    required=["q"],
)

_WORD_SCHEMA = _obj(
    {
        "word": {
            "type": "string",
            "minLength": 1,
            "description": "A single Zolai (or English) headword.",
        },
    },
    required=["word"],
)

_WORD_LIMIT_SCHEMA = _obj(
    {
        "word": {"type": "string", "minLength": 1, "description": "The headword to look up."},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Cap on sub-results per evidence source (default 20).",
        },
    },
    required=["word"],
)

_TEXT_SCHEMA = _obj(
    {
        "text": {
            "type": "string",
            "minLength": 1,
            "description": "A Zolai sentence or passage.",
        },
    },
    required=["text"],
)

_ZVS_SCHEMA = _obj(
    {
        "text": {"type": "string", "minLength": 1, "description": "The text to validate."},
        "context": {
            "type": "string",
            "enum": ["auto", "modern", "historical", "scripture"],
            "description": (
                "Exception registry to apply. 'modern' refuses every historical exception; "
                "'historical' and 'scripture' allow Bible-only exceptions; 'auto' (default) "
                "detects from the text."
            ),
        },
        "source": {
            "type": "string",
            "description": "Provenance label echoed into the report (defaults to '<mcp>').",
        },
        "categories": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Restrict validation to these rule categories (default: documented rules).",
        },
        "disabled_rules": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Rule ids to suppress for this run.",
        },
        "include_noisy": {
            "type": "boolean",
            "description": "Include the lower-confidence phonotactic noise rules (default false).",
        },
    },
    required=["text"],
)


LANGUAGE_SPECS: dict[str, ToolSpec] = {
    "lex_search": ToolSpec(
        name="lex_search",
        title="Search the bilingual dictionary",
        description=(
            "Partial bilingual dictionary search across BOTH dictionary tables (dictionary "
            "ZO->EN and dictionary_en_zo EN->ZO), returned as one merged, cursor-paginated "
            "stream. Use this when you are not certain of a headword's spelling, or to "
            "enumerate every entry matching a stem; for one known headword prefer "
            "dictionary_lookup. What it matches: the ZO side on its zolai/english columns, "
            "the EN side on headword AND the whole translations string — so a query for "
            "'pasian' also returns entries whose English translation merely mentions "
            "'Pasian'. Ordering is row id ASC across the two tables, NOT a relevance "
            "ranking, so the first hit can be a translation-text match rather than the "
            "headword you meant: scan the page instead of trusting position one. Limits: "
            "LIKE wildcards in q are escaped so % and _ are literal; next_cursor is null on "
            "the last page, and that — not a short page — is the end-of-stream signal. An "
            "empty items list means nothing matched the query, never that the dictionary is "
            "empty (state='no_match')."
        ),
        parameters=_LEX_SEARCH_SCHEMA,
        scope="dataset:read",
        destructive=False,
    ),
    "word_detail": ToolSpec(
        name="word_detail",
        title="Word detail",
        description=(
            "The flagship single-word view: forms, corpus frequency, document frequency, POS "
            "tags, morphology, examples, collocations, grammar usage, sources and a confidence "
            "score, assembled from every indexed corpus for one headword. Start here for 'tell "
            "me about this word'. Limits: forms and morphology are backfill gaps "
            "for many words — an empty list means 'no recorded variants in the "
            "indexed corpus', never 'this word has only one form'. Frequencies come "
            "from the corpus observation row: an entry with no such row reports 0, "
            "which is 'nothing recorded', not 'never occurs'. A zero confidence with "
            "populated fields means the pack assembled from evidence but has no calibrated "
            "score, not that the word is unknown. When nothing at all was assembled the "
            "result carries state='no_evidence' with a reason; an all-zero frequency is "
            "'nothing recorded', not 'never occurs'."
        ),
        parameters=_WORD_LIMIT_SCHEMA,
        scope="dataset:read",
        destructive=False,
    ),
    "syllable_segment": ToolSpec(
        name="syllable_segment",
        title="Segment a word into syllables",
        description=(
            "Split one Zolai word into syllables with the deterministic offline rule "
            "segmenter — no DB, no network, no model. The word is stripped and lowercased "
            "before segmentation, and the response echoes the original word alongside the "
            "syllables it produced. Use it for pronunciation help and for checking a "
            "syllable count. Limits: accuracy was measured at 98.49% over 1,725 clean words "
            "(100% on 2-5 syllable words); residual errors are almost all single-syllable "
            "proper names and English loans, so a surprising split is usually a non-Zolai "
            "token rather than a bug. Prefer foundation_analyze_phonology for tones, sandhi "
            "and phonotactic validity; syllables here are not morphemes."
        ),
        parameters=_WORD_SCHEMA,
        scope="dataset:read",
        destructive=False,
    ),
    "pos_tag": ToolSpec(
        name="pos_tag",
        title="POS tag a sentence",
        description=(
            "Part-of-speech tag a Zolai sentence token by token: whitespace tokenization "
            "plus the rule/lexicon tagger, with closed classes resolved first (agreement "
            "'a', ergative 'in', negation 'kei', question 'hiam', tense/aspect markers), "
            "each token returned as {word, pos}. Use it for grammar teaching and for 'what "
            "part of speech is X'. Limits: tags come from a lexicon plus suffix heuristics, "
            "so an unseen word is a guess — present low-confidence tags as guesses. This is "
            "the rule/lexicon tagger, NOT the CRF model in zolai/pos_tagger/train.py; do not "
            "quote that model's cross-validation accuracy for these results, and its gold "
            "set was auto-annotated from the Bible and dictionary rather than by speakers."
        ),
        parameters=_TEXT_SCHEMA,
        scope="pos:read",
        destructive=False,
    ),
    "zvs_validate": ToolSpec(
        name="zvs_validate",
        title="Validate ZVS 2018 orthography",
        description=(
            "Strict ZVS 2018 orthography check with the full rule set and the "
            "historical/scripture exception registry. Flags the forbidden forms "
            "(pathian->pasian, ram->gam, fapa->tapa, bawipa->topa, siangpahrang->kumpipa, "
            "cu/cun->tua, suah->suahtakna, nunnak->nuntakna) plus the documented phonotactic "
            "rules, each with a stable rule_id and a preferred replacement. context controls "
            "the exception registry: modern refuses every historical exception, "
            "historical/scripture allow Bible-only exceptions, auto (default) detects. "
            "Limits: this FINDS problems and NEVER rewrites your text — pass rule_id through "
            "unchanged rather than paraphrasing it. Phonetic-noise rules are off unless "
            "include_noisy is set. 'ram' and 'fapa' are also a personal name and a legitimate "
            "word elsewhere, so report those as 'check the context', not 'wrong'. "
            "is_valid=true means 'no violation of the enabled rules', not 'this text is "
            "idiomatic Zolai'."
        ),
        parameters=_ZVS_SCHEMA,
        # Tool-only: no REST route backs it (the /desktop route shells out to a
        # sibling repo), so there is no route scope to inherit. See the module
        # docstring.
        scope=None,
        destructive=False,
    ),
}

#: Declaration order == publication order inside the member/admin tiers.
LANGUAGE_TOOL_NAMES: tuple[str, ...] = tuple(LANGUAGE_SPECS)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _require_str(inp: dict[str, Any], key: str) -> str:
    value = str(inp.get(key) or "").strip()
    if not value:
        raise ToolError(f"{key} is required")
    return value


def _clamp_int(inp: dict[str, Any], key: str, default: int, maximum: int) -> int:
    """Clamp an optional integer the way the published schema says it is clamped.

    The route's own ``Query(ge=, le=)`` bounds are enforced by FastAPI's
    dependency parser, which a direct in-process call bypasses — so the tool
    applies them itself instead of trusting the caller's number.
    """
    raw = inp.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


# ── Handlers ─────────────────────────────────────────────────────────────────
#
# Each handler names the exact symbol it calls so the sentinel-substitution
# tests in tests/test_tool_language.py can prove the call reaches that service.


def _lex_search(inp: dict[str, Any]) -> Any:
    from fastapi import HTTPException, Response

    from ...api.lexicon_router import lexicon_search

    q = _require_str(inp, "q")
    limit = _clamp_int(inp, "limit", 50, 200)
    cursor = str(inp.get("cursor") or "").strip() or None
    try:
        out = lexicon_search(Response(), q=q, limit=limit, cursor=cursor)
    except HTTPException as exc:
        # The only HTTPException this route raises is a malformed cursor, and
        # the cursor is opaque: a forged one must be a typed caller error, not
        # a stack trace on the wire.
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        if detail.get("error") == "invalid_cursor":
            raise ToolError(
                "cursor is opaque: copy next_cursor back verbatim "
                "(expected '<id>:<table>' with a known table name)"
            ) from exc
        raise ToolError(f"lexicon search rejected the request: {detail}") from exc
    except OSError as exc:
        raise ToolError(
            "The dictionary is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    if not out.get("items"):
        # Additive honesty key: {items: []} alone reads as "the dictionary is
        # empty" to a model that has no way to tell a query miss from a dead
        # source.  Every original key is preserved.
        return {**out, "state": "no_match", "reason": _NO_MATCH_REASON}
    return out


def _word_detail(inp: dict[str, Any]) -> Any:
    from ...api.rag_router import WordDetailResponse, get_retriever

    word = _require_str(inp, "word")
    limit = _clamp_int(inp, "limit", 20, 100)
    # ``query_word`` returns an EvidencePack (a dataclass whose ``evidence_items``
    # the wire never carries); reshaping through the route's own response model
    # is what keeps the tool's payload identical to GET /api/v1/word/{word}.
    pack = get_retriever().query_word(word, limit=limit)
    data = WordDetailResponse(**pack.__dict__).model_dump()
    if not data.get("sources") and not data.get("examples") and not data.get("frequency"):
        data["state"] = "no_evidence"
        data["reason"] = _NO_EVIDENCE_REASON
    return data


def _syllable_segment(inp: dict[str, Any]) -> Any:
    from ...syllable import segment

    word = _require_str(inp, "word")
    syllables = segment(word.strip().lower())
    return {"word": word, "syllables": syllables, "count": len(syllables)}


def _pos_tag(inp: dict[str, Any]) -> Any:
    from ...pos_tagger import get_pos_tagger

    text = _require_str(inp, "text")
    tagged = get_pos_tagger().tag(text)
    return {"text": text, "tokens": [{"word": w, "pos": p} for w, p in tagged]}


def _zvs_validate(inp: dict[str, Any]) -> Any:
    from ...zvs import validate

    text = _require_str(inp, "text")
    context = str(inp.get("context") or "auto").strip() or "auto"
    if context not in ("auto", "modern", "historical", "scripture"):
        raise ToolError("context must be one of: auto, modern, historical, scripture")
    categories = inp.get("categories")
    if categories is not None and not isinstance(categories, (list, tuple)):
        raise ToolError("categories must be a list of rule category names")
    disabled_rules = inp.get("disabled_rules")
    if disabled_rules is not None and not isinstance(disabled_rules, (list, tuple)):
        raise ToolError("disabled_rules must be a list of rule ids")
    try:
        report = validate(
            text,
            context=context,
            source=str(inp.get("source") or "<mcp>"),
            categories=list(categories) if categories is not None else None,
            disabled_rules=list(disabled_rules) if disabled_rules is not None else None,
            include_noisy=bool(inp.get("include_noisy")),
        )
    except ToolError:
        raise
    except Exception as exc:  # a dead DB behind get_bible_words(), not the caller's fault
        raise ToolError(
            "The ZVS 2018 rule registry is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {
        "is_valid": report.is_valid,
        "count": report.count,
        "context": context,
        # ``Violation`` has no ``suggestion`` field: the preferred replacement is
        # ``preferred``. Every field the wire needs is copied verbatim.
        "violations": [
            {
                "rule_id": v.rule_id,
                "category": v.category,
                "forbidden": v.forbidden,
                "preferred": v.preferred,
                "message": v.message,
                "start": v.start,
                "end": v.end,
            }
            for v in report.violations
        ],
    }


LANGUAGE_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "lex_search": _lex_search,
    "word_detail": _word_detail,
    "syllable_segment": _syllable_segment,
    "pos_tag": _pos_tag,
    "zvs_validate": _zvs_validate,
}

# Same coherence gate the foundation family runs: a spec with no handler, or a
# handler with no spec, is a start-up failure and never a published tool that
# errors on every call.
assert_handlers_cover_specs(LANGUAGE_SPECS, LANGUAGE_HANDLERS, subject="language tools")
