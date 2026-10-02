"""Grammar pattern contract: :class:`GrammarPattern` (Master Prompt §36).

Contract view of the ``grammar_patterns`` row; Phase 1 adds the six additive
columns (normalized, components, sources, evidence_ids, confidence, status).
"""

from __future__ import annotations

from pydantic import Field

from .base import KnowledgeContract

__all__ = ["GrammarPattern"]


class GrammarPattern(KnowledgeContract):
    """A grammar pattern with its normalized form, components and evidence links."""

    id: int | None = None
    pattern_id: str = ""
    pattern: str
    normalized: str | None = None
    description: str | None = None
    function: str = ""
    examples: list[str] = Field(default_factory=list)
    frequency: int = Field(default=0, ge=0)
    components: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
