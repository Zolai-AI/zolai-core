"""Notification service for sending emails via SMTP with circuit breaker protection."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from zolai.resilience import get_circuit_breaker

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """SMTP email configuration."""

    host: str = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port: int = int(os.environ.get("SMTP_PORT", "587"))
    user: str = os.environ.get("SMTP_USER", "")
    password: str = os.environ.get("SMTP_PASS", "")
    from_email: str = os.environ.get("SMTP_FROM", "pcore.system@gmail.com")
    use_tls: bool = os.environ.get("SMTP_TLS", "true").lower() == "true"
    admin_emails: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.admin_emails is None:
            admin_raw = os.environ.get("ADMIN_EMAILS", "")
            self.admin_emails = [e.strip() for e in admin_raw.split(",") if e.strip()]


@dataclass
class NotificationEvent:
    """Structured notification event."""

    template_name: str
    recipient: str
    subject: str
    body_text: str
    body_html: str | None = None
    metadata: dict[str, Any] = None  # type: ignore


class TemplateRenderer:
    """Renders Jinja2 templates for notifications."""

    def __init__(self, template_dir: str | None = None):
        if template_dir is None:
            template_dir = os.path.join(
                os.path.dirname(__file__), "templates"
            )
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Ensure template directory exists
        os.makedirs(template_dir, exist_ok=True)

    def render(
        self, template_name: str, context: dict[str, Any]
    ) -> tuple[str, str | None]:
        """Render subject and body from template.

        Returns (body_text, body_html).
        """
        # Render subject
        subject_template = self.env.get_template(f"{template_name}_subject.txt")
        subject = subject_template.render(**context).strip()

        # Render text body
        text_template = self.env.get_template(f"{template_name}.txt")
        body_text = text_template.render(**context)

        # Render HTML body if exists
        body_html = None
        html_path = os.path.join(
            self.env.loader.searchpath[0], f"{template_name}.html"
        )
        if os.path.exists(html_path):
            html_template = self.env.get_template(f"{template_name}.html")
            body_html = html_template.render(**context)

        return subject, body_text, body_html


class NotificationService:
    """Async email notification service with circuit breaker and rate limiting."""

    def __init__(
        self,
        config: EmailConfig | None = None,
        template_renderer: TemplateRenderer | None = None,
    ):
        self.config = config or EmailConfig()
        self.renderer = template_renderer or TemplateRenderer()
        self._rate_limit_lock = threading.Lock()
        self._rate_limits: dict[str, tuple[float, int]] = {}  # recipient -> (window_start, count)
        self._dedup_lock = threading.Lock()
        self._dedup_cache: dict[str, float] = {}  # key -> timestamp
        self._enabled = os.environ.get("ZOLAI_NOTIFICATIONS_ENABLED", "true").lower() != "false"

    def _check_rate_limit(self, recipient: str) -> bool:
        """Check if recipient is within rate limit (10 per minute)."""
        now = time.time()
        with self._rate_limit_lock:
            window_start, count = self._rate_limits.get(recipient, (now, 0))
            if now - window_start > 60:
                window_start = now
                count = 0
            if count >= 10:
                return False
            self._rate_limits[recipient] = (window_start, count + 1)
            return True

    def _check_dedup(self, key: str, window_seconds: int = 300) -> bool:
        """Check deduplication window (default 5 minutes)."""
        now = time.time()
        with self._dedup_lock:
            last_sent = self._dedup_cache.get(key)
            if last_sent and now - last_sent < window_seconds:
                return False
            self._dedup_cache[key] = now
            # Clean old entries
            self._dedup_cache = {
                k: v for k, v in self._dedup_cache.items() if now - v < window_seconds * 2
            }
            return True

    def _make_dedup_key(self, template_name: str, recipient: str, subject: str) -> str:
        """Create deduplication key."""
        import hashlib
        content = f"{template_name}:{recipient}:{subject}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    async def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> tuple[bool, str | None]:
        """Send a single email via SMTP with circuit breaker.

        Returns (success, error_message).
        """
        if not self._enabled:
            return False, "Notifications disabled via ZOLAI_NOTIFICATIONS_ENABLED=false"

        if not self.config.user or not self.config.password:
            return False, "SMTP credentials not configured"

        cb = get_circuit_breaker("smtp_email")

        try:
            return await cb.acall(self._send_email_impl, to, subject, body_text, body_html)
        except Exception as exc:
            logger.error("Circuit breaker rejected email to %s: %s", to, exc)
            return False, f"Circuit breaker open: {exc}"

    async def _send_email_impl(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> tuple[bool, str | None]:
        """Internal email sending implementation."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.config.from_email
        msg["To"] = to

        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                password=self.config.password,
                use_tls=self.config.use_tls,
                timeout=30,
            )
            logger.info("Email sent successfully to %s", to)
            return True, None
        except Exception as exc:
            logger.exception("Failed to send email to %s", to)
            return False, str(exc)

    async def send_notification(
        self,
        template_name: str,
        recipient: str,
        context: dict[str, Any],
        dedup: bool = True,
    ) -> tuple[bool, str | None]:
        """Send a notification using a template.

        Args:
            template_name: Name of the template (without extension)
            recipient: Email address of recipient
            context: Template rendering context
            dedup: Whether to apply deduplication

        Returns:
            (success, error_message)
        """
        if not self._enabled:
            return False, "Notifications disabled"

        # Check rate limit
        if not self._check_rate_limit(recipient):
            return False, f"Rate limit exceeded for {recipient}"

        # Render template
        try:
            subject, body_text, body_html = self.renderer.render(template_name, context)
        except Exception as exc:
            logger.exception("Failed to render template %s", template_name)
            return False, f"Template render error: {exc}"

        # Check deduplication
        if dedup:
            dedup_key = self._make_dedup_key(template_name, recipient, subject)
            if not self._check_dedup(dedup_key):
                return False, "Deduplication: similar notification sent recently"

        # Send email
        return await self.send_email(recipient, subject, body_text, body_html)

    async def send_admin_alert(
        self,
        template_name: str,
        context: dict[str, Any],
        dedup: bool = True,
    ) -> list[tuple[str, bool, str | None]]:
        """Send notification to all admin emails.

        Returns list of (recipient, success, error_message).
        """
        results = []
        for admin_email in self.config.admin_emails:
            success, error = await self.send_notification(
                template_name, admin_email, context, dedup=dedup
            )
            results.append((admin_email, success, error))
        return results

    async def send_test_notification(
        self, template_name: str, recipient: str
    ) -> tuple[bool, str | None]:
        """Send a test notification with sample context."""
        test_context = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "test": True,
            "template_name": template_name,
            "app_name": "Zolai AI",
            "environment": os.environ.get("ENVIRONMENT", "development"),
        }
        return await self.send_notification(template_name, recipient, test_context, dedup=False)


# Global service instance
_notification_service: NotificationService | None = None
_service_lock = threading.Lock()


def get_notification_service() -> NotificationService:
    """Get or create the global notification service."""
    global _notification_service
    with _service_lock:
        if _notification_service is None:
            _notification_service = NotificationService()
        return _notification_service


def reset_notification_service() -> None:
    """Reset the global notification service (for testing)."""
    global _notification_service
    with _service_lock:
        _notification_service = None
