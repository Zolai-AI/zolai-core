"""Observation build pipeline — capabilities 1-7 wired end to end (§18/§36).

    sources → sentences → tokenize → normalize → observations rows
            → stats / contexts / pairs → attestation hook → summary dict

Execution model (one connection, one transaction):

- observations are buffered and written in ``INSERT OR IGNORE`` chunks — the
  ``ux_obs_source_ref`` UNIQUE key makes a re-run a no-op for existing rows;
- co-occurrence pairs accumulate in a connection-scoped TEMP table, so memory
  stays flat on a full-corpus run;
- stats rows are upserted last (last-writer-wins for the recomputed surfaces,
  ``min``/``max`` merge for ``first_seen``/``last_seen``).

Rule mode only: no network, no LLM, no translation engine (plan §25).  The
attestation column is filled through an injected ``attestor`` callable —
default ``None`` keeps this module free of ``learning/`` imports; the CLI wires
the real word-attestation lookup.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine

from .contexts import DEFAULT_TOP_K, ContextCollector
from .cooccurrence import (
    COLLOCATION_TOP_K,
    MIN_FREQ,
    NEIGHBOR_TOP_K,
    PAIR_WINDOW,
    PairCollector,
)
from .normalize import normalize_tokens
from .sentences import available_sources, iter_sentences, select_sources
from .stats import StatsAggregator
from .store import ObservationStore, build_observation_row, build_stats_row
from .tokenize import tokenize_sentence

__all__ = [
    "OBSERVATION_EXTRACTOR",
    "PIPELINE_VERSION",
    "ObservationPipeline",
    "build_observations",
]

#: Version stamped on rows + the summary — bump when pipeline output changes.
PIPELINE_VERSION = "phase2-v1"

#: ``observations.extractor`` value — which tool produced the row.
OBSERVATION_EXTRACTOR = "observation-pipeline"

#: ``word -> JSON-able summary`` (wired to word attestation by the CLI).
Attestor = Callable[[str], Mapping[str, Any] | None]


class ObservationPipeline:
    """Deterministic, offline observation build over the declarative sources."""

    def __init__(
        self,
        engine: Engine,
        *,
        sources: Sequence[str] | None = None,
        limit: int | None = None,
        window: int = PAIR_WINDOW,
        context_top_k: int = DEFAULT_TOP_K,
        neighbor_top_k: int = NEIGHBOR_TOP_K,
        collocation_top_k: int = COLLOCATION_TOP_K,
        min_freq: int = MIN_FREQ,
        batch_size: int = 1000,
        attestor: Attestor | None = None,
    ) -> None:
        self.engine = engine
        self.sources = list(sources) if sources else None
        self.limit = limit
        self.window = window
        self.context_top_k = context_top_k
        self.neighbor_top_k = neighbor_top_k
        self.collocation_top_k = collocation_top_k
        self.min_freq = min_freq
        self.batch_size = max(1, batch_size)
        self.attestor = attestor
        self.store = ObservationStore(engine)

    # -- Build -------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        """Run one observation build and return the summary dict."""
        started_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")
        started = time.monotonic()

        migration = self.store.ensure_tables()
        if migration["errors"]:
            raise RuntimeError(f"observation DDL failed: {migration['errors']}")

        requested = select_sources(self.sources)
        available = {s.name for s in available_sources(self.engine)}
        selected = [s for s in requested if s.name in available]
        skipped_sources = [s.name for s in requested if s.name not in available]
        if not selected:
            raise ValueError(
                "no observation source tables available "
                f"(requested: {[s.name for s in requested]!r})"
            )

        # Reflect table metadata before the write transaction opens.
        self.store.warm()

        observation_batch: list[dict[str, Any]] = []
        observations = 0
        observations_inserted = 0
        sentences_seen = 0
        sentences_empty = 0
        seen_at = started_wall

        with self.engine.begin() as conn:
            contexts = ContextCollector(top_k=self.context_top_k)
            pairs = PairCollector(
                conn,
                window=self.window,
                min_freq=self.min_freq,
                neighbor_top_k=self.neighbor_top_k,
                collocation_top_k=self.collocation_top_k,
            )
            stats = StatsAggregator()

            for source in selected:
                for sentence in iter_sentences(conn, source, limit=self.limit):
                    raw_tokens = tokenize_sentence(sentence.text)
                    tokens, zvs_corrected = normalize_tokens(raw_tokens)
                    if not tokens:
                        sentences_empty += 1
                        continue
                    sentences_seen += 1
                    stats.observe(
                        tokens,
                        raw_tokens,
                        document_id=sentence.document_id,
                        source_id=sentence.source_id,
                    )
                    contexts.observe(tokens, sentence.text)
                    pairs.observe(tokens)
                    observation_batch.append(
                        build_observation_row(
                            sentence,
                            tokens,
                            extractor=OBSERVATION_EXTRACTOR,
                            pipeline_version=PIPELINE_VERSION,
                            zvs_corrected=zvs_corrected,
                        )
                    )
                    observations += 1
                    if len(observation_batch) >= self.batch_size:
                        observations_inserted += self.store.insert_observations(
                            observation_batch, conn=conn
                        )
                        observation_batch.clear()
            if observation_batch:
                observations_inserted += self.store.insert_observations(
                    observation_batch, conn=conn
                )
                observation_batch.clear()

            stats_rows = self._finalize_stats(stats, contexts, pairs, seen_at)
            self.store.upsert_stats(stats_rows, conn=conn)
            summary = {
                "pipeline_version": PIPELINE_VERSION,
                "sources": [s.name for s in selected],
                "sources_skipped": skipped_sources,
                "sentences": sentences_seen,
                "sentences_empty": sentences_empty,
                "observations": observations,
                "observations_inserted": observations_inserted,
                "tokens": stats.total_tokens,
                "documents": stats.total_documents,
                "words": stats.distinct_words,
                "stats_rows": len(stats_rows),
                "pair_occurrences": pairs.occurrences,
                "limit": self.limit,
                "started_at": started_wall,
                "finished_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }

        summary["migration"] = migration
        return summary

    # -- Finalization ------------------------------------------------------

    def _finalize_stats(
        self,
        stats: StatsAggregator,
        contexts: ContextCollector,
        pairs: PairCollector,
        seen_at: str,
    ) -> list[dict[str, Any]]:
        """Derive every word's roll-up row (deterministic word order)."""
        frequencies = stats.frequencies()
        neighbors = pairs.neighbors()
        collocations = pairs.collocations(
            frequencies, total_tokens=stats.total_tokens
        )
        rows: list[dict[str, Any]] = []
        for word in sorted(stats.words):
            attestation = self._attest(word)
            rows.append(
                build_stats_row(
                    word,
                    scalars=stats.scalars(word),
                    surface_forms=stats.surface_forms(word),
                    contexts=contexts.as_dict(word),
                    neighbors=neighbors.get(word, []),
                    collocations=collocations.get(word, []),
                    attestation=attestation,
                    seen_at=seen_at,
                    pipeline_version=PIPELINE_VERSION,
                )
            )
        return rows

    def _attest(self, word: str) -> dict[str, Any]:
        if self.attestor is None:
            return {}
        result = self.attestor(word)
        return dict(result) if result else {}


def build_observations(engine: Engine, **kwargs: Any) -> dict[str, Any]:
    """Module-level convenience wrapper (CLI / engine entry points)."""
    return ObservationPipeline(engine, **kwargs).build()
