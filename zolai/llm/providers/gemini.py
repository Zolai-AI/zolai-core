"""Gemini API provider."""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiProvider(LLMProvider):
    """Google Gemini API provider.

    Uses the google-genai SDK for Gemini API access.
    Requires GEMINI_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: str | None = None,
        priority: int = 2,
    ) -> None:
        super().__init__(name="gemini", priority=priority)
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client: Any = None

    def _check_availability(self) -> bool:
        """Check if Gemini API key is available."""
        return bool(self._api_key)

    def _ensure_client(self) -> Any:
        """Lazily create the Gemini client."""
        if self._client is not None:
            return self._client

        try:
            from google import genai  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "google-genai is required: pip install 'zolai[gemini]'"
            )

        if not self._api_key:
            raise RuntimeError("No Gemini API key available. Set GEMINI_API_KEY env var.")

        self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate using Gemini API."""
        model = model or DEFAULT_MODEL

        # Build prompt from messages
        prompt = self._build_prompt(messages)

        client = self._ensure_client()
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
        )
        return response.text

    def list_models(self) -> list[str]:
        """List available Gemini models."""
        return [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]

    def _build_prompt(self, messages: list[dict[str, str]]) -> str:
        """Build prompt from messages."""
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"{role}\n{content}")
        return "\n\n".join(parts)
