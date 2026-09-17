"""Ollama local LLM provider."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .base import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-coder:480b-cloud"


class OllamaProvider(LLMProvider):
    """Local Ollama LLM provider.

    Connects to a local Ollama instance for inference.
    No API key required.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        priority: int = 1,
    ) -> None:
        super().__init__(name="ollama", priority=priority)
        self.endpoint = endpoint or os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)

    def _check_availability(self) -> bool:
        """Check if Ollama is running."""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.endpoint}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate using Ollama API."""
        model = model or DEFAULT_MODEL

        # Convert messages to prompt format
        prompt = self._build_prompt(messages)

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.endpoint}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": kwargs.get("temperature", 0.7),
                        "num_predict": kwargs.get("max_tokens", 2048),
                    },
                },
            )
            data = resp.json()
            return data.get("response", "").strip()

    def list_models(self) -> list[str]:
        """List available Ollama models."""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.endpoint}/api/tags")
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return [DEFAULT_MODEL]

    def _build_prompt(self, messages: list[dict[str, str]]) -> str:
        """Build prompt from messages."""
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"{role}\n{content}")
        return "\n\n".join(parts)
