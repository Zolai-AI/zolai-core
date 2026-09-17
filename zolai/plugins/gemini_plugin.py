"""Gemini WebAPI plugin for Zolai Desktop.

Provides access to Gemini via browser cookies (free, no API key needed)
or falls back to API key authentication. This plugin enables the desktop
app to use Gemini without requiring a paid API key.

Usage:
    The plugin is auto-discovered by the plugin registry.
    It exposes a /plugins/gemini-webapi/chat endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from . import Plugin

logger = logging.getLogger(__name__)


class GeminiWebAPIPlugin(Plugin):
    """Gemini WebAPI plugin using browser cookies for authentication."""

    def __init__(self) -> None:
        self._cookies_available = False
        self._api_key_available = False
        self._client_available = False

    @property
    def name(self) -> str:
        return "gemini-webapi"

    @property
    def description(self) -> str:
        return "Access Gemini via browser cookies (free) or API key fallback"

    def init(self) -> None:
        """Verify dependencies and warm up cookie cache."""
        self._cookies_available = False
        self._api_key_available = False

        # Check browser-cookie3
        try:
            import browser_cookie3  # noqa: F401
            self._cookies_available = True
        except ImportError:
            logger.info(
                "browser-cookie3 not installed — cookie auth unavailable. "
                "Install with: pip install browser-cookie3"
            )

        # Check gemini-webapi
        try:
            import gemini_webapi  # noqa: F401
            self._client_available = True
        except ImportError:
            self._client_available = False
            logger.info(
                "gemini-webapi not installed — WebAPI unavailable. "
                "Install with: pip install gemini-webapi"
            )

        # Check API key
        import os
        if os.environ.get("GEMINI_API_KEY"):
            self._api_key_available = True

        logger.info(
            "Gemini WebAPI plugin initialized: "
            "cookies=%s, api_key=%s, client=%s",
            self._cookies_available,
            self._api_key_available,
            self._client_available,
        )

    def cleanup(self) -> None:
        """No persistent resources to clean up."""
        pass

    def is_available(self) -> bool:
        """Check if at least one auth method works."""
        if not self._client_available:
            return False
        return self._cookies_available or self._api_key_available

    def get_capabilities(self) -> list[str]:
        caps = []
        if self._cookies_available:
            caps.append("cookie-auth")
        if self._api_key_available:
            caps.append("api-key-auth")
        if self._client_available:
            caps.append("chat")
        return caps

    async def chat(
        self,
        message: str,
        model: str = "gemini-2.0-flash",
        system_prompt: str = "",
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Send a chat message to Gemini via WebAPI.

        Returns:
            Dict with 'response', 'model', 'auth_method' keys.
        """
        from ..llm.gemini.cookies import get_gemini_client

        try:
            client = await get_gemini_client()

            # Build messages
            contents = []
            if system_prompt:
                contents.append({"role": "user", "parts": [system_prompt]})
                contents.append({"role": "model", "parts": ["Understood."]})
            contents.append({"role": "user", "parts": [message]})

            # Send via gemini-webapi
            response = await client.generate_content(
                message,
                model=model,
            )

            auth_method = "cookies" if self._cookies_available else "api_key"
            return {
                "response": response.text if hasattr(response, "text") else str(response),
                "model": model,
                "auth_method": auth_method,
                "success": True,
            }
        except Exception as e:
            logger.error("Gemini WebAPI chat failed: %s", e)
            return {
                "response": f"Gemini error: {e}",
                "model": model,
                "auth_method": "none",
                "success": False,
                "error": str(e),
            }


# ---------------------------------------------------------------------------
# Module-level plugin instance for auto-discovery
# ---------------------------------------------------------------------------

plugin = GeminiWebAPIPlugin()


def create_plugin() -> GeminiWebAPIPlugin:
    """Factory for plugin registry."""
    return GeminiWebAPIPlugin()
