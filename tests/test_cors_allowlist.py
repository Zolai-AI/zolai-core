"""CORS allowlist contract (core#1) — no origin is ever reflected.

The deployed app wires ``CORSMiddleware`` with an explicit allowlist
(``zolai.api.server`` line 526 -> ``cors_kwargs(get_cors_origins())``).
Because ``allow_credentials=True``, a wildcard is impossible: Starlette
would reflect **any** ``Origin`` — including ``null`` — together with
``Access-Control-Allow-Credentials: true``, letting any page read
authenticated responses.  This module pins the list to exactly the five
trusted origins and proves the reflection behaviour for all three cases:

1. a member of the allowlist  -> echoed back, with credentials,
2. an unknown origin          -> **no** ``Access-Control-Allow-Origin``,
3. ``Origin: null``           -> **no** ``Access-Control-Allow-Origin``.

Case 2/3 is the whole point: the request still reaches the server (this is
not a request filter), but the browser is never told the response may be
read cross-origin, so the response is invisible to the hostile page.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.api.security import (
    CORS_ALLOWED_ORIGINS,
    DEFAULT_CORS_ORIGINS,
    SecurityConfig,
    cors_kwargs,
    get_cors_origins,
)
from zolai.api.server import create_app

#: Every origin the founder's allowlist requires — order-independent.
REQUIRED_ORIGINS: tuple[str, ...] = (
    "https://zolai.space",
    "https://studio.zolai.space",
    "https://journey.zolai.space",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)

#: Origins that must NOT be trusted.  ``api.zolai.space`` is the API's own
#: host (a browser never originates from it) and ``localhost:3000`` was the
#: stale Next.js dev port — neither belongs in the browser allowlist.
FORBIDDEN_ORIGINS: tuple[str, ...] = (
    "https://api.zolai.space",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

UNKNOWN = "https://evil.example"
NULL_ORIGIN = "null"

#: The one path CORS is asserted against — public, credential-free, always 200.
PROBE_PATH = "/api/v1/health"


@pytest.fixture()
def client(scratch_db: Path) -> Iterator[TestClient]:
    """App on a throwaway DB — ``create_app``'s lifespan runs migrations.

    Without the redirect the lifespan migrates ``config.paths.db``, i.e. the
    canonical corpus; the ``no_canonical_db_writes`` fence correctly fails the
    test for it, and CORS never needed the real store anyway.
    """
    with TestClient(create_app()) as test_client:
        yield test_client


def _simple_request(client: TestClient, origin: str):
    return client.get(PROBE_PATH, headers={"Origin": origin})


def _preflight(client: TestClient, origin: str):
    return client.options(
        PROBE_PATH,
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


# ---------------------------------------------------------------------------
# The list is exactly the required five
# ---------------------------------------------------------------------------


def test_allowlist_is_exactly_the_required_origins() -> None:
    assert set(DEFAULT_CORS_ORIGINS) == set(REQUIRED_ORIGINS)
    assert len(DEFAULT_CORS_ORIGINS) == len(REQUIRED_ORIGINS) == 5


def test_guard_tuple_matches_the_default_list() -> None:
    """``CORS_ALLOWED_ORIGINS`` is the drift guard for ``DEFAULT_CORS_ORIGINS``."""
    assert CORS_ALLOWED_ORIGINS == REQUIRED_ORIGINS
    assert list(CORS_ALLOWED_ORIGINS) == DEFAULT_CORS_ORIGINS


def test_stale_origins_are_gone() -> None:
    for origin in FORBIDDEN_ORIGINS:
        assert origin not in DEFAULT_CORS_ORIGINS, origin


def test_no_wildcard_anywhere() -> None:
    assert "*" not in DEFAULT_CORS_ORIGINS
    kwargs = cors_kwargs()
    assert "*" not in kwargs["allow_origins"]
    assert "*" not in kwargs["allow_methods"]
    assert "*" not in kwargs["allow_headers"]
    assert kwargs["allow_credentials"] is True


def test_security_config_uses_the_same_allowlist() -> None:
    """The unused-but-live ``SecurityConfig`` field must not drift either."""
    assert SecurityConfig().cors_origins == DEFAULT_CORS_ORIGINS


def test_env_override_is_wholesale_not_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZOLAI_CORS_ORIGINS", "https://a.test, https://b.test ,")
    assert get_cors_origins() == ["https://a.test", "https://b.test"]


def test_blank_env_falls_back_to_the_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZOLAI_CORS_ORIGINS", "   ")
    assert get_cors_origins() == list(DEFAULT_CORS_ORIGINS)


# ---------------------------------------------------------------------------
# Case 1 — an allowlisted origin is echoed, with credentials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origin", REQUIRED_ORIGINS)
def test_allowlisted_simple_request_is_echoed(
    client: TestClient, origin: str
) -> None:
    response = _simple_request(client, origin)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.parametrize("origin", REQUIRED_ORIGINS)
def test_allowlisted_preflight_is_echoed(client: TestClient, origin: str) -> None:
    response = _preflight(client, origin)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_mcp_binding_headers_pass_preflight(client: TestClient) -> None:
    """Browser MCP clients (studio.zolai.space) send these on tools/call."""
    response = client.options(
        PROBE_PATH,
        headers={
            "Origin": "https://studio.zolai.space",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,mcp-protocol-version,mcp-method,mcp-name"
            ),
        },
    )
    assert response.status_code == 200, response.text
    allowed = {
        h.strip().lower()
        for h in response.headers["access-control-allow-headers"].split(",")
    }
    assert {"mcp-method", "mcp-name", "mcp-protocol-version"} <= allowed
    assert response.headers["access-control-allow-origin"] == "https://studio.zolai.space"


# ---------------------------------------------------------------------------
# Case 2 — an unknown origin gets NO CORS headers (never reflected)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origin", FORBIDDEN_ORIGINS)
def test_unknown_simple_request_gets_no_allow_origin(
    client: TestClient, origin: str
) -> None:
    """No ``Access-Control-Allow-Origin`` => the browser cannot read the reply.

    Note Starlette still emits ``Access-Control-Allow-Credentials`` and
    ``Expose-Headers`` on a non-CORS request.  Those are inert without
    ``Allow-Origin`` (the browser requires it first), so the assertion targets
    the one header that actually grants cross-origin access.
    """
    response = _simple_request(client, origin)
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", FORBIDDEN_ORIGINS)
def test_unknown_preflight_gets_no_allow_origin(
    client: TestClient, origin: str
) -> None:
    """An untrusted preflight is refused: **400** and no ``Allow-Origin``.

    Starlette still emits the static CORS fields (``Allow-Methods``,
    ``Allow-Headers``, ``Max-Age``, ``Allow-Credentials``) on its refusal
    path, but never ``Allow-Origin`` — so the browser blocks the actual
    request.  The 400 is the extra signal that nothing was authorized.
    """
    response = _preflight(client, origin)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_evil_origin_is_never_reflected(client: TestClient) -> None:
    """The response is served but never handed to a foreign page."""
    response = _simple_request(client, UNKNOWN)
    assert response.status_code == 200  # still served, just not readable
    assert "access-control-allow-origin" not in response.headers


def test_evil_preflight_is_not_allowed(client: TestClient) -> None:
    response = _preflight(client, UNKNOWN)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Case 3 — ``Origin: null`` gets NO CORS headers
# ---------------------------------------------------------------------------


def test_null_origin_simple_request_gets_no_allow_origin(
    client: TestClient,
) -> None:
    """``Origin: null`` (sandboxed iframe / file://) is untrusted like any other."""
    response = _simple_request(client, NULL_ORIGIN)
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_null_origin_preflight_gets_no_allow_origin(client: TestClient) -> None:
    response = _preflight(client, NULL_ORIGIN)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_null_origin_is_not_in_the_allowlist() -> None:
    assert NULL_ORIGIN not in DEFAULT_CORS_ORIGINS


# ---------------------------------------------------------------------------
# Reflection is impossible by construction
# ---------------------------------------------------------------------------


def test_a_lookalike_origin_is_not_trusted(client: TestClient) -> None:
    """Suffix/prefix tricks do not match — the comparison is exact."""
    for origin in (
        "https://zolai.space.evil.example",
        "https://evilzolai.space",
        "https://studio.zolai.space:8443",
        "http://zolai.space",
        "https://STUDIO.zolai.space",
    ):
        assert "access-control-allow-origin" not in _simple_request(
            client, origin
        ).headers, origin
