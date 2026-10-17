"""One canonical tool definition, rendered per wire format.

:class:`~zolai.agent.tools.types.ToolSpec` (built in
:mod:`zolai.agent.tools.registry`) is the **single source of truth** for a
tool's name, description and parameters.  Every wire shape is *rendered* from
that one definition here, so the two published surfaces can never drift:

- ``openai`` — the OpenAI ``tools`` array.  This is also the shape every
  OpenAI-*compatible* endpoint receives: the ``anthropic`` and ``google-gemini``
  catalog rows are served by those vendors' OpenAI-compatibility layers, so the
  wire is OpenAI's and no vendor-specific renderer exists (or should).
- ``mcp`` — an MCP ``tools/list`` entry: ``inputSchema`` plus honest
  ``annotations`` hints.

Purity: this module reads a :class:`ToolSpec` and returns plain dicts.  It
touches no database, no network and no registry state, so both renderers are
directly unit-testable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only (no runtime cycle)
    from .types import ToolSpec

#: Supported render formats (``registry.render_many`` dispatches on these).
FORMATS: tuple[str, ...] = ("openai", "mcp")

#: Human-readable MCP titles.  Presentation only — a tool missing from this map
#: falls back to its name with underscores spaced, so a 13th tool can never
#: render without a title.
_TITLES: dict[str, str] = {
    "rag_search": "RAG search",
    "word_evidence": "Word evidence",
    "word_related": "Word related",
    "analyze_text": "Analyze text",
    "grammar_check": "Grammar check",
    "dictionary_lookup": "Dictionary lookup",
    "verse_lookup": "Verse lookup",
    "knowledge_stats": "Knowledge stats",
    "kb_research": "Knowledge-base research",
    "review_queue_submit": "Submit to the review queue",
    "provider_status": "Provider status",
    "web_search": "Web search",
}


class RenderError(ValueError):
    """A spec cannot be rendered for a wire format (programmer error)."""


def _object_schema(spec: ToolSpec) -> dict[str, Any]:
    """The spec's parameter schema, asserted to be a JSON-Schema object.

    Both formats require an object schema: OpenAI rejects a non-object
    ``parameters`` and the MCP spec requires ``inputSchema.type == "object"``.
    Failing loudly here beats shipping a tool an LLM client cannot call.
    """
    schema = spec.parameters
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise RenderError(
            f"tool {spec.name!r}: parameters must be a JSON-Schema object "
            f"(got {type(schema).__name__})"
        )
    return schema


def title_for(spec: ToolSpec) -> str:
    """Human-readable title for ``spec`` (MCP ``title``)."""
    return _TITLES.get(spec.name) or spec.name.replace("_", " ").strip()


def render_openai(spec: ToolSpec) -> dict[str, Any]:
    """The OpenAI (and OpenAI-compatible) ``tools`` entry for ``spec``."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": _object_schema(spec),
        },
    }


def render_mcp(spec: ToolSpec) -> dict[str, Any]:
    """The MCP ``tools/list`` entry for ``spec``.

    ``annotations`` carries only hints the registry can actually justify:
    ``readOnlyHint``/``idempotentHint`` follow ``spec.writes`` (every registry
    tool is either a pure read or an additive proposal write — nothing here
    deletes, overwrites or is otherwise destructive, so ``destructiveHint`` is
    honestly ``False``) and ``openWorldHint`` follows ``spec.requires_network``.
    """
    read_only = not spec.writes
    return {
        "name": spec.name,
        "title": title_for(spec),
        "description": spec.description,
        "inputSchema": _object_schema(spec),
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": False,
            "idempotentHint": read_only,
            "openWorldHint": bool(spec.requires_network),
        },
    }


def render_spec(spec: ToolSpec, fmt: str) -> dict[str, Any]:
    """Render one ``spec`` in ``fmt`` (``openai`` | ``mcp``)."""
    if fmt == "openai":
        return render_openai(spec)
    if fmt == "mcp":
        return render_mcp(spec)
    raise RenderError(f"unknown tool format {fmt!r}; expected one of {list(FORMATS)}")


def render_many(specs: Iterable[ToolSpec], fmt: str) -> list[dict[str, Any]]:
    """Render every spec in ``specs`` in ``fmt`` (declaration order preserved)."""
    return [render_spec(spec, fmt) for spec in specs]


def dumps(payload: Any) -> str:
    """JSON for a wire payload (MCP text content, prompt sections)."""
    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = [
    "FORMATS",
    "RenderError",
    "dumps",
    "render_many",
    "render_mcp",
    "render_openai",
    "render_spec",
    "title_for",
]
