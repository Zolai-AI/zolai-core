"""Startup guard: ``ZOLAI_API_AUTH`` should be ``enforce`` in production (core#3).

``warn`` is the shipped default and is the right default for development — it
dual-accepts and logs unauthenticated ``/api/v1`` reads so nothing breaks
while consumer keys are being issued.  In production the same mode is a data
disclosure: data-bearing routes answer ``200`` with no credential.  All four
were measured open:

    /api/v1/predictions/health   -> table inventory + row counts
    /desktop/tables              -> table inventory + row counts
    /api/v1/audit                -> audit rows, including api_keys activity
    /api/v1/foundation/stats     -> table inventory + row counts

``_warn_if_auth_not_enforced()`` logs one warning from the lifespan so an
operator sees it in the journal.  It **never** refuses to start and never
changes behaviour: flipping the mode is an operational decision that must be
paired with issuing consumer keys first, and tests legitimately run in
``warn``/``off``.

These tests call the guard directly rather than booting the app, so no DB is
touched and no FastAPI instance is constructed.
"""

from __future__ import annotations

import logging

import pytest

from zolai.api import auth
from zolai.api.server import _is_production_environment, _warn_if_auth_not_enforced

#: The four endpoints whose anonymous ``200`` motivated the guard.
LEAKED_ENDPOINTS: tuple[str, ...] = (
    "/api/v1/predictions/health",
    "/desktop/tables",
    "/api/v1/audit",
    "/api/v1/foundation/stats",
)


@pytest.fixture()
def warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture WARNING records from the server logger for one call."""
    seen: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                seen.append(record.getMessage())

    from zolai.api import server

    handler = _Capture()
    server.logger.addHandler(handler)
    monkeypatch.setattr(server.logger, "level", logging.DEBUG, raising=False)
    try:
        yield seen  # type: ignore[misc]
    finally:
        server.logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# The mode resolver is the input to the guard
# ---------------------------------------------------------------------------


def test_api_auth_mode_defaults_to_warn() -> None:
    """The shipped default must stay ``warn`` — the guard warns, never breaks."""
    assert auth.api_auth_mode() == "warn"


@pytest.mark.parametrize(
    "raw,expected",
    [("enforce", "enforce"), ("WARN", "warn"), ("off", "off"), ("nonsense", "warn")],
)
def test_api_auth_mode_is_normalised(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", raw)
    assert auth.api_auth_mode() == expected


# ---------------------------------------------------------------------------
# Production detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env,expected",
    [
        (None, True),  # unset: a deployed uvicorn unit is the common case
        ("", True),
        ("production", True),
        ("Production", True),
        ("PRODUCTION", True),
        ("prod", True),
        ("staging", True),
        ("1.2.3.4", True),
        ("development", False),
        ("dev", False),
        ("test", False),
        ("ci", False),
    ],
)
def test_is_production_environment(
    monkeypatch: pytest.MonkeyPatch, env: str | None, expected: bool
) -> None:
    if env is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", env)
    assert _is_production_environment() is expected


# ---------------------------------------------------------------------------
# When the warning fires — and when it must not
# ---------------------------------------------------------------------------


def test_warn_mode_in_production_fires_a_warning(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str]
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    _warn_if_auth_not_enforced()
    assert len(warnings) == 1


def test_off_mode_in_production_fires_a_warning(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str]
) -> None:
    """``off`` is worse than ``warn`` in production, so it warns too."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ZOLAI_API_AUTH", "off")
    _warn_if_auth_not_enforced()
    assert len(warnings) == 1


def test_enforce_in_production_is_silent(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str]
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
    _warn_if_auth_not_enforced()
    assert warnings == []


@pytest.mark.parametrize("mode", ["warn", "off"])
@pytest.mark.parametrize(
    "env", ["development", "dev", "test", "ci"],
)
def test_non_production_never_warns(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str], mode: str, env: str
) -> None:
    """Tests run in ``warn``/``off`` — the guard must stay quiet for them."""
    monkeypatch.setenv("ENVIRONMENT", env)
    monkeypatch.setenv("ZOLAI_API_AUTH", mode)
    _warn_if_auth_not_enforced()
    assert warnings == []


def test_warning_names_the_mode_and_the_remedy(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str]
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    _warn_if_auth_not_enforced()
    message = warnings[0]
    assert "ZOLAI_API_AUTH" in message
    assert "'warn'" in message or '"warn"' in message
    assert "enforce" in message


def test_warning_names_every_measured_leak(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str]
) -> None:
    """An operator must be able to act on the warning without a second lookup."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    _warn_if_auth_not_enforced()
    message = warnings[0]
    for endpoint in LEAKED_ENDPOINTS:
        assert endpoint in message, endpoint


def test_warning_is_not_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard returns ``None`` and raises nothing, in every combination."""
    for env in (None, "production", "development"):
        for mode in ("warn", "enforce", "off"):
            if env is None:
                import os

                os.environ.pop("ENVIRONMENT", None)
            else:
                monkeypatch.setenv("ENVIRONMENT", env)
            monkeypatch.setenv("ZOLAI_API_AUTH", mode)
            assert _warn_if_auth_not_enforced() is None


def test_guard_is_wired_into_the_lifespan() -> None:
    """Regression: the helper must actually be called from ``create_app``.

    Read from source rather than booting the app — booting would run
    migrations against the live DB, which this suite must never do.
    """
    import inspect

    from zolai.api import server

    source = inspect.getsource(server.create_app)
    assert "_warn_if_auth_not_enforced()" in source
