"""Pydantic shapes for the admin AI-provider surface (P1 §A).

Masking contract: responses carry :class:`ProviderSecretOut` (``mode`` /
``ref_masked`` / ``configured``) — **never** a plaintext key, and never an
``enc:v1:`` payload either.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProviderSecretOut(BaseModel):
    """Masked view of ``ai_providers.api_key_ref``."""

    mode: Literal["env", "encrypted", "none"] = "none"
    ref_masked: str = ""
    configured: bool = False


class ProviderOut(BaseModel):
    """One catalog row for the settings table."""

    catalog_id: str
    name: str
    adapter: Literal["brain", "openai", "openrouter", "custom"]
    base_url: str = ""
    models: list[str] = Field(default_factory=list)
    selected_model: str = ""
    docs: str = ""
    requires_key: bool = True
    enabled: bool = True
    is_active: bool = False
    tier: str = "standard"
    timeout_s: int = 60
    secret: ProviderSecretOut = Field(default_factory=ProviderSecretOut)


class ProviderListOut(BaseModel):
    items: list[ProviderOut]
    count: int


class ProviderUpdateIn(BaseModel):
    """PUT ``/api/v1/admin/ai-providers/{catalog_id}`` — all fields optional.

    ``catalog_id`` and ``adapter`` are immutable (stable join key + dispatch).
    ``secret`` is write-only: it is stored as ``enc:v1:`` (or refused) and
    never echoed back; ``secret_env`` stores an ``env:NAME`` reference.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    models: list[str] | None = None
    selected_model: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    tier: str | None = Field(default=None, max_length=40)
    timeout_s: int | None = Field(default=None, ge=1, le=600)
    secret: str | None = Field(default=None, max_length=4096)
    secret_env: str | None = Field(default=None, max_length=120)
    clear_secret: bool = False


class ProviderTestOut(BaseModel):
    """``POST .../{catalog_id}/test`` — 1-token probe result."""

    catalog_id: str
    ok: bool
    status: int | None = None
    latency_ms: float = 0.0
    error: str | None = None
    model: str | None = None


class ActivateOut(BaseModel):
    catalog_id: str
    is_active: bool = True
    active_count: int = 1


class RefreshModelsOut(BaseModel):
    """``POST .../{catalog_id}/refresh-models`` — refreshed model list.

    ``source`` is honest about where the list came from: ``remote`` = the
    provider's ``/models`` endpoint answered (and the row was updated);
    ``catalog`` = the fetch was skipped or failed and the catalog-declared
    list was returned unchanged (never a 500).
    """

    catalog_id: str
    models: list[str] = Field(default_factory=list)
    source: Literal["remote", "catalog"] = "catalog"


class ErrorBody(BaseModel):
    """Stable machine-readable error envelope (``detail.error``)."""

    error: str
    detail: Any | None = None
