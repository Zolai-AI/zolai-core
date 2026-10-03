"""Base building blocks for the Phase 1 knowledge contracts (Master Prompt §36).

Contains the shared lifecycle enum (``KnowledgeStatus``), the whitelisted
transition table, the evidence gate, and the tier-weighted confidence helper.

Invariants carried forward from the audit:

* **evidence before confidence, confidence before status** — human judgment is
  expressed through the status enum, never by editing numbers by hand.
* Tier weights are imported **lazily** from :mod:`zolai.foundation.evidence`
  and are never copied into this module.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DISCOVERY_STATUSES",
    "GATED_STATUSES",
    "DiscoveryStatusError",
    "EvidenceGateError",
    "InvalidTransition",
    "KnowledgeContract",
    "KnowledgeStatus",
    "confidence_from_evidence",
    "require_discovery_status",
    "validate_transition",
]


class KnowledgeStatus(str, Enum):
    """Knowledge lifecycle status — exactly 6 values (Master Prompt §36)."""

    OBSERVED = "OBSERVED"
    CANDIDATE = "CANDIDATE"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    DEPRECATED = "DEPRECATED"

    def can_transition_to(self, target: KnowledgeStatus) -> bool:
        """Whether ``target`` is a whitelisted successor of this status."""
        return target in ALLOWED_TRANSITIONS[self]


#: Whitelisted lifecycle transitions.
#:
#: Main chain: ``OBSERVED -> CANDIDATE -> SUPPORTED -> VERIFIED``.
#: Every non-terminal state may also move to ``REJECTED``; any state except
#: ``DEPRECATED`` may move to ``DEPRECATED`` (terminal).
ALLOWED_TRANSITIONS: dict[KnowledgeStatus, frozenset[KnowledgeStatus]] = {
    KnowledgeStatus.OBSERVED: frozenset(
        {KnowledgeStatus.CANDIDATE, KnowledgeStatus.REJECTED, KnowledgeStatus.DEPRECATED}
    ),
    KnowledgeStatus.CANDIDATE: frozenset(
        {KnowledgeStatus.SUPPORTED, KnowledgeStatus.REJECTED, KnowledgeStatus.DEPRECATED}
    ),
    KnowledgeStatus.SUPPORTED: frozenset(
        {KnowledgeStatus.VERIFIED, KnowledgeStatus.REJECTED, KnowledgeStatus.DEPRECATED}
    ),
    KnowledgeStatus.VERIFIED: frozenset(
        {KnowledgeStatus.REJECTED, KnowledgeStatus.DEPRECATED}
    ),
    KnowledgeStatus.REJECTED: frozenset({KnowledgeStatus.DEPRECATED}),
    KnowledgeStatus.DEPRECATED: frozenset(),
}

#: Statuses that require at least one linked evidence row (the evidence gate).
GATED_STATUSES = frozenset({KnowledgeStatus.SUPPORTED, KnowledgeStatus.VERIFIED})


class InvalidTransition(ValueError):
    """Raised when a status change is not in :data:`ALLOWED_TRANSITIONS`."""


class EvidenceGateError(ValueError):
    """Raised when a gated status is set without at least one evidence link."""


#: Statuses a *machine* discovery writer may emit (Master Prompt §34).
#:
#: Discovery proposes — it never promotes.  ``SUPPORTED``/``VERIFIED`` are
#: reached only through the Phase 4 human review queue, ``REJECTED``/
#: ``DEPRECATED`` only through a review decision.
DISCOVERY_STATUSES: frozenset[KnowledgeStatus] = frozenset(
    {KnowledgeStatus.OBSERVED, KnowledgeStatus.CANDIDATE}
)


class DiscoveryStatusError(ValueError):
    """Raised when a discovery writer is handed a status it may not emit."""


def require_discovery_status(status: KnowledgeStatus | str) -> KnowledgeStatus:
    """Validate a machine-discovered row status against :data:`DISCOVERY_STATUSES`.

    Every discovery write path (``hypotheses`` and the ``disc_*``
    ``grammar_patterns`` namespaces) funnels through this guard so that a bug
    can never silently mint a ``SUPPORTED``/``VERIFIED`` row — promotion is a
    human act (§34), never a build's.

    Returns:
        The coerced :class:`KnowledgeStatus`.

    Raises:
        DiscoveryStatusError: When ``status`` is outside
            ``{OBSERVED, CANDIDATE}``.
    """
    try:
        resolved = KnowledgeStatus(status)
    except ValueError as exc:
        raise DiscoveryStatusError(
            f"unknown knowledge status {status!r}; discovery may only write "
            f"{'/'.join(sorted(s.value for s in DISCOVERY_STATUSES))}"
        ) from exc
    if resolved not in DISCOVERY_STATUSES:
        raise DiscoveryStatusError(
            f"discovery refuses status {resolved.value}: machine writers may only "
            "emit "
            f"{'/'.join(sorted(s.value for s in DISCOVERY_STATUSES))} — "
            "SUPPORTED/VERIFIED require the Phase 4 human review queue"
        )
    return resolved


def validate_transition(
    current: KnowledgeStatus | str, target: KnowledgeStatus | str
) -> KnowledgeStatus:
    """Validate ``current -> target`` against the whitelist.

    Re-setting the same status is an idempotent no-op and is allowed.

    Returns:
        The (coerced) target status.

    Raises:
        InvalidTransition: When the pair is not whitelisted.
    """
    current = KnowledgeStatus(current)
    target = KnowledgeStatus(target)
    if target != current and not current.can_transition_to(target):
        raise InvalidTransition(
            f"Invalid status transition {current.value} -> {target.value}: "
            f"allowed from {current.value}: "
            f"{sorted(s.value for s in ALLOWED_TRANSITIONS[current]) or '[]'}"
        )
    return target


def confidence_from_evidence(evidence: Iterable[Any]) -> float:
    """Compute confidence strictly from evidence tier weights, rounded to 2 dp.

    Each item may be a contract :class:`Evidence`, the foundation ``Evidence``
    dataclass, an ``EvidenceTier`` member, a bare tier int (1-5), or anything
    exposing a ``tier`` attribute.  The numeric weights come from
    ``zolai.foundation.evidence.EvidenceTier.weight()`` via a **lazy import**
    so they are never duplicated here.

    There is deliberately no manual override parameter: confidence is
    evidence-derived only (human judgment lives in ``KnowledgeStatus``).

    Returns:
        ``0.0`` when there is no evidence (evidence before confidence).
    """
    items = list(evidence)
    if not items:
        return 0.0

    from zolai.foundation.evidence import EvidenceTier  # lazy — never copy weights

    total = 0.0
    for item in items:
        tier: Any = item if isinstance(item, (int, EvidenceTier)) else getattr(item, "tier", None)
        if tier is None:
            raise TypeError(f"evidence item {item!r} has no 'tier' attribute")
        total += EvidenceTier(tier).weight()
    return round(total / len(items), 2)


class KnowledgeContract(BaseModel):
    """Shared base for knowledge-bearing contracts.

    Provides the lifecycle ``status`` plus the ``evidence_ids`` links
    (``foundation_evidence.id`` references) and enforces the evidence gate:
    constructing a contract with ``SUPPORTED`` or ``VERIFIED`` status and an
    empty ``evidence_ids`` list raises.
    """

    status: KnowledgeStatus = KnowledgeStatus.OBSERVED
    evidence_ids: list[int] = Field(
        default_factory=list,
        description="Linked foundation_evidence.id values (evidence before status)",
    )

    @model_validator(mode="after")
    def _evidence_gate(self) -> KnowledgeContract:
        if self.status in GATED_STATUSES and not self.evidence_ids:
            raise EvidenceGateError(
                f"status {self.status.value} requires at least one linked evidence "
                "row (evidence before confidence, confidence before status)"
            )
        return self

    def transition_to(self, target: KnowledgeStatus | str) -> KnowledgeContract:
        """Return a copy of this contract moved to ``target`` if whitelisted."""
        validate_transition(self.status, target)
        return self.model_copy(update={"status": KnowledgeStatus(target)})
