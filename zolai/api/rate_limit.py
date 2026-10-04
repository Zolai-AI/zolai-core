"""Enhanced Rate Limiting for Phase 8 Production.

Per-scope rate limits with Redis backend (optional, in-memory fallback).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

# Per-scope rate limits (requests per minute)
SCOPE_LIMITS = {
    "dataset:read": 120,
    "dataset:create": 30,
    "dataset:edit": 30,
    "dataset:run_quality": 10,
    "dataset:validate": 20,
    "dataset:publish": 5,
    "dataset:deprecate": 5,
    "source:read": 60,
    "source:write": 20,
    "pos:read": 60,
    "pos:annotate": 20,
    "pos:review": 20,
    "pos:adjudicate": 10,
    "annotation:read": 60,
    "annotation:review": 20,
    "quality:read": 60,
    "quality:run": 10,
    "quality:waive": 10,
    "eval:read": 60,
    "eval:run": 10,
    "pipeline:read": 60,
    "pipeline:run": 10,
    "catalog:read": 60,
    "audit:read": 60,
    "user:manage": 10,
    "role:manage": 10,
    "apikey:manage": 10,
    "settings:read": 60,
    "settings:write": 10,
    "rag:read": 60,
    # Admin scopes
    "*": 1000,  # Admin wildcard
}

# Default limits for unknown scopes
DEFAULT_LIMIT = 60
DEFAULT_WINDOW = 60  # seconds


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""
    tokens: float
    last_update: float
    capacity: int
    refill_rate: float  # tokens per second


class InMemoryRateLimiter:
    """In-memory rate limiter with token bucket algorithm."""

    def __init__(self, window_seconds: int = DEFAULT_WINDOW):
        self.window = window_seconds
        self.buckets: dict[str, RateLimitBucket] = {}
        self.lock = Lock()

    def _get_bucket(self, key: str, capacity: int) -> RateLimitBucket:
        """Get or create a bucket for the key."""
        now = time.time()
        if key not in self.buckets:
            self.buckets[key] = RateLimitBucket(
                tokens=float(capacity),
                last_update=now,
                capacity=capacity,
                refill_rate=capacity / 60.0,  # per minute -> per second
            )
        return self.buckets[key]

    def _refill(self, bucket: RateLimitBucket, now: float) -> None:
        """Refill bucket based on elapsed time."""
        elapsed = now - bucket.last_update
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_rate)
        bucket.last_update = now

    def check_limit(self, key: str, capacity: int, cost: int = 1) -> tuple[bool, int, int]:
        """Check if request is within limit.

        Returns:
            (allowed, remaining_tokens, retry_after_seconds)
        """
        now = time.time()
        with self.lock:
            bucket = self._get_bucket(key, capacity)
            self._refill(bucket, now)

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                remaining = int(bucket.tokens)
                return True, remaining, 0
            else:
                # Calculate retry after
                needed = cost - bucket.tokens
                retry_after = int(needed / bucket.refill_rate) + 1
                return False, 0, retry_after

    def cleanup_expired(self, max_age: int = 3600) -> int:
        """Remove buckets not accessed for max_age seconds."""
        now = time.time()
        with self.lock:
            expired = [
                k for k, b in self.buckets.items()
                if now - b.last_update > max_age
            ]
            for k in expired:
                del self.buckets[k]
            return len(expired)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware for FastAPI."""

    def __init__(
        self,
        app,
        limiter: InMemoryRateLimiter | None = None,
        scope_limits: dict[str, int] | None = None,
        window_seconds: int = DEFAULT_WINDOW,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.limiter = limiter or InMemoryRateLimiter(window_seconds)
        self.scope_limits = scope_limits or SCOPE_LIMITS
        self.exempt_paths = exempt_paths or ["/health", "/metrics", "/", "/docs", "/openapi.json"]
        self._cleanup_counter = 0

    def _get_client_key(self, request: Request) -> str:
        """Extract client identifier from request."""
        # Use API key if present
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"apikey:{api_key[:16]}"

        # Fall back to IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"

    def _get_required_scopes(self, request: Request) -> list[str]:
        """Extract required scopes from request path."""
        # This would ideally come from route dependencies
        # For now, infer from path
        path = request.url.path
        if path.startswith("/api/v1/rag"):
            return ["rag:read"]
        elif path.startswith("/api/v1/word") or path.startswith("/api/v1/search"):
            return ["dataset:read"]
        elif path.startswith("/api/v1/analyze"):
            return ["rag:read"]
        elif path.startswith("/api/v1/admin"):
            return ["apikey:manage"]
        return ["dataset:read"]  # Default

    async def dispatch(self, request: Request, call_next):
        # Skip exempt paths
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        # Skip non-API paths
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_key = self._get_client_key(request)
        scopes = self._get_required_scopes(request)

        # Check each required scope
        for scope in scopes:
            limit = self.scope_limits.get(scope, self.scope_limits.get("*", DEFAULT_LIMIT))
            bucket_key = f"{client_key}:{scope}"
            allowed, remaining, retry_after = self.limiter.check_limit(bucket_key, limit)

            if not allowed:
                # Rate limited
                response = Response(
                    content='{"detail": "Rate limit exceeded"}',
                    status_code=429,
                    media_type="application/json",
                )
                response.headers["X-RateLimit-Limit"] = str(self.scope_limits.get(scope, DEFAULT_LIMIT))
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["Retry-After"] = str(retry_after)
                return response

            # Add rate limit headers to response
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(self.scope_limits.get(scope, DEFAULT_LIMIT))
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        # Should not reach here
        return await call_next(request)


# Global limiter instance
_global_limiter: InMemoryRateLimiter | None = None


def get_rate_limiter() -> InMemoryRateLimiter:
    """Get global rate limiter instance."""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = InMemoryRateLimiter()
    return _global_limiter


def create_rate_limit_middleware(
    scope_limits: dict[str, int] | None = None,
    window_seconds: int = DEFAULT_WINDOW,
) -> type[RateLimitMiddleware]:
    """Factory to create rate limit middleware with custom config."""
    limiter = InMemoryRateLimiter(window_seconds)

    class ConfiguredMiddleware(RateLimitMiddleware):
        def __init__(self, app):
            super().__init__(app, limiter=limiter, scope_limits=scope_limits, window_seconds=window_seconds)

    return ConfiguredMiddleware
