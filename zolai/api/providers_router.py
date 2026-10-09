"""Public provider catalog + the shared per-request selection guard.

Two things live here (Phase B):

- ``GET /api/v1/providers`` — **public** (declared in
  :data:`zolai.api.rbac.PUBLIC_ROUTES`, so it is open in every auth mode and
  never 401s).  It returns the *enabled* rows only, and only the five fields a
  client needs to choose a target: ``catalog_id``, ``name``, ``adapter``,
  ``models``, ``selected_model``.  The row's ``api_key_ref`` is never read —
  zero secrets by construction, the same masking discipline as the admin
  routes without even the masked ``secret`` block.
- :func:`validate_selection` — the guard ``assistant_router`` and
  ``agent_router`` run **before** a run/chat starts.  It is a DB read only (no
  socket), so an unknown or disabled ``provider`` is a real **404** and a model
  the row does not list a real **400**, in ``rule`` mode too — status codes,
  not a 200 body with ``error`` in it (ce04c72 contract).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..llm import provider_settings as settings
from ..llm.adapter import MODEL_NOT_CONFIGURED, NO_ACTIVE_PROVIDER
from ..llm.adapter import ProviderError as AdapterProviderError

router = APIRouter(prefix="/api/v1", tags=["providers"])


def _item(row: dict[str, Any]) -> dict[str, Any]:
    """The public projection — five fields, no secret, no admin bookkeeping."""
    return {
        "catalog_id": str(row.get("catalog_id") or ""),
        "name": str(row.get("name") or ""),
        "adapter": str(row.get("adapter") or "openai"),
        "models": settings.parse_models(row.get("models")),
        "selected_model": str(row.get("selected_model") or ""),
    }


@router.get("/providers", summary="Public AI provider catalog (enabled rows)")
def public_providers() -> dict[str, Any]:
    """What a client may target — enabled rows, zero secrets, anon-safe."""
    rows = [r for r in settings.list_provider_rows() if int(r.get("enabled") or 0)]
    items = [_item(r) for r in rows]
    return {"items": items, "count": len(items)}


#: Stable code → HTTP status (404 = not available to you, 400 = bad request).
_STATUS_BY_CODE: dict[str, int] = {
    settings.PROVIDER_UNKNOWN: 404,
    settings.PROVIDER_INACTIVE: 404,
    settings.PROVIDER_MODEL_UNKNOWN: 400,
    settings.ASSISTANT_MODEL_UNKNOWN_FOR_PROVIDER: 400,
    settings.ASSISTANT_MODEL_NOT_SELECTED: 409,
    NO_ACTIVE_PROVIDER: 409,
    MODEL_NOT_CONFIGURED: 400,
}


def _http_for(exc: Exception) -> HTTPException:
    code = str(getattr(exc, "code", "") or "")
    status = _STATUS_BY_CODE.get(code, 400)
    error = {
        settings.PROVIDER_UNKNOWN: "provider_unknown",
        settings.PROVIDER_INACTIVE: "provider_inactive",
        settings.PROVIDER_MODEL_UNKNOWN: "provider_model_unknown",
        NO_ACTIVE_PROVIDER: "no_active_provider",
        MODEL_NOT_CONFIGURED: "model_not_configured",
    }.get(code, "provider_selection_invalid")
    detail: dict[str, Any] = {"error": error, "code": code or "PROVIDER_SELECTION_INVALID"}
    catalog_id = getattr(exc, "catalog_id", None)
    if catalog_id:
        detail["catalog_id"] = catalog_id
    return HTTPException(status_code=status, detail=detail)


def validate_selection(
    provider: str | None = None,
    model: str | None = None,
    *,
    assistant: str | None = None,
) -> dict[str, Any] | None:
    """Validate an optional override for a chat/run request.

    ``None``/``None`` returns ``None`` (the caller keeps its default
    resolution path — no extra DB read, no behavior change).  Otherwise returns
    the validated ``{"row", "model", "catalog_id"}`` or raises an
    :class:`fastapi.HTTPException` carrying the stable provider-selection code.
    """
    if not provider and not model:
        return None
    try:
        return settings.resolve_selection(provider, model, assistant=assistant)
    except (settings.ProviderError, AdapterProviderError) as exc:
        # resolve_selection can raise either module's ProviderError: its own
        # PROVIDER_* codes, or the adapter's NO_ACTIVE_PROVIDER /
        # MODEL_NOT_CONFIGURED from pick_provider/resolve_model.  Both carry a
        # stable ``code`` — one catch keeps the 4xx mapping total (a bare
        # adapter.ProviderError used to escape as a 500).
        raise _http_for(exc) from exc


__all__ = ["router", "validate_selection", "public_providers"]
