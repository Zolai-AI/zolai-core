"""Evidence contracts: :class:`Evidence` (§11 superset) and :class:`Observation`.

The foundation ``Evidence`` **dataclass** in :mod:`zolai.foundation.evidence`
is unchanged; this module defines the storage-facing pydantic superset and the
two-way adapters between the shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["Evidence", "Observation"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evidence(BaseModel):
    """A single piece of evidence — §11 superset of the foundation dataclass.

    Core fields mirror ``zolai.foundation.evidence.Evidence``; the trailing
    block adds the §11 provenance fields that land as additive columns on
    ``foundation_evidence`` in Phase 1.

    ``tier`` is stored as an int (1-5) so the contract round-trips through
    JSON and SQLite unchanged; weights always resolve through the foundation
    ``EvidenceTier`` (see :func:`confidence_from_evidence`).
    """

    id: int | None = Field(default=None, description="foundation_evidence.id when persisted")
    fact_type: str = Field(description="'word' | 'sentence' | 'paragraph' | 'grammar'")
    fact_key: str = Field(description="e.g. 'word:pasian', 'sentence:GEN 1:1'")
    tier: int = Field(ge=1, le=5, description="1=T1 Bible … 5=T5 LLM (foundation EvidenceTier)")
    source: str = Field(description="Source table/system: 'bible_verses', 'dictionary', …")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Source-internal confidence")
    provenance_hash: str = Field(default="", description="SHA256 (truncated) of the source record(s)")
    payload: dict[str, Any] = Field(default_factory=dict, description="Source-specific detail")
    created_at: str = Field(default_factory=_now)

    # --- §11 superset — additive columns on foundation_evidence -------------
    source_id: str | None = None
    document_id: str | None = None
    sentence_id: str | None = None
    observed_text: str | None = None
    method: str | None = None
    extractor: str | None = None

    @property
    def tier_weight(self) -> float:
        """Numeric tier weight via the foundation ``EvidenceTier`` (lazy import)."""
        from zolai.foundation.evidence import EvidenceTier  # lazy — never copy weights

        return EvidenceTier(self.tier).weight()

    @classmethod
    def from_foundation(cls, ev: Any) -> Evidence:
        """Adapt the unchanged foundation dataclass into the §11 contract."""
        from zolai.foundation.evidence import Evidence as FoundationEvidence  # lazy

        if not isinstance(ev, FoundationEvidence):
            raise TypeError(f"expected foundation Evidence dataclass, got {type(ev).__name__}")
        return cls(
            fact_type=ev.fact_type,
            fact_key=ev.fact_key,
            tier=ev.tier.value,
            source=ev.source,
            confidence=ev.confidence,
            provenance_hash=ev.provenance_hash,
            payload=dict(ev.payload),
            created_at=ev.created_at,
        )

    def to_foundation(self) -> Any:
        """Adapt back to the unchanged foundation ``Evidence`` dataclass."""
        from zolai.foundation.evidence import Evidence as FoundationEvidence  # lazy
        from zolai.foundation.evidence import EvidenceTier  # lazy

        return FoundationEvidence(
            fact_type=self.fact_type,
            fact_key=self.fact_key,
            tier=EvidenceTier(self.tier),
            source=self.source,
            confidence=self.confidence,
            provenance_hash=self.provenance_hash,
            payload=dict(self.payload),
            created_at=self.created_at,
        )


class Observation(BaseModel):
    """A raw observed unit (tokenized text + context + source ref).

    DDL deferred to Phase 2 — contract type only.
    """

    id: int | None = None
    text: str = Field(min_length=1, description="Observed text as written in the source")
    tokens: list[str] = Field(default_factory=list)
    context: str = Field(default="", description="Surrounding context for the observation")
    source_ref: str = Field(default="", description="Stable source reference, e.g. 'bible_verses:12345'")
    source_id: str | None = None
    document_id: str | None = None
    sentence_id: str | None = None
    method: str = Field(default="", description="How the unit was observed/extracted")
    extractor: str = Field(default="", description="Tool/version that produced the observation")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
