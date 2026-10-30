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

from zolai.api import auth
from zolai.api.security import (
    DEFAULT_CORS_ORIGINS,
    SECURITY_HEADERS,
    SecurityHeadersMiddleware,
    SecurityMiddleware,
    cors_kwargs,
    get_cors_origins,
)
from zolai.api.server import HealthDetailResponse, HealthResponse, create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

ALLOWED = "https://studio.zolai.space"
FOREIGN = "https://evil.example"


#: Plaintext admin key minted by :func:`client` on the throwaway auth DB.
_ADMIN_KEY: dict[str, str] = {}


@pytest.fixture()
def client(tmp_path, monkeypatch, scratch_db) -> Iterator[TestClient]:
    # ``scratch_db`` matters: ``TestClient`` used as a context manager *does*
    # run the app lifespan, which migrates ``config.paths.db``.  Without the
    # redirect every test in this module wrote to the canonical corpus (caught
    # by the ``no_canonical_db_writes`` fence as 12 teardown errors).
    #
    # ``/metrics`` is on the admin-gated ops surface (``rbac.OPS_ADMIN_PREFIXES``).
    # The key lives on a throwaway DB (never the live one) via the
    # ``auth._get_manager`` seam. It is NOT attached globally: negative tests
    # (e.g. ``test_admin_health_is_not_public``) must still observe 401, so the
    # gated ``/metrics`` test presents it per-request via ``admin_key``.
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    _ADMIN_KEY.clear()
    _ADMIN_KEY.update(auth.create_api_key(name="sec-admin", scopes=["apikey:manage"]))
    with TestClient(create_app()) as test_client:
        yield test_client
    mgr.dispose()


@pytest.fixture()
def admin_key() -> str:
    """Admin credential (``apikey:manage``) for the gated ops surface."""
    return _ADMIN_KEY["plaintext"]


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
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }


def test_health_response_carries_security_headers(client: TestClient) -> None:
    _assert_security_headers(client.get("/api/v1/health").headers)


def test_preflight_carries_security_headers(client: TestClient) -> None:
    _assert_security_headers(_preflight(client, ALLOWED).headers)


def test_metrics_still_responds_with_security_headers(
    client: TestClient, admin_key: str
) -> None:
    response = client.get("/metrics", headers={"X-API-Key": admin_key})
    assert response.status_code == 200
    _assert_security_headers(response.headers)


def test_metrics_requires_admin_key(client: TestClient) -> None:
    """``/metrics`` is admin-gated: anonymous reads 401 (core#3)."""
    assert client.get("/metrics").status_code == 401


def test_both_header_paths_emit_one_authoritative_set() -> None:
    """core#17: no second, disagreeing header policy anywhere in the app.

    ``SecurityMiddleware`` (request filtering + headers) and
    ``SecurityHeadersMiddleware`` (headers only, the wired one) historically
    disagreed on CSP.  Both must now emit exactly ``SECURITY_HEADERS``,
    proven on real responses — whichever runs, the set is identical.
    """

    async def inner(scope, receive, send) -> None:
        from starlette.responses import JSONResponse

        await JSONResponse({"ok": True})(scope, receive, send)

    for app in (SecurityMiddleware(inner), SecurityHeadersMiddleware(inner)):
        response = TestClient(app).get("/")
        assert response.status_code == 200
        _assert_security_headers(response.headers)
