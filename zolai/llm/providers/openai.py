"""OpenAI API provider."""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(LLMProvider):
    """OpenAI API provider.

    Uses the OpenAI SDK for GPT model access.
    Requires OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: str | None = None,
        priority: int = 4,
    ) -> None:
        super().__init__(name="openai", priority=priority)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client: Any = None

    def _check_availability(self) -> bool:
        """Check if OpenAI API key is available."""
        return bool(self._api_key)

    def _ensure_client(self) -> Any:
        """Lazily create the OpenAI client."""
        if self._client is not None:
            return self._client

        try:
            from openai import AsyncOpenAI  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "openai is required: pip install openai"
            )

        if not self._api_key:
            raise RuntimeError("No OpenAI API key available. Set OPENAI_API_KEY env var.")

        self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate using OpenAI API."""
        model = model or DEFAULT_MODEL

        client = self._ensure_client()
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        return response.choices[0].message.content or ""

    def list_models(self) -> list[str]:
        """List available OpenAI models."""
        return [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ]
