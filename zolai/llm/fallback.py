"""Fallback chain for LLM providers with rule-based fallback."""

from __future__ import annotations

import logging
from typing import Any

from .providers.base import LLMProvider, get_provider_registry

logger = logging.getLogger(__name__)


class RuleBasedFallback(LLMProvider):
    """Rule-based fallback using SQLite dictionary data.

    Provides basic translation and grammar checking without AI.
    """

    def __init__(self) -> None:
        super().__init__(name="rule_based", priority=999)

    def _check_availability(self) -> bool:
        """Always available."""
        return True

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate response using rule-based lookup."""
        from ..offline.rule_engine import RuleEngine

        engine = RuleEngine()

        # Get the last user message
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        if not user_message:
            return "No input provided."

        # Try dictionary lookup
        result = engine.translate(user_message)
        if result:
            return result

        return f"Rule-based engine cannot process: {user_message}"

    def list_models(self) -> list[str]:
        """No models for rule-based."""
        return []


class FallbackChain:
    """Tries providers in priority order, falls back to rule-based.

    Usage::

        chain = FallbackChain()
        response = await chain.generate(
            [{"role": "user", "content": "Hello"}],
            preferred_provider="ollama",
        )
    """

    def __init__(self) -> None:
        self._registry = get_provider_registry()
        self._rule_based = RuleBasedFallback()

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        preferred_provider: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate with automatic fallback.

        Returns:
            Dict with 'response', 'provider', 'model', 'fallback_used'.
        """
        providers = self._registry.get_available_providers()

        # If preferred provider specified, try it first
        if preferred_provider:
            preferred = self._registry.get_provider(preferred_provider)
            if preferred and preferred.is_available:
                providers = [preferred] + [p for p in providers if p.name != preferred_provider]

        # Try each provider
        for provider in providers:
            try:
                response = await provider.generate(messages, model=model, **kwargs)
                return {
                    "response": response,
                    "provider": provider.name,
                    "model": model or provider.list_models()[0] if provider.list_models() else "default",
                    "fallback_used": False,
                }
            except Exception as e:
                logger.warning("Provider %s failed: %s", provider.name, e)
                continue

        # Fall back to rule-based
        logger.info("All providers failed, using rule-based fallback")
        try:
            response = await self._rule_based.generate(messages, model=model, **kwargs)
            return {
                "response": response,
                "provider": "rule_based",
                "model": "rule_based",
                "fallback_used": True,
            }
        except Exception as e:
            logger.error("Rule-based fallback failed: %s", e)
            return {
                "response": "All providers and fallback failed. Please check your configuration.",
                "provider": "none",
                "model": "none",
                "fallback_used": True,
            }


# Global fallback chain instance
_fallback_chain: FallbackChain | None = None


def get_fallback_chain() -> FallbackChain:
    """Get or create the global fallback chain."""
    global _fallback_chain
    if _fallback_chain is None:
        _fallback_chain = FallbackChain()
    return _fallback_chain
