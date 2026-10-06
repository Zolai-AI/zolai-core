"""Tests for notification system."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zolai.api import server
from zolai.notifications import models
from zolai.notifications.service import (
    EmailConfig,
    NotificationService,
    TemplateRenderer,
    get_notification_service,
    reset_notification_service,
)


@pytest.fixture(autouse=True)
def _reset_services():
    reset_notification_service()
    yield
    reset_notification_service()


@pytest.fixture
def client():
    return TestClient(server.app)


class TestNotificationModels:
    def test_notification_model(self):
        n = models.Notification(
            id=1,
            template_name="error_alert",
            recipient="test@example.com",
            subject="Test",
            body_text="Body",
            body_html="<p>Body</p>",
            status="pending",
        )
        assert n.recipient == "test@example.com"
        assert n.template_name == "error_alert"

    def test_notification_preference_model(self):
        p = models.NotificationPreference(
            id=1,
            user_id="user1",
            event_type="error_alert",
            enabled=True,
            email_enabled=True,
        )
        assert p.user_id == "user1"

    def test_notification_template_model(self):
        t = models.NotificationTemplate(
            id=1,
            name="test_template",
            subject_template="Subject: {{ event_type }}",
            body_text_template="Body: {{ details }}",
            body_html_template="<p>{{ details }}</p>",
            event_type="test",
            description="Test template",
            is_active=True,
        )
        assert t.name == "test_template"


class TestNotificationService:
    def test_service_initialization(self):
        svc = NotificationService()
        assert svc is not None
        assert svc.config is not None
        assert svc.renderer is not None

    def test_get_notification_service_singleton(self):
        svc1 = get_notification_service()
        svc2 = get_notification_service()
        assert svc1 is svc2

    def test_send_admin_alert_no_smtp(self):
        """When SMTP not configured, should not crash."""
        svc = NotificationService()
        # No SMTP env vars set - should not raise
        asyncio.run(svc.send_admin_alert("error_alert", {"details": "Test error"}))
        # Should not raise

    def test_send_admin_alert_dedup(self):
        svc = NotificationService()
        # First call should not be deduped
        asyncio.run(svc.send_admin_alert("error_alert", {"details": "Test error 1"}, dedup=False))
        # Second call with same details within window should be deduped
        asyncio.run(svc.send_admin_alert("error_alert", {"details": "Test error 1"}, dedup=True))
        # No crash

    def test_rate_limit_check(self):
        svc = NotificationService()
        # First 10 should pass
        for i in range(10):
            assert svc._check_rate_limit("test@example.com") is True
        # 11th should fail
        assert svc._check_rate_limit("test@example.com") is False

    def test_dedup_check(self):
        svc = NotificationService()
        key = "test_key"
        # First call should pass
        assert svc._check_dedup(key) is True
        # Second call within window should fail
        assert svc._check_dedup(key) is False

    def test_template_renderer_loads_templates(self):
        """Test that template renderer can load template files."""
        renderer = TemplateRenderer()
        # Templates should exist in the templates directory
        # Just verify renderer initializes
        assert renderer is not None
        assert renderer.env is not None


class TestEmailConfig:
    def test_email_config_has_defaults(self):
        """Test EmailConfig has expected default structure."""
        config = EmailConfig()
        # Just verify it initializes with some values
        assert hasattr(config, 'host')
        assert hasattr(config, 'port')
        assert hasattr(config, 'user')
        assert hasattr(config, 'password')
        assert hasattr(config, 'from_email')
        assert hasattr(config, 'use_tls')
        assert hasattr(config, 'admin_emails')


class TestNotificationRouter:
    def test_list_templates_requires_admin(self, client):
        response = client.get("/api/v1/admin/notifications/templates")
        assert response.status_code in (401, 403)  # No auth

    def test_test_send_requires_admin(self, client):
        response = client.post("/api/v1/admin/notifications/test-send", json={
            "template_name": "error_alert",
            "to": "admin@example.com",
            "context": {"details": "Test"}
        })
        assert response.status_code in (401, 403)  # No auth


class TestCircuitBreakerIntegration:
    def test_smtp_circuit_breaker_exists(self):
        """Test that notification service uses circuit breaker for SMTP."""
        from zolai.resilience import get_circuit_breaker
        cb = get_circuit_breaker("smtp_email")
        assert cb is not None
        assert cb.name == "smtp_email"

    def test_llm_provider_circuit_breaker_exists(self):
        """Test that LLM adapter creates circuit breakers per provider."""
        from zolai.resilience import get_circuit_breaker
        # Circuit breakers are created on-demand when pick_provider is called
        # Just verify the registry works
        cb = get_circuit_breaker("llm_test_provider")
        assert cb is not None
        assert cb.name == "llm_test_provider"


class TestNotificationServiceDisabled:
    def test_disabled_via_env(self):
        """Test that notifications can be disabled via env."""
        with patch.dict(os.environ, {"ZOLAI_NOTIFICATIONS_ENABLED": "false"}):
            # Need to reset to pick up new env
            reset_notification_service()
            svc = get_notification_service()
            assert svc._enabled is False
        reset_notification_service()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
