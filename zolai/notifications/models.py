"""Notification system for email alerts and admin management.

Provides:
- SQLAlchemy models for Notification, NotificationPreference, NotificationTemplate (from zolai.data.models)
- Async email service via aiosmtplib with circuit breaker protection
- Jinja2 templates for various notification types
- Admin API endpoints for template/preference management and test sends
- Rate limiting per recipient and deduplication window
"""

from zolai.data.models import (
    NotificationPreferenceRecord as NotificationPreference,
)
from zolai.data.models import (
    NotificationRecord as Notification,
)
from zolai.data.models import (
    NotificationTemplateRecord as NotificationTemplate,
)

__all__ = [
    "Notification",
    "NotificationPreference",
    "NotificationTemplate",
]
