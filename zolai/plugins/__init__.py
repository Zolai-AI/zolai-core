"""Plugin system for Zolai Desktop.

Provides an abstract Plugin base class and a registry that discovers
and loads plugins at startup. Plugins can extend the API with new
capabilities (e.g., Gemini WebAPI).

Usage::

    from zolai.plugins import get_plugin_registry
    registry = get_plugin_registry()
    registry.discover()  # auto-discovers plugins in this package
    for plugin in registry.list_plugins():
        print(f"{plugin.name}: available={plugin.is_available()}")
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class Plugin(ABC):
    """Abstract base class for Zolai plugins.

    Subclasses must implement all abstract methods. The registry
    will instantiate each plugin and call init() on startup.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g., 'gemini-webapi')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this plugin provides."""
        ...

    @abstractmethod
    def init(self) -> None:
        """Initialize the plugin. Called once at startup."""
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up resources. Called on shutdown."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the plugin's dependencies are met."""
        ...

    def get_capabilities(self) -> list[str]:
        """Return list of capability strings this plugin provides."""
        return []

    def to_dict(self) -> dict[str, Any]:
        """Serialize plugin info for API responses."""
        return {
            "name": self.name,
            "description": self.description,
            "available": self.is_available(),
            "capabilities": self.get_capabilities(),
        }


class PluginRegistry:
    """Registry that discovers, loads, and manages plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._initialized = False

    def register(self, plugin: Plugin) -> None:
        """Manually register a plugin instance."""
        self._plugins[plugin.name] = plugin
        logger.info("Registered plugin: %s", plugin.name)

    def discover(self) -> None:
        """Auto-discover plugins in the zolai.plugins package.

        Looks for modules with a `plugin` attribute that is a Plugin subclass,
        or modules with a `create_plugin()` factory function.
        """
        try:
            import zolai.plugins as pkg
        except ImportError:
            logger.warning("Cannot import zolai.plugins package")
            return

        import pkgutil

        for _importer, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
            if modname.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f"zolai.plugins.{modname}")
            except Exception as e:
                logger.warning("Failed to import plugin module %s: %s", modname, e)
                continue

            # Look for a plugin attribute or create_plugin factory
            plugin_obj = getattr(mod, "plugin", None)
            if plugin_obj is None and hasattr(mod, "create_plugin"):
                try:
                    plugin_obj = mod.create_plugin()
                except Exception as e:
                    logger.warning("create_plugin() failed in %s: %s", modname, e)
                    continue

            if isinstance(plugin_obj, Plugin):
                self.register(plugin_obj)
            else:
                logger.debug("Module %s has no Plugin instance", modname)

    def init_all(self) -> None:
        """Initialize all registered plugins."""
        for name, plugin in self._plugins.items():
            try:
                plugin.init()
                logger.info("Initialized plugin: %s", name)
            except Exception as e:
                logger.error("Failed to initialize plugin %s: %s", name, e)
        self._initialized = True

    def cleanup_all(self) -> None:
        """Clean up all registered plugins."""
        for name, plugin in self._plugins.items():
            try:
                plugin.cleanup()
                logger.info("Cleaned up plugin: %s", name)
            except Exception as e:
                logger.error("Failed to cleanup plugin %s: %s", name, e)

    def list_plugins(self) -> list[Plugin]:
        """Return all registered plugins."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> Plugin | None:
        """Get a plugin by name."""
        return self._plugins.get(name)

    @property
    def is_initialized(self) -> bool:
        return self._initialized


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    """Get or create the global plugin registry."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry
