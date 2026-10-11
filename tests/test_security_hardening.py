"""Security hardening HTTP-contract tests.

Three live-API defects (unit-level probes of ``create_app()``; no lifespan):

- **core#1 CORS** — the inline ``allow_origins=["*"]`` + ``allow_credentials``
  made Starlette reflect *any* request ``Origin`` (including ``null``).  The app
  now uses an explicit allowlist (:func:`zolai.api.security.get_cors_origins`).
- **core#16 health leak** — public ``/health`` and ``/api/v1/health`` no longer
  expose ``data_root`` (the filesystem path).  The detail shape lives behind the
  authenticated ``/api/v1/admin/health`` only.
- **core#17 headers** — :class:`zolai.api.security.SecurityHeadersMiddleware` is
  wired onto every response (headers only, no request filtering).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api.security import (
    DEFAULT_CORS_ORIGINS,
    SECURITY_HEADERS,
    cors_kwargs,
    get_cors_origins,
)
from zolai.api.server import HealthDetailResponse, HealthResponse, create_app

ALLOWED = "https://studio.zolai.space"
FOREIGN = "https://evil.example"


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _preflight(client: TestClient, origin: str) -> object:
    return client.options(
        "/api/v1/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


# ---------------------------------------------------------------------------
# core#1 — CORS never reflects an arbitrary origin with credentials
# ---------------------------------------------------------------------------


def test_default_origins_are_an_explicit_allowlist() -> None:
    assert ALLOWED in DEFAULT_CORS_ORIGINS
    assert "http://localhost:5173" in DEFAULT_CORS_ORIGINS
    assert "*" not in DEFAULT_CORS_ORIGINS


def test_cors_kwargs_never_contain_wildcards() -> None:
    kwargs = cors_kwargs()
    assert "*" not in kwargs["allow_origins"]
    assert kwargs["allow_credentials"] is True
    assert "*" not in kwargs["allow_methods"]
    assert "*" not in kwargs["allow_headers"]


def test_env_overrides_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZOLAI_CORS_ORIGINS", "https://a.test, https://b.test ,")
    assert get_cors_origins() == ["https://a.test", "https://b.test"]


def test_blank_env_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZOLAI_CORS_ORIGINS", "   ")
    assert get_cors_origins() == list(DEFAULT_CORS_ORIGINS)


def test_evil_origin_is_not_reflected(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": FOREIGN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_null_origin_is_not_reflected(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": "null"})
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_is_echoed_with_credentials(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": ALLOWED})
    assert response.headers.get("access-control-allow-origin") == ALLOWED
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_evil_preflight_is_not_allowed(client: TestClient) -> None:
    response = _preflight(client, FOREIGN)
    assert "access-control-allow-origin" not in response.headers


def test_allowed_preflight_echoes_origin(client: TestClient) -> None:
    response = _preflight(client, ALLOWED)
    assert response.headers.get("access-control-allow-origin") == ALLOWED
    assert response.headers.get("access-control-allow-credentials") == "true"


# ---------------------------------------------------------------------------
# core#16 — public health must not leak the data path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
def test_public_health_omits_data_root(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "uptime_s" in body
    assert "data_root" not in body
    assert "/app/data" not in response.text


def test_health_models_split_public_and_detail() -> None:
    assert "data_root" not in HealthResponse.model_fields
    assert "data_root" in HealthDetailResponse.model_fields


def test_admin_health_is_not_public(client: TestClient) -> None:
    response = client.get("/api/v1/admin/health")
    assert response.status_code == 401
    assert "data_root" not in response.text


# ---------------------------------------------------------------------------
# core#17 — hardening headers on every response
# ---------------------------------------------------------------------------


def _assert_security_headers(headers: object) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert headers.get(name) == value, name  # type: ignore[attr-defined]


def test_security_headers_are_conservative() -> None:
    assert SECURITY_HEADERS == {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-XSS-Protection": "1; mode=block",
        "Content-Security-Policy": "default-src 'self'",
    }


def test_health_response_carries_security_headers(client: TestClient) -> None:
    _assert_security_headers(client.get("/api/v1/health").headers)


def test_preflight_carries_security_headers(client: TestClient) -> None:
    _assert_security_headers(_preflight(client, ALLOWED).headers)


def test_metrics_still_responds_with_security_headers(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    _assert_security_headers(response.headers)
