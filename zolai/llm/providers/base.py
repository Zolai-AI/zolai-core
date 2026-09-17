"""Abstract base class for LLM providers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Metadata about a provider."""

    name: str
    priority: int
    is_available: bool
    models: list[str] = field(default_factory=list)
    description: str = ""


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement generate() and list_models().
    Providers are tried in priority order by the FallbackChain.
    """

    def __init__(self, name: str, priority: int = 100) -> None:
        self.name = name
        self.priority = priority
        self._is_available: bool | None = None

    @property
    def is_available(self) -> bool:
        """Check if provider is available (cached after first check)."""
        if self._is_available is None:
            self._is_available = self._check_availability()
        return self._is_available

    def _check_availability(self) -> bool:
        """Override to implement availability check."""
        return True

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a response from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Optional model override.
            **kwargs: Additional provider-specific parameters.

        Returns:
            The generated text response.
        """
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available models for this provider."""
        ...

    def get_info(self) -> ProviderInfo:
        """Get provider metadata."""
        return ProviderInfo(
            name=self.name,
            priority=self.priority,
            is_available=self.is_available,
            models=self.list_models(),
            description=self.__class__.__doc__ or "",
        )


class ProviderRegistry:
    """Registry for LLM providers.

    Usage::

        registry = ProviderRegistry()
        registry.register(OllamaProvider())
        registry.register(GeminiProvider())

        provider = registry.get_provider("ollama")
        response = await provider.generate([{"role": "user", "content": "Hello"}])
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, provider: LLMProvider) -> None:
        """Register a provider."""
        self._providers[provider.name] = provider
        logger.info("Registered provider: %s (priority=%d)", provider.name, provider.priority)

    def get_provider(self, name: str) -> LLMProvider | None:
        """Get a provider by name."""
        return self._providers.get(name)

    def list_providers(self) -> list[ProviderInfo]:
        """List all registered providers with their info."""
        return sorted(
            [p.get_info() for p in self._providers.values()],
            key=lambda x: x.priority,
        )

    def get_available_providers(self) -> list[LLMProvider]:
        """Get providers sorted by priority that are available."""
        return sorted(
            [p for p in self._providers.values() if p.is_available],
            key=lambda x: x.priority,
        )

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: str) -> bool:
        return name in self._providers


# Global registry instance
_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """Get or create the global provider registry."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
