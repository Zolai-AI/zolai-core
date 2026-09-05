# Zolai Toolkit — Unified language data pipeline
from __future__ import annotations

__version__ = "2.0.0"

# Core submodules
from . import (
    cli,  # noqa: F401
    config,  # noqa: F401
    zvs,  # noqa: F401
)
from .zvs import validate  # noqa: F401
