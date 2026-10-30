"""Security Hardening for Phase 8 Production.

API key rotation, input sanitization, CORS, request validation.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

# Security constants
MAX_REQUEST_SIZE = 1024 * 1024  # 1MB default
MAX_RAG_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB for RAG
API_KEY_WARN_DAYS = 90
API_KEY_EXPIRE_DAYS = 365

# Input sanitization patterns
SQL_INJECTION_PATTERNS = [
    r"(?i)(union\s+select)",
    r"(?i)(drop\s+table)",
    r"(?i)(delete\s+from)",
    r"(?i)(insert\s+into)",
    r"(?i)(update\s+.*\s+set)",
    r"(?i)(exec\s*\()",
    r"(?i)(script\s*>)",
    r"(?i)(onerror\s*=)",
    r"(?i)(onload\s*=)",
]

XSS_PATTERNS = [
    r"<script[^>]*>.*?</script>",
    r"javascript:",
    r"on\w+\s*=",
    r"<iframe",
    r"<object",
    r"<embed",
]


class SecurityConfig(BaseModel):
    """Security configuration."""
    max_request_size: int = MAX_REQUEST_SIZE
    max_rag_request_size: int = MAX_RAG_REQUEST_SIZE
    api_key_warn_days: int = API_KEY_WARN_DAYS
    api_key_expire_days: int = API_KEY_EXPIRE_DAYS
    cors_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))
    enable_sql_protection: bool = True
    enable_xss_protection: bool = True


class SecurityMiddleware(BaseHTTPMiddleware):
    """Security middleware for request validation."""

    def __init__(self, app, config: SecurityConfig | None = None):
        super().__init__(app)
        self.config = config or SecurityConfig()
        self._sql_patterns = [re.compile(p) for p in SQL_INJECTION_PATTERNS]
        self._xss_patterns = [re.compile(p) for p in XSS_PATTERNS]

    async def dispatch(self, request: Request, call_next):
        # Check request size
        content_length = request.headers.get("content-length")
        if content_length:
            size = int(content_length)
            max_size = (
                self.config.max_rag_request_size
                if "/rag" in request.url.path
                else self.config.max_request_size
            )
            if size > max_size:
                return Response(
                    content='{"detail": "Request too large"}',
                    status_code=413,
                    media_type="application/json",
                )

        # Check for SQL injection in query params
        if self.config.enable_sql_protection:
            for param_name, param_value in request.query_params.items():
                if self._check_sql_injection(param_value):
                    log.warning("SQL injection attempt in query param: %s=%s", param_name, param_value[:100])
                    raise HTTPException(status_code=400, detail="Invalid query parameter")

        # Check for XSS in headers
        if self.config.enable_xss_protection:
            for header_name, header_value in request.headers.items():
                if self._check_xss(header_value):
                    log.warning("XSS attempt in header: %s", header_name)
                    raise HTTPException(status_code=400, detail="Invalid header")

        # Process request
        response = await call_next(request)

        # One authoritative header set (core#17).  This block used to carry
        # its own CSP (style-src 'self' 'unsafe-inline') that disagreed
        # with SECURITY_HEADERS (default-src 'self'), so the policy would
        # have differed by code path and been untestable.  Both
        # implementations now read the same dict.  Only
        # SecurityHeadersMiddleware is wired today (zolai/api/server.py),
        # but this path must never reintroduce a second policy if it is
        # ever wired.
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value

        return response

    def _check_sql_injection(self, value: str) -> bool:
        """Check for SQL injection patterns."""
        for pattern in self._sql_patterns:
            if pattern.search(value):
                return True
        return False

    def _check_xss(self, value: str) -> bool:
        """Check for XSS patterns."""
        for pattern in self._xss_patterns:
            if pattern.search(value):
                return True
        return False


def validate_api_key_age(
    created_at: str,
    warn_days: int = API_KEY_WARN_DAYS,
    expire_days: int = API_KEY_EXPIRE_DAYS,
) -> tuple[bool, str | None]:
    """Validate API key age.

    Returns:
        (is_valid, warning_message)
    """
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return True, None  # Can't parse, assume valid

    now = datetime.now(timezone.utc)
    age = now - created

    if age > timedelta(days=expire_days):
        return False, f"API key expired (age: {age.days} days, max: {expire_days} days)"

    if age > timedelta(days=warn_days):
        return True, f"API key will expire in {expire_days - age.days} days (age: {age.days} days)"

    return True, None


class SanitizedInput(BaseModel):
    """Base model with input sanitization."""

    @field_validator("*", mode="before")
    @classmethod
    def sanitize_strings(cls, v):
        if isinstance(v, str):
            # Remove null bytes
            v = v.replace("\x00", "")
            # Limit length
            if len(v) > 10000:
                raise ValueError("String too long")
        return v


def create_cors_middleware(origins: list[str] | None = None) -> CORSMiddleware:
    """Create restrictive CORS middleware."""
    return CORSMiddleware(**cors_kwargs(origins))


#: Trusted browser origins — the only three deployed surfaces that call this
#: API, plus the two local dev origins Vite actually binds to (host ``0.0.0.0``
#: is reachable as both ``localhost`` and ``127.0.0.1``).  Nothing else is
#: trusted, so the list stays short and auditable.
#:
#: ``ZOLAI_CORS_ORIGINS`` (comma-separated) overrides it wholesale; there is no
#: merge.  ``allow_credentials=True`` REQUIRES an explicit allowlist — a
#: wildcard makes Starlette reflect **any** ``Origin`` (including ``null``)
#: with credentials, which is the core#1 defect this list exists to prevent.
#:
#: The request ``Origin`` is never reflected unless it matches an entry here:
#: Starlette's ``CORSMiddleware`` echoes the caller's origin only for allowlist
#: hits, so an unknown origin and ``Origin: null`` get **no**
#: ``Access-Control-Allow-Origin`` — which is the only header the browser
#: consults, so the response stays unreadable to that page.  (Starlette does
#: still emit the static ``Allow-Credentials``/``Expose-Headers`` fields on a
#: non-CORS request; they are inert without ``Allow-Origin``.)
DEFAULT_CORS_ORIGINS: list[str] = [
    "https://zolai.space",
    "https://studio.zolai.space",
    "https://journey.zolai.space",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

#: Exact contract — a guard against the allowlist silently growing or drifting.
#: ``tests/test_cors_allowlist.py`` asserts :data:`DEFAULT_CORS_ORIGINS` equals
#: this tuple, so adding an origin is a deliberate two-line change.
CORS_ALLOWED_ORIGINS: tuple[str, ...] = (
    "https://zolai.space",
    "https://studio.zolai.space",
    "https://journey.zolai.space",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)

#: Explicit method/header allow-lists (no ``*``) so credentials stay safe.
CORS_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_HEADERS: list[str] = [
    "Authorization",
    "Content-Type",
    "X-API-Key",
    "X-Request-ID",
    # MCP streamable-HTTP binding: browsers (studio.zolai.space) send these on
    # tools/call and every post-handshake request, so preflight must allow them.
    "Mcp-Method",
    "Mcp-Name",
    "MCP-Protocol-Version",
]
CORS_EXPOSE_HEADERS: list[str] = [
    "X-Request-ID",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
]
CORS_MAX_AGE = 86400


def get_cors_origins() -> list[str]:
    """Resolve the CORS allowlist, honouring ``ZOLAI_CORS_ORIGINS``.

    The env var is a comma-separated list; blank entries are dropped.  When it
    is unset/empty the trusted :data:`DEFAULT_CORS_ORIGINS` are used.
    """
    raw = os.environ.get("ZOLAI_CORS_ORIGINS", "")
    if raw.strip():
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
        if origins:
            return origins
    return list(DEFAULT_CORS_ORIGINS)


def cors_kwargs(origins: list[str] | None = None) -> dict:
    """Build ``CORSMiddleware`` kwargs with an explicit origin allowlist."""
    return {
        "allow_origins": list(origins or DEFAULT_CORS_ORIGINS),
        "allow_credentials": True,
        "allow_methods": list(CORS_METHODS),
        "allow_headers": list(CORS_HEADERS),
        "expose_headers": list(CORS_EXPOSE_HEADERS),
        "max_age": CORS_MAX_AGE,
    }


#: Hardening response headers applied to every API response.
#:
#: The **single** source for both header code paths in this module
#: (SecurityMiddleware and SecurityHeadersMiddleware) — never inline
#: literals.  core#17 also asked for HSTS and a deny-all
#: Permissions-Policy, not only the four classic flags.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "1; mode=block",
    "Content-Security-Policy": "default-src 'self'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware:
    """Attach :data:`SECURITY_HEADERS` to every HTTP response — headers only.

    Deliberately does **no** request filtering (unlike
    :class:`SecurityMiddleware`): a JSON API must accept large ``/rag`` bodies
    and arbitrary query strings without a false-positive ``400``.  Implemented
    as raw ASGI so streaming responses are never buffered or rewritten.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)
