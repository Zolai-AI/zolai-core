"""Ops-surface authorization gate (core#3) — ``rbac.OPS_ADMIN_PREFIXES``.

``/metrics``, ``/api/metrics/*``, ``/review``, ``/docs``, ``/redoc`` and
``/openapi.json`` sit **outside** ``/api/v1`` and never carried a route-level
dependency, so they were readable with no credential — leaking the table
inventory with row counts and audit rows (``/api/v1/audit``, ``/desktop/tables``,
``/api/v1/foundation/stats``, ``/api/v1/predictions/health`` all answered 200
anonymously).

The gate lives in :class:`zolai.api.auth_middleware.ApiKeyMiddleware` *before*
``is_protected_path``:

- ``401`` for anonymous in **warn and enforce** (not 403 — we do not confirm
  the surface exists to an unauthenticated caller),
- ``403`` for a resolved **non-admin** role,
- ``200`` for an admin key,
- only ``ZOLAI_API_AUTH=off`` bypasses,
- ``ZOLAI_DEV_DOCS`` (default **off**) re-opens the explorer trio only.

No new rate-limit bucket: this is authorization, not throttling. ``VALID_ACTIONS``
is untouched — ``role_for`` is the existing role model, so this adds no second
authorization system.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth, rbac
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

#: Every path on the gated ops surface, plus the two routes the founder
#: measured leaking table inventory + audit rows without a credential.
OPS_PATHS: tuple[str, ...] = (
    "/metrics",
    "/api/metrics/info",
    "/review/",
    "/docs",
    "/redoc",
    "/openapi.json",
)


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway DB wired into the auth service (singleton seam).

    Never the live ``data/zolai.db`` — the same ``auth._get_manager`` seam
    ``test_api_auth.py`` uses.
    """
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    """App without lifespan — the ops gate is middleware, not a route."""
    return TestClient(create_app())


def _key(mgr: DatabaseManager, *, name: str, scopes: list[str]) -> str:
    return auth.create_api_key(name=name, scopes=scopes)["plaintext"]


def _admin(mgr: DatabaseManager) -> dict[str, str]:
    return {"X-API-Key": _key(mgr, name="ops-admin", scopes=["apikey:manage"])}


def _member(mgr: DatabaseManager) -> dict[str, str]:
    """A valid non-admin credential (``rag:read`` is a member scope)."""
    return {"X-API-Key": _key(mgr, name="reader", scopes=["rag:read"])}


def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")


# ---------------------------------------------------------------------------
# Anonymous → 401 (warn *and* enforce)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", OPS_PATHS)
def test_anonymous_ops_read_is_401_in_default_warn_mode(
    client: TestClient, path: str
) -> None:
    """Default ``warn`` mode still refuses anonymous ops reads."""
    assert auth.api_auth_mode() == "warn", "test assumes the shipped default"
    response = client.get(path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", OPS_PATHS)
def test_anonymous_ops_read_is_401_in_enforce_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    _enforce(monkeypatch)
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", OPS_PATHS)
def test_anonymous_401_body_is_the_envelope_not_a_traceback(
    client: TestClient, path: str
) -> None:
    body = client.get(path).json()
    assert body == {"detail": {"error": "unauthorized", "reason": "missing_api_key"}}


def test_bogus_key_is_401_not_403(client: TestClient) -> None:
    """An unresolvable credential is 401 — we never reveal the role."""
    response = client.get("/metrics", headers={"X-API-Key": "zolai_sk_deadbeef"})
    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "invalid_api_key"


# ---------------------------------------------------------------------------
# Non-admin → 403 · admin → 200
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", OPS_PATHS)
def test_member_role_is_403(
    client: TestClient, auth_db: DatabaseManager, path: str
) -> None:
    """A *resolved* non-admin is 403 with the required role named."""
    response = client.get(path, headers=_member(auth_db))
    assert response.status_code == 403
    body = response.json()["detail"]
    assert body["error"] == "forbidden"
    assert body["required_role"] == "admin"
    assert body["reason"] == "insufficient_role"


@pytest.mark.parametrize("path", OPS_PATHS)
def test_admin_key_is_200(
    client: TestClient, auth_db: DatabaseManager, path: str
) -> None:
    assert client.get(path, headers=_admin(auth_db)).status_code == 200


def test_admin_403_is_not_leaked_to_anonymous(
    client: TestClient, auth_db: DatabaseManager
) -> None:
    """Anonymous sees 401; only a resolved credential earns the 403."""
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers=_member(auth_db)).status_code == 403


# ---------------------------------------------------------------------------
# ``ZOLAI_API_AUTH=off`` bypass (the only bypass) + scope is not the gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", OPS_PATHS)
def test_off_mode_bypasses_the_gate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("ZOLAI_API_AUTH", "off")
    assert client.get(path).status_code == 200


def test_scoped_admin_key_still_passes(client: TestClient, auth_db: DatabaseManager) -> None:
    """The gate is the **role**, not an ops scope — no new action to add.

    ``apikey:manage`` is what makes ``role_for`` resolve to ``admin``; the
    key carries no special ``ops:read`` action, so ``VALID_ACTIONS`` stays
    frozen at 33.
    """
    assert client.get("/metrics", headers=_admin(auth_db)).status_code == 200


def test_ops_gate_does_not_inject_rate_limit_headers(
    client: TestClient, auth_db: DatabaseManager
) -> None:
    """Authorization, not throttling — no new bucket on this surface."""
    response = client.get("/metrics", headers=_admin(auth_db))
    assert response.status_code == 200
    assert "x-ratelimit-limit" not in response.headers


# ---------------------------------------------------------------------------
# ``ZOLAI_DEV_DOCS`` escape hatch — defaults safe, explorer trio only
# ---------------------------------------------------------------------------


def test_hatch_defaults_closed() -> None:
    assert rbac.dev_docs_open() is False


@pytest.mark.parametrize("path", ("/docs", "/redoc", "/openapi.json"))
@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("ON", True)])
def test_hatch_opens_explorer_trio(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("ZOLAI_DEV_DOCS", value)
    assert rbac.dev_docs_open() is expected
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ("/docs", "/redoc", "/openapi.json"))
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
def test_hatch_stays_closed_for_unfalsy_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, value: str
) -> None:
    monkeypatch.setenv("ZOLAI_DEV_DOCS", value)
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ("/metrics", "/api/metrics/info", "/review/"))
def test_hatch_never_reopens_metrics_or_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The hatch covers the explorer trio only — never data-bearing ops.

    ``/metrics``, ``/api/metrics/*`` and ``/review/`` stay admin-gated even
    with ``ZOLAI_DEV_DOCS=1``; they are the surface that leaked the table
    inventory and audit rows.
    """
    monkeypatch.setenv("ZOLAI_DEV_DOCS", "1")
    assert rbac.is_ops_admin_path(path) is True, path
    assert client.get(path).status_code == 401


def test_docs_redirect_rides_along_with_the_hatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/docs/oauth2-redirect`` is part of the explorer, so prefix-matched."""
    monkeypatch.setenv("ZOLAI_DEV_DOCS", "1")
    assert client.get("/docs/oauth2-redirect").status_code == 200


# ---------------------------------------------------------------------------
# The predicate itself (the middleware is one caller of it)
# ---------------------------------------------------------------------------


def test_is_ops_admin_path_covers_the_measured_leaks() -> None:
    for path in (
        "/metrics",
        "/api/metrics/summary",
        "/review/",
        "/docs",
        "/redoc",
        "/openapi.json",
    ):
        assert rbac.is_ops_admin_path(path) is True, path


def test_is_ops_admin_path_leaves_public_and_api_v1_alone() -> None:
    for path in ("/", "/health", "/api/v1/health", "/api/v1/lexicon", "/bible/status"):
        assert rbac.is_ops_admin_path(path) is False, path
