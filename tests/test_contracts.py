"""Tests for the Phase 1 knowledge contracts (Master Prompt §36).

Covers:
- all 11 contracts importable from ``zolai.shared.contracts``
- ``KnowledgeStatus`` has exactly 6 values + whitelisted transitions
- JSON round-trip (``model_dump_json`` → ``model_validate_json``) per contract
- evidence gate: SUPPORTED/VERIFIED without evidence raises
- ``confidence_from_evidence``: ≤2 dp, evidence-derived only (no override arg)
- POS fields validated against the 17-tag UPOS allowlist
- foundation ``Evidence`` adapters leave the dataclass unchanged
"""
from __future__ import annotations

import inspect
import json

import pytest
from pydantic import ValidationError

from zolai.shared.contracts import (
    ALLOWED_TRANSITIONS,
    GATED_STATUSES,
    Evidence,
    EvidenceGateError,
    GrammarPattern,
    Hypothesis,
    InvalidTransition,
    KnowledgeClaim,
    KnowledgeContract,
    KnowledgeStatus,
    KnowledgeVersion,
    MorphologicalRelation,
    Observation,
    POSHypothesis,
    Source,
    Word,
    WordForm,
    confidence_from_evidence,
    validate_transition,
)

ALL_11 = [
    Word,
    WordForm,
    Observation,
    Evidence,
    Hypothesis,
    KnowledgeClaim,
    GrammarPattern,
    MorphologicalRelation,
    POSHypothesis,
    Source,
    KnowledgeVersion,
]


def _evidence(tier: int, ev_id: int | None = 1) -> Evidence:
    return Evidence(
        id=ev_id,
        fact_type="word",
        fact_key="word:pasian",
        tier=tier,
        source="bible_verses",
        confidence=0.9,
        provenance_hash="abc123",
        payload={"verse": "GEN 1:1"},
    )


# ---------------------------------------------------------------------------
# Inventory / status enum
# ---------------------------------------------------------------------------


class TestContractInventory:
    def test_all_11_importable(self) -> None:
        for contract in ALL_11:
            assert inspect.isclass(contract)
            assert issubclass(contract, KnowledgeContract) or contract in (
                WordForm,
                Observation,
                Evidence,
                Source,
                KnowledgeVersion,
            )

    def test_exactly_six_status_values(self) -> None:
        assert len(KnowledgeStatus) == 6
        assert [s.value for s in KnowledgeStatus] == [
            "OBSERVED",
            "CANDIDATE",
            "SUPPORTED",
            "VERIFIED",
            "REJECTED",
            "DEPRECATED",
        ]

    def test_gated_statuses(self) -> None:
        assert GATED_STATUSES == frozenset(
            {KnowledgeStatus.SUPPORTED, KnowledgeStatus.VERIFIED}
        )

    def test_default_status_is_observed(self) -> None:
        h = Hypothesis(subject="word:x", predicate="pos:NOUN")
        assert h.status is KnowledgeStatus.OBSERVED
        assert h.evidence_ids == []


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_main_chain_allowed(self) -> None:
        chain = [
            KnowledgeStatus.OBSERVED,
            KnowledgeStatus.CANDIDATE,
            KnowledgeStatus.SUPPORTED,
            KnowledgeStatus.VERIFIED,
        ]
        for current, target in zip(chain, chain[1:]):
            assert validate_transition(current, target) is target

    def test_reject_and_deprecate_branches(self) -> None:
        assert validate_transition("OBSERVED", "REJECTED") is KnowledgeStatus.REJECTED
        assert validate_transition("VERIFIED", "REJECTED") is KnowledgeStatus.REJECTED
        assert validate_transition("CANDIDATE", "DEPRECATED") is KnowledgeStatus.DEPRECATED
        assert validate_transition("REJECTED", "DEPRECATED") is KnowledgeStatus.DEPRECATED

    def test_idempotent_same_status_allowed(self) -> None:
        assert (
            validate_transition(KnowledgeStatus.CANDIDATE, KnowledgeStatus.CANDIDATE)
            is KnowledgeStatus.CANDIDATE
        )

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            ("CANDIDATE", "OBSERVED"),
            ("SUPPORTED", "CANDIDATE"),
            ("VERIFIED", "SUPPORTED"),
            ("DEPRECATED", "OBSERVED"),
            ("DEPRECATED", "VERIFIED"),
            ("REJECTED", "VERIFIED"),
        ],
    )
    def test_non_whitelisted_raises(self, current: str, target: str) -> None:
        with pytest.raises(InvalidTransition):
            validate_transition(current, target)

    def test_transition_table_covers_all_states(self) -> None:
        assert set(ALLOWED_TRANSITIONS) == set(KnowledgeStatus)
        assert ALLOWED_TRANSITIONS[KnowledgeStatus.DEPRECATED] == frozenset()

    def test_contract_transition_to_returns_copy(self) -> None:
        h = Hypothesis(subject="w", predicate="p")
        moved = h.transition_to(KnowledgeStatus.CANDIDATE)
        assert moved is not h
        assert moved.status is KnowledgeStatus.CANDIDATE
        assert h.status is KnowledgeStatus.OBSERVED
        with pytest.raises(InvalidTransition):
            h.transition_to(KnowledgeStatus.VERIFIED)


# ---------------------------------------------------------------------------
# Evidence gate
# ---------------------------------------------------------------------------


class TestEvidenceGate:
    @pytest.mark.parametrize("gated", ["SUPPORTED", "VERIFIED"])
    @pytest.mark.parametrize("contract", [Hypothesis, KnowledgeClaim, GrammarPattern])
    def test_gated_without_evidence_raises(
        self, contract: type, gated: str
    ) -> None:
        kwargs: dict = {"status": gated}
        if contract is Hypothesis:
            kwargs.update(subject="word:x", predicate="pos:NOUN")
        elif contract is KnowledgeClaim:
            kwargs.update(claim_type="lexicon", subject="word:x", predicate="is-a", object="noun")
        else:
            kwargs.update(pattern="Subject Object Verb")
        with pytest.raises(ValidationError) as excinfo:
            contract(**kwargs)
        assert "evidence" in str(excinfo.value).lower()

    @pytest.mark.parametrize("gated", ["SUPPORTED", "VERIFIED"])
    def test_gated_with_evidence_passes(self, gated: str) -> None:
        h = Hypothesis(
            subject="word:x",
            predicate="pos:NOUN",
            status=gated,
            evidence_ids=[1],
        )
        assert h.status.value == gated
        assert KnowledgeClaim(
            claim_type="lexicon",
            subject="word:x",
            predicate="is-a",
            object="noun",
            status=gated,
            evidence_ids=[42],
        ).evidence_ids == [42]

    @pytest.mark.parametrize("open_status", ["OBSERVED", "CANDIDATE", "REJECTED", "DEPRECATED"])
    def test_ungated_statuses_pass_without_evidence(self, open_status: str) -> None:
        h = Hypothesis(subject="word:x", predicate="pos:NOUN", status=open_status)
        assert h.status.value == open_status

    def test_evidence_gate_error_is_value_error(self) -> None:
        assert issubclass(EvidenceGateError, ValueError)


# ---------------------------------------------------------------------------
# Confidence helper
# ---------------------------------------------------------------------------


class TestConfidenceFromEvidence:
    def test_single_tier_weights_match_foundation(self) -> None:
        from zolai.foundation.evidence import EvidenceTier

        for tier in (1, 2, 3, 4, 5):
            got = confidence_from_evidence([_evidence(tier)])
            assert got == round(EvidenceTier(tier).weight(), 2)

    def test_two_decimals_max(self) -> None:
        for tiers in ([1, 2], [1, 2, 3], [2, 3, 4, 5], [3, 3, 4], [1, 4, 5]):
            value = confidence_from_evidence([_evidence(t) for t in tiers])
            assert value == round(value, 2), f"{value} exceeds 2 dp"
            decimals = len(str(value).split(".")[1]) if "." in str(value) else 0
            assert decimals <= 2

    def test_evidence_only_no_manual_override(self) -> None:
        sig = inspect.signature(confidence_from_evidence)
        assert list(sig.parameters) == ["evidence"]
        # Result depends only on the evidence passed in.
        weak = confidence_from_evidence([_evidence(5)])
        strong = confidence_from_evidence([_evidence(1)])
        assert weak < strong

    def test_empty_evidence_is_zero(self) -> None:
        assert confidence_from_evidence([]) == 0.0

    def test_mean_of_tier_weights(self) -> None:
        # (1.0 + 0.4) / 2 = 0.7
        assert confidence_from_evidence([_evidence(1), _evidence(5)]) == 0.7

    def test_accepts_bare_tiers_and_foundation_members(self) -> None:
        from zolai.foundation.evidence import EvidenceTier

        assert confidence_from_evidence([1]) == 1.0
        assert confidence_from_evidence([EvidenceTier.DICTIONARY]) == 0.9

    def test_contract_evidence_exposes_tier_weight(self) -> None:
        assert _evidence(5).tier_weight == 0.4

    def test_item_without_tier_raises(self) -> None:
        with pytest.raises(TypeError):
            confidence_from_evidence([object()])


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


class TestJsonRoundTrip:
    def _samples(self) -> list[KnowledgeContract | object]:
        return [
            Word(word="Pasian", pos_canonical="NOUN", frequency=7, morph_features={"tone": "T1"}),
            WordForm(surface="piangsak", lemma="piang", form_type="derived"),
            Observation(text="Pasian in gam a piangsak hi.", tokens=["Pasian", "in", "gam"]),
            _evidence(1, ev_id=17),
            Hypothesis(
                subject="word:pasian",
                predicate="pos:NOUN",
                probability=0.8,
                evidence_ids=[1, 2],
                extras={"note": "Bible parallel"},
            ),
            KnowledgeClaim(
                claim_type="lexicon",
                subject="word:pasian",
                predicate="means",
                object="God",
                confidence=1.0,
                evidence_ids=[1],
                source_ids=[3],
            ),
            GrammarPattern(
                pattern_id="gp-001",
                pattern="S O V",
                normalized="s-ov",
                components=["subject", "object", "verb"],
                sources=["grammar_patterns"],
                evidence_ids=[9],
                frequency=12,
            ),
            MorphologicalRelation(
                surface="piangsak",
                root="piang",
                relation_type="suffix",
                function="causative",
                evidence_ids=[5],
                status="CANDIDATE",
            ),
            POSHypothesis(word="pasian", pos="NOUN", probability=0.95, evidence_ids=[4]),
            Source(source="dict_zo_en_master_v1.jsonl", doc_hash="f" * 64, source_type="dictionary"),
            KnowledgeVersion(
                version="2026.10.0",
                git_commit="abc1234",
                row_counts={"vocabulary": 104906},
                quality={"syllable_accuracy": 0.9849},
                eval_run_id=12,
            ),
        ]

    @pytest.mark.parametrize("index", range(11))
    def test_round_trip(self, index: int) -> None:
        model = self._samples()[index]
        payload = model.model_dump_json()
        restored = type(model).model_validate_json(payload)
        assert restored == model
        # And via plain dict JSON too (storage path).
        assert type(model).model_validate_json(json.dumps(model.model_dump())) == model


# ---------------------------------------------------------------------------
# POS allowlist
# ---------------------------------------------------------------------------


class TestPosAllowlist:
    def test_upos_allowlist_has_17_tags(self) -> None:
        from zolai.data.pos_normalize import UPOS_ALLOWLIST

        assert len(UPOS_ALLOWLIST) == 17

    @pytest.mark.parametrize("pos", ["NOUN", "VERB", "ADJ", "PROPN", "SCONJ"])
    def test_valid_pos_hypothesis(self, pos: str) -> None:
        h = POSHypothesis(word="pasian", pos=pos)
        assert h.pos == pos
        assert h.subject == "word:pasian"
        assert h.predicate == f"pos:{pos}"
        assert h.kind == "pos"

    @pytest.mark.parametrize("pos", ["NOUNX", "n", "pt par", ""])
    def test_invalid_pos_hypothesis_raises(self, pos: str) -> None:
        with pytest.raises(ValidationError):
            POSHypothesis(word="pasian", pos=pos)

    def test_word_pos_canonical_validated(self) -> None:
        assert Word(word="gam", pos_canonical="NOUN").pos_canonical == "NOUN"
        with pytest.raises(ValidationError):
            Word(word="gam", pos_canonical="junk-tag")

    def test_word_pos_canonical_none_allowed(self) -> None:
        assert Word(word="gam").pos_canonical is None


# ---------------------------------------------------------------------------
# Evidence adapters (foundation dataclass unchanged)
# ---------------------------------------------------------------------------


class TestFoundationAdapters:
    def test_from_and_to_foundation_round_trip(self) -> None:
        from zolai.foundation.evidence import Evidence as FoundationEvidence
        from zolai.foundation.evidence import EvidenceTier

        foundation = FoundationEvidence.create(
            fact_type="word",
            fact_key="word:gam",
            tier=EvidenceTier.DICTIONARY,
            source="dictionary",
            confidence=0.9,
            payload={"english": "earth"},
        )
        contract = Evidence.from_foundation(foundation)
        assert contract.tier == 2
        assert contract.payload == {"english": "earth"}
        assert contract.provenance_hash == foundation.provenance_hash
        assert contract.created_at == foundation.created_at

        back = contract.to_foundation()
        assert isinstance(back, FoundationEvidence)
        assert back.tier is EvidenceTier.DICTIONARY
        assert back.confidence == foundation.confidence
        assert back.payload == foundation.payload
        # Foundation dataclass itself never gains fields.
        assert set(FoundationEvidence.__dataclass_fields__) == {
            "fact_type",
            "fact_key",
            "tier",
            "source",
            "confidence",
            "provenance_hash",
            "payload",
            "created_at",
        }

    def test_from_foundation_rejects_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            Evidence.from_foundation({"fact_type": "word"})
