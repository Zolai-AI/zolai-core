"""OpenRouter API provider."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .base import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "meta-llama/llama-3.1-8b-instruct:free"


class OpenRouterProvider(LLMProvider):
    """OpenRouter API provider.

    Uses OpenAI-compatible endpoint at openrouter.ai.
    Supports free models via OPENROUTER_API_KEY.
    """

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        priority: int = 3,
    ) -> None:
        super().__init__(name="openrouter", priority=priority)
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.endpoint = endpoint or DEFAULT_ENDPOINT

    def _check_availability(self) -> bool:
        """Check if OpenRouter API key is available."""
        return bool(self._api_key)

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate using OpenRouter API."""
        model = model or DEFAULT_MODEL

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://zolai.space",
            "X-Title": "Zolai Language Preservation",
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2048),
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.endpoint}/chat/completions",
                headers=headers,
                json=payload,
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def list_models(self) -> list[str]:
        """List available OpenRouter models."""
        return [
            "meta-llama/llama-3.1-8b-instruct:free",
            "meta-llama/llama-3.1-70b-instruct:free",
            "google/gemma-2-9b-it:free",
            "mistralai/mistral-7b-instruct:free",
            "qwen/qwen-2-7b-instruct:free",
        ]
