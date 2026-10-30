"""Admin API-key endpoints — issue / list / rotate / revoke.

Mounted under ``/api/v1/admin/api-keys``.  Every route requires a
**presented, valid** API key holding the frozen scope ``apikey:manage``:
the router-level :func:`zolai.api.auth.require_scope` runs in ``strict`` mode,
so an absent or invalid key is **401** in ``warn`` *and* ``enforce`` — only
``ZOLAI_API_AUTH=off`` bypasses (CLI ``zolai apikey create`` is the bootstrap
path for the first key).  The plaintext secret is returned **once** on
create/rotate and never again — the DB stores only the SHA-256 hash and a
display prefix.

Failures answer the house envelope from :mod:`zolai.api.errors`: a stable
``error`` code plus a message written for the caller.  Every handler used to
pass ``str(exc)`` as the *code* itself, so the driver's own words sat in the
machine-readable slot; the raw text now goes to the log with a traceback.

**Minting is capped at the caller's own rank** (403 ``scope_above_actor``).
``apikey:manage`` authorises creating keys, not creating *any* key: an admin
that could mint ``*`` would mint itself a superadmin on a second credential, so
``create`` and ``rotate`` compare :func:`zolai.api.rbac.role_for` of the scopes
about to be issued against the caller's resolved role.  Rotation replays the old
key's scopes and is therefore re-checked too — rotating a legacy ``*`` key does
not hand ``*`` back to an admin.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import auth, rbac
from .errors import api_error, internal_http_error

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/api-keys",
    tags=["admin"],
    # strict=True: warn mode must NOT dual-accept absent keys on minting routes.
    # require_role is the second half: `apikey:manage` is a *scope*, and a scope
    # on its own says nothing about the rank of the credential holding it.
    dependencies=[
        Depends(auth.require_scope("apikey:manage", strict=True)),
        Depends(rbac.require_role("admin", strict=True)),
    ],
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


def _actor_record(request: Request) -> dict[str, Any]:
    """The resolved credential (session | OAuth | key), or an empty one."""
    record = getattr(request.state, "api_key", None)
    return record if isinstance(record, dict) else {}


def _actor_label(request: Request) -> str:
    """Who minted this, for the audit row: the credential the server resolved —
    never the ``created_by`` string the caller typed at itself."""
    record = _actor_record(request)
    return str(record.get("username") or record.get("name") or "admin-api")[:64]


def _refuse_wider_than_actor(request: Request, scopes: list[str]) -> None:
    """Minting cap: a key may never hold power above the one that issues it.

    ``apikey:manage`` says you can create keys; it does not say you can create
    *anything*.  Without this, any admin key mints ``*`` — which is
    :data:`zolai.api.rbac.SUPERADMIN_SCOPES` — and hands itself superadmin on a
    second credential, which is exactly the rank it was outranked by.
    """
    requested_role = rbac.role_for({"scopes": list(scopes)})
    actor_role = rbac.role_for(_actor_record(request))
    if rbac.rank_of({"role": requested_role}) <= rbac.rank_of({"role": actor_role}):
        return
    raise HTTPException(
        status_code=403,
        detail=api_error(
            "scope_above_actor",
            (
                f"That key would hold {requested_role} power and this credential is "
                f"{actor_role}, so it may only issue keys at or below its own rank. A "
                "superadmin has to issue it, or drop the scopes that rank above you."
            ),
            requested_role=requested_role,
            actor_role=actor_role,
        ),
    )


@router.get("")
def list_api_keys() -> dict[str, Any]:
    """List key metadata — hashes/prefixes only, never plaintext."""
    items = auth.list_api_keys()
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
def create_api_key(body: ApiKeyCreateIn, request: Request) -> dict[str, Any]:
    """Issue a key. The response contains the plaintext secret **once**."""
    _refuse_wider_than_actor(request, body.scopes)
    try:
        key = auth.create_api_key(
            name=body.name,
            scopes=body.scopes,
            created_by=body.created_by,
            expires_days=body.expires_days,
            actor=_actor_label(request),
        )
    except ValueError as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=409,
            operation="admin.api_keys.create",
            code="api_key_conflict",
            message=(
                "That key could not be issued. Check the name is unique and every scope is in "
                "the frozen vocabulary (GET /api/v1/auth/scopes lists them), then retry."
            ),
            log_context={"name": body.name},
        ) from exc
    return {"key": key, "plaintext": key["plaintext"]}


@router.post("/{key_id}/rotate")
def rotate_api_key(key_id: int, request: Request) -> dict[str, Any]:
    """Issue a replacement key and revoke the old one (issue-then-revoke).

    Rotation replays the old key's scopes, so it is re-checked against the
    **caller**, not the issuer: an admin rotating a legacy ``*`` key does not
    get ``*`` back.  Refusing rather than quietly narrowing keeps the answer
    honest — the replacement would not behave like the key it replaced.
    """
    old = auth.get_api_key(key_id)
    if old is not None:
        _refuse_wider_than_actor(request, list(old.get("scopes") or []))
    try:
        rotated = auth.rotate_api_key(key_id, actor=_actor_label(request))
    except LookupError as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=404,
            operation="admin.api_keys.rotate",
            code="api_key_not_found",
            message="No API key with that id exists. List the keys, then retry with a live id.",
            log_context={"key_id": key_id},
        ) from exc
    except ValueError as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=409,
            operation="admin.api_keys.rotate",
            code="api_key_conflict",
            message=(
                "That key could not be rotated: it is already revoked. Issue a new key instead, "
                "or pick a key that is still active."
            ),
            log_context={"key_id": key_id},
        ) from exc
    return {"key": rotated, "plaintext": rotated["plaintext"], "old_id": rotated["old_id"]}


@router.post("/{key_id}/revoke")
def revoke_api_key(key_id: int, request: Request) -> dict[str, Any]:
    """Revoke a key — it is rejected (401) from the very next request."""
    try:
        revoked = auth.revoke_api_key(key_id, actor=_actor_label(request))
    except LookupError as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=404,
            operation="admin.api_keys.revoke",
            code="api_key_not_found",
            message="No API key with that id exists. List the keys, then retry with a live id.",
            log_context={"key_id": key_id},
        ) from exc
    except ValueError as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=409,
            operation="admin.api_keys.revoke",
            code="api_key_conflict",
            message=(
                "That key could not be revoked: it is already revoked. Nothing changed, so the "
                "caller is already rejected."
            ),
            log_context={"key_id": key_id},
        ) from exc
    return {"id": revoked["id"], "revoked_at": revoked["revoked_at"]}
