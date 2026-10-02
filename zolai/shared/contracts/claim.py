"""Knowledge claim contract: :class:`KnowledgeClaim` (Master Prompt §36).

Backed by ``knowledge_claims`` + the ``claim_evidence`` link table.  Confidence
is always evidence-derived (see :func:`confidence_from_evidence`); human
judgment is expressed via ``KnowledgeStatus``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic import Field

from .base import KnowledgeContract, KnowledgeStatus, confidence_from_evidence

__all__ = ["KnowledgeClaim"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeClaim(KnowledgeContract):
    """A subject/predicate/object knowledge claim with evidence-backed confidence.

    ``evidence_ids`` are ``foundation_evidence.id`` values; persisted links
    live in ``claim_evidence`` (enforced by ``ClaimRepository``).
    """

    id: int | None = None
    claim_type: str = Field(min_length=1, description="e.g. 'lexicon' | 'grammar' | 'morphology'")
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str | None = None
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Evidence-derived only (never hand-set)"
    )
    source_ids: list[int] = Field(default_factory=list, description="provenance.id source links")
    notes: str = ""
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @classmethod
    def from_evidence(
        cls,
        *,
        claim_type: str,
        subject: str,
        predicate: str,
        object: str | None = None,
        evidence: Sequence[Any] = (),
        status: KnowledgeStatus | str | None = None,
        source_ids: Sequence[int] = (),
        notes: str = "",
    ) -> KnowledgeClaim:
        """Build a claim whose confidence comes strictly from its evidence.

        ``status`` defaults to ``SUPPORTED`` when there is evidence and
        ``OBSERVED`` otherwise, so the evidence gate is satisfied by
        construction.  Each evidence item must expose ``tier`` (and may expose
        ``id``, which is collected into ``evidence_ids``).
        """
        confidence = confidence_from_evidence(evidence)
        evidence_ids = [int(item.id) for item in evidence if getattr(item, "id", None) is not None]
        if status is None:
            status = KnowledgeStatus.SUPPORTED if evidence_ids else KnowledgeStatus.OBSERVED
        return cls(
            claim_type=claim_type,
            subject=subject,
            predicate=predicate,
            object=object,
            confidence=confidence,
            evidence_ids=evidence_ids,
            source_ids=[int(s) for s in source_ids],
            status=KnowledgeStatus(status),
            notes=notes,
        )
