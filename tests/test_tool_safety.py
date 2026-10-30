"""B0 — safety plumbing for the tool surface: ``destructive``/``scope``/guards.

Done-when asserted here:

- the MCP ``destructiveHint`` is rendered from the spec's own ``destructive``
  declaration, so a tool that **mutates** durable state advertises ``true``
  instead of the hard-coded ``false`` every tool used to publish (the defect
  this batch exists to close);
- the OpenAI renderer is **unchanged** by that — function calling has no
  annotations concept, so nothing may leak into the ``tools`` array;
- every registered spec declares ``destructive`` explicitly, and a handler that
  mutates cannot be published as non-destructive (the enumeration is done at
  runtime from the handler sources, never hand-listed);
- the per-tool ``scope`` is **narrowing-only** and validated against the frozen
  action vocabulary at import time;
- the import-time guards fire: a dotted tool name (illegal on the OpenAI wire
  the same spec is served to), an out-of-vocabulary scope, a declared tool with
  no handler, and a handler with no declared tool;
- the published surface is exactly the declared one: public/member/admin stay
  8 with byte-identical name sets, and the member/admin tiers carry the B1a
  language family, the learning reads and (admin only) the learning write-back
  and learning-run reads (public 8 / member 31 / admin 42) and nothing else.

Pure: no DB, no network, no FastAPI app.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import sys
import textwrap
from pathlib import Path

import pytest

from zolai.agent.tools import executor as executor_mod
from zolai.agent.tools import registry as registry_mod
from zolai.agent.tools.executor import HANDLERS
from zolai.agent.tools.family import (
    ToolFamilyError,
    assert_handlers_cover_specs,
    build_family,
)
from zolai.agent.tools.registry import (
    SPECS,
    allow_list,
    assert_specs_are_publishable,
    mcp_tools,
    names_for,
    openai_tool_defs,
    tool_scope_allows,
)
from zolai.agent.tools.render import render_mcp, render_openai
from zolai.agent.tools.types import (
    TOOL_NAME_RE,
    ToolSpec,
    ToolSpecError,
    valid_tool_scopes,
)

_WORD = {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]}

# ── The published surface, frozen ────────────────────────────────────────────
#
# Captured from the registry before B0 (public 8 / member 21 / admin 26) and
# re-asserted here.  B0 added plumbing + annotations only, so that batch changed
# no name.  B1a (zolai/agent/tools/language.py) added exactly five member-tier
# language tools; the learning family added five member reads, three admin-only
# write-back channels and three admin-only ledger reads.  Nothing was removed or
# reordered, so the public tier is still byte-identical and every earlier name
# is still present in both grown tiers.

EXPECTED_COUNTS = {"public": 8, "member": 31, "admin": 42}

#: The five B1a language tools — member tier, never anonymous.
LANGUAGE_TOOL_NAMES: tuple[str, ...] = (
    "lex_search",
    "pos_tag",
    "syllable_segment",
    "word_detail",
    "zvs_validate",
)

#: The learning family reads — member tier, read-only.
LEARNING_READ_TOOL_NAMES: tuple[str, ...] = (
    "study_quiz_generate",
    "study_review_schedule",
    "study_learner_stats",
    "predict_word_completion",
    "suggest_word_corrections",
)

#: The learning write-back channels — admin tier only.
LEARNING_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "study_record_review",
    "contribute_word",
    "submit_usage_example",
)

#: The read-only learning-cycle ledger reads — admin tier only.
LEARNING_RUN_TOOL_NAMES: tuple[str, ...] = (
    "learning_run_status",
    "learning_version_history",
    "learning_rag_status",
)

EXPECTED_TIERS: dict[str, list[str]] = {
    "public": [
        "dictionary_lookup",
        "grammar_check",
        "rag_search",
        "search_myanmar_dictionary",
        "translate_myanmar_to_zo",
        "translate_zo_to_myanmar",
        "verse_lookup",
        "word_evidence",
    ],
    "member": sorted(
        [
            "dictionary_lookup",
            "grammar_check",
            "rag_search",
            "search_myanmar_dictionary",
            "translate_myanmar_to_zo",
            "translate_zo_to_myanmar",
            "verse_lookup",
            "word_evidence",
            "word_related",
            "analyze_text",
            "foundation_analyze_corpus",
            "foundation_analyze_morphology",
            "foundation_analyze_phonology",
            "foundation_categorize_error",
            "foundation_disambiguate_translation",
            "foundation_get_adaptive_difficulty",
            "foundation_search_cross_lingual",
            "foundation_search_ranked",
            "foundation_search_topical",
            "foundation_translate_enhanced",
            "foundation_validate_structure",
            *LANGUAGE_TOOL_NAMES,
            *LEARNING_READ_TOOL_NAMES,
        ]
    ),
    "admin": sorted(
        [
            "dictionary_lookup",
            "grammar_check",
            "rag_search",
            "search_myanmar_dictionary",
            "translate_myanmar_to_zo",
            "translate_zo_to_myanmar",
            "verse_lookup",
            "word_evidence",
            "word_related",
            "analyze_text",
            "foundation_analyze_corpus",
            "foundation_analyze_morphology",
            "foundation_analyze_phonology",
            "foundation_categorize_error",
            "foundation_disambiguate_translation",
            "foundation_get_adaptive_difficulty",
            "foundation_search_cross_lingual",
            "foundation_search_ranked",
            "foundation_search_topical",
            "foundation_translate_enhanced",
            "foundation_validate_structure",
            *LANGUAGE_TOOL_NAMES,
            *LEARNING_READ_TOOL_NAMES,
            *LEARNING_WRITE_TOOL_NAMES,
            "knowledge_stats",
            "kb_research",
            "provider_status",
            "review_queue_submit",
            "web_search",
            *LEARNING_RUN_TOOL_NAMES,
        ]
    ),
}
#: A handler that mutates durable state leaves one of these in its source: a
#: data-changing SQL verb, a commit, or a repository mutator.
_MUTATION_PATTERNS = (
    re.compile(r"\binsert\s+into\b", re.I),
    re.compile(r"\breplace\s+into\b", re.I),
    re.compile(r"\bupdate\s+\w+\s+set\b", re.I),
    re.compile(r"\bdelete\s+from\b", re.I),
    re.compile(r"\bdrop\s+table\b", re.I),
    re.compile(r"\balter\s+table\b", re.I),
    re.compile(r"\btruncate\b", re.I),
    re.compile(r"\.commit\(\)"),
    re.compile(r"\badd_to_queue\("),
    re.compile(r"\bstore_correction\("),
    re.compile(r"\b(insert|upsert|delete)_row\("),
)


def mutating_handlers() -> dict[str, str]:
    """Tools whose handler source mutates durable state — enumerated, not listed.

    Walks the registry's handler table and keeps the handlers whose own source
    contains a data-changing statement.  Deriving the set at runtime is the
    point: a tool added later without a re-run of a hand-written list still gets
    caught by the assertion below.
    """
    found: dict[str, str] = {}
    for name in SPECS:
        handler = HANDLERS.get(name)
        assert handler is not None, name  # registry-wide coverage is asserted in executor
        source = textwrap.dedent(inspect.getsource(handler))
        for pattern in _MUTATION_PATTERNS:
            match = pattern.search(source)
            if match:
                found[name] = match.group(0)
                break
    return found


def _write_module(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
    return tmp_path / f"{name}.py"


def _import_fresh(tmp_path: Path, name: str) -> None:
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module(name)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop(name, None)


class TestDestructiveHint:
    def test_a_mutating_tool_publishes_destructive_hint_true(self) -> None:
        # Non-vacuity: the registry really does carry a writing tool, and it is
        # the one whose annotation used to lie.
        mutating = mutating_handlers()
        assert mutating, "expected at least one handler to mutate durable state"

        entries = {e["name"]: e["annotations"] for e in mcp_tools(names_for("admin"))}
        for name in mutating:
            annotations = entries[name]
            assert annotations["readOnlyHint"] is False, name
            assert annotations["destructiveHint"] is True, (
                f"{name} mutates durable state ({mutating[name]!r}) but published "
                f"destructiveHint={annotations['destructiveHint']!r}"
            )

    def test_review_queue_submit_is_the_destructive_tool(self) -> None:
        # The concrete, hand-checked case: the only handler that writes a row.
        annotations = render_mcp(SPECS["review_queue_submit"])["annotations"]
        assert annotations == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        }

    def test_annotation_follows_the_declared_field_for_every_tool(self) -> None:
        for name, spec in SPECS.items():
            annotations = render_mcp(spec)["annotations"]
            assert annotations["destructiveHint"] is spec.is_destructive, name

    def test_read_only_tools_stay_non_destructive(self) -> None:
        readers = {name for name, spec in SPECS.items() if not spec.writes}
        assert readers, "expected read-only tools"
        for name in readers:
            annotations = render_mcp(SPECS[name])["annotations"]
            assert annotations["readOnlyHint"] is True, name
            assert annotations["destructiveHint"] is False, name

    def test_undeclared_destructive_fails_closed(self) -> None:
        # ``None`` (undeclared) resolves to True — the MCP schema's own default.
        undeclared = ToolSpec(name="probe", description="d", parameters=_WORD)
        assert undeclared.destructive is None
        assert undeclared.is_destructive is True
        assert render_mcp(undeclared)["annotations"]["destructiveHint"] is True

    def test_every_registered_spec_declares_destructive(self) -> None:
        undeclared = [name for name, spec in SPECS.items() if spec.destructive is None]
        assert undeclared == []
        # …and the registry refuses to publish a spec that has not declared it.
        with pytest.raises(ToolSpecError, match="destructive is undeclared"):
            assert_specs_are_publishable(
                {"probe": ToolSpec(name="probe", description="d", parameters=_WORD)}
            )

    def test_destructive_requires_writes(self) -> None:
        spec = ToolSpec(
            name="probe", description="d", parameters=_WORD, destructive=True
        )
        with pytest.raises(ToolSpecError, match="destructive=True without writes=True"):
            assert_specs_are_publishable({"probe": spec})

    def test_a_writing_tool_cannot_be_published_as_harmless(self) -> None:
        # The other half of the rule: ``writes=True`` ⇒ ``destructive=True``.
        # This is the exact defect the batch exists to close — a tool that
        # changes server state published with ``destructiveHint: false``.
        writer = ToolSpec(
            name="probe", description="d", parameters=_WORD, writes=True, destructive=False
        )
        with pytest.raises(ToolSpecError, match="writes=True without destructive=True"):
            assert_specs_are_publishable({"probe": writer})

        reader = ToolSpec(
            name="probe", description="d", parameters=_WORD, writes=False, destructive=False
        )
        assert_specs_are_publishable({"probe": reader})  # must not raise

    def test_every_writing_tool_in_the_registry_is_destructive(self) -> None:
        # Enumerated at runtime, never hand-listed: add a writing tool without
        # the flag and both this and the import-time gate fail.
        writers = [name for name, spec in SPECS.items() if spec.writes]
        assert writers, "expected the registry to carry at least one writing tool"
        for name in writers:
            assert SPECS[name].is_destructive is True, name
            assert render_mcp(SPECS[name])["annotations"]["destructiveHint"] is True, name

    def test_registry_itself_satisfies_the_publishable_gate(self) -> None:
        assert_specs_are_publishable(SPECS)  # must not raise

    def test_the_openai_renderer_is_untouched_by_this(self) -> None:
        # Function calling has no annotations concept: the field must not leak
        # into the wire, and the entry shape must stay exactly three keys.
        for name, spec in SPECS.items():
            entry = render_openai(spec)
            assert set(entry) == {"type", "function"}
            assert set(entry["function"]) == {"name", "description", "parameters"}
            assert "annotations" not in json.dumps(entry)
            assert render_openai(spec) == spec.native_def(), name
        payload = json.dumps(openai_tool_defs(names_for("admin")))
        assert "destructiveHint" not in payload
        assert "annotations" not in payload

    def test_mutating_handlers_match_the_declared_destructive_flag(self) -> None:
        # The durable guard: any handler that mutates must be declared
        # destructive.  Add a writing tool without the flag and this fails.
        for name in mutating_handlers():
            assert SPECS[name].is_destructive is True, (
                f"{name} mutates but is published as non-destructive"
            )

    def test_mutating_enumeration_finds_the_known_writer(self) -> None:
        assert "review_queue_submit" in mutating_handlers()
        assert "rag_search" not in mutating_handlers()


class TestToolNameGuard:
    def test_dotted_name_is_refused(self) -> None:
        with pytest.raises(ToolSpecError, match="illegal tool name"):
            ToolSpec(name="dictionary.lookup", description="d", parameters=_WORD)

    def test_other_openai_illegal_characters_are_refused(self) -> None:
        for name in ("has space", "colon:name", "slash/name", "emoji✨", "", "x" * 65):
            with pytest.raises(ToolSpecError, match="illegal tool name"):
                ToolSpec(name=name, description="d", parameters=_WORD)

    def test_the_registered_names_all_satisfy_the_grammar(self) -> None:
        for name in SPECS:
            assert TOOL_NAME_RE.match(name), name

    def test_a_dotted_name_fails_at_module_import(self, tmp_path: Path) -> None:
        _write_module(
            tmp_path,
            "b0_probe_dotted_name",
            "from zolai.agent.tools.types import ToolSpec\n"
            "SPEC = ToolSpec(name='a.b', description='d', parameters={'type': 'object'})\n",
        )
        with pytest.raises(ToolSpecError, match="illegal tool name"):
            _import_fresh(tmp_path, "b0_probe_dotted_name")


class TestScopeField:
    def test_scope_must_be_an_exact_action_of_the_frozen_vocabulary(self) -> None:
        assert len(valid_tool_scopes()) == 33
        ToolSpec(name="probe", description="d", parameters=_WORD, scope="dataset:read")

    def test_out_of_vocabulary_scope_is_refused(self) -> None:
        for bad in ("dataset:destroy", "catalog:*", "*", "Dataset:Read", "dataset"):
            with pytest.raises(ToolSpecError, match="unknown tool scope|illegal tool scope"):
                ToolSpec(name="probe", description="d", parameters=_WORD, scope=bad)

    def test_empty_scope_is_refused(self) -> None:
        with pytest.raises(ToolSpecError, match="illegal tool scope"):
            ToolSpec(name="probe", description="d", parameters=_WORD, scope="   ")

    def test_an_out_of_vocabulary_scope_fails_at_module_import(self, tmp_path: Path) -> None:
        _write_module(
            tmp_path,
            "b0_probe_bad_scope",
            "from zolai.agent.tools.types import ToolSpec\n"
            "SPEC = ToolSpec(name='probe', description='d',\n"
            "                parameters={'type': 'object'}, scope='dataset:destroy')\n",
        )
        with pytest.raises(ToolSpecError, match="unknown tool scope"):
            _import_fresh(tmp_path, "b0_probe_bad_scope")

    def test_a_scope_the_caller_lacks_hides_the_tool_from_the_list(self) -> None:
        probe = ToolSpec(
            name="probe_scoped",
            description="d",
            parameters=_WORD,
            admin_only=True,
            scope="dataset:create",
        )
        every = set(valid_tool_scopes())
        with pytest.MonkeyPatch.context() as mp:
            # The probe joins the *registry* (so ``get_tool`` resolves it) and
            # the admin tier (so it would otherwise be published); only the
            # caller's grants vary.
            mp.setattr(registry_mod, "SPECS", {**SPECS, "probe_scoped": probe})
            mp.setattr(registry_mod, "ADMIN_TOOLS", (*registry_mod.ADMIN_TOOLS, "probe_scoped"))
            assert "probe_scoped" in allow_list("admin")
            assert "probe_scoped" not in allow_list("admin", granted={"catalog:read"})
            assert "probe_scoped" not in allow_list("admin", granted=set())
            assert "probe_scoped" in allow_list("admin", granted=every)
            assert "probe_scoped" not in allow_list(
                "admin", granted=every - {"dataset:create"}
            )

    def test_the_agent_run_path_narrows_by_the_callers_own_scopes(self) -> None:
        # End-to-end through the two functions the runtime actually calls: a
        # caller holding only ``agent:run`` loses the two ``catalog:read``
        # tools from its tool set instead of being offered them.
        from zolai.agent.orchestrator import run_agent_goal

        src = Path(run_agent_goal.__code__.co_filename).read_text(encoding="utf-8")
        assert "allow_list(scope, granted=granted)" in src, (
            "the orchestrator must forward the caller's grants to allow_list"
        )
        read_only_admin = {
            name
            for name in allow_list("admin")
            if SPECS[name].scope == "catalog:read"
        }
        assert read_only_admin, "expected at least one scoped admin tool"
        assert allow_list("admin", granted={"agent:run", "agent:read"}).isdisjoint(
            read_only_admin
        )

    def test_allow_list_for_record_reuses_the_one_role_resolver(self) -> None:
        from zolai.agent.tools.registry import (
            allow_list_for_record,
            tool_tier_for_record,
        )
        from zolai.api.rbac import ADMIN_SCOPES

        assert tool_tier_for_record(None) == "public"
        assert allow_list_for_record(None) == allow_list("public")

        # The two ``catalog:read`` admin tools are the scoped ones, so an admin
        # record that does *not* hold ``catalog:read`` loses exactly those.
        scoped_admin = {name for name in allow_list("admin") if SPECS[name].scope}
        assert scoped_admin, "expected at least one scoped admin tool"
        narrow_admin = {"scopes": ["apikey:manage", "agent:run"]}
        assert tool_tier_for_record(narrow_admin) == "admin"
        assert allow_list_for_record(narrow_admin) == allow_list("admin") - scoped_admin
        assert allow_list_for_record(narrow_admin) < allow_list("admin")

        admin_record = {"scopes": ["apikey:manage", *valid_tool_scopes()]}
        assert tool_tier_for_record(admin_record) == "admin"
        assert allow_list_for_record(admin_record) == allow_list("admin")

        # A member record reaches exactly the member-tier tools its own scopes
        # satisfy — the per-tool scope only ever *removes*, never adds — and it
        # can never reach the admin extras.
        #
        # (Before B1a no member-tier tool declared a ``scope``, so a minimal
        # member record kept the whole tier unconditionally.  The B1a language
        # family is the first member-tier narrowing, so the assertion is now
        # "the tier minus exactly the tools whose action this record lacks"
        # rather than "the tier".)
        member_record = {"scopes": ["agent:run", "agent:read"]}
        assert tool_tier_for_record(member_record) == "member"
        member_via_record = allow_list_for_record(member_record)
        assert member_via_record == allow_list("member", granted=set(member_record["scopes"]))
        assert member_via_record < allow_list("member")
        assert allow_list("member") - member_via_record == {
            name for name in allow_list("member") if SPECS[name].scope
        }

        # A record holding the whole member scope set keeps **the entire member
        # tier** — every tool action a tier advertises is held by the role that
        # tier belongs to (the RBAC task closed the last gap: ``pos_tag`` needed
        # ``pos:read``, which no account role used to carry).  The parity rule
        # itself is guarded by ``TestTierScopeParity`` in
        # tests/test_role_hierarchy.py.
        # ``ADMIN_SCOPES`` means two different things two lines apart: the one
        # this function imported from ``rbac`` is the set that *defines* admin
        # (the three write actions); ``session_auth``'s is everything an admin
        # *account* holds.  Aliased rather than shadowed.
        from zolai.api.session_auth import ADMIN_SCOPES as ACCOUNT_ADMIN_SCOPES
        from zolai.api.session_auth import MEMBER_SCOPES

        read_member = {"scopes": list(MEMBER_SCOPES)}
        assert allow_list_for_record(read_member) == allow_list("member")
        assert allow_list_for_record(read_member) < allow_list("admin")

        read_admin = {"scopes": list(ACCOUNT_ADMIN_SCOPES)}
        assert allow_list_for_record(read_admin) == allow_list("admin")

        # A record with no readable scope list: the tier decides, unchanged.
        assert tool_tier_for_record({"scopes": None}) == "member"
        assert allow_list_for_record({"scopes": None}) == allow_list("member")
        assert ADMIN_SCOPES, "the role vocabulary the registry borrows must exist"

    def test_scope_only_ever_narrows(self) -> None:
        # Property, over the whole frozen vocabulary and every tier: with any
        # grant set the result is a subset of the tier, never a superset.
        for tier in ("public", "member", "admin"):
            base = allow_list(tier)
            grants: list[set[str]] = [set(), {"catalog:read"}, set(valid_tool_scopes())]
            for granted in grants:
                assert allow_list(tier, granted=granted) <= base
            assert allow_list(tier) == base  # no grants ⇒ unchanged

    def test_tool_scope_allows_is_a_no_op_without_a_declared_scope(self) -> None:
        assert tool_scope_allows(SPECS["rag_search"], set()) is True
        assert tool_scope_allows(SPECS["knowledge_stats"], set()) is False
        assert tool_scope_allows(SPECS["knowledge_stats"], {"catalog:read"}) is True
        assert tool_scope_allows(SPECS["rag_search"], None) is True

    def test_the_full_vocabulary_keeps_the_published_surface_whole(self) -> None:
        # A caller holding every action loses nothing: the per-tool scopes in
        # the registry do not shrink any tier for a fully-provisioned caller.
        for tier, expected in EXPECTED_COUNTS.items():
            assert len(allow_list(tier, granted=set(valid_tool_scopes()))) == expected


class TestCoverageGuards:
    def test_a_declared_tool_with_no_handler_fails_the_family_build(self) -> None:
        with pytest.raises(ToolFamilyError, match="handler missing="):
            build_family(
                family="probe",
                declared=("one", "two"),
                models={"one": object, "two": object},
                handlers={"one": lambda inp: None},
                name_of=lambda key: f"probe_{key}",
                spec_of=lambda key, model, name: ToolSpec(
                    name=name, description="d", parameters=_WORD, destructive=False
                ),
            )

    def test_a_handler_with_no_declared_tool_fails_the_family_build(self) -> None:
        # The models table stays exact (so the *model* check passes) and the
        # handler table carries one extra — the orphan-handler direction.
        with pytest.raises(ToolFamilyError, match=r"extra=\['orphan'\]"):
            build_family(
                family="probe",
                declared=("one",),
                models={"one": object},
                handlers={"one": lambda inp: None, "orphan": lambda inp: None},
                name_of=lambda key: f"probe_{key}",
                spec_of=lambda key, model, name: ToolSpec(
                    name=name, description="d", parameters=_WORD, destructive=False
                ),
            )

    def test_a_declared_tool_with_no_input_model_fails_the_family_build(self) -> None:
        with pytest.raises(ToolFamilyError, match="input model missing="):
            build_family(
                family="probe",
                declared=("one", "two"),
                models={"one": object},
                handlers={"one": lambda inp: None, "two": lambda inp: None},
                name_of=lambda key: f"probe_{key}",
                spec_of=lambda key, model, name: ToolSpec(
                    name=name, description="d", parameters=_WORD, destructive=False
                ),
            )

    def test_the_family_helper_publishes_a_complete_family(self) -> None:
        family = build_family(
            family="probe",
            declared=("b_second", "a_first"),
            models={"b_second": object, "a_first": object},
            handlers={"b_second": lambda inp: "b", "a_first": lambda inp: "a"},
            name_of=lambda key: f"probe_{key}",
            spec_of=lambda key, model, name: ToolSpec(
                name=name, description="d", parameters=_WORD, destructive=False
            ),
        )
        # declaration order is publication order
        assert family.names == ("probe_b_second", "probe_a_first")
        assert set(family.handlers) == set(family.specs)
        assert family.handlers["probe_a_first"]({}) == "a"

    def test_registry_tools_and_handlers_cover_each_other(self) -> None:
        assert_handlers_cover_specs(SPECS, HANDLERS, subject="test")

    def test_registry_wide_orphan_tool_is_caught(self) -> None:
        orphan = ToolSpec(
            name="probe_orphan", description="d", parameters=_WORD, destructive=False
        )
        with pytest.raises(ToolFamilyError, match="missing_handler=\\['probe_orphan'\\]"):
            assert_handlers_cover_specs({**SPECS, "probe_orphan": orphan}, HANDLERS)

    def test_registry_wide_orphan_handler_is_caught(self) -> None:
        with pytest.raises(ToolFamilyError, match="orphan_handler=\\['probe_ghost'\\]"):
            assert_handlers_cover_specs(
                SPECS, {**HANDLERS, "probe_ghost": lambda inp: None}
            )

    def test_the_executor_module_asserted_the_invariant_at_import(self) -> None:
        # The executor runs the same check on the real tables at import; if the
        # module loaded, the registry was coherent.
        assert set(HANDLERS) == set(SPECS)
        assert executor_mod.HANDLERS is HANDLERS

    def test_the_foundation_family_was_built_through_the_helper(self) -> None:
        from zolai.agent.tools import foundation

        assert foundation.FAMILY.name == "foundation"
        assert set(foundation.FAMILY.specs) == set(foundation.FOUNDATION_HANDLERS)
        assert foundation.FOUNDATION_TOOL_NAMES == foundation.FAMILY.names


class TestPublishedSurfaceUnchanged:
    def test_role_to_tool_counts_are_the_pre_batch_counts(self) -> None:
        assert {tier: len(allow_list(tier)) for tier in EXPECTED_COUNTS} == EXPECTED_COUNTS

    def test_role_to_tool_name_sets_are_identical(self) -> None:
        for tier, expected in EXPECTED_TIERS.items():
            assert sorted(names_for(tier)) == expected, tier
            assert allow_list(tier) == set(expected), tier

    def test_mcp_list_counts_match(self) -> None:
        for tier, expected in EXPECTED_COUNTS.items():
            assert len(mcp_tools(allow_list(tier))) == expected

    def test_tool_order_within_a_tier_is_registry_declaration_order(self) -> None:
        for tier in EXPECTED_COUNTS:
            declared = [name for name in SPECS if name in allow_list(tier)]
            assert names_for(tier) == declared

    def test_the_family_prefix_never_collides_with_a_bare_tool(self) -> None:
        assert all(not name.startswith("foundation_") for name in EXPECTED_TIERS["public"])
        assert all(name.startswith("foundation_") for name in SPECS if "analyze_corpus" in name)

    def test_every_spec_still_renders_in_both_formats(self) -> None:
        for name, spec in SPECS.items():
            assert render_mcp(spec)["name"] == name
            assert render_openai(spec)["function"]["name"] == name
            ast.parse(json.dumps(render_mcp(spec)))  # still plain JSON
