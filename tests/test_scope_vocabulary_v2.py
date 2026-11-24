"""Draft v2 scope declaration — additive, opt-in, enforced nowhere.

Stage 1 gap #2: describe proposed actions without widening the frozen 33-action
vocabulary, the four-role model, or any live route.
"""

from __future__ import annotations

import pytest

from zolai.api import auth, rbac

FROZEN_V1 = frozenset(
    {
        "dashboard:read",
        "dataset:read",
        "dataset:create",
        "dataset:edit",
        "dataset:run_quality",
        "dataset:validate",
        "dataset:publish",
        "dataset:deprecate",
        "source:read",
        "source:write",
        "pos:read",
        "pos:annotate",
        "pos:review",
        "pos:adjudicate",
        "annotation:read",
        "annotation:review",
        "quality:read",
        "quality:run",
        "quality:waive",
        "eval:read",
        "eval:run",
        "pipeline:read",
        "pipeline:run",
        "rag:read",
        "catalog:read",
        "audit:read",
        "user:manage",
        "role:manage",
        "apikey:manage",
        "settings:read",
        "settings:write",
        "agent:read",
        "agent:run",
    }
)

# Source: workspace docs/governance/rbac-capabilities.contract.json,
# "proposed_scope_additions" (Stage 1 inventory, gap #2).
PROPOSED_V2 = frozenset(
    {
        "cost:read",
        "cost:manage",
        "observer:read",
        "observer:manage",
        "telemetry:read",
        "learning:read",
        "learning:run",
        "learning:manage",
        "contribution:read",
        "contribution:review",
        "contribution:manage",
    }
)


class TestVocabularySets:
    def test_frozen_v1_set_is_unchanged(self) -> None:
        assert len(auth.VALID_ACTIONS) == 33
        assert auth.VALID_ACTIONS == FROZEN_V1

    def test_v2_declaration_is_versioned_exact_and_disjoint(self) -> None:
        assert auth.SCOPE_VOCABULARY_V2 == "2.0.0-draft"
        assert auth.V2_PROPOSED_ACTIONS == PROPOSED_V2
        assert len(auth.V2_PROPOSED_ACTIONS) == 11
        assert auth.V2_PROPOSED_ACTIONS.isdisjoint(auth.VALID_ACTIONS)


class TestFrozenV1StillRejectsProposedActions:
    def test_validate_scopes_keeps_frozen_error(self) -> None:
        with pytest.raises(ValueError, match="frozen 33-action"):
            auth.validate_scopes(["cost:read"])
        with pytest.raises(ValueError, match="frozen 33-action"):
            auth.validate_scopes(["contribution:*"])

    def test_v1_matcher_and_route_guard_ignore_proposed_actions(self) -> None:
        assert auth.scope_allows(["cost:manage"], "cost:manage") is False
        with pytest.raises(ValueError, match="frozen vocabulary"):
            auth.require_scope("cost:read")

    def test_proposed_scope_does_not_escalate_role(self) -> None:
        assert rbac.role_for({"scopes": ["cost:manage"]}) == "member"


class TestValidateScopesV2:
    def test_exact_v1_and_v2_actions(self) -> None:
        assert auth.validate_scopes_v2(["cost:read", "observer:manage"]) == [
            "cost:read",
            "observer:manage",
        ]
        assert auth.validate_scopes_v2(["dataset:read"]) == ["dataset:read"]

    def test_sugar_forms_and_dedupe(self) -> None:
        assert auth.validate_scopes_v2(["cost:*", "*:read", "*"]) == [
            "cost:*",
            "*:read",
            "*",
        ]
        assert auth.validate_scopes_v2(["cost:read", "cost:read", "*:manage"]) == [
            "cost:read",
            "*:manage",
        ]

    def test_unknown_and_empty_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in the v2 draft scope vocabulary"):
            auth.validate_scopes_v2(["bogus:read"])
        with pytest.raises(ValueError, match="expected resource:action"):
            auth.validate_scopes_v2(["bogus"])
        with pytest.raises(ValueError, match="must not be empty"):
            auth.validate_scopes_v2([])
        with pytest.raises(ValueError, match="must not be empty"):
            auth.validate_scopes_v2(["  "])


class TestScopeAllowsV2:
    def test_exact_wildcard_and_sugar(self) -> None:
        assert auth.scope_allows_v2(["cost:read"], "cost:read") is True
        assert auth.scope_allows_v2(["cost:*"], "cost:manage") is True
        assert auth.scope_allows_v2(["*:read"], "cost:read") is True
        assert auth.scope_allows_v2(["*"], "contribution:manage") is True

    def test_frozen_v1_actions_still_match(self) -> None:
        assert auth.scope_allows_v2(["dataset:read"], "dataset:read") is True
        assert auth.scope_allows_v2(["agent:*"], "agent:run") is True

    def test_no_implication_and_unknown_actions(self) -> None:
        assert auth.scope_allows_v2(["cost:read"], "cost:manage") is False
        assert auth.scope_allows_v2(["*:read"], "dataset:edit") is False
        assert auth.scope_allows_v2([], "cost:read") is False
        assert auth.scope_allows_v2(["*"], "unknown:read") is False
