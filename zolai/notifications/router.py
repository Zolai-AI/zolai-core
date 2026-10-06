"""Admin notification endpoints for template/preference management and test sends."""

from __future__ import annotations

from typing import Any, Generator

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api import auth
from ..data.database import get_manager
from .models import Notification, NotificationPreference, NotificationTemplate
from .service import get_notification_service


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency providing a database session."""
    mgr = get_manager()
    session = mgr.get_session()
    try:
        yield session
    finally:
        session.close()


DB_SESSION = Depends(get_db_session)

router = APIRouter(prefix="/api/v1/admin/notifications", tags=["admin-notifications"])

ADMIN_DEP = Depends(auth.require_scope("settings:write", strict=True))
READ_DEP = Depends(auth.require_scope("settings:read", strict=True))


# --- Pydantic Schemas ---

class NotificationTemplateCreate(BaseModel):
    name: str
    subject_template: str
    body_text_template: str
    body_html_template: str | None = None
    event_type: str
    description: str | None = None
    is_active: bool = True


class NotificationTemplateUpdate(BaseModel):
    subject_template: str | None = None
    body_text_template: str | None = None
    body_html_template: str | None = None
    event_type: str | None = None
    description: str | None = None
    is_active: bool | None = None


class NotificationTemplateOut(BaseModel):
    id: int
    name: str
    subject_template: str
    body_text_template: str
    body_html_template: str | None
    event_type: str
    description: str | None
    is_active: bool
    created_at: str
    updated_at: str

    model_config = ConfigDict(
        from_attributes=True)


class NotificationTemplateList(BaseModel):
    items: list[NotificationTemplateOut]
    count: int


class NotificationPreferenceCreate(BaseModel):
    user_id: str
    event_type: str
    enabled: bool = True
    email_enabled: bool = True


class NotificationPreferenceUpdate(BaseModel):
    enabled: bool | None = None
    email_enabled: bool | None = None


class NotificationPreferenceOut(BaseModel):
    id: int
    user_id: str
    event_type: str
    enabled: bool
    email_enabled: bool
    created_at: str
    updated_at: str

    model_config = ConfigDict(
        from_attributes=True)


class TestSendRequest(BaseModel):
    template_name: str
    recipient: EmailStr
    context: dict[str, Any] | None = None


class TestSendResponse(BaseModel):
    recipient: str
    success: bool
    error: str | None = None


class AdminAlertRequest(BaseModel):
    template_name: str
    context: dict[str, Any] | None = None
    recipients: list[EmailStr] | None = None  # Defaults to configured admin emails


class AdminAlertResponse(BaseModel):
    results: list[TestSendResponse]


# --- Template Endpoints ---

@router.get("/templates", response_model=NotificationTemplateList, dependencies=[READ_DEP])
def list_templates(db: Session = DB_SESSION) -> NotificationTemplateList:
    """List all notification templates."""
    templates = db.execute(
        select(NotificationTemplate).order_by(NotificationTemplate.name)
    ).scalars().all()
    return NotificationTemplateList(
        items=[NotificationTemplateOut.model_validate(t) for t in templates],
        count=len(templates),
    )


@router.get("/templates/{name}", response_model=NotificationTemplateOut, dependencies=[READ_DEP])
def get_template(name: str, db: Session = DB_SESSION) -> NotificationTemplateOut:
    """Get a notification template by name."""
    template = db.execute(
        select(NotificationTemplate).where(NotificationTemplate.name == name)
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail={"error": "template_not_found", "name": name})
    return NotificationTemplateOut.model_validate(template)


@router.post(
    "/templates",
    response_model=NotificationTemplateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[ADMIN_DEP],
)
def create_template(
    body: NotificationTemplateCreate, db: Session = DB_SESSION
) -> NotificationTemplateOut:
    """Create a new notification template."""
    existing = db.execute(
        select(NotificationTemplate).where(NotificationTemplate.name == body.name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "template_exists", "name": body.name})

    template = NotificationTemplate(
        name=body.name,
        subject_template=body.subject_template,
        body_text_template=body.body_text_template,
        body_html_template=body.body_html_template,
        event_type=body.event_type,
        description=body.description,
        is_active=body.is_active,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return NotificationTemplateOut.model_validate(template)


@router.put(
    "/templates/{name}",
    response_model=NotificationTemplateOut,
    dependencies=[ADMIN_DEP],
)
def update_template(
    name: str, body: NotificationTemplateUpdate, db: Session = DB_SESSION
) -> NotificationTemplateOut:
    """Update a notification template."""
    template = db.execute(
        select(NotificationTemplate).where(NotificationTemplate.name == name)
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail={"error": "template_not_found", "name": name})

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(template, field, value)

    db.commit()
    db.refresh(template)
    return NotificationTemplateOut.model_validate(template)


@router.delete("/templates/{name}", dependencies=[ADMIN_DEP])
def delete_template(name: str, db: Session = DB_SESSION) -> dict[str, Any]:
    """Delete a notification template."""
    template = db.execute(
        select(NotificationTemplate).where(NotificationTemplate.name == name)
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail={"error": "template_not_found", "name": name})

    db.delete(template)
    db.commit()
    return {"name": name, "deleted": True}


# --- Preference Endpoints ---

@router.get(
    "/preferences",
    response_model=list[NotificationPreferenceOut],
    dependencies=[READ_DEP],
)
def list_preferences(
    user_id: str | None = None,
    event_type: str | None = None,
    db: Session = DB_SESSION,
) -> list[NotificationPreferenceOut]:
    """List notification preferences with optional filters."""
    query = select(NotificationPreference)
    if user_id:
        query = query.where(NotificationPreference.user_id == user_id)
    if event_type:
        query = query.where(NotificationPreference.event_type == event_type)
    prefs = db.execute(
        query.order_by(NotificationPreference.user_id, NotificationPreference.event_type)
    ).scalars().all()
    return [NotificationPreferenceOut.model_validate(p) for p in prefs]


@router.get(
    "/preferences/{user_id}/{event_type}",
    response_model=NotificationPreferenceOut,
    dependencies=[READ_DEP],
)
def get_preference(
    user_id: str, event_type: str, db: Session = DB_SESSION
) -> NotificationPreferenceOut:
    """Get a specific notification preference."""
    pref = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.event_type == event_type,
        )
    ).scalar_one_or_none()
    if not pref:
        raise HTTPException(
            status_code=404,
            detail={"error": "preference_not_found", "user_id": user_id, "event_type": event_type},
        )
    return NotificationPreferenceOut.model_validate(pref)


@router.post(
    "/preferences",
    response_model=NotificationPreferenceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[ADMIN_DEP],
)
def create_preference(
    body: NotificationPreferenceCreate, db: Session = DB_SESSION
) -> NotificationPreferenceOut:
    """Create or update a notification preference."""
    existing = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == body.user_id,
            NotificationPreference.event_type == body.event_type,
        )
    ).scalar_one_or_none()

    if existing:
        existing.enabled = body.enabled
        existing.email_enabled = body.email_enabled
        db.commit()
        db.refresh(existing)
        return NotificationPreferenceOut.model_validate(existing)

    pref = NotificationPreference(
        user_id=body.user_id,
        event_type=body.event_type,
        enabled=body.enabled,
        email_enabled=body.email_enabled,
    )
    db.add(pref)
    db.commit()
    db.refresh(pref)
    return NotificationPreferenceOut.model_validate(pref)


@router.put(
    "/preferences/{user_id}/{event_type}",
    response_model=NotificationPreferenceOut,
    dependencies=[ADMIN_DEP],
)
def update_preference(
    user_id: str,
    event_type: str,
    body: NotificationPreferenceUpdate,
    db: Session = DB_SESSION,
) -> NotificationPreferenceOut:
    """Update a notification preference."""
    pref = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.event_type == event_type,
        )
    ).scalar_one_or_none()
    if not pref:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "preference_not_found",
                "user_id": user_id,
                "event_type": event_type,
            },
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pref, field, value)

    db.commit()
    db.refresh(pref)
    return NotificationPreferenceOut.model_validate(pref)


@router.delete("/preferences/{user_id}/{event_type}", dependencies=[ADMIN_DEP])
def delete_preference(
    user_id: str, event_type: str, db: Session = DB_SESSION
) -> dict[str, Any]:
    """Delete a notification preference."""
    pref = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.event_type == event_type,
        )
    ).scalar_one_or_none()
    if not pref:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "preference_not_found",
                "user_id": user_id,
                "event_type": event_type,
            },
        )

    db.delete(pref)
    db.commit()
    return {"user_id": user_id, "event_type": event_type, "deleted": True}


# --- Test Send Endpoints ---

@router.post("/test-send", response_model=TestSendResponse, dependencies=[ADMIN_DEP])
async def test_send_notification(body: TestSendRequest) -> TestSendResponse:
    """Send a test notification to a specific recipient."""
    service = get_notification_service()
    context = body.context or {
        "timestamp": "2026-01-01T00:00:00Z",
        "test": True,
        "template_name": body.template_name,
        "app_name": "Zolai AI",
        "environment": "test",
    }
    success, error = await service.send_notification(
        body.template_name, body.recipient, context, dedup=False
    )
    return TestSendResponse(recipient=body.recipient, success=success, error=error)


@router.post("/admin-alert", response_model=AdminAlertResponse, dependencies=[ADMIN_DEP])
async def send_admin_alert(body: AdminAlertRequest) -> AdminAlertResponse:
    """Send an alert to all configured admin emails (or custom recipients)."""
    service = get_notification_service()
    context = body.context or {
        "timestamp": "2026-01-01T00:00:00Z",
        "test": True,
        "template_name": body.template_name,
        "app_name": "Zolai AI",
        "environment": "production",
    }
    results = await service.send_admin_alert(body.template_name, context, dedup=False)
    return AdminAlertResponse(
        results=[
            TestSendResponse(recipient=r, success=s, error=e) for r, s, e in results
        ]
    )


# --- History Endpoint ---

class NotificationHistoryOut(BaseModel):
    items: list[dict[str, Any]]
    count: int


@router.get("/history", response_model=NotificationHistoryOut, dependencies=[READ_DEP])
def get_notification_history(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    recipient: str | None = None,
    db: Session = DB_SESSION,
) -> NotificationHistoryOut:
    """Get notification delivery history."""
    query = select(Notification).order_by(Notification.created_at.desc())
    if status:
        query = query.where(Notification.status == status)
    if recipient:
        query = query.where(Notification.recipient == recipient)

    total = db.execute(select(Notification).with_only_columns(Notification.id)).scalars().all()
    total_count = len(total)

    notifications = db.execute(query.limit(limit).offset(offset)).scalars().all()

    return NotificationHistoryOut(
        items=[
            {
                "id": n.id,
                "template_name": n.template_name,
                "recipient": n.recipient,
                "subject": n.subject,
                "status": n.status,
                "error_message": n.error_message,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        count=total_count,
    )
