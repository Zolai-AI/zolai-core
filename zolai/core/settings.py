"""Settings management for Zolai."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "provider": "ollama",
    "model": "qwen3-coder:480b-cloud",
    "language": "en",
    "theme": "dark",
    "auto_zvs_correction": True,
    "spaced_repetition_enabled": True,
    "daily_goal_words": 10,
    "audio_enabled": False,
    "offline_mode": False,
}


class SettingsManager:
    """Manage application settings.

    Settings are stored in data/settings.json.
    """

    def __init__(self) -> None:
        self._settings_path = config.paths.data / "settings.json"
        self._settings: dict[str, Any] | None = None

    def _ensure_settings_file(self) -> None:
        """Create settings file if it doesn't exist."""
        if not self._settings_path.exists():
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._save(DEFAULT_SETTINGS)

    def _load(self) -> dict[str, Any]:
        """Load settings from file."""
        self._ensure_settings_file()

        try:
            with open(self._settings_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load settings: %s", e)
            return DEFAULT_SETTINGS.copy()

    def _save(self, settings: dict[str, Any]) -> None:
        """Save settings to file."""
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save settings: %s", e)

    def get_all(self) -> dict[str, Any]:
        """Get all settings."""
        if self._settings is None:
            self._settings = self._load()
        return self._settings.copy()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a specific setting."""
        settings = self.get_all()
        return settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a specific setting."""
        settings = self.get_all()
        settings[key] = value
        self._save(settings)
        self._settings = settings

    def update(self, updates: dict[str, Any]) -> None:
        """Update multiple settings."""
        settings = self.get_all()
        settings.update(updates)
        self._save(settings)
        self._settings = settings

    def reset(self) -> None:
        """Reset all settings to defaults."""
        self._save(DEFAULT_SETTINGS.copy())
        self._settings = None

    def get_provider_settings(self) -> dict[str, Any]:
        """Get provider-related settings."""
        settings = self.get_all()
        return {
            "provider": settings.get("provider", "ollama"),
            "model": settings.get("model", "qwen3-coder:480b-cloud"),
            "offline_mode": settings.get("offline_mode", False),
        }

    def get_learning_settings(self) -> dict[str, Any]:
        """Get learning-related settings."""
        settings = self.get_all()
        return {
            "spaced_repetition_enabled": settings.get("spaced_repetition_enabled", True),
            "daily_goal_words": settings.get("daily_goal_words", 10),
            "auto_zvs_correction": settings.get("auto_zvs_correction", True),
        }

    def get_ui_settings(self) -> dict[str, Any]:
        """Get UI-related settings."""
        settings = self.get_all()
        return {
            "language": settings.get("language", "en"),
            "theme": settings.get("theme", "dark"),
            "audio_enabled": settings.get("audio_enabled", False),
        }


# Global settings instance
_settings_manager: SettingsManager | None = None


def get_settings_manager() -> SettingsManager:
    """Get or create the global settings manager."""
    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    return _settings_manager
