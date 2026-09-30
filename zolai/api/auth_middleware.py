"""API-key middleware for the versioned surface (``/api/v1``).

Pure-ASGI (same shape as :mod:`zolai.monitoring.middleware`) so nothing is
buffered.  Behaviour:

- Gate: ``path == "/api/v1"`` or ``startswith("/api/v1/")`` — legacy unversioned
  routes, ``/health``, ``/metrics`` and ``/api/metrics/*`` are byte-identical
  to before this middleware exists.
- Mode ``warn`` (default) dual-accepts: a missing/invalid key is logged
  (rate limited) and the request continues unauthenticated.  Mode ``enforce``
  answers **401**.  Mode ``off`` bypasses entirely (rollback).
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
import threading
import time
from typing import Any

from starlette.concurrency import run_in_threadpool

from . import auth

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


def extract_key(headers: Any) -> str | None:
    """Read the plaintext key from ``Authorization: Bearer`` or ``X-API-Key``."""
    if not headers:
        return None
    for name, value in headers:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
        elif name == b"x-api-key":
            token = value.decode("latin-1").strip()
            if token:
                return token
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
