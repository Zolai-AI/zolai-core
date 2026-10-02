"""Source contract: :class:`Source` (Master Prompt §36).

Contract view of the ``provenance`` row.  Field mapping (plan inventory):

* ``filename`` → ``source``
* ``sha256`` → ``doc_hash``
* ``generator_script`` → ``pipeline``
* ``version`` → ``dataset_version``

``status`` here is the provenance **file** status (``active``/…), which is
distinct from the knowledge lifecycle :class:`~.base.KnowledgeStatus`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["Source"]


class Source(BaseModel):
    """A dataset/file source with pipeline provenance."""

    id: int | None = None
    source: str = Field(min_length=1, description="Source filename (provenance.filename)")
    source_type: str = Field(default="", description="e.g. 'bible' | 'dictionary' | 'corpus'")
    doc: str | None = Field(default=None, description="Logical document this source belongs to")
    doc_hash: str = Field(default="", description="SHA-256 of the source file (provenance.sha256)")
    dataset_version: str | None = Field(default=None, description="provenance.version")
    pipeline: str | None = Field(default=None, description="provenance.generator_script")
    pipeline_version: str | None = None
    extractor_version: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    row_count: int = Field(default=0, ge=0)
    status: str = Field(default="active", description="Provenance file status — not KnowledgeStatus")
    updated_at: str = ""
