"""API-key middleware tests — modes, exemptions, rate limit, metrics order.

Covers the P0-1 / ADR-014 done-when:

- enforce: no key 401 · valid 200 · expired 401 · revoked 401 · bad scope 403
- default warn: accepts but logs unauthenticated ``/api/v1``
- ``/health``, ``/metrics``, ``/api/metrics/*`` and legacy routes byte-identical
- >rate-limit 429 + Retry-After + X-RateLimit-*
- 401s are counted by MetricsMiddleware (registration order)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import Sample, text_string_to_metric_families

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway DB wired into the auth service (singleton seam)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    """App without lifespan — auth routes do not need startup migrations."""
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Isolate cache / rate buckets / failure-log throttle around each test."""
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")


def _auth_headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


# ---------------------------------------------------------------------------
# Enforce mode
# ---------------------------------------------------------------------------


def test_enforce_missing_key_is_401(client: TestClient, auth_db, monkeypatch) -> None:
    _enforce(monkeypatch)
    response = client.get("/api/v1/admin/api-keys")
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["error"] == "unauthorized"
    assert detail["reason"] == "missing_api_key"


def test_enforce_valid_key_is_200(client: TestClient, auth_db, monkeypatch) -> None:
    _enforce(monkeypatch)
    key = auth.create_api_key(name="ok", scopes=["apikey:manage"])
    response = client.get("/api/v1/admin/api-keys", headers=_auth_headers(key))
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_enforce_x_api_key_header_is_accepted(
    client: TestClient, auth_db, monkeypatch
) -> None:
    _enforce(monkeypatch)
    key = auth.create_api_key(name="hdr", scopes=["apikey:manage"])
    response = client.get(
        "/api/v1/admin/api-keys", headers={"X-API-Key": key["plaintext"]}
    )
    assert response.status_code == 200


def test_enforce_expired_key_is_401(client: TestClient, auth_db, monkeypatch) -> None:
    _enforce(monkeypatch)
    key = auth.create_api_key(name="old", scopes=["apikey:manage"], expires_days=90)
    with auth_db.engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text("UPDATE api_keys SET expires_at = '2000-01-01 00:00:00' WHERE id = :id"),
            {"id": key["id"]},
        )
    response = client.get("/api/v1/admin/api-keys", headers=_auth_headers(key))
    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "invalid_api_key"


def test_enforce_revoked_key_is_401_immediately(
    client: TestClient, auth_db, monkeypatch
) -> None:
    _enforce(monkeypatch)
    key = auth.create_api_key(name="bye", scopes=["apikey:manage"])
    assert client.get("/api/v1/admin/api-keys", headers=_auth_headers(key)).status_code == 200
    auth.revoke_api_key(key["id"], actor="test")
    response = client.get("/api/v1/admin/api-keys", headers=_auth_headers(key))
    assert response.status_code == 401


def test_enforce_bad_scope_is_403_with_action(
    client: TestClient, auth_db, monkeypatch
) -> None:
    _enforce(monkeypatch)
    key = auth.create_api_key(name="reader", scopes=["dataset:read"])
    response = client.get("/api/v1/admin/api-keys", headers=_auth_headers(key))
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["action"] == "apikey:manage"
    assert detail["reason"] == "missing_scope"


def test_rate_limit_429_with_retry_after_and_headers(
    client: TestClient, auth_db, monkeypatch
) -> None:
    _enforce(monkeypatch)
    monkeypatch.setenv("ZOLAI_API_RATE_LIMIT_RPM", "3")
    key = auth.create_api_key(name="hot", scopes=["apikey:manage"])
    headers = _auth_headers(key)

    for i in range(3):
        response = client.get("/api/v1/admin/api-keys", headers=headers)
        assert response.status_code == 200, i
        assert response.headers["x-ratelimit-limit"] == "3"
        assert response.headers["x-ratelimit-remaining"] == str(2 - i)

    blocked = client.get("/api/v1/admin/api-keys", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers["x-ratelimit-limit"] == "3"
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    detail = blocked.json()["detail"]
    assert detail["error"] == "rate_limited"
    assert detail["limit_rpm"] == 3


# ---------------------------------------------------------------------------
# Warn (default) + off
# ---------------------------------------------------------------------------


def test_default_warn_mode_accepts_but_logs(
    client: TestClient, auth_db, caplog: pytest.LogCaptureFixture, monkeypatch
) -> None:
    monkeypatch.delenv("ZOLAI_API_AUTH", raising=False)
    with caplog.at_level(logging.WARNING, logger="zolai.api.auth"):
        response = client.get("/api/v1/admin/api-keys")
    assert response.status_code == 200
    assert "missing_key" in caplog.text


def test_warn_mode_still_enforces_scopes_on_presented_key(
    client: TestClient, auth_db, monkeypatch
) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    key = auth.create_api_key(name="narrow", scopes=["dataset:read"])
    response = client.get("/api/v1/admin/api-keys", headers=_auth_headers(key))
    assert response.status_code == 403
    assert response.json()["detail"]["action"] == "apikey:manage"


def test_off_mode_bypasses_entirely(
    client: TestClient, auth_db, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "off")
    with caplog.at_level(logging.WARNING, logger="zolai.api.auth"):
        response = client.get("/api/v1/admin/api-keys")
    assert response.status_code == 200
    assert "missing_key" not in caplog.text


# ---------------------------------------------------------------------------
# Exemptions — byte-identical to the pre-middleware surface
# ---------------------------------------------------------------------------


def test_exempt_and_legacy_routes_untouched_in_enforce_mode(
    client: TestClient, auth_db, monkeypatch
) -> None:
    _enforce(monkeypatch)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    # Not on the gated surface → no rate-limit headers injected.
    assert "x-ratelimit-limit" not in health.headers

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "zolai_http_requests_total" in metrics.text
    assert "x-ratelimit-limit" not in metrics.headers

    info = client.get("/api/metrics/info")
    assert info.status_code == 200
    assert info.json()["version"]
    assert "x-ratelimit-limit" not in info.headers

    # Legacy unversioned route — same 200 as before the middleware existed.
    legacy = client.get("/bible/status")
    assert legacy.status_code == 200
    assert legacy.json()["status"] == "ok"
    assert "x-ratelimit-limit" not in legacy.headers


def test_api_v1_health_is_exempt_in_enforce_mode(
    client: TestClient, auth_db, monkeypatch
) -> None:
    _enforce(monkeypatch)
    response = client.get("/api/v1/health")
    # Not defined on core today — but it must never be answered 401/429 by auth.
    assert response.status_code in (200, 404)
    assert response.status_code != 401


# ---------------------------------------------------------------------------
# Middleware order — 401s must flow through MetricsMiddleware
# ---------------------------------------------------------------------------


def _http_samples(text: str) -> list[Sample]:
    samples: list[Sample] = []
    for family in text_string_to_metric_families(text):
        samples.extend(family.samples)
    return samples


def _total_401(samples: list[Sample]) -> float:
    return sum(
        s.value
        for s in samples
        if s.name == "zolai_http_requests_total" and s.labels.get("status") == "401"
    )


def test_401_responses_are_counted_in_http_metrics(
    client: TestClient, auth_db, monkeypatch
) -> None:
    """ApiKeyMiddleware sits *inside* MetricsMiddleware → 401s increment the counter."""
    before = _total_401(_http_samples(client.get("/metrics").text))

    _enforce(monkeypatch)
    response = client.get("/api/v1/admin/api-keys")
    assert response.status_code == 401

    after = _total_401(_http_samples(client.get("/metrics").text))
    assert after >= before + 1
