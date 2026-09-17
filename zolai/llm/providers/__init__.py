"""Multi-provider LLM abstraction layer for Zolai."""

from .base import LLMProvider, ProviderRegistry, get_provider_registry
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .webapi import WebAPIProvider

__all__ = [
    "LLMProvider",
    "ProviderRegistry",
    "get_provider_registry",
    "GeminiProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "WebAPIProvider",
]


def _register_default_providers() -> None:
    """Register all default providers with the global registry."""
    registry = get_provider_registry()
    providers = [
        OllamaProvider(),
        GeminiProvider(),
        OpenAIProvider(),
        OpenRouterProvider(),
        WebAPIProvider(),
    ]
    for provider in providers:
        if provider.name not in registry:
            registry.register(provider)


# Auto-register providers on import
_register_default_providers()
