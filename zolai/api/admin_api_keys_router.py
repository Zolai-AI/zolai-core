"""Admin API-key endpoints — issue / list / rotate / revoke.

Mounted under ``/api/v1/admin/api-keys``.  Every route requires a
**presented, valid** API key holding the frozen scope ``apikey:manage``:
the router-level :func:`zolai.api.auth.require_scope` runs in ``strict`` mode,
so an absent or invalid key is **401** in ``warn`` *and* ``enforce`` — only
``ZOLAI_API_AUTH=off`` bypasses (CLI ``zolai apikey create`` is the bootstrap
path for the first key).  The plaintext secret is returned **once** on
create/rotate and never again — the DB stores only the SHA-256 hash and a
display prefix.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import auth

router = APIRouter(
    prefix="/api/v1/admin/api-keys",
    tags=["admin"],
    # strict=True: warn mode must NOT dual-accept absent keys on minting routes.
    dependencies=[Depends(auth.require_scope("apikey:manage", strict=True))],
)


class ApiKeyCreateIn(BaseModel):
    """POST /api/v1/admin/api-keys body."""

    name: str = Field(min_length=1, max_length=120, examples=["mcp-server"])
    scopes: list[str] = Field(min_length=1, examples=[["dataset:read", "catalog:read"]])
    expires_days: int | None = Field(default=None, ge=1, le=3650)
    created_by: str = Field(default="api", max_length=64)

    @field_validator("scopes")
    @classmethod
    def _scopes_in_vocabulary(cls, value: list[str]) -> list[str]:
        return auth.validate_scopes(value)


def _detail(message: str) -> dict[str, str]:
    return {"error": message}


@router.get("")
def list_api_keys() -> dict[str, Any]:
    """List key metadata — hashes/prefixes only, never plaintext."""
    items = auth.list_api_keys()
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
def create_api_key(body: ApiKeyCreateIn) -> dict[str, Any]:
    """Issue a key. The response contains the plaintext secret **once**."""
    try:
        key = auth.create_api_key(
            name=body.name,
            scopes=body.scopes,
            created_by=body.created_by,
            expires_days=body.expires_days,
            actor=body.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=_detail(str(exc))) from exc
    return {"key": key, "plaintext": key["plaintext"]}


@router.post("/{key_id}/rotate")
def rotate_api_key(key_id: int) -> dict[str, Any]:
    """Issue a replacement key and revoke the old one (issue-then-revoke)."""
    try:
        rotated = auth.rotate_api_key(key_id, actor="api")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_detail(str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=_detail(str(exc))) from exc
    return {"key": rotated, "plaintext": rotated["plaintext"], "old_id": rotated["old_id"]}


@router.post("/{key_id}/revoke")
def revoke_api_key(key_id: int) -> dict[str, Any]:
    """Revoke a key — it is rejected (401) from the very next request."""
    try:
        revoked = auth.revoke_api_key(key_id, actor="api")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_detail(str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=_detail(str(exc))) from exc
    return {"id": revoked["id"], "revoked_at": revoked["revoked_at"]}
