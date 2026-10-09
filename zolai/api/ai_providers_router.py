"""Admin AI-provider endpoints — list / edit / activate / test / refresh-models / delete.

Mounted under ``/api/v1/admin/ai-providers`` (registered **before** the
catch-all in :mod:`zolai.api.server`).  Every route is ``strict`` scope-gated:

- ``GET /`` → ``settings:read`` strict (401 for anon *and* member in warn+enforce)
- ``PUT /{catalog_id}``, ``POST .../activate``, ``POST .../test``,
  ``POST .../refresh-models``, ``DELETE /{catalog_id}`` → ``settings:write`` strict

Responses are **masked** — see :mod:`zolai.api.ai_provider_schemas`.  Unknown
``catalog_id`` is a real **404** (the ce04c72 contract: status codes, not a 200
body with ``error`` in it).

Notifications
-------------
Emits admin_action notifications on provider activate and test.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..llm import provider_settings as settings
from ..notifications import get_notification_service
from . import auth
from .ai_provider_schemas import (
    ActivateOut,
    ProviderListOut,
    ProviderOut,
    ProviderSecretOut,
    ProviderTestOut,
    ProviderUpdateIn,
    RefreshModelsOut,
)

router = APIRouter(prefix="/api/v1/admin/ai-providers", tags=["admin-ai"])

logger = logging.getLogger(__name__)

READ_DEP = Depends(auth.require_scope("settings:read", strict=True))
WRITE_DEP = Depends(auth.require_scope("settings:write", strict=True))


async def _emit_admin_action(action: str, admin_user: str, details: str) -> None:
    """Emit an admin action notification to admins."""
    try:
        service = get_notification_service()
        context = {
            "timestamp": "2026-01-01T00:00:00Z",
            "action": action,
            "admin_user": admin_user,
            "details": details,
            "app_name": "Zolai AI",
            "environment": "production",
        }
        await service.send_admin_alert("admin_action", context, dedup=False)
    except Exception:
        pass


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


#: Adapters whose remote listing is worth attempting (OpenAI-compatible
#: ``GET {base}/models`` shape: ``{"data": [{"id": ...}]}``).  ``brain`` and
#: ``custom`` have no stable listing endpoint — and the gemini catalog row is
#: OpenAI-compat but Google lists a different shape — all three are
#: catalog-declared, never fetched.
_REMOTE_FETCH_ADAPTERS = frozenset({"openai", "openrouter"})
_CATALOG_ONLY_IDS = frozenset({"google-gemini"})


def _models_endpoint(base_url: str) -> str:
    """``.../chat/completions`` (or ``.../v1``) → ``.../models``."""
    base = str(base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/models"


def _fetch_remote_models(row: dict[str, Any], timeout_s: float = 15.0) -> list[str]:
    """GET the provider's ``/models`` endpoint with the row's key.

    Module-level so tests monkeypatch it (no socket in the default suite).
    Raises on any failure — the caller degrades to the catalog list (never a
    500, plan item 10).
    """
    import httpx

    from ..llm import adapter as adapter_mod

    url = _models_endpoint(str(row.get("base_url") or ""))
    if not url or url == "/models":
        raise ValueError(f"provider {row.get('catalog_id')!r} has no base_url")
    key = adapter_mod.resolve_api_key(row)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = httpx.get(url, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("unexpected /models payload shape")
    models = [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]
    if not models:
        raise ValueError("empty model list from remote endpoint")
    return models


def _catalog_models(row: dict[str, Any]) -> list[str]:
    """Catalog-declared list for this row (fallback for every degrade path)."""
    from ..llm.catalog import find_catalog

    entry = find_catalog(str(row.get("catalog_id") or ""))
    if entry is not None:
        return [str(m) for m in entry.models]
    return _parse_models(row.get("models"))


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
    # Emit admin action notification (fire and forget)
    import asyncio

    async def _emit():
        await _emit_admin_action(
            "provider_activate",
            "admin",  # Would need to get from request state
            f"Activated provider {catalog_id}",
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_emit())
    except RuntimeError:
        # No event loop running (e.g., in tests)
        pass
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
    # Emit admin action notification
    import asyncio

    async def _emit() -> None:
        await _emit_admin_action(
            "provider_test",
            "admin",
            f"Tested provider {catalog_id}: {'success' if result.get('ok') else 'failed'}",
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_emit())
    except RuntimeError:
        # No event loop running (e.g., in tests)
        pass
    return ProviderTestOut(
        catalog_id=catalog_id,
        ok=bool(result.get("ok")),
        status=result.get("status"),
        latency_ms=float(result.get("latency_ms") or 0.0),
        error=result.get("error"),
        model=str(row.get("selected_model") or "") or None,
    )


@router.post("/{catalog_id}/refresh-models", response_model=RefreshModelsOut, dependencies=[WRITE_DEP])
def refresh_models(catalog_id: str) -> RefreshModelsOut:
    """Refresh the row's ``models`` list from the provider (button).

    ``openai``/``openrouter`` rows fetch ``GET {base}/models`` with the row's
    key and persist the result; ``brain``/``custom``/gemini rows and **any**
    fetch failure degrade to the catalog-declared list with
    ``source="catalog"`` — this route never 500s (plan item 10).
    """
    row = _require_row(catalog_id)
    adapter_name = str(row.get("adapter") or "openai")
    fetchable = (
        adapter_name in _REMOTE_FETCH_ADAPTERS
        and str(row.get("catalog_id") or "") not in _CATALOG_ONLY_IDS
    )
    source = "catalog"
    models: list[str]
    if fetchable:
        try:
            models = _fetch_remote_models(row, timeout_s=float(row.get("timeout_s") or 15))
            source = "remote"
        except Exception as exc:  # degrade — never 500
            logger.warning("refresh-models %s fetch failed, catalog fallback: %s", catalog_id, exc)
            models = _catalog_models(row)
    else:
        models = _catalog_models(row)

    if source == "remote":
        try:
            settings.upsert_provider(catalog_id, fields={"models": models})
        except (LookupError, settings.ProviderError) as exc:
            # The fresh list is still returned — a persist failure degrades the
            # source label, not the request.
            logger.warning("refresh-models %s persist failed: %s", catalog_id, exc)
            source = "catalog"
    return RefreshModelsOut(catalog_id=catalog_id, models=models, source=source)  # type: ignore[arg-type]


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
