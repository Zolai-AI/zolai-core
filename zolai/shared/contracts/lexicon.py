"""Lexicon contracts: :class:`Word` and :class:`WordForm` (Master Prompt §36)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import KnowledgeContract

__all__ = ["Word", "WordForm"]


def _validate_upos(value: str | None) -> str | None:
    """Validate a UPOS tag against the canonical allowlist (lazy import)."""
    if value is None:
        return None
    from zolai.data.pos_normalize import UPOS_ALLOWLIST  # lazy — avoid import cycles

    if value not in UPOS_ALLOWLIST:
        raise ValueError(
            f"{value!r} is not a member of UPOS_ALLOWLIST (17-tag Universal Dependencies set)"
        )
    return value


class Word(KnowledgeContract):
    """A lexical entry — contract view of the ``vocabulary`` row.

    Storage (Phase 1): ``vocabulary`` gains only the additive ``status`` column;
    ``evidence_ids`` / ``doc_freq`` / ``sent_freq`` / ``first_seen`` /
    ``last_seen`` are contract-level until their columns land (Phase 2+).
    ``pos_canonical`` is validated against the 17-tag UPOS allowlist.
    """

    id: int | None = None
    word: str = Field(min_length=1, description="Headword as written (ZVS 2018)")
    normalized_form: str | None = Field(
        default=None, description="Normalized form; defaults to the lower-cased headword"
    )
    surface_forms: list[str] = Field(default_factory=list)
    language: str = "zo"
    orthography: str = "zvs2018"
    frequency: int = Field(default=0, ge=0)
    doc_freq: int | None = Field(default=None, ge=0)
    sent_freq: int | None = Field(default=None, ge=0)
    pos_canonical: str | None = None
    morph_features: dict[str, Any] = Field(default_factory=dict)
    review_status: str | None = Field(
        default=None, description="Human-review flag (distinct from knowledge status)"
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    content_hash: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    version: int = Field(default=1, ge=1)

    @field_validator("pos_canonical")
    @classmethod
    def _pos_canonical_upos(cls, value: str | None) -> str | None:
        return _validate_upos(value)

    @model_validator(mode="after")
    def _default_normalized_form(self) -> Word:
        if self.normalized_form is None:
            self.normalized_form = self.word.lower()
        return self


class WordForm(BaseModel):
    """Surface form of a word — DDL deferred to Phase 2 (L2).

    Contract type only; no table exists yet, so no lifecycle status is
    asserted here.
    """

    id: int | None = None
    surface: str = Field(min_length=1)
    lemma: str = Field(min_length=1, description="Dictionary form this surface maps to")
    root: str | None = None
    form_type: str = Field(
        default="", description="e.g. 'inflected' | 'derived' | 'compound' | 'affixed'"
    )
    morph_features: dict[str, Any] = Field(default_factory=dict)
    frequency: int = Field(default=0, ge=0)
    contexts: list[str] = Field(default_factory=list)
    language: str = "zo"
