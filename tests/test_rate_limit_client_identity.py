"""Client identity for every anonymous bucket.

core#25 took the ``X-Forwarded-For``-first rule out of
:func:`zolai.api.auth_middleware.client_ip` (PR #25): a caller could rotate that
header and pick its own bucket on the public chat quota, OAuth registration and
token issuance, and the failed-login throttle.  These tests pin the rule itself
and cover the one place the same header was still read —
:meth:`zolai.api.rate_limit.RateLimitMiddleware._get_client_key`, a second
identity source that had to be pointed at the same answer.
"""

from __future__ import annotations

import pytest

from zolai.api.auth_middleware import client_ip
from zolai.api.rate_limit import RateLimitMiddleware

FORGED = "198.51.100.7"
PEER = "203.0.113.9"


def _scope(peer: str | None, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/assistant/chat",
        "raw_path": b"/api/v1/assistant/chat",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": (peer, 50000) if peer else None,
        "server": ("test", 80),
        "state": {},
    }


def test_the_header_cannot_choose_the_bucket() -> None:
    """The bypass, in one line: a caller-supplied value never becomes identity."""
    scope = _scope(PEER, [(b"x-forwarded-for", FORGED.encode())])
    assert client_ip(scope) == PEER


def test_a_scope_without_a_peer_is_one_known_bucket_not_a_crash() -> None:
    assert client_ip(_scope(None, [(b"x-forwarded-for", FORGED.encode())])) == "unknown"


def test_the_standalone_limiter_uses_the_same_identity() -> None:
    """``RateLimitMiddleware`` read ``X-Forwarded-For`` itself (core#25 follow-up).

    It is not mounted in ``server.py``, so this is latent rather than live — but
    a second, weaker rule is how the bypass comes back.
    """
    from types import SimpleNamespace

    middleware = RateLimitMiddleware(app=SimpleNamespace())
    request = SimpleNamespace(
        scope=_scope(PEER, [(b"x-forwarded-for", FORGED.encode())]),
        headers={},
    )
    assert middleware._get_client_key(request) == f"ip:{PEER}"


def test_a_presented_key_still_buys_the_limiter_a_key_bucket() -> None:
    from types import SimpleNamespace

    middleware = RateLimitMiddleware(app=SimpleNamespace())
    request = SimpleNamespace(
        scope=_scope(PEER),
        headers={"X-API-Key": "zolai_" + "a" * 24},
    )
    assert middleware._get_client_key(request).startswith("apikey:")


@pytest.mark.parametrize("xff", ["10.0.0.1", "10.0.0.2, 10.0.0.3", ""])
def test_no_form_of_the_header_moves_the_identity(xff: str) -> None:
    scope = _scope(PEER, [(b"x-forwarded-for", xff.encode())])
    assert client_ip(scope) == PEER
