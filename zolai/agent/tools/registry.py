"""Server-side tool registry for the agent runtime (P3 §C).

The client never supplies a tool list: this registry is the only source of
``## TOOLS`` schemas and the executor's allow-list.  Classification:

- **public** — retrieval/analysis only (safe for the anonymous assistant):
  ``rag_search``, ``word_evidence``, ``word_related``, ``analyze_text``,
  ``grammar_check``, ``dictionary_lookup``, ``verse_lookup``
- **admin extras** — knowledge-base introspection + proposal surfaces:
  ``knowledge_stats``, ``kb_research``, ``review_queue_submit``,
  ``provider_status`` (names/enabled/model — **never keys**)
- **optional** — ``web_search`` (only when ``ZOLAI_AGENT_WEB_SEARCH=1`` and
  the engine mode allows network; the handler enforces both gates)
"""

from __future__ import annotations

from typing import Any

from .types import ToolSpec

_OBJ = "object"


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": _OBJ, "properties": properties, "required": required or []}


_PUB_QUERY = _obj(
    {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
)
_WORD = _obj({"word": {"type": "string", "minLength": 1}})
_TEXT = _obj({"text": {"type": "string", "minLength": 1}})
_REF = _obj({"ref": {"type": "string", "minLength": 1}})

#: Public (retrieval-only) tools — safe for anonymous callers.
PUBLIC_TOOLS: tuple[str, ...] = (
    "rag_search",
    "word_evidence",
    "word_related",
    "analyze_text",
    "grammar_check",
    "dictionary_lookup",
    "verse_lookup",
)

#: Admin extras — knowledge-base introspection + proposal surfaces.
ADMIN_TOOLS: tuple[str, ...] = (
    "knowledge_stats",
    "kb_research",
    "review_queue_submit",
    "provider_status",
)

#: Optional network tool (handler-gated on env + engine mode).
OPTIONAL_TOOLS: tuple[str, ...] = ("web_search",)

SPECS: dict[str, ToolSpec] = {
    "rag_search": ToolSpec(
        name="rag_search",
        description="Hybrid search across dictionary, Bible, phrases and grammar patterns.",
        parameters=_PUB_QUERY,
    ),
    "word_evidence": ToolSpec(
        name="word_evidence",
        description="Multi-source attestation/evidence records for a Zolai word.",
        parameters=_WORD,
    ),
    "word_related": ToolSpec(
        name="word_related",
        description="Ranked words related to a headword (family + same-POS neighbours).",
        parameters=_WORD,
    ),
    "analyze_text": ToolSpec(
        name="analyze_text",
        description="Word-by-word analysis of a Zolai sentence (interlinear glossing).",
        parameters=_TEXT,
    ),
    "grammar_check": ToolSpec(
        name="grammar_check",
        description="Validate text against ZVS 2018 orthography and SOV/grammar rules.",
        parameters=_TEXT,
    ),
    "dictionary_lookup": ToolSpec(
        name="dictionary_lookup",
        description="Exact-then-prefix lookup in the Zolai↔English dictionary.",
        parameters=_WORD,
    ),
    "verse_lookup": ToolSpec(
        name="verse_lookup",
        description="Fetch a parallel Bible verse by reference (e.g. 'GEN 1:1').",
        parameters=_REF,
    ),
    "knowledge_stats": ToolSpec(
        name="knowledge_stats",
        description="Aggregate row counts of the knowledge base tables.",
        parameters=_obj({}),
        admin_only=True,
    ),
    "kb_research": ToolSpec(
        name="kb_research",
        description="Search stored hypotheses/claims for prior knowledge on a topic.",
        parameters=_PUB_QUERY,
        admin_only=True,
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
    ),
    "provider_status": ToolSpec(
        name="provider_status",
        description="List AI providers (name, enabled, active, model) — never API keys.",
        parameters=_obj({}),
        admin_only=True,
    ),
    "web_search": ToolSpec(
        name="web_search",
        description="Web search (opt-in: ZOLAI_AGENT_WEB_SEARCH=1 + non-rule engine mode).",
        parameters=_PUB_QUERY,
        admin_only=True,
        requires_network=True,
    ),
}


def get_tool(name: str) -> ToolSpec | None:
    """Registered spec for ``name`` (``None`` for unknown tools)."""
    return SPECS.get(str(name or "").strip())


def allow_list(scope: str) -> frozenset[str]:
    """Tool allow-list for a caller scope.

    ``public`` → the 5 retrieval tools the anonymous assistant may use;
    ``member`` → the full public set;
    ``admin``  → public + admin extras + the optional web tool.
    """
    if scope == "public":
        return frozenset(
            {"rag_search", "dictionary_lookup", "word_evidence", "verse_lookup", "grammar_check"}
        )
    if scope == "member":
        return frozenset(PUBLIC_TOOLS)
    if scope == "admin":
        return frozenset(PUBLIC_TOOLS) | frozenset(ADMIN_TOOLS) | frozenset(OPTIONAL_TOOLS)
    raise ValueError(f"unknown tool scope {scope!r}; expected public|member|admin")


def names_for(scope: str) -> list[str]:
    """Sorted tool names for ``scope`` (registry declaration order preserved)."""
    allowed = allow_list(scope)
    return [name for name in SPECS if name in allowed]


def json_schemas(names: list[str] | tuple[str, ...] | frozenset[str]) -> list[dict[str, Any]]:
    """JSON-Schema list for the ``## TOOLS`` prompt section."""
    out: list[dict[str, Any]] = []
    for name in names:
        spec = get_tool(name)
        if spec is not None:
            out.append(spec.json_schema())
    return out


def native_tool_defs(names: list[str] | tuple[str, ...] | frozenset[str]) -> list[dict[str, Any]]:
    """OpenAI native ``tools`` array (only ever sent to openai/openrouter)."""
    out: list[dict[str, Any]] = []
    for name in names:
        spec = get_tool(name)
        if spec is not None:
            out.append(spec.native_def())
    return out
