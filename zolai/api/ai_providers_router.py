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
from ..llm.catalog import BRAIN_CATALOG_ID  # type: ignore[import-not-found]
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

router = APIRouter(tags=["admin-ai"])

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


def _secret_view(row: dict[str, Any]) -> ProviderSecretOut:
    """Masked secret view that reflects what actually resolves.

    ``mask_secret_ref`` only inspects ``api_key_ref``, so a row that carries no
    ref but takes its key from the environment — the Brain, via
    ``AI_BRAIN_API_KEY`` and the catalog's ``env_keys`` — was reported as
    ``configured: false`` while working perfectly. The Studio renders that flag
    next to the row, so an operator reads a broken provider where none exists.

    Only a boolean is added: the key itself is never read into the response.
    Rows that need no key keep reporting ``configured: false``, which is honest —
    there is nothing to configure.
    """
    view = dict(settings.mask_secret_ref(row.get("api_key_ref")))  # type: ignore[arg-type]
    if not view.get("configured") and bool(row.get("requires_key", 1)):
        try:
            from ..llm import adapter as adapter_mod

            view["configured"] = bool(adapter_mod.resolve_api_key(row))
        except Exception:  # pragma: no cover - defensive: never fail a read
            pass
    return ProviderSecretOut(**view)  # type: ignore[arg-type]


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
        secret=_secret_view(row),
    )


def _require_row(catalog_id: str) -> dict[str, Any]:
    row = settings.get_provider_row(catalog_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        )
    return row


#: Adapters whose remote listing is worth attempting (OpenAI-compatible
#: ``GET {base}/models`` shape: ``{"data": [{"id": ...}]}``).
#:
#: ``brain`` **was** excluded here on the belief that it had no listing
#: endpoint. It does: the Brain answers ``GET {AI_BRAIN_URL}/models`` with the
#: OpenAI shape, and was serving nine model ids while the Studio's picker showed
#: four. Excluding it meant ``refresh-models`` on the Brain could never
#: discover anything and the seed list was the only thing ever shown.
#: ``custom`` stays excluded — a custom base URL has no guarantee of any listing
#: shape — and gemini stays catalog-only because Google lists a different shape
#: (see ``_CATALOG_ONLY_IDS``).
_REMOTE_FETCH_ADAPTERS = frozenset({"openai", "openrouter", "brain"})
_CATALOG_ONLY_IDS = frozenset({"google-gemini"})


def _models_endpoint(base_url: str) -> str:
    """``.../chat/completions`` (or ``.../v1``) → ``.../models``."""
    base = str(base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/models"


def _effective_base_url(row: dict[str, Any]) -> str:
    """The endpoint this row actually calls, for a blank ``base_url`` too.

    The Brain row is seeded with an **empty** ``base_url`` on purpose: its URL is
    resolved from config at request time (``AI_BRAIN_URL`` / ``PCORE_BRAIN_URL``,
    falling back to the public default). Reading the column directly therefore
    raised "provider has no base_url", and ``refresh-models`` on the Brain
    silently degraded to the catalog list every single time — which is why the
    model picker showed four ids while the Brain served nine.
    """
    base = str(row.get("base_url") or "").strip()
    if not base and str(row.get("catalog_id") or "").strip() == BRAIN_CATALOG_ID:
        from ..llm.catalog import brain_completions_url

        base = brain_completions_url()
    return base


def _fetch_remote_models(row: dict[str, Any], timeout_s: float = 15.0) -> list[str]:
    """GET the provider's ``/models`` endpoint with the row's key.

    Module-level so tests monkeypatch it (no socket in the default suite).
    Raises on any failure — the caller degrades to the catalog list (never a
    500, plan item 10).
    """
    import httpx

    from ..llm import adapter as adapter_mod

    url = _models_endpoint(_effective_base_url(row))
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


@router.get("/api/v1/admin/ai-providers", response_model=ProviderListOut, dependencies=[READ_DEP])
def list_ai_providers() -> ProviderListOut:
    """Catalog rows + masked secret + active flag (never a plaintext key)."""
    rows = settings.list_provider_rows()
    items = [_to_out(r) for r in rows]
    return ProviderListOut(items=items, count=len(items))


@router.put("/api/v1/admin/ai-providers/{catalog_id}", response_model=ProviderOut, dependencies=[WRITE_DEP])
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


@router.post("/api/v1/admin/ai-providers/{catalog_id}/activate", response_model=ActivateOut, dependencies=[WRITE_DEP])
def activate_ai_provider(catalog_id: str) -> ActivateOut:
    """Set the single global active row (every other row is cleared).

    Refuses a row that cannot authenticate. Activating one silently guarantees
    that every assistant and `/rag` request falls back to `retrieval_only`, with
    no error anywhere — which is how this deployment served evidence-only
    answers for days while the catalog showed an "active" row.

    The check happens **before** any write, so a refused activation leaves the
    currently active provider untouched. It asks whether a key actually resolves
    rather than trusting ``secret.configured``: the Brain is seeded with a blank
    ``api_key_ref`` and still works, because its key comes from ``AI_BRAIN_API_KEY``
    via the catalog's ``env_keys``. Rows that declare ``requires_key: false``
    (local endpoints such as Ollama) are exempt.
    """
    row = settings.get_provider_row(catalog_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"error": "provider_not_found", "catalog_id": catalog_id}
        )

    if bool(row.get("requires_key", 1)):
        from ..llm import adapter as adapter_mod

        try:
            resolvable = bool(adapter_mod.resolve_api_key(row))
        except Exception:  # pragma: no cover - defensive: never block activation
            resolvable = True
        if not resolvable:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "provider_not_configured",
                    "catalog_id": catalog_id,
                    "hint": (
                        "No API key resolves for this row, so activating it would make every "
                        "assistant and /rag request fall back to retrieval-only with no error. "
                        "Store a secret with PUT /admin/ai-providers/{id}, or set the env var "
                        "its api_key_ref points at, then activate."
                    ),
                },
            )

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


@router.post("/api/v1/admin/ai-providers/{catalog_id}/test", response_model=ProviderTestOut, dependencies=[WRITE_DEP])
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


@router.post("/api/v1/admin/ai-providers/{catalog_id}/refresh-models", response_model=RefreshModelsOut, dependencies=[WRITE_DEP])
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


@router.delete("/api/v1/admin/ai-providers/{catalog_id}", dependencies=[WRITE_DEP])
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
