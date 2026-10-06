"""Notification system package for zolai-core."""

from .models import Notification, NotificationPreference, NotificationTemplate
from .service import (
    EmailConfig,
    NotificationEvent,
    NotificationService,
    TemplateRenderer,
    get_notification_service,
    reset_notification_service,
)

__all__ = [
    "Notification",
    "NotificationPreference",
    "NotificationTemplate",
    "EmailConfig",
    "NotificationEvent",
    "NotificationService",
    "TemplateRenderer",
    "get_notification_service",
    "reset_notification_service",
]
