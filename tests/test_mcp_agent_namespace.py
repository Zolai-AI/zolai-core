"""The ``foundation_*`` tool family — namespace, coverage and anti-drift guards.

The family is *generated*: one tool per typed ``/api/v1/foundation/*``
contract, with the name ``foundation_<operation_id>``, the contract's own
description, and the route's Pydantic request model as the input schema
(:mod:`zolai.agent.tools.foundation`).  These tests hold the two properties
that generation is supposed to buy:

* **Namespacing** — every family name carries the frozen ``foundation_``
  prefix, and no bare agent-tool name can collide with it (a message may list
  both families).
* **Coverage** — the contract registry, the input-model table and the handler
  table agree exactly; a contract added without a handler fails the import-time
  guard instead of publishing a tool that cannot run.
* **Anti-drift** — the published spec's ``parameters`` are byte-identical to
  the route's ``model_json_schema()``, so a tool can never describe a different
  body than the route it wraps accepts.
"""

from __future__ import annotations

import pytest

from zolai.agent.tools import executor as exec_mod
from zolai.agent.tools import family as fam
from zolai.agent.tools import foundation as fnd
from zolai.agent.tools.executor import HANDLERS
from zolai.agent.tools.registry import (
    ANONYMOUS_TOOLS,
    SPECS,
    allow_list,
    names_for,
)
from zolai.agent.tools.types import ToolCall
from zolai.api.foundation_router import (
    FOUNDATION_TOOL_CONTRACTS,
    CorpusAnalysisRequest,
)


def _family_names() -> set[str]:
    return {fnd.tool_name(contract.operation_id) for contract in FOUNDATION_TOOL_CONTRACTS}


def _bare_names() -> set[str]:
    """Registered names that do *not* belong to the generated family."""
    return {name for name in SPECS if not name.startswith(fnd.PREFIX)}


class TestNamespace:
    def test_the_prefix_is_frozen(self) -> None:
        # Renaming this breaks every client's cached tool list.
        assert fnd.PREFIX == "foundation_"

    def test_every_contract_gets_a_prefixed_tool_name(self) -> None:
        for contract in FOUNDATION_TOOL_CONTRACTS:
            expected = f"foundation_{contract.operation_id}"
            assert fnd.tool_name(contract.operation_id) == expected
            assert expected in SPECS

    def test_the_family_matches_the_contracts_registry_exactly(self) -> None:
        expected = _family_names()
        assert set(fnd.FOUNDATION_SPECS) == expected
        assert set(fnd.FOUNDATION_HANDLERS) == expected
        assert set(fnd.FOUNDATION_TOOL_NAMES) == expected
        assert len(fnd.FOUNDATION_TOOL_NAMES) == len(FOUNDATION_TOOL_CONTRACTS)

    def test_every_registered_tool_has_a_handler(self) -> None:
        assert set(SPECS) == set(HANDLERS)

    def test_no_bare_name_collides_with_the_family(self) -> None:
        family = set(fnd.FOUNDATION_SPECS)
        assert _bare_names().isdisjoint(family)
        # …and nothing escaped the prefix in the other direction.
        assert all(name.startswith(fnd.PREFIX) for name in family)

    def test_the_family_is_member_tier_and_never_anonymous(self) -> None:
        family = set(fnd.FOUNDATION_TOOL_NAMES)
        assert family <= allow_list("member")
        assert family <= allow_list("admin")
        assert family.isdisjoint(allow_list("public"))
        assert family.isdisjoint(ANONYMOUS_TOOLS)

    def test_public_and_member_namespaces_are_nested(self) -> None:
        public = allow_list("public")
        member = allow_list("member")
        admin = allow_list("admin")
        assert public <= member <= admin
        # The member namespace is exactly the public set + the family + the two
        # remaining bare analysis tools.
        assert member == public | set(fnd.FOUNDATION_TOOL_NAMES) | {"word_related", "analyze_text"}

    def test_the_member_list_is_public_then_family_then_analysis(self) -> None:
        """Declaration order is stable (snapshot of the contract order)."""
        assert names_for("member")[:1] == ["rag_search"]
        assert [n for n in names_for("member") if n.startswith(fnd.PREFIX)] == list(
            fnd.FOUNDATION_TOOL_NAMES
        )


class TestAntiDrift:
    def test_the_input_schema_is_the_route_request_model(self) -> None:
        for contract in FOUNDATION_TOOL_CONTRACTS:
            spec = SPECS[fnd.tool_name(contract.operation_id)]
            model = fnd._OPERATION_MODELS[contract.operation_id]
            assert spec.parameters == model.model_json_schema(), contract.operation_id

    def test_the_description_carries_the_contract_and_its_route(self) -> None:
        for contract in FOUNDATION_TOOL_CONTRACTS:
            spec = SPECS[fnd.tool_name(contract.operation_id)]
            assert contract.description in spec.description
            assert contract.method in spec.description
            assert contract.path in spec.description

    def test_every_family_spec_is_a_pure_read_with_its_own_title(self) -> None:
        for name, spec in fnd.FOUNDATION_SPECS.items():
            assert spec.title.startswith("Foundation:"), name
            assert spec.admin_only is False
            assert spec.requires_network is False
            assert spec.writes is False

    def test_the_input_model_table_covers_every_contract(self) -> None:
        declared = {contract.operation_id for contract in FOUNDATION_TOOL_CONTRACTS}
        assert set(fnd._OPERATION_MODELS) == declared
        assert set(fnd._OPERATION_HANDLERS) == declared


class TestCoverageGuard:
    """The three-way coverage guard — now shared, in :mod:`zolai.agent.tools.family`.

    ``foundation`` no longer owns a private assertion: ``build_family`` runs the
    shared :func:`~zolai.agent.tools.family.assert_tables_cover` on both tables
    while the module imports, so a family that is out of step is a start-up
    failure.  These tests drive that guard directly against the two *live*
    tables, so it is a real mutation of the real family that trips it.
    """

    DECLARED = frozenset(contract.operation_id for contract in FOUNDATION_TOOL_CONTRACTS)

    def _check_models(self) -> None:
        fam.assert_tables_cover(
            "foundation", "input model", self.DECLARED, fnd._OPERATION_MODELS
        )

    def _check_handlers(self) -> None:
        fam.assert_tables_cover(
            "foundation", "handler", self.DECLARED, fnd._OPERATION_HANDLERS
        )

    def test_the_guard_accepts_the_current_family(self) -> None:
        self._check_models()  # must not raise
        self._check_handlers()

    def test_a_contract_without_an_input_model_is_refused(self, monkeypatch) -> None:
        monkeypatch.delitem(fnd._OPERATION_MODELS, "analyze_corpus")
        with pytest.raises(fam.ToolFamilyError, match=r"input model missing=\['analyze_corpus'\]"):
            self._check_models()

    def test_a_contract_without_a_handler_is_refused(self, monkeypatch) -> None:
        monkeypatch.delitem(fnd._OPERATION_HANDLERS, "categorize_error")
        with pytest.raises(fam.ToolFamilyError, match=r"handler missing=\['categorize_error'\]"):
            self._check_handlers()

    def test_a_stray_input_model_is_refused(self, monkeypatch) -> None:
        monkeypatch.setitem(fnd._OPERATION_MODELS, "not_a_contract", CorpusAnalysisRequest)
        with pytest.raises(fam.ToolFamilyError, match=r"extra=\['not_a_contract'\]"):
            self._check_models()

    def test_a_stray_handler_is_refused(self, monkeypatch) -> None:
        monkeypatch.setitem(fnd._OPERATION_HANDLERS, "not_a_contract", lambda inp: {})
        with pytest.raises(fam.ToolFamilyError, match=r"extra=\['not_a_contract'\]"):
            self._check_handlers()


class TestHandlerWiring:
    def test_a_missing_required_argument_is_an_honest_tool_error(self) -> None:
        """Validation runs before the analyzer, so this needs no DB."""
        result = exec_mod.execute_tool(
            ToolCall(name="foundation_analyze_corpus", input={}),
            allow=allow_list("member"),
        )
        assert result.ok is False
        assert "CorpusAnalysisRequest" in (result.error or "")
        assert "text" in (result.error or "")

    def test_an_ill_typed_argument_is_an_honest_tool_error(self) -> None:
        result = exec_mod.execute_tool(
            ToolCall(name="foundation_analyze_phonology", input={"word": 5}),
            allow=allow_list("member"),
        )
        assert result.ok is False
        assert (result.error or "").startswith("PhonologyAnalysisRequest:")

    def test_the_family_is_unreachable_anonymously(self) -> None:
        result = exec_mod.execute_tool(
            ToolCall(name="foundation_validate_structure", input={"sentence": "x"}),
            allow=allow_list("public"),
        )
        assert result.ok is False
        assert "not allowed" in (result.error or "")

    def test_an_unknown_family_name_is_refused(self) -> None:
        result = exec_mod.execute_tool(
            ToolCall(name="foundation_not_a_contract", input={}),
            allow=allow_list("admin"),
        )
        assert result.ok is False
        assert "unknown tool" in (result.error or "")
