"""Zolai Toolkit configuration and paths — env-aware."""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Toolkit root = the zolai project directory
# config.py is at: zolai/zolai/config.py
#   .parent      = zolai/
#   .parent x2   = zolai-project/
_TOOLKIT_ROOT = Path(__file__).resolve().parent.parent  # zolai/config.py -> zolai/


def _env_path(key: str, default: Path) -> Path:
    val = os.environ.get(key, "").strip()
    return Path(val) if val else default


def _env_int(key: str, default: int):
    """Factory for an int field from env — invalid values fall back to default."""

    def _factory() -> int:
        raw = os.environ.get(key, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    return _factory


def _data_root() -> Path:
    return _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT / "data")


@dataclass
class Paths:
    """Centralized path configuration — all overridable via env vars."""

    root: Path = field(default_factory=lambda: _env_path("ZOLAI_ROOT", _TOOLKIT_ROOT))

    # Use global data root as primary
    data: Path = field(
        default_factory=lambda: _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data")
    )

    data_raw: Path = field(
        default_factory=lambda: _env_path(
            "ZOLAI_DATA_RAW", _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data") / "raw"
        )
    )
    data_cleaned: Path = field(
        default_factory=lambda: _env_path(
            "ZOLAI_DATA_CLEANED",
            _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data") / "master",
        )
    )
    data_training: Path = field(
        default_factory=lambda: _env_path(
            "ZOLAI_DATA_TRAINING",
            _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data") / "training",
        )
    )
    data_knowledge: Path = field(
        default_factory=lambda: _env_path(
            "ZOLAI_DATA_KNOWLEDGE",
            _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data") / "knowledge",
        )
    )
    data_archive: Path = field(
        default_factory=lambda: _env_path(
            "ZOLAI_DATA_ARCHIVE",
            _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data") / "archive",
        )
    )
    db: Path = field(
        default_factory=lambda: _env_path(
            "ZOLAI_DB_PATH",
            _env_path("ZOLAI_DATA_ROOT", _TOOLKIT_ROOT.parent / "data") / "zolai.db",
        )
    )

    # External input folder — used by ingest scripts, NOT overriding toolkit data
    external_data: Path = field(
        default_factory=lambda: _env_path("ZOLAI_EXTERNAL_DATA", _TOOLKIT_ROOT.parent / "data")
    )

    @property
    def zolai_db(self) -> Path:
        """Canonical DB path — data/zolai.db (env-overridable)."""
        return self.data / "zolai.db"

    @property
    def frontend(self) -> Path:
        """Path to the desktop frontend directory (zolai-tauri/frontend)."""
        return _env_path(
            "ZOLAI_FRONTEND_DIR",
            self.root.parent / "zolai-tauri" / "frontend",
        )

    @property
    def datasets_scripts(self) -> Path:
        """Path to the zolai-datasets scripts directory.

        The real desktop tool scripts live in `zolai-datasets/scripts/{my,bible,training,...}`
        rather than inside this repo, so resolution crosses into the sibling repo.
        """
        return _env_path(
            "ZOLAI_DATASETS_SCRIPTS",
            self.root.parent / "zolai-datasets" / "scripts",
        )

    def ensure_dirs(self):
        for p in [self.data_raw, self.data_cleaned, self.data_training, self.data_knowledge, self.data_archive]:
            p.mkdir(parents=True, exist_ok=True)


@dataclass
class CrawlerConfig:
    """Crawler settings."""

    max_depth: int = int(os.environ.get("ZOLAI_CRAWLER_MAX_DEPTH", "2"))
    delay_seconds: float = float(os.environ.get("ZOLAI_CRAWLER_DELAY", "2.0"))
    max_concurrent: int = int(os.environ.get("ZOLAI_CRAWLER_CONCURRENT", "5"))
    user_agent: str = "ZolaiToolkit/1.0 (Language Research)"
    target_mb: int = int(os.environ.get("ZOLAI_CRAWLER_TARGET_MB", "500"))


@dataclass
class CleanerConfig:
    """Data cleaning settings."""

    min_sentence_length: int = int(os.environ.get("ZOLAI_MIN_SENTENCE_LENGTH", "10"))
    max_sentence_length: int = int(os.environ.get("ZOLAI_MAX_SENTENCE_LENGTH", "5000"))
    min_zolai_density: float = float(os.environ.get("ZOLAI_MIN_ZOLAI_DENSITY", "0.3"))
    dedup_similarity: float = float(os.environ.get("ZOLAI_DEDUP_SIMILARITY", "0.85"))


@dataclass
class AppConfig:
    """Top-level configuration."""

    paths: Paths = field(default_factory=Paths)
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    cleaner: CleanerConfig = field(default_factory=CleanerConfig)
    api_host: str = os.environ.get("ZOLAI_API_HOST", "127.0.0.1")
    api_port: int = int(os.environ.get("ZOLAI_API_PORT", "8000"))
    # API-key auth on /api/v1 (ADR-014): warn (default, dual-accept) | enforce | off.
    # Read live via zolai.api.auth.api_auth_mode()/api_rate_limit_rpm() so ops can
    # flip the window without a restart; these fields are the import-time defaults.
    api_auth_mode: str = field(
        default_factory=lambda: os.environ.get("ZOLAI_API_AUTH", "warn")
    )
    api_rate_limit_rpm: int = field(default_factory=_env_int("ZOLAI_API_RATE_LIMIT_RPM", 60))
    gui_theme: str = os.environ.get("ZOLAI_GUI_THEME", "dark")
    monthly_budget_usd: float = field(
        default_factory=lambda: float(os.getenv("ZOLAI_MONTHLY_BUDGET_USD", "50.0"))
    )


# Load .env if present
try:
    from dotenv import load_dotenv

    _env_file = _TOOLKIT_ROOT / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)  # don't override already-set env vars
except ImportError:
    pass

# Global config instance
config = AppConfig()
