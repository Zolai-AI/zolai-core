# Zolai Toolkit — Unified language data pipeline
from __future__ import annotations

__version__ = "2.0.0"

# Core submodules
from . import (
    cli,  # noqa: F401
    config,  # noqa: F401
    zvs,  # noqa: F401
)

# NLP tools
from .classifier import ZolaiClassifier, get_classifier  # noqa: F401
from .dependency import ZolaiDependency, get_dependency  # noqa: F401
from .mt import ZolaiMT, get_mt  # noqa: F401
from .ner import ZolaiNER, get_ner  # noqa: F401
from .qa import ZolaiQA, get_qa  # noqa: F401
from .summarizer import ZolaiSummarizer, get_summarizer  # noqa: F401
from .zvs import validate  # noqa: F401
