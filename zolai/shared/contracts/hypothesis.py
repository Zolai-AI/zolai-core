"""Hypothesis contracts: :class:`Hypothesis`, :class:`POSHypothesis`,
:class:`MorphologicalRelation` (Master Prompt §36).

One canonical storage — the polymorphic ``hypotheses`` table (``kind`` column).
``POSHypothesis`` (``kind='pos'``) and ``MorphologicalRelation``
(``kind='morph_relation'``) are typed views over the same rows; no duplicate
layer is introduced either way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import KnowledgeContract

__all__ = ["CollocationHypothesis", "Hypothesis", "MorphologicalRelation", "POSHypothesis"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Hypothesis(KnowledgeContract):
    """Generic subject/predicate/object hypothesis backed by ``hypotheses``."""

    id: int | None = None
    kind: str = Field(default="generic", description="'pos' | 'morph_relation' | 'generic'")
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str | None = None
    probability: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    extras: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class POSHypothesis(Hypothesis):
    """POS hypothesis — ``hypotheses`` row with ``kind='pos'``.

    ``pos`` is validated against the 17-tag UPOS allowlist.  The stored
    ``subject`` / ``predicate`` follow the canonical encoding
    ``subject=word:{headword}``, ``predicate=pos:{UPOS}``.
    """

    kind: Literal["pos"] = "pos"
    word: str = Field(min_length=1, description="Headword the POS is hypothesized for")
    pos: str = Field(min_length=1, description="UPOS tag from UPOS_ALLOWLIST")
    # Encoded canonical slots — recomputed by the validator below (defaults keep
    # them optional at input time; the base Hypothesis keeps them required).
    subject: str = Field(default="", description="Encoded 'word:{headword}'")
    predicate: str = Field(default="", description="Encoded 'pos:{UPOS}'")

    @model_validator(mode="after")
    def _encode_spo(self) -> POSHypothesis:
        from zolai.data.pos_normalize import UPOS_ALLOWLIST  # lazy — avoid import cycles

        if self.pos not in UPOS_ALLOWLIST:
            raise ValueError(
                f"pos {self.pos!r} is not a member of UPOS_ALLOWLIST "
                "(17-tag Universal Dependencies set)"
            )
        self.subject = f"word:{self.word}"
        self.predicate = f"pos:{self.pos}"
        return self


class MorphologicalRelation(Hypothesis):
    """Surface→root morphological relation — ``kind='morph_relation'`` row.

    ``relation_type`` / ``function`` are carried in ``extras`` (per the plan),
    the root is stored in the ``object`` slot, and ``subject``/``predicate``
    use the canonical encoding ``word:{surface}`` / ``morph:{relation_type}``.
    """

    kind: Literal["morph_relation"] = "morph_relation"
    surface: str = Field(min_length=1, description="Surface form observed in text")
    root: str = Field(min_length=1, description="Root/lemma the surface maps to")
    relation_type: str = Field(
        default="", description="e.g. 'prefix' | 'suffix' | 'compound' | 'stem'"
    )
    function: str = Field(default="", description="Grammatical function of the relation")
    # Encoded canonical slots — recomputed by the validator below (defaults keep
    # them optional at input time; the base Hypothesis keeps them required).
    subject: str = Field(default="", description="Encoded 'word:{surface}'")
    predicate: str = Field(default="", description="Encoded 'morph:{relation_type}'")

    @model_validator(mode="after")
    def _encode_spo(self) -> MorphologicalRelation:
        self.subject = f"word:{self.surface}"
        self.predicate = f"morph:{self.relation_type}"
        if self.object is None:
            self.object = self.root
        self.extras = {**self.extras, "type": self.relation_type, "function": self.function}
        return self


class CollocationHypothesis(Hypothesis):
    """Collocation hypothesis — ``hypotheses`` row with ``kind='collocation'``.

    The two words are encoded in the canonical slots:
    ``subject=word:{word1}``, ``predicate=collocates_with``,
    ``object=word:{word2}``.  ``extras`` carries ``pmi``, ``count``, ``window``.
    """

    kind: Literal["collocation"] = "collocation"
    word1: str = Field(min_length=1, description="First word in the collocation pair")
    word2: str = Field(min_length=1, description="Second word in the collocation pair")
    pmi: float = Field(default=0.0, ge=0.0, description="Pointwise mutual information score")
    count: int = Field(default=0, ge=0, description="Window co-occurrence count")
    window: int = Field(default=2, ge=1, description="Half-window size used for extraction")
    # Encoded canonical slots — recomputed by the validator below.
    subject: str = Field(default="", description="Encoded 'word:{word1}'")
    predicate: str = Field(default="", description="Encoded 'collocates_with'")
    object: str = Field(default="", description="Encoded 'word:{word2}'")

    @model_validator(mode="after")
    def _encode_spo(self) -> CollocationHypothesis:
        self.subject = f"word:{self.word1}"
        self.predicate = "collocates_with"
        self.object = f"word:{self.word2}"
        self.extras = {**self.extras, "pmi": self.pmi, "count": self.count, "window": self.window}
        return self
