"""Gemini WebAPI provider (cookie-based)."""

from __future__ import annotations

import logging
from typing import Any

from .base import LLMProvider

logger = logging.getLogger(__name__)


class WebAPIProvider(LLMProvider):
    """Gemini WebAPI provider using browser cookies.

    Uses gemini-webapi library with browser cookies for authentication.
    Falls back to API key if cookies are unavailable.
    """

    def __init__(self, priority: int = 5) -> None:
        super().__init__(name="webapi", priority=priority)
        self._client: Any = None

    def _check_availability(self) -> bool:
        """Check if gemini-webapi is available and cookies exist."""
        try:
            from ..gemini.cookies import get_gemini_api_key, get_gemini_cookies
            return bool(get_gemini_cookies() or get_gemini_api_key())
        except Exception:
            return False

    def _ensure_client(self) -> Any:
        """Lazily create the WebAPI client."""
        if self._client is not None:
            return self._client

        try:
            import asyncio

            from ..gemini.cookies import get_gemini_client
            # Note: This is a sync wrapper; actual usage should use async
            self._client = get_gemini_client
            return self._client
        except ImportError:
            raise ImportError(
                "gemini-webapi is required: pip install gemini-webapi"
            )

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate using Gemini WebAPI."""
        try:
            from ..gemini.cookies import get_gemini_client
            client = await get_gemini_client()

            # Build prompt from messages
            prompt = self._build_prompt(messages)

            # Use gemini-webapi to generate
            response = await client.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error("WebAPI generation failed: %s", e)
            raise

    def list_models(self) -> list[str]:
        """List available WebAPI models."""
        return ["gemini-2.5-flash", "gemini-2.5-pro"]

    def _build_prompt(self, messages: list[dict[str, str]]) -> str:
        """Build prompt from messages."""
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"{role}\n{content}")
        return "\n\n".join(parts)
