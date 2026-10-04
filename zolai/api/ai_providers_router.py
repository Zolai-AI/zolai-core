"""Admin AI-provider endpoints — list / edit / activate / test / delete.

Mounted under ``/api/v1/admin/ai-providers`` (registered **before** the
catch-all in :mod:`zolai.api.server`).  Every route is ``strict`` scope-gated:

- ``GET /`` → ``settings:read`` strict (401 for anon *and* member in warn+enforce)
- ``PUT /{catalog_id}``, ``POST .../activate``, ``POST .../test``,
  ``DELETE /{catalog_id}`` → ``settings:write`` strict

Responses are **masked** — see :mod:`zolai.api.ai_provider_schemas`.  Unknown
``catalog_id`` is a real **404** (the ce04c72 contract: status codes, not a 200
body with ``error`` in it).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..llm import provider_settings as settings
from . import auth
from .ai_provider_schemas import (
    ActivateOut,
    ProviderListOut,
    ProviderOut,
    ProviderSecretOut,
    ProviderTestOut,
    ProviderUpdateIn,
)

router = APIRouter(prefix="/api/v1/admin/ai-providers", tags=["admin-ai"])

READ_DEP = Depends(auth.require_scope("settings:read", strict=True))
WRITE_DEP = Depends(auth.require_scope("settings:write", strict=True))


def _parse_models(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(m) for m in raw]
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(m) for m in parsed] if isinstance(parsed, list) else []


def _to_out(row: dict[str, Any]) -> ProviderOut:
    return ProviderOut(
        catalog_id=str(row.get("catalog_id") or ""),
        name=str(row.get("name") or ""),
        adapter=str(row.get("adapter") or "openai"),  # type: ignore[arg-type]
        base_url=str(row.get("base_url") or ""),
        models=_parse_models(row.get("models")),
        selected_model=str(row.get("selected_model") or ""),
        docs=str(row.get("docs") or ""),
        requires_key=bool(row.get("requires_key", 1)),
        enabled=bool(row.get("enabled", 1)),
        is_active=bool(row.get("is_active", 0)),
        tier=str(row.get("tier") or "standard"),
        timeout_s=int(row.get("timeout_s") or 60),
        secret=ProviderSecretOut(
            **settings.mask_secret_ref(row.get("api_key_ref"))  # type: ignore[arg-type]
        ),
    )


def _require_row(catalog_id: str) -> dict[str, Any]:
    row = settings.get_provider_row(catalog_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        )
    return row


@router.get("", response_model=ProviderListOut, dependencies=[READ_DEP])
def list_ai_providers() -> ProviderListOut:
    """Catalog rows + masked secret + active flag (never a plaintext key)."""
    rows = settings.list_provider_rows()
    items = [_to_out(r) for r in rows]
    return ProviderListOut(items=items, count=len(items))


@router.put("/{catalog_id}", response_model=ProviderOut, dependencies=[WRITE_DEP])
def update_ai_provider(catalog_id: str, body: ProviderUpdateIn) -> ProviderOut:
    """Upsert display/behaviour fields; optional write-only secret.

    Unknown ``catalog_id`` → **404** (rows are seeded, admins never invent one).
    """
    fields: dict[str, Any] = {
        k: v
        for k, v in body.model_dump(
            include={
                "name",
                "base_url",
                "models",
                "selected_model",
                "enabled",
                "tier",
                "timeout_s",
            }
        ).items()
        if v is not None
    }
    try:
        row = settings.upsert_provider(
            catalog_id,
            fields=fields,
            secret=body.secret,
            secret_env=body.secret_env,
            clear_secret=body.clear_secret,
        )
    except LookupError:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        ) from None
    except settings.ProviderError as exc:
        raise HTTPException(status_code=400, detail={"error": exc.code, "detail": exc.message}) from exc
    return _to_out(row)


@router.post("/{catalog_id}/activate", response_model=ActivateOut, dependencies=[WRITE_DEP])
def activate_ai_provider(catalog_id: str) -> ActivateOut:
    """Set the single global active row (every other row is cleared)."""
    try:
        settings.activate_provider(catalog_id)
    except LookupError:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        ) from None
    rows = settings.list_provider_rows()
    active = sum(1 for r in rows if r.get("is_active"))
    return ActivateOut(catalog_id=catalog_id, is_active=True, active_count=active)


@router.post("/{catalog_id}/test", response_model=ProviderTestOut, dependencies=[WRITE_DEP])
def test_ai_provider(catalog_id: str) -> ProviderTestOut:
    """1-token chat probe → ``{ok, status, latency_ms, error}`` (button)."""
    try:
        row = settings.get_provider_row(catalog_id)
        if row is None:
            raise LookupError(catalog_id)
        result = settings.test_connection(catalog_id)
    except LookupError:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        ) from None
    return ProviderTestOut(
        catalog_id=catalog_id,
        ok=bool(result.get("ok")),
        status=result.get("status"),
        latency_ms=float(result.get("latency_ms") or 0.0),
        error=result.get("error"),
        model=str(row.get("selected_model") or "") or None,
    )


@router.delete("/{catalog_id}", dependencies=[WRITE_DEP])
def delete_ai_provider(catalog_id: str) -> dict[str, Any]:
    """Delete a ``custom`` row only; catalog rows answer 409 (disable instead)."""
    try:
        settings.delete_provider(catalog_id)
    except LookupError:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        ) from None
    except settings.ProviderError as exc:  # DELETE_FORBIDDEN on a seeded row
        raise HTTPException(status_code=409, detail={"error": exc.code, "detail": exc.message}) from exc
    return {"catalog_id": catalog_id, "deleted": True}
