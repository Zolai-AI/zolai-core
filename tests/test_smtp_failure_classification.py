"""SMTP failures must say something an operator can act on.

A bare ``logger.exception`` printed a 12-line traceback per attempt and said
nothing useful, so a *permanent* failure — every send failing for days — looked
like transient noise in the logs. These tests pin the classification.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.notifications.service import classify_smtp_failure

aiosmtplib = pytest.importorskip("aiosmtplib")


class TestClassifySmtpFailure:
    def test_server_disconnect_does_not_blame_credentials(self):
        """The trap: this looks like a password problem and is not.

        Gmail completes TCP, TLS and EHLO, then drops the connection at AUTH —
        identically for a valid user, a wrong password and an account that does
        not exist. Telling an operator to rotate credentials sends them the
        wrong way.
        """
        exc = aiosmtplib.errors.SMTPServerDisconnected("Unexpected EOF received")
        advice = classify_smtp_failure(exc)
        assert "NOT the cause" in advice
        assert "different host" in advice
        assert "relay" in advice.lower()

    def test_auth_rejection_points_at_the_app_password(self):
        exc = aiosmtplib.SMTPAuthenticationError(535, "5.7.8 Username and Password not accepted")
        advice = classify_smtp_failure(exc)
        assert "App Password" in advice

    def test_connect_failure_points_at_host_port_or_firewall(self):
        exc = aiosmtplib.SMTPConnectError("Error connecting to smtp.gmail.com on port 587")
        advice = classify_smtp_failure(exc)
        assert "587" in advice and "465" in advice
        assert "firewall" in advice.lower()

    def test_unclassified_still_names_the_exception_type(self):
        advice = classify_smtp_failure(ValueError("boom"))
        assert "ValueError" in advice
        assert "unclassified" in advice.lower()

    def test_never_returns_empty(self):
        for exc in (
            aiosmtplib.errors.SMTPServerDisconnected("Unexpected EOF received"),
            ValueError(""),
            RuntimeError(),
        ):
            assert classify_smtp_failure(exc).strip()


class TestEmailConfigStillResolvesEnv:
    """Regression guard for a mistake this change nearly shipped.

    The classifier was inserted between ``@dataclass`` and ``class
    EmailConfig``, silently removing the decorator. ``__post_init__`` then never
    ran, so SMTP credentials resolved to empty and every send failed with
    "SMTP credentials not configured" instead of reaching AUTH — a config bug
    wearing the same clothes as the network bug it was meant to diagnose.
    """

    def test_post_init_populates_credentials_from_env(self, monkeypatch):
        import importlib

        import zolai.notifications.service as svc_mod

        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASS", "hunter2")
        importlib.reload(svc_mod)

        config = svc_mod.EmailConfig()
        assert config.user == "user@example.com", (
            "__post_init__ did not run - EmailConfig lost its @dataclass"
        )
        assert config.password == "hunter2"

    def test_explicit_arguments_still_win(self, monkeypatch):
        import importlib

        import zolai.notifications.service as svc_mod

        monkeypatch.setenv("SMTP_USER", "env@example.com")
        importlib.reload(svc_mod)

        config = svc_mod.EmailConfig(user="explicit@example.com")
        assert config.user == "explicit@example.com"
