"""300s background sampler for database and corpus gauges.

The sampler keeps the cheap gauges (file sizes, curated row counts, corpus
counters) fresh without paying for a ``COUNT(*)`` on every scrape.  The first
sample runs eagerly so ``/api/metrics/summary`` never returns zeros, and
subsequent refreshes are throttled by :data:`CACHE_TTL_S`.

Everything is best-effort: a missing table or a locked DB degrades to the last
known values instead of raising.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .metrics import (
    BIBLE_VERSES,
    CORPUS_SENTENCES,
    DB_LATENCY,
    DB_SIZE_BYTES,
    DB_TABLE_ROWS,
    DB_WAL_BYTES,
    DICTIONARY_ENTRIES,
    window_stats,
)

logger = logging.getLogger(__name__)

#: Full re-sample cadence (seconds).
SAMPLE_INTERVAL_S = 300.0

#: How long a computed sample stays valid for synchronous callers.
CACHE_TTL_S = 60.0

#: Curated tables tracked by ``zolai_db_table_rows`` (label cardinality cap).
CURATED_TABLES: tuple[str, ...] = (
    "dictionary",
    "dictionary_en_zo",
    "bible_verses",
    "grammar_patterns",
    "phrases",
    "vocabulary",
    "zolai_vocabulary",
    "translations",
    "word_usage",
    "training_exercises",
    "word_alignments",
    "syllable_data",
)

#: Business gauges and the table backing each one.
BUSINESS_TABLES: dict[str, str] = {
    "dictionary_entries": "dictionary",
    "bible_verses": "bible_verses",
    # The parallel sentence corpus lives in `translations`.
    "corpus_sentences": "translations",
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None
_LAST_SAMPLE_AT = 0.0


def _count(mgr: Any, table: str) -> int | None:
    from sqlalchemy import text

    try:
        with mgr.engine.connect() as conn:
            value = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        return int(value or 0)
    except Exception as exc:  # noqa: BLE001 — gauge must never break scraping
        logger.debug("row count unavailable for %s: %s", table, exc)
        return None


def _file_sizes(mgr: Any) -> tuple[int, int]:
    path = mgr._get_db_path()
    if path is None:
        return 0, 0
    try:
        size = path.stat().st_size if path.exists() else 0
    except OSError:
        size = 0
    wal_path = path.parent / f"{path.name}-wal"
    try:
        wal = wal_path.stat().st_size if wal_path.exists() else 0
    except OSError:
        wal = 0
    return int(size), int(wal)


def sample_now(*, force: bool = False, mgr: Any = None) -> dict[str, Any]:
    """Refresh the gauges and return the sampled values.

    Args:
        force: Recompute even when the cache is fresh.
        mgr: DatabaseManager to sample (defaults to the singleton).

    Returns:
        ``{size_bytes, wal_bytes, rows_total, query_p95_ms, counts}``.
    """
    global _CACHE, _LAST_SAMPLE_AT

    with _LOCK:
        if (
            not force
            and _CACHE is not None
            and (time.time() - _LAST_SAMPLE_AT) < CACHE_TTL_S
        ):
            return dict(_CACHE)

        from ..data.database import get_manager

        mgr = mgr if mgr is not None else get_manager()
        size_bytes, wal_bytes = _file_sizes(mgr)

        rows_total = 0
        counts: dict[str, int] = {}
        for table in CURATED_TABLES:
            value = _count(mgr, table)
            if value is None:
                continue
            counts[table] = value
            rows_total += value
            DB_TABLE_ROWS.labels(table=table).set(value)

        for label, table in BUSINESS_TABLES.items():
            value = counts.get(table)
            if value is None:
                value = _count(mgr, table)
            if value is None:
                continue
            if label == "dictionary_entries":
                DICTIONARY_ENTRIES.set(value)
            elif label == "bible_verses":
                BIBLE_VERSES.set(value)
            elif label == "corpus_sentences":
                CORPUS_SENTENCES.set(value)

        DB_SIZE_BYTES.set(size_bytes)
        DB_WAL_BYTES.set(wal_bytes)

        query_p95_ms = round(window_stats(DB_LATENCY.recent(300.0))["p95_s"] * 1000, 3)

        _CACHE = {
            "size_bytes": size_bytes,
            "wal_bytes": wal_bytes,
            "rows_total": rows_total,
            "query_p95_ms": query_p95_ms,
            "counts": counts,
        }
        _LAST_SAMPLE_AT = time.time()
        return dict(_CACHE)


class MetricsSampler(threading.Thread):
    """Daemon thread re-sampling the gauges every ``interval`` seconds."""

    def __init__(self, interval: float = SAMPLE_INTERVAL_S, mgr: Any = None) -> None:
        super().__init__(name="zolai-metrics-sampler", daemon=True)
        self.interval = interval
        self._mgr = mgr
        self._stop = threading.Event()

    def stop(self) -> None:
        """Ask the sampler to exit (safe to call from a lifespan hook)."""
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                sample_now(force=True, mgr=self._mgr)
            except Exception:  # noqa: BLE001 — never take the process down
                logger.exception("metrics sample failed")
            if self._stop.wait(self.interval):
                break


_SAMPLER: MetricsSampler | None = None
_SAMPLER_LOCK = threading.Lock()


def get_sampler() -> MetricsSampler:
    """Return the process-wide sampler (created on first call)."""
    global _SAMPLER
    with _SAMPLER_LOCK:
        if _SAMPLER is None:
            _SAMPLER = MetricsSampler()
        return _SAMPLER


def start_sampler() -> MetricsSampler:
    """Start the background sampler; returns the running instance.

    A ``threading.Thread`` can only be started once, so a sampler that was
    previously stopped (app shutdown) is replaced with a fresh thread rather
    than restarted.
    """
    global _SAMPLER
    with _SAMPLER_LOCK:
        if _SAMPLER is None or _SAMPLER.ident is not None:
            _SAMPLER = MetricsSampler()
        sampler = _SAMPLER
    if not sampler.is_alive():
        sampler.start()
    return sampler


def stop_sampler() -> None:
    """Stop the background sampler if it is running."""
    with _SAMPLER_LOCK:
        sampler = _SAMPLER
    if sampler is not None:
        sampler.stop()
