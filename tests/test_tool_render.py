"""W0 — one canonical tool definition rendered to the OpenAI and MCP shapes.

Done-when asserted here:

- both wire shapes are rendered from the **same**
  :class:`~zolai.agent.tools.types.ToolSpec` ``name``/``description``/
  ``parameters`` — a change in the registry cannot reach one format and miss
  the other (the drift guard);
- the OpenAI shape is unchanged (every existing call site keeps working) and
  the MCP shape is a valid ``tools/list`` entry (``inputSchema`` is an object
  schema);
- ``annotations`` is honest — read-only/idempotent follow the registry's
  ``writes`` flag, ``openWorldHint`` follows ``requires_network``, and nothing
  in the registry is destructive;
- an unrenderable spec fails loudly instead of publishing a tool no client can
  call.

Pure: no DB, no network, no FastAPI.
"""

from __future__ import annotations

import json

import pytest

from zolai.agent.tools import registry as registry_mod
from zolai.agent.tools.registry import (
    SPECS,
    allow_list,
    json_schemas,
    mcp_tools,
    names_for,
    native_tool_defs,
    openai_tool_defs,
)
from zolai.agent.tools.render import (
    FORMATS,
    RenderError,
    render_many,
    render_mcp,
    render_openai,
    render_spec,
    title_for,
)
from zolai.agent.tools.types import ToolSpec

_WORD = {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]}


def _spec(**overrides) -> ToolSpec:
    base = {
        "name": "probe_tool",
        "description": "A probe tool.",
        "parameters": _WORD,
    }
    base.update(overrides)
    return ToolSpec(**base)


class TestCanonicalDefinition:
    def test_both_formats_share_name_description_and_parameters(self) -> None:
        spec = _spec()
        openai = render_openai(spec)
        mcp = render_mcp(spec)
        assert openai["function"]["name"] == mcp["name"] == spec.name
        assert openai["function"]["description"] == mcp["description"] == spec.description
        assert openai["function"]["parameters"] is spec.parameters
        assert mcp["inputSchema"] is spec.parameters

    def test_every_registered_tool_renders_in_every_format(self) -> None:
        for name in SPECS:
            spec = SPECS[name]
            for fmt in FORMATS:
                rendered = render_spec(spec, fmt)
                assert rendered, name
            assert render_openai(spec)["function"]["name"] == name
            assert render_mcp(spec)["name"] == name

    def test_mcp_input_schema_is_an_object_schema(self) -> None:
        for entry in mcp_tools(names_for("admin")):
            assert entry["inputSchema"]["type"] == "object"
            assert isinstance(entry["inputSchema"]["properties"], dict)

    def test_render_many_preserves_declaration_order(self) -> None:
        names = ["verse_lookup", "rag_search", "dictionary_lookup"]
        assert [d["function"]["name"] for d in render_many(
            [SPECS[n] for n in names], "openai"
        )] == names
        assert [t["name"] for t in render_many([SPECS[n] for n in names], "mcp")] == names

    def test_unknown_format_is_an_error(self) -> None:
        with pytest.raises(RenderError, match="unknown tool format"):
            render_spec(_spec(), "anthropic")

    def test_non_object_parameters_fail_loudly(self) -> None:
        bad = _spec(parameters={"type": "string"})
        with pytest.raises(RenderError, match="JSON-Schema object"):
            render_openai(bad)
        with pytest.raises(RenderError, match="JSON-Schema object"):
            render_mcp(bad)


class TestOpenAiShapeUnchanged:
    def test_openai_entry_is_the_function_array_shape(self) -> None:
        entry = render_openai(SPECS["rag_search"])
        assert set(entry) == {"type", "function"}
        assert entry["type"] == "function"
        assert set(entry["function"]) == {"name", "description", "parameters"}

    def test_native_def_delegates_to_the_renderer(self) -> None:
        for name in ("rag_search", "knowledge_stats", "web_search"):
            assert SPECS[name].native_def() == render_openai(SPECS[name])

    def test_openai_tool_defs_matches_the_legacy_alias(self) -> None:
        names = names_for("admin")
        assert openai_tool_defs(names) == native_tool_defs(names)
        assert [d["function"]["name"] for d in openai_tool_defs(names)] == names

    def test_unknown_names_are_skipped_not_invented(self) -> None:
        assert openai_tool_defs(["rag_search", "definitely_missing"]) == openai_tool_defs(
            ["rag_search"]
        )
        assert mcp_tools(["definitely_missing"]) == []

    def test_prompt_section_schema_is_unchanged(self) -> None:
        for schema in json_schemas(names_for("public")):
            assert set(schema) == {"name", "description", "parameters"}


class TestMcpShape:
    def test_entry_keys(self) -> None:
        entry = render_mcp(SPECS["verse_lookup"])
        assert set(entry) == {"name", "title", "description", "inputSchema", "annotations"}
        assert entry["title"] == "Verse lookup"
        assert entry["annotations"]["readOnlyHint"] is True

    def test_title_falls_back_for_an_unmapped_tool(self) -> None:
        assert title_for(_spec(name="brand_new_tool")) == "brand new tool"

    def test_annotations_are_honest_about_writes(self) -> None:
        read = render_mcp(SPECS["rag_search"])["annotations"]
        write = render_mcp(SPECS["review_queue_submit"])["annotations"]
        assert read["readOnlyHint"] is True
        assert read["idempotentHint"] is True
        assert write["readOnlyHint"] is False
        assert write["idempotentHint"] is False
        assert write["destructiveHint"] is True
        # No spec may rely on a default: every published tool states
        # ``destructive`` explicitly, and the hint is exactly that declaration.
        # The registry's rule — enforced at import, not just here, by
        # ``assert_specs_are_publishable`` — is that **a tool that writes is
        # advertised destructive**, so a client asks the user before an agent
        # changes anything, however additive the change happens to be.
        for name, spec in SPECS.items():
            assert spec.destructive is not None, f"{name} leaves destructive undeclared"
            hints = render_mcp(spec)["annotations"]
            assert hints["readOnlyHint"] is (not spec.writes)
            assert hints["destructiveHint"] is spec.is_destructive
            assert spec.is_destructive is spec.writes, f"{name}: destructive must track writes"

    def test_open_world_hint_follows_requires_network(self) -> None:
        assert render_mcp(SPECS["web_search"])["annotations"]["openWorldHint"] is True
        assert render_mcp(SPECS["rag_search"])["annotations"]["openWorldHint"] is False

    def test_writes_flag_only_on_the_proposal_surface(self) -> None:
        writers = {name for name, spec in SPECS.items() if spec.writes}
        assert writers == {"review_queue_submit"}

    def test_scope_tool_counts(self) -> None:
        # anon 8 / member 26 / admin 31 — the counts the MCP surface publishes.
        # The public tier is unchanged from before B1a; the member/admin tiers
        # carry the five B1a language tools.
        assert len(mcp_tools(allow_list("public"))) == 8
        assert len(mcp_tools(allow_list("member"))) == 26
        assert len(mcp_tools(allow_list("admin"))) == 31

    def test_rendered_strings_are_plain(self) -> None:
        payload = json.dumps(registry_mod.mcp_tools(names_for("admin")), ensure_ascii=False)
        assert "\\u2026" not in payload
        assert chr(0x2026) not in payload
