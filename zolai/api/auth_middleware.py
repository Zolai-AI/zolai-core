"""API-key middleware for the versioned surface (``/api/v1``).

Pure-ASGI (same shape as :mod:`zolai.monitoring.middleware`) so nothing is
buffered.  Behaviour:

- Gate: ``path == "/api/v1"`` or ``startswith("/api/v1/")`` — legacy unversioned
  routes, ``/health``, ``/metrics`` and ``/api/metrics/*`` are byte-identical
  to before this middleware exists.
- Mode ``warn`` (default) dual-accepts: a missing/invalid key is logged
  (rate limited) and the request continues unauthenticated.  Mode ``enforce``
  answers **401**.  Mode ``off`` bypasses entirely (rollback).
- **Public paths never 401** (:func:`zolai.api.rbac.is_public_path` — the same
  table ``require_scope`` reads).  An anonymous caller on a public path is
  rate limited per **IP** instead: ``ZOLAI_PUBLIC_RATE_LIMIT_RPM`` (default
  120/min), with ``POST /api/v1/assistant/chat`` on the stricter
  ``ZOLAI_PUBLIC_CHAT_RATE_LIMIT_RPM`` bucket (default 10/min) → **429**.
- Authenticated requests are rate limited with an in-memory token bucket per
  key id: **429** + ``Retry-After`` + ``X-RateLimit-*`` headers.
- The matched key record is published on ``scope["state"]["api_key"]`` for the
  ``require_scope`` dependency.

Registration order in ``server.py`` matters: CORS first, this middleware next,
``MetricsMiddleware`` last (outermost) — so 401/429 responses still flow
through the metrics wrapper and are counted in ``zolai_http_requests_total``.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any

from starlette.concurrency import run_in_threadpool

from . import auth, session_auth
from .rbac import is_public_chat_path, is_public_path

#: Only this prefix is gated (boundary-aware: ``/api/v1x`` is not gated).
API_PREFIX = "/api/v1"

#: Paths never gated — documented exemptions (ADR-002 / ADR-014).
EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/metrics",
        "/api/v1/health",
    }
)

#: Bucket store upper bound before stale entries are pruned.
_BUCKET_PRUNE_SIZE = 4096

HeaderList = list[tuple[bytes, bytes]]


def is_protected_path(path: str) -> bool:
    """True when ``path`` sits on the gated ``/api/v1`` surface."""
    if path in EXEMPT_PATHS:
        return False
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


#: Defaults for the anonymous buckets (P2 — plan §B).
DEFAULT_PUBLIC_RATE_LIMIT_RPM = 120
DEFAULT_PUBLIC_CHAT_RATE_LIMIT_RPM = 10


def _env_rpm(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(1, value)


def public_rate_limit_rpm() -> int:
    """Anonymous requests/minute on public paths (``ZOLAI_PUBLIC_RATE_LIMIT_RPM``)."""
    return _env_rpm("ZOLAI_PUBLIC_RATE_LIMIT_RPM", DEFAULT_PUBLIC_RATE_LIMIT_RPM)


def public_chat_rate_limit_rpm() -> int:
    """Anonymous chat requests/minute (``ZOLAI_PUBLIC_CHAT_RATE_LIMIT_RPM``)."""
    return _env_rpm("ZOLAI_PUBLIC_CHAT_RATE_LIMIT_RPM", DEFAULT_PUBLIC_CHAT_RATE_LIMIT_RPM)


def client_ip(scope: Any) -> str:
    """Best-effort client IP for the anonymous bucket (``X-Forwarded-For`` first)."""
    headers = scope.get("headers") or []
    for name, value in headers:
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    return client[0] if client and client[0] else "unknown"


def extract_key(headers: Any) -> str | None:
    """Read the plaintext key from ``X-API-Key`` or ``Authorization: Bearer``.

    ``X-API-Key`` is scanned **first** and wins when both are present: it is the
    machine-credential header (MCP / Tauri / scripts), and a session token that
    happens to ride along in ``Authorization`` must never take precedence over a
    real key.  With only one of the two headers the result is identical to a
    first-match scan, so the existing key path is unchanged.
    """
    if not headers:
        return None
    for name, value in headers:
        if name == b"x-api-key":
            token = value.decode("latin-1").strip()
            if token:
                return token
    for name, value in headers:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


def extract_bearer(scope: Any) -> str | None:
    """Read the ``Authorization: Bearer`` token from a raw ASGI *scope*.

    Deliberately narrower than :func:`extract_key` (which takes the headers
    list): used by ``POST /api/v1/auth/logout``, which must revoke the **session
    the caller presented** and must ignore ``X-API-Key`` entirely — an API key
    has no session to revoke.
    """
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


# ---------------------------------------------------------------------------
# In-memory token bucket per key id (DB-backed multi-worker limits: DEFER)
# ---------------------------------------------------------------------------


class _Bucket:
    __slots__ = ("capacity", "tokens", "updated")

    def __init__(self, capacity: int, now: float) -> None:
        self.capacity = capacity
        self.tokens = float(capacity)
        self.updated = now


class RateLimiter:
    """Token bucket per key: ``capacity`` requests, refilled over 60s."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def hit(self, key_id: str, capacity: int) -> tuple[bool, int, float, int]:
        """Consume one token.

        Returns ``(allowed, remaining, seconds_until_full, retry_after)``.
        """
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > _BUCKET_PRUNE_SIZE:
                self._prune(now)
            bucket = self._buckets.get(key_id)
            if bucket is None or bucket.capacity != capacity:
                bucket = _Bucket(capacity, now)
                self._buckets[key_id] = bucket
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(float(capacity), bucket.tokens + elapsed * capacity / 60.0)
            bucket.updated = now
            reset = (capacity - bucket.tokens) * 60.0 / capacity
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, int(bucket.tokens), reset, 0
            retry_after = max(1, int(math.ceil((1.0 - bucket.tokens) * 60.0 / capacity)))
            return False, 0, reset, retry_after

    def reset(self) -> None:
        """Drop every bucket (tests / mode flips)."""
        with self._lock:
            self._buckets.clear()

    def _prune(self, now: float) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.updated > 600.0]
        for k in stale:
            self._buckets.pop(k, None)


limiter = RateLimiter()


def reset_rate_limiter() -> None:
    """Clear all rate-limit buckets (tests)."""
    limiter.reset()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


async def _send_json(
    send: Any, status: int, payload: dict[str, Any], extra_headers: HeaderList | None = None
) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers: HeaderList = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _rate_headers(capacity: int, remaining: int, reset: float) -> HeaderList:
    return [
        (b"x-ratelimit-limit", str(capacity).encode("ascii")),
        (b"x-ratelimit-remaining", str(max(0, remaining)).encode("ascii")),
        (b"x-ratelimit-reset", str(int(math.ceil(max(0.0, reset)))).encode("ascii")),
    ]


class ApiKeyMiddleware:
    """ASGI middleware authenticating + rate limiting ``/api/v1`` requests."""

    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def _resolve_session(token: str | None) -> dict[str, Any] | None:
        """Resolve a ``zolai_ss_*`` session token, or ``None`` to stay on the key path.

        Imported lazily: :mod:`zolai.api.session_auth` pulls in argon2, and this
        middleware is on the hot path for every API-key request.
        """
        if not token or not token.startswith(session_auth.SESSION_PREFIX):
            return None
        if not session_auth.sessions_enabled():
            # Kill switch: the token is simply not a credential any more, so the
            # caller lands on the ordinary ``invalid_api_key`` path.
            return None
        return session_auth.resolve_session(token)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if not is_protected_path(path) or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        mode = auth.api_auth_mode()
        if mode == "off":
            await self.app(scope, receive, send)
            return

        token = extract_key(scope.get("headers"))
        record: dict[str, Any] | None = None
        if token:
            record = await run_in_threadpool(auth.resolve_key, token)

        if record is None:
            # Username/session credentials: **only** a ``zolai_ss_*`` Bearer that
            # the key lookup above did not already satisfy, and only while
            # ``ZOLAI_AUTH_SESSIONS`` is on.  Precedence is therefore
            # X-API-Key / any non-session Bearer -> key path (unchanged);
            # ``zolai_ss_*`` -> session path; a session token while the flag is
            # off falls through as an ordinary invalid key, so rollback looks
            # like a bad credential rather than a new failure mode.
            session_record = await run_in_threadpool(self._resolve_session, token)
            if session_record is not None:
                state = scope.setdefault("state", {})
                state["api_key"] = session_record
                state["session"] = {
                    "id": session_record.get("session_id"),
                    "user_id": session_record.get("user_id"),
                    "username": session_record.get("username"),
                    "expires_at": session_record.get("expires_at"),
                }

                capacity = auth.api_rate_limit_rpm()
                allowed, remaining, reset, retry_after = await run_in_threadpool(
                    limiter.hit, f"session:{session_record.get('session_id')}", capacity
                )
                rate_headers = _rate_headers(capacity, remaining, reset)
                if not allowed:
                    await _send_json(
                        send,
                        429,
                        {
                            "detail": {
                                "error": "rate_limited",
                                "limit_rpm": capacity,
                                "retry_after_s": retry_after,
                            }
                        },
                        rate_headers + [(b"retry-after", str(retry_after).encode("ascii"))],
                    )
                    return

                async def send_session_wrapper(message: Any) -> None:
                    if message.get("type") == "http.response.start":
                        existing = list(message.get("headers") or [])
                        message["headers"] = existing + rate_headers
                    await send(message)

                await self.app(scope, receive, send_session_wrapper)
                return

            # P2 public class: anonymous reads (and the public assistant) stay
            # open in warn *and* enforce — rate limited per IP, never 401.
            if is_public_path(scope.get("method"), path):
                if token:
                    # An *invalid* key was still worth logging (rate limited).
                    auth.log_auth_failure(
                        "invalid_key", path, token[: auth.PREFIX_LEN]
                    )
                chat = is_public_chat_path(scope.get("method"), path)
                capacity = public_chat_rate_limit_rpm() if chat else public_rate_limit_rpm()
                bucket = f"anon:{client_ip(scope)}" + (":chat" if chat else "")
                allowed, remaining, reset, retry_after = await run_in_threadpool(
                    limiter.hit, bucket, capacity
                )
                if not allowed:
                    await _send_json(
                        send,
                        429,
                        {
                            "detail": {
                                "error": "rate_limited",
                                "scope": "public_chat" if chat else "public",
                                "limit_rpm": capacity,
                                "retry_after_s": retry_after,
                            }
                        },
                        _rate_headers(capacity, remaining, reset)
                        + [(b"retry-after", str(retry_after).encode("ascii"))],
                    )
                    return
                await self.app(scope, receive, send)
                return

            reason = "missing_key" if not token else "invalid_key"
            auth.log_auth_failure(reason, path, token[: auth.PREFIX_LEN] if token else None)
            if mode == "enforce":
                await _send_json(
                    send,
                    401,
                    {
                        "detail": {
                            "error": "unauthorized",
                            "reason": "missing_api_key" if not token else "invalid_api_key",
                        }
                    },
                )
                return
            # warn: dual-accept, continue unauthenticated (require_scope allows
            # ordinary routes).  Publish *why* the key failed so strict
            # dependencies (admin key minting) can answer an accurate 401.
            state = scope.setdefault("state", {})
            state["api_key_error"] = "missing_api_key" if not token else "invalid_api_key"
            await self.app(scope, receive, send)
            return

        # Publish the verified key for route dependencies (require_scope).
        state = scope.setdefault("state", {})
        state["api_key"] = {
            "id": record.get("id"),
            "name": record.get("name"),
            "key_prefix": record.get("key_prefix"),
            "scopes": record.get("scopes") or [],
        }

        capacity = auth.api_rate_limit_rpm()
        allowed, remaining, reset, retry_after = await run_in_threadpool(
            limiter.hit, str(record.get("id")), capacity
        )
        rate_headers = _rate_headers(capacity, remaining, reset)

        if not allowed:
            await _send_json(
                send,
                429,
                {
                    "detail": {
                        "error": "rate_limited",
                        "limit_rpm": capacity,
                        "retry_after_s": retry_after,
                    }
                },
                rate_headers + [(b"retry-after", str(retry_after).encode("ascii"))],
            )
            return

        async def send_wrapper(message: Any) -> None:
            if message.get("type") == "http.response.start":
                existing = list(message.get("headers") or [])
                message["headers"] = existing + rate_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
