"""Read-only TTS voice/generation registry API (member-gated).

Routes (all GET — this surface never writes):

- ``GET /api/v1/tts/voices`` — active voices in the registry.
- ``GET /api/v1/tts/voices/{voice_key}`` — one voice; **404** for unknown keys.
- ``GET /api/v1/tts/generations`` — generation log rows, optional ``batch_id``.

Contract:

- behind ``rbac.require_role(ROLE_MEMBER)`` (401 anonymous in warn *and*
  enforce; only ``ZOLAI_API_AUTH=off`` bypasses);
- an unreachable database is **503**, never a 500 or a silent empty list;
- responses are plain JSON scalars only — no internal enum objects and no
  key material;
- ``publishable`` is ``legal_status == 'verified'``: everything else
  (including the ``unverified`` default for free-tier terms) reports
  ``false``.  There is **no** publish/write route here on purpose — the gate
  lives in the zolai-tts manifest flow, and provider-owned default voices are
  never claimed as Zolai-owned audio.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from . import rbac

router = APIRouter(
    prefix="/api/v1/tts",
    tags=["tts"],
    # A credential must be present (strict: 401 anonymous in warn *and*
    # enforce), and it must belong to at least a member.  Read-only surface.
    dependencies=[Depends(rbac.require_role(rbac.ROLE_MEMBER, strict=True))],
)

#: The only ``legal_status`` that reports ``publishable: true``.
PUBLISHABLE_LEGAL_STATUS = "verified"

_VOICES_COLUMNS = (
    "id, voice_key, provider, provider_voice_id, style_default, "
    "is_active, legal_status, legal_notes"
)


def _get_manager() -> Any:
    """Resolve the DatabaseManager singleton (late import avoids cycles).

    Monkeypatch seam for tests — same pattern as ``zolai.api.auth``.
    """
    from ..data.database import get_manager

    return get_manager()


def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read-only query; DB failure ⇒ 503 (never a 500, never a guess)."""
    try:
        manager = _get_manager()
        with manager.engine.connect() as conn:
            rows = conn.execute(text(sql), params or {}).fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "reason": "database_unreachable",
                "message": f"TTS registry store unavailable: {type(exc).__name__}",
            },
        ) from exc
    return [dict(row._mapping) for row in rows]


def _publishable(legal_status: Any) -> bool:
    """Publish gate mirrored for read-only display: verified or nothing."""
    return str(legal_status or "").strip().lower() == PUBLISHABLE_LEGAL_STATUS


def _voice_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Plain-scalar view of a voice row (no internal enums, no secrets)."""
    return {
        "voice_key": str(row.get("voice_key") or ""),
        "provider": str(row.get("provider") or ""),
        "provider_voice_id": str(row.get("provider_voice_id") or ""),
        "style_default": row.get("style_default"),
        "is_active": bool(row.get("is_active", 1)),
        "legal_status": str(row.get("legal_status") or "unverified"),
        "legal_notes": row.get("legal_notes"),
        "publishable": _publishable(row.get("legal_status")),
    }


@router.get("/voices", summary="Active TTS voices in the registry")
def list_voices(request: Request) -> dict[str, Any]:
    """Every active voice, plain JSON scalars only."""
    rows = _query(
        f"SELECT {_VOICES_COLUMNS} FROM tts_voices WHERE is_active = 1 ORDER BY voice_key"
    )
    return {"count": len(rows), "voices": [_voice_payload(row) for row in rows]}


@router.get(
    "/voices/{voice_key}",
    summary="One registry voice by voice_key (404 if unknown)",
)
def get_voice(voice_key: str) -> dict[str, Any]:
    """Authoritative lookup: unknown key ⇒ **404** (no default substitution)."""
    rows = _query(
        f"SELECT {_VOICES_COLUMNS} FROM tts_voices WHERE voice_key = :voice_key",
        {"voice_key": voice_key},
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "resource": "tts_voice",
                "voice_key": voice_key,
            },
        )
    return {"voice": _voice_payload(rows[0])}


@router.get("/generations", summary="Generation log (optionally by batch_id)")
def list_generations(
    batch_id: str | None = Query(None, description="Filter by batch id"),
    limit: int = Query(50, ge=1, le=500, description="Page size (newest first)"),
) -> dict[str, Any]:
    """Read-only slice of ``tts_generations`` (newest first)."""
    if batch_id:
        rows = _query(
            "SELECT id, voice_id, batch_id, input_sha256, source_ref, model, "
            "api_version, style, manifest_path, created_at FROM tts_generations "
            "WHERE batch_id = :batch_id ORDER BY id DESC LIMIT :limit",
            {"batch_id": batch_id, "limit": limit},
        )
    else:
        rows = _query(
            "SELECT id, voice_id, batch_id, input_sha256, source_ref, model, "
            "api_version, style, manifest_path, created_at FROM tts_generations "
            "ORDER BY id DESC LIMIT :limit",
            {"limit": limit},
        )
    # Plain scalars only: drop nothing, wrap nothing in enums.
    items = [
        {key: (str(value) if value is not None and key == "manifest_path" else value)
         for key, value in row.items()}
        for row in rows
    ]
    return {"count": len(items), "generations": items}
