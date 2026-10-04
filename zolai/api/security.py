"""Security Hardening for Phase 8 Production.

API key rotation, input sanitization, CORS, request validation.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
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
    cors_origins: list[str] = Field(default_factory=lambda: ["https://zolai.space", "https://api.zolai.space"])
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

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'"
        )

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
    allowed_origins = origins or ["https://zolai.space", "https://api.zolai.space"]
    return CORSMiddleware(
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
        max_age=86400,
    )
