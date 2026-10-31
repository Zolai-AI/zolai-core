"""Authenticated, quarantined user-text learning intake."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..data.database import get_manager
from ..foundation.user_submissions import UserTextSubmissionService
from . import rbac

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])


class UserTextSubmissionIn(BaseModel):
    """User-authored Zolai text, never an identity-bearing payload."""

    content: str = Field(..., min_length=1, max_length=100_000)
    content_type: str = Field(default="text", pattern="^(sentence|article|text)$")


class UserTextSubmissionOut(BaseModel):
    """Quarantine result without echoing submitted text."""

    id: int
    content_hash: str
    status: str
    validation_status: str
    quarantine_reason: str
    duplicate: bool = False


@router.post(
    "/submissions",
    response_model=UserTextSubmissionOut,
    dependencies=[Depends(rbac.require_role(rbac.ROLE_MEMBER, strict=True))],
)
def submit_user_text(payload: UserTextSubmissionIn, request: Request) -> dict[str, Any]:
    """Analyze a submission and retain it as a non-canonical candidate."""
    record = getattr(request.state, "api_key", None)
    identity = rbac.person_identity(record)
    user_id = identity[0] if identity else None
    submitted_by = identity[1] if identity else None
    auth_source = (
        str(record.get("auth_source") or "api_key")
        if isinstance(record, dict)
        else None
    )
    return UserTextSubmissionService(get_manager()).submit(
        payload.content,
        payload.content_type,
        user_id=user_id,
        submitted_by=submitted_by,
        auth_source=auth_source,
    )
