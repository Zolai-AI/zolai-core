"""Observation engine — the seven §36 capabilities behind the ``observations`` layer.

Layer order (plan §18): raw → staging → normalized → **observations** →
hypotheses → canonical.  This package owns the observation zone only:
it reads the canonical source tables, never mutates them, and writes the three
rebuildable Phase 2 tables (``observations``, ``word_observation_stats``,
``attestation_index``).

Deterministic + offline by construction: rule mode, no network, no LLM
(§25).  Entry point: :func:`build_observations`.
"""

from __future__ import annotations

from .contexts import ContextCollector
from .cooccurrence import PairCollector
from .normalize import normalize_token, normalize_tokens
from .pipeline import (
    OBSERVATION_EXTRACTOR,
    PIPELINE_VERSION,
    ObservationPipeline,
    build_observations,
)
from .sentences import SENTENCE_SOURCES, Sentence, SentenceSource, iter_sentences
from .stats import StatsAggregator
from .store import ObservationStore, ensure_tables
from .tokenize import tokenize_sentence

__all__ = [
    "OBSERVATION_EXTRACTOR",
    "PIPELINE_VERSION",
    "SENTENCE_SOURCES",
    "ContextCollector",
    "ObservationPipeline",
    "ObservationStore",
    "PairCollector",
    "Sentence",
    "SentenceSource",
    "StatsAggregator",
    "build_observations",
    "ensure_tables",
    "iter_sentences",
    "normalize_token",
    "normalize_tokens",
    "tokenize_sentence",
]
