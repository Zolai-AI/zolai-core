"""Knowledge version contract: :class:`KnowledgeVersion` (Master Prompt §36).

Backed by the ``knowledge_versions`` snapshot table (one row per released
knowledge-base build).  ``version`` is the unique release identifier (TEXT);
``row_version`` is the integer optimistic-lock counter required by
``BaseRepository`` and is deliberately distinct from ``version``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .base import KnowledgeStatus

__all__ = ["KnowledgeVersion"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeVersion(BaseModel):
    """A release snapshot: versions, counts, quality metrics, eval link.

    Status here tracks the release lifecycle of the snapshot (default
    ``OBSERVED``); the evidence gate does not apply — the evidence for a
    release lives in its linked ``eval_runs`` row.
    """

    id: int | None = None
    version: str = Field(min_length=1, description="Unique release identifier (TEXT)")
    git_commit: str | None = None
    source_versions: dict[str, str] = Field(
        default_factory=dict, description="Per-source dataset versions"
    )
    pipeline_version: str | None = None
    schema_version: str | None = None
    row_counts: dict[str, int] = Field(default_factory=dict, description="Table → row count")
    quality: dict[str, float] = Field(default_factory=dict, description="Quality metric → value")
    eval_run_id: int | None = Field(default=None, description="FK → eval_runs.id")
    manifest_hash: str | None = None
    status: KnowledgeStatus = KnowledgeStatus.OBSERVED
    row_version: int = Field(default=1, ge=1, description="Optimistic-lock counter (not `version`)")
    created_at: str = Field(default_factory=_now)
