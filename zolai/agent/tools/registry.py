"""Server-side tool registry for the agent runtime (P3 §C).

The client never supplies a tool list: this registry is the only source of
``## TOOLS`` schemas and the executor's allow-list.  The :data:`SPECS`
definitions are **canonical** — the OpenAI ``tools`` array
(:func:`openai_tool_defs`), the MCP ``tools/list`` entries
(:func:`mcp_tools`) and the prompt section (:func:`json_schemas`) are all
rendered from them (see :mod:`zolai.agent.tools.render`).  Classification:

- **public** — retrieval/analysis only (safe for the anonymous assistant):
  ``rag_search``, ``dictionary_lookup``, ``word_evidence``, ``verse_lookup``,
  ``grammar_check``, plus the three Myanmar lookups
  ``search_myanmar_dictionary``, ``translate_zo_to_myanmar``,
  ``translate_myanmar_to_zo``
- **member** — the public set above, the remaining analysis tools
  (``analyze_text``, ``word_related``), the whole generated ``foundation_*``
  family (one tool per typed ``/api/v1/foundation/*`` contract — see
  :mod:`zolai.agent.tools.foundation`), the ``language`` family
  (``lex_search``, ``word_detail``, ``syllable_segment``, ``pos_tag``,
  ``zvs_validate`` — see :mod:`zolai.agent.tools.language`) and the
  ``learning`` reads (``study_*``, ``predict_*``, ``suggest_*`` — see
  :mod:`zolai.agent.tools.learning`).  The language
  tools are **not** anonymous: widening the anonymous set is a founder decision
  (registry-extension §5.7) and was not taken here.
- **admin extras** — knowledge-base introspection + proposal surfaces:
  ``knowledge_stats``, ``kb_research``, ``review_queue_submit``,
  ``provider_status`` (names/enabled/model — **never keys**), the
  ``learning`` write-back channels (``study_record_review``,
  ``contribute_word``, ``submit_usage_example``) and the read-only learning
  ledger reads (``learning_run_status``, ``learning_version_history``,
  ``learning_rag_status`` — see :mod:`zolai.agent.tools.learning_runs`).
- **optional** — ``web_search`` (only when ``ZOLAI_AGENT_WEB_SEARCH=1`` and
  the engine mode allows network; the handler enforces both gates)
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .foundation import FOUNDATION_SPECS, FOUNDATION_TOOL_NAMES
from .language import LANGUAGE_SPECS, LANGUAGE_TOOL_NAMES
from .learning import (
    LEARNING_READ_TOOLS,
    LEARNING_SPECS,
    LEARNING_TOOL_NAMES,
)
from .learning_runs import LEARNING_RUN_SPECS, LEARNING_RUN_TOOL_NAMES
from .render import render_many
from .types import ToolSpec, ToolSpecError

_OBJ = "object"

# ── destructiveHint policy ───────────────────────────────────────────────────
#
# Every spec below states ``destructive`` explicitly; an undeclared spec would
# still fail closed (:attr:`ToolSpec.is_destructive` → ``True``), but a
# published tool is never allowed to leave the decision to a default — see
# :func:`assert_specs_are_publishable`.
#
# The rule this registry enforces is one-directional and stated up front:
# **a tool that writes, mutates, deletes, activates, rotates, revokes,
# enables/disables, retunes, imports or exports is ``destructive=True``.**
# ``destructiveHint`` is the only hint an LLM client reads before it decides
# whether a call needs a human's confirmation, so it is published on the side
# of *over*-claiming: a tool that appends a row is announced as one that may
# change server state, because that is what it does.  ``readOnlyHint``
# (which follows ``writes``) already carries the narrower "this is not a
# read" fact.
#
# :func:`assert_specs_are_publishable` turns the rule into an import-time gate
# in both directions — ``writes=True`` without ``destructive=True`` is refused,
# and so is ``destructive=True`` without ``writes=True`` — so the published
# surface can never carry a tool whose two classification flags disagree, and a
# tool added later without the flag fails at start-up rather than advertising
# itself as harmless.


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": _OBJ, "properties": properties, "required": required or []}


_PUB_QUERY = _obj(
    {
        "query": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        "source_filter": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "dictionary",
                    "dictionary_en_zo",
                    "bible_verses",
                    "phrases",
                    "grammar_patterns",
                ],
            },
        },
    },
    required=["query"],
)
_WORD = _obj({"word": {"type": "string", "minLength": 1}}, required=["word"])
_TEXT = _obj({"text": {"type": "string", "minLength": 1}}, required=["text"])
_REF = _obj({"ref": {"type": "string", "minLength": 1}}, required=["ref"])
#: Myanmar search takes a free-text query (Myanmar script) + a bounded limit.
_MY_QUERY = _obj(
    {
        "query": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    required=["query"],
)

#: The three Myanmar/Burmese lookups.  Public: they read the same dictionary and
#: Bible tables the public Zolai reads do.  Their names are frozen to the ones
#: the retired Cloudflare Worker published, so a client configuration written
#: against that surface keeps working unchanged.
MYANMAR_TOOLS: tuple[str, ...] = (
    "search_myanmar_dictionary",
    "translate_zo_to_myanmar",
    "translate_myanmar_to_zo",
)

_MYANMAR_SPECS: dict[str, ToolSpec] = {
    "search_myanmar_dictionary": ToolSpec(
        name="search_myanmar_dictionary",
        description=(
            "Search the dictionary and the Bible corpus by Myanmar (Burmese) text. Returns the "
            "matching Zolai/English rows it actually found; an empty list means the corpus has "
            "no such Myanmar text — it is never a guess."
        ),
        parameters=_MY_QUERY,
        title="Search the Myanmar dictionary",
        destructive=False,
    ),
    "translate_zo_to_myanmar": ToolSpec(
        name="translate_zo_to_myanmar",
        description=(
            "Translate one Zolai word into Myanmar (Burmese) using the dictionary. Returns "
            "found=false with a note when the dictionary holds no Myanmar translation for the "
            "word — never an invented one."
        ),
        parameters=_WORD,
        title="Translate Zolai to Myanmar",
        destructive=False,
    ),
    "translate_myanmar_to_zo": ToolSpec(
        name="translate_myanmar_to_zo",
        description=(
            "Translate one Myanmar (Burmese) word into Zolai using the dictionary. Returns "
            "found=false with a note when the dictionary holds no Zolai entry for the word — "
            "never an invented one."
        ),
        parameters=_WORD,
        title="Translate Myanmar to Zolai",
        destructive=False,
    ),
}

#: Public (retrieval-only) tools — safe for anonymous callers.
PUBLIC_TOOLS: tuple[str, ...] = (
    "rag_search",
    "word_evidence",
    "word_related",
    "analyze_text",
    "grammar_check",
    "dictionary_lookup",
    "verse_lookup",
) + MYANMAR_TOOLS

#: The anonymous subset of :data:`PUBLIC_TOOLS` (the assistant's public set).
ANONYMOUS_TOOLS: frozenset[str] = frozenset(
    {
        "rag_search",
        "dictionary_lookup",
        "word_evidence",
        "verse_lookup",
        "grammar_check",
        *MYANMAR_TOOLS,
    }
)

#: Admin extras — knowledge-base introspection + proposal surfaces, plus the
#: read-only local-learning ledger (runs, versions, vector index).
ADMIN_TOOLS: tuple[str, ...] = (
    "knowledge_stats",
    "kb_research",
    "review_queue_submit",
    "provider_status",
) + LEARNING_RUN_TOOL_NAMES

#: Optional network tool (handler-gated on env + engine mode).
OPTIONAL_TOOLS: tuple[str, ...] = ("web_search",)

SPECS: dict[str, ToolSpec] = {
    "rag_search": ToolSpec(
        name="rag_search",
        description=(
            "Hybrid search across dictionary, Bible, phrases and grammar patterns. Optional "
            "source_filter limits results to specific tables: dictionary, dictionary_en_zo, "
            "bible_verses, phrases, grammar_patterns."
        ),
        parameters=_PUB_QUERY,
        destructive=False,
    ),
    "word_evidence": ToolSpec(
        name="word_evidence",
        description="Multi-source attestation/evidence records for a Zolai word.",
        parameters=_WORD,
        destructive=False,
    ),
    "word_related": ToolSpec(
        name="word_related",
        description="Ranked words related to a headword (family + same-POS neighbours).",
        parameters=_WORD,
        destructive=False,
    ),
    "analyze_text": ToolSpec(
        name="analyze_text",
        description="Word-by-word analysis of a Zolai sentence (interlinear glossing).",
        parameters=_TEXT,
        destructive=False,
    ),
    "grammar_check": ToolSpec(
        name="grammar_check",
        description="Validate text against ZVS 2018 orthography and SOV/grammar rules.",
        parameters=_TEXT,
        destructive=False,
    ),
    "dictionary_lookup": ToolSpec(
        name="dictionary_lookup",
        description="Exact-then-prefix lookup in the Zolai↔English dictionary.",
        parameters=_WORD,
        destructive=False,
    ),
    "verse_lookup": ToolSpec(
        name="verse_lookup",
        description="Fetch a parallel Bible verse by reference (e.g. 'GEN 1:1').",
        parameters=_REF,
        destructive=False,
    ),
    "knowledge_stats": ToolSpec(
        name="knowledge_stats",
        description="Aggregate row counts of the knowledge base tables.",
        parameters=_obj({}),
        admin_only=True,
        scope="catalog:read",
        destructive=False,
    ),
    "kb_research": ToolSpec(
        name="kb_research",
        description="Search stored hypotheses/claims for prior knowledge on a topic.",
        parameters=_PUB_QUERY,
        admin_only=True,
        scope="catalog:read",
        destructive=False,
    ),
    "review_queue_submit": ToolSpec(
        name="review_queue_submit",
        description="Submit a proposal candidate to the human foundation review queue.",
        parameters=_obj(
            {
                "fact_type": {"type": "string", "minLength": 1},
                "fact_key": {"type": "string", "minLength": 1},
                "priority": {"type": "integer", "minimum": 0, "maximum": 10},
            },
            required=["fact_type", "fact_key"],
        ),
        admin_only=True,
        writes=True,
        # Durable write: appends a candidate to the human foundation review
        # queue (FoundationReviewQueueRepository.add_to_queue).  It changes
        # durable server state, so it is declared destructive — see the policy
        # comment above; ``assert_specs_are_publishable`` refuses this spec if
        # the two flags ever disagree.
        destructive=True,
    ),
    "provider_status": ToolSpec(
        name="provider_status",
        description="List AI providers (name, enabled, active, model) — never API keys.",
        parameters=_obj({}),
        admin_only=True,
        destructive=False,
    ),
    "web_search": ToolSpec(
        name="web_search",
        description="Web search (opt-in: ZOLAI_AGENT_WEB_SEARCH=1 + non-rule engine mode).",
        parameters=_PUB_QUERY,
        admin_only=True,
        requires_network=True,
        destructive=False,
    ),
    **_MYANMAR_SPECS,
    # The generated ``foundation_*`` family — one tool per typed
    # ``/api/v1/foundation/*`` contract, so a tool can never disagree with the
    # route it wraps (zolai/agent/tools/foundation.py).
    **FOUNDATION_SPECS,
    # B1a — the language family: five bilingual/linguistic reads that call the
    # very functions their REST twins call (zolai/agent/tools/language.py).
    # Member tier only; the anonymous set is untouched.
    **LANGUAGE_SPECS,
    # The learning family: study/quiz/SRS/ngram reads (member+) plus the
    # SM-2 and corpus contribution write-back channels, which are admin-only
    # specs (zolai/agent/tools/learning.py) — the member list stays read-only.
    **LEARNING_SPECS,
    # Admin-only reads over the local learning cycle — run ledger, knowledge
    # versions, RAG index state (zolai/agent/tools/learning_runs.py).
    **LEARNING_RUN_SPECS,
}


def get_tool(name: str) -> ToolSpec | None:
    """Registered spec for ``name`` (``None`` for unknown tools)."""
    return SPECS.get(str(name or "").strip())


def assert_specs_are_publishable(specs: dict[str, ToolSpec]) -> None:
    """Import-time gate on the published surface.

    Raises :class:`~zolai.agent.tools.types.ToolSpecError` when a registered
    tool would be advertised with a classification nobody chose:

    - ``destructive`` left undeclared (the fail-closed default would ship a
      read tool claiming it might destroy data, and a write tool claiming it
      is harmless);
    - ``writes=True`` without ``destructive=True`` — **a tool that writes is
      destructive** (see the policy comment at the top of this module), so a
      writing tool published as harmless is exactly the defect this batch
      closes;
    - ``destructive=True`` without ``writes=True`` — destruction is the
      *stronger* claim, so it must be backed by the weaker one;
    - a registry key that disagrees with the spec's own name, or an anonymous
      tool that is also admin-only.
    """
    problems: list[str] = []
    for key, spec in specs.items():
        if spec.name != key:
            problems.append(f"{key!r}: spec is named {spec.name!r}")
        if spec.destructive is None:
            problems.append(f"{key!r}: destructive is undeclared")
        if spec.writes and not spec.is_destructive:
            problems.append(f"{key!r}: writes=True without destructive=True")
        if spec.is_destructive and not spec.writes:
            problems.append(f"{key!r}: destructive=True without writes=True")
        if spec.admin_only and key in ANONYMOUS_TOOLS:
            problems.append(f"{key!r}: admin_only and anonymous at once")
    if problems:
        raise ToolSpecError(
            "tool registry is not publishable as declared: " + "; ".join(sorted(problems))
        )


def tool_scope_allows(spec: ToolSpec, granted: Iterable[str] | None) -> bool:
    """Narrowing-only check: do ``granted`` satisfy this spec's own scope?

    ``granted=None`` (no scopes supplied) keeps every tool — a *tier* is not a
    scope grant, so the registry must never widen on its own.  A tool with no
    ``scope`` is always kept; a tool whose ``scope`` the caller does not hold is
    dropped **from the tool set**, so the client never sees a tool it cannot
    call (as opposed to a tool that errors at call time).
    """
    if granted is None or spec.scope is None:
        return True
    return spec.scope in set(granted)


def allow_list(scope: str, *, granted: Iterable[str] | None = None) -> frozenset[str]:
    """Tool allow-list for a caller scope.

    ``public`` → the anonymous retrieval/analysis set (retrieval tools +
    3 Myanmar lookups); ``member`` → every Zolai analysis tool, the generated
    ``foundation_*`` family, the ``language`` family and the ``learning``
    family reads; ``admin`` → that plus the ``learning`` write-back channels,
    the admin extras and the optional web tool.

    ``granted`` is the caller's own scope set.  It can only ever *narrow* the
    tier (see :func:`tool_scope_allows`), so the result is always a subset of
    the tier's set — a per-tool ``scope`` never widens what a role may call.
    """
    if scope == "public":
        tier: frozenset[str] = ANONYMOUS_TOOLS
    elif scope == "member":
        tier = (
            frozenset(PUBLIC_TOOLS)
            | frozenset(FOUNDATION_TOOL_NAMES)
            | frozenset(LANGUAGE_TOOL_NAMES)
            | frozenset(LEARNING_READ_TOOLS)
        )
    elif scope == "admin":
        tier = (
            frozenset(PUBLIC_TOOLS)
            | frozenset(FOUNDATION_TOOL_NAMES)
            | frozenset(LANGUAGE_TOOL_NAMES)
            | frozenset(LEARNING_TOOL_NAMES)
            | frozenset(ADMIN_TOOLS)
            | frozenset(OPTIONAL_TOOLS)
        )
    else:
        raise ValueError(f"unknown tool scope {scope!r}; expected public|member|admin")
    if granted is None:
        return tier
    kept = set()
    for name in tier:
        spec = SPECS.get(name)
        if spec is not None and tool_scope_allows(spec, granted):
            kept.add(name)
    return frozenset(kept)


#: Role → tool tier.  Three tiers serve four roles: ``superadmin`` earns the
#: ``admin`` inventory (its extra power is *granting roles*, which is a route
#: guard, not a tool) and an unknown role earns ``member`` — never a tier that
#: :func:`allow_list` would raise on, and never the anonymous set for a caller
#: the server just identified.
ROLE_TO_TIER: dict[str, str] = {
    "anonymous": "public",
    "member": "member",
    "admin": "admin",
    "superadmin": "admin",
}


def tool_tier_for_record(record: dict[str, Any] | None) -> str:
    """The tool tier for an auth record, via the **one** role resolver.

    Delegates to :func:`zolai.api.rbac.role_for` — the same function every
    route, middleware check and ``require_scope`` guard already uses — so this
    module never becomes a second source of truth about who a caller is.
    ``anonymous`` maps onto the ``public`` tier; the key-less record is the
    only way to land there.

    A record carrying no readable ``scopes`` list yields ``None`` grants, which
    :func:`allow_list` treats as "no narrowing" — the key stays usable and only
    the tier decides, exactly as before this field existed.

    **Roles are four, tool tiers are three, and that is the whole design.**
    ``superadmin`` is an *authorization* concept — the power to grant roles —
    not a larger inventory of tools.  It therefore maps onto the ``admin`` set,
    and :data:`ROLE_TO_TIER` is the only place that mapping is written, so no
    caller can hand :func:`allow_list` a tier it would raise on.
    """
    from ...api.rbac import role_for  # lazy: no import cycle

    return ROLE_TO_TIER.get(role_for(record), "member")


def allow_list_for_record(record: dict[str, Any] | None) -> frozenset[str]:
    """Tool allow-list for an auth record: tier **and** per-tool scopes.

    The single entry point for "what may this caller see?".  It composes the
    two things the registry already knows — the role's tier (resolved by
    :func:`tool_tier_for_record` through ``rbac.role_for``) and the record's own
    granted actions — so a tool whose ``scope`` the caller lacks is **absent
    from the tool set** rather than failing at call time.
    """
    scopes = record.get("scopes") if isinstance(record, dict) else None
    granted = scopes if isinstance(scopes, (list, tuple, set, frozenset)) else None
    return allow_list(tool_tier_for_record(record), granted=granted)


assert_specs_are_publishable(SPECS)


def names_for(scope: str) -> list[str]:
    """Tool names for ``scope`` (registry declaration order preserved)."""
    return ordered_names(allow_list(scope))


def ordered_names(allow: Iterable[str]) -> list[str]:
    """Registered tool names inside ``allow``, in registry declaration order."""
    allowed = set(allow)
    return [name for name in SPECS if name in allowed]


def _specs_for(names: Iterable[str]) -> list[ToolSpec]:
    """Resolve names to specs, silently skipping unknown names."""
    out: list[ToolSpec] = []
    for name in names:
        spec = get_tool(name)
        if spec is not None:
            out.append(spec)
    return out


def json_schemas(names: list[str] | tuple[str, ...] | frozenset[str]) -> list[dict[str, Any]]:
    """JSON-Schema list for the ``## TOOLS`` prompt section."""
    return [spec.json_schema() for spec in _specs_for(names)]


def openai_tool_defs(names: Iterable[str]) -> list[dict[str, Any]]:
    """OpenAI ``tools`` array for ``names`` (one canonical definition each)."""
    return render_many(_specs_for(names), "openai")


def mcp_tools(names: Iterable[str]) -> list[dict[str, Any]]:
    """MCP ``tools/list`` entries for ``names`` (``inputSchema`` + annotations)."""
    return render_many(_specs_for(names), "mcp")


def native_tool_defs(names: list[str] | tuple[str, ...] | frozenset[str]) -> list[dict[str, Any]]:
    """OpenAI native ``tools`` array (only ever sent to openai/openrouter).

    Thin alias of :func:`openai_tool_defs` kept for the existing call sites —
    same list, same order, same shapes.
    """
    return openai_tool_defs(names)
