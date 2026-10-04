"""Prometheus metric definitions and exact percentile windows.

Naming follows the exporter convention ``zolai_<domain>_<metric>``:

- counters end in ``_total``
- durations are in ``_seconds``
- meta/info series end in ``_info``

Every metric lives on the **process-global default registry** so ``/metrics``,
the HTTP middleware, the DB listeners, the background sampler and the alert
evaluator all read one source of truth.  ``process_*`` and ``python_*`` keep
their default collectors (never disabled).

Label rules (hard requirements):

- HTTP labels use **route templates** (``/api/items/{id}``) — never raw paths.
- DB labels use an **operation class** (``select``/``insert``/``update``/
  ``delete``/``other``) — never SQL text.
"""

from __future__ import annotations

import functools
import math
import platform
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from prometheus_client import Counter, Gauge, Histogram

__all__ = [
    "ALERTS_ACTIVE",
    "ALERT_STATE",
    "DB_LATENCY",
    "ANALYSIS_LATENCY",
    "ANALYSIS_OPS",
    "BIBLE_VERSES",
    "BUILD_INFO",
    "CORPUS_SENTENCES",
    "DB_INTEGRITY_STATUS",
    "DB_QUERY_ERRORS",
    "DB_QUERY_LATENCY",
    "DB_SIZE_BYTES",
    "DB_TABLE_ROWS",
    "DB_WAL_BYTES",
    "DICTIONARY_ENTRIES",
    "EVAL_LAST_RUN",
    "EVAL_METRIC_VALUE",
    "EVAL_RUNS",
    "HTTP_IN_FLIGHT",
    "HTTP_LATENCY",
    "HTTP_REQUESTS",
    "LATENCY",
    "ROUTE_WINDOWS",
    "metric_value",
    "WORDS_TRANSLATED",
    "observe_request",
    "parse_window",
    "record_operation",
    "percentile",
    "route_window",
    "set_build_info",
    "track_operation",
    "window_stats",
]

# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------
#: HTTP latency: sub-ms route handlers up to slow LLM-backed calls.
HTTP_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
#: DB latency: local SQLite queries.
DB_BUCKETS = (0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)
#: Linguistic analysis operations: dictionary-fast to corpus-heavy.
OP_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0)

#: Status codes at or above this value count as request errors (5xx).
ERROR_STATUS = 500

#: Retained samples per window (global + per route).
DEFAULT_WINDOW_LEN = 4096

#: Cap on distinct route templates tracked individually (label cardinality).
MAX_TRACKED_ROUTES = 256

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "zolai_http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "zolai_http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template.",
    ["method", "route"],
    buckets=HTTP_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge(
    "zolai_http_requests_in_flight",
    "HTTP requests currently being served.",
)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_QUERY_LATENCY = Histogram(
    "zolai_db_query_duration_seconds",
    "Database query latency in seconds, by operation class.",
    ["operation"],
    buckets=DB_BUCKETS,
)
DB_QUERY_ERRORS = Counter(
    "zolai_db_query_errors_total",
    "Database statements that raised, by exception class.",
    ["error"],
)
DB_SIZE_BYTES = Gauge(
    "zolai_db_size_bytes",
    "Size of the SQLite database file in bytes.",
)
DB_WAL_BYTES = Gauge(
    "zolai_db_wal_bytes",
    "Size of the SQLite write-ahead log file in bytes.",
)
DB_INTEGRITY_STATUS = Gauge(
    "zolai_db_integrity_status",
    "1 when the most recent foreign-key/integrity check passed, else 0.",
)
DB_TABLE_ROWS = Gauge(
    "zolai_db_table_rows",
    "Row count per curated table.",
    ["table"],
)

# ---------------------------------------------------------------------------
# Linguistic analysis
# ---------------------------------------------------------------------------
ANALYSIS_OPS = Counter(
    "zolai_analysis_operations_total",
    "Lemmatic/linguistic analysis operations started, by operation.",
    ["operation"],
)
ANALYSIS_LATENCY = Histogram(
    "zolai_analysis_duration_seconds",
    "Linguistic analysis operation latency in seconds, by operation.",
    ["operation"],
    buckets=OP_BUCKETS,
)

# ---------------------------------------------------------------------------
# Business / corpus
# ---------------------------------------------------------------------------
WORDS_TRANSLATED = Counter(
    "zolai_words_translated_total",
    "Translation requests served, by direction.",
    ["direction"],
)
CORPUS_SENTENCES = Gauge(
    "zolai_corpus_sentences",
    "Sentences in the canonical corpus.",
)
DICTIONARY_ENTRIES = Gauge(
    "zolai_dictionary_entries",
    "Entries in the Zolai->English dictionary.",
)
BIBLE_VERSES = Gauge(
    "zolai_bible_verses",
    "Parallel Bible verses available.",
)

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
EVAL_RUNS = Counter(
    "zolai_eval_runs_total",
    "Evaluation runs recorded, by eval set.",
    ["set_name"],
)
EVAL_METRIC_VALUE = Gauge(
    "zolai_eval_metric_value",
    "Latest value of each evaluation metric, by set and metric.",
    ["set_name", "metric"],
)
EVAL_LAST_RUN = Gauge(
    "zolai_eval_last_run_timestamp_seconds",
    "Unix timestamp of the most recent evaluation run.",
)

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
ALERTS_ACTIVE = Gauge(
    "zolai_alerts_active",
    "Number of alert rules currently firing.",
)
ALERT_STATE = Gauge(
    "zolai_alert_state",
    "Alert rule state: 1 firing, 0 ok.",
    ["rule"],
)

# ---------------------------------------------------------------------------
# Build metadata
# ---------------------------------------------------------------------------
BUILD_INFO = Gauge(
    "zolai_build_info",
    "Build information (version, interpreter, git commit).",
    ["version", "python", "commit"],
)


def set_build_info(version: str, commit: str = "unknown") -> None:
    """Publish ``zolai_build_info`` (idempotent — replaces any prior labels)."""
    BUILD_INFO.clear()
    BUILD_INFO.labels(
        version=version,
        python=platform.python_version(),
        commit=commit or "unknown",
    ).set(1)


# ---------------------------------------------------------------------------
# Exact percentile windows (bounded ring buffers)
# ---------------------------------------------------------------------------
class SampleWindow:
    """Bounded ring buffer of ``(timestamp, duration, status)`` samples.

    The buffer keeps the most recent :data:`DEFAULT_WINDOW_LEN` samples, so
    percentiles are **exact over the retained window** while memory stays flat
    under sustained traffic.  Timestamps let callers filter by wall-clock
    window (``?window=5m``) instead of sample count.
    """

    __slots__ = ("_lock", "_samples", "maxlen")

    def __init__(self, maxlen: int = DEFAULT_WINDOW_LEN) -> None:
        self.maxlen = maxlen
        self._samples: deque[tuple[float, float, int]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def observe(self, duration: float, status: int = 200, ts: float | None = None) -> None:
        """Record one sample (``duration`` seconds, response ``status``)."""
        with self._lock:
            self._samples.append((ts if ts is not None else time.time(), duration, status))

    def recent(self, window_s: float | None = None) -> list[tuple[float, float, int]]:
        """Return retained samples, optionally filtered to ``window_s`` seconds."""
        with self._lock:
            samples = list(self._samples)
        if window_s is None:
            return samples
        cutoff = time.time() - window_s
        return [s for s in samples if s[0] >= cutoff]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._samples)


#: Global latency window backing ``/api/metrics/summary`` and ``.../performance``.
LATENCY = SampleWindow()

#: Recent database query durations (backs ``db.query_p95_ms`` + alert rule).
DB_LATENCY = SampleWindow()

#: Per-route windows (route template -> window).
ROUTE_WINDOWS: dict[str, SampleWindow] = {}
_ROUTE_LOCK = threading.Lock()


def route_window(route: str) -> SampleWindow:
    """Return (creating if needed) the sample window for a route template."""
    window = ROUTE_WINDOWS.get(route)
    if window is not None:
        return window
    with _ROUTE_LOCK:
        window = ROUTE_WINDOWS.get(route)
        if window is None:
            if len(ROUTE_WINDOWS) >= MAX_TRACKED_ROUTES:
                # Cardinality guard: overflow routes share one bucket.
                route = "__other__"
                window = ROUTE_WINDOWS.get(route)
                if window is None:
                    window = SampleWindow()
                    ROUTE_WINDOWS[route] = window
            else:
                window = SampleWindow()
                ROUTE_WINDOWS[route] = window
        return window


def parse_window(value: str | None, default: float = 300.0) -> float:
    """Parse a Prometheus-style duration (``5m``, ``30s``, ``1h``) to seconds.

    Bare numbers are treated as seconds; ``None``/unparsable input yields
    ``default``.
    """
    if value is None:
        return default
    raw = str(value).strip().lower()
    if not raw:
        return default
    unit = raw[-1]
    factors = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    if unit in factors:
        try:
            return float(raw[:-1]) * factors[unit]
        except ValueError:
            return default
    try:
        return float(raw)
    except ValueError:
        return default


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile of ``values`` (``q`` in 0..1); 0.0 when empty."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def window_stats(samples: list[tuple[float, float, int]]) -> dict[str, Any]:
    """Aggregate ``(ts, duration, status)`` samples into percentile stats."""
    durations = [duration for _, duration, _ in samples]
    errors = sum(1 for _, _, status in samples if status >= ERROR_STATUS)
    return {
        "samples": len(samples),
        "p50_s": round(percentile(durations, 0.50), 6),
        "p90_s": round(percentile(durations, 0.90), 6),
        "p95_s": round(percentile(durations, 0.95), 6),
        "p99_s": round(percentile(durations, 0.99), 6),
        "max_s": round(max(durations), 6) if durations else 0.0,
        "error_rate": round(errors / len(samples), 6) if samples else 0.0,
    }


def metric_value(metric: Any) -> float:
    """Sum the current samples of a Counter or Gauge across all label sets.

    Reads the public ``collect()`` API (never private ``_value`` handles) and
    skips ``_created`` timestamp samples.  Not for histograms — those need
    ``window_stats``/bucket maths instead.
    """
    total = 0.0
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_created"):
                continue
            total += float(sample.value)
    return total


def observe_request(method: str, route: str, status: int, duration: float) -> None:
    """Record a completed HTTP request in every HTTP metric/window."""
    status_label = str(status)
    HTTP_REQUESTS.labels(method=method, route=route, status=status_label).inc()
    HTTP_LATENCY.labels(method=method, route=route).observe(duration)
    LATENCY.observe(duration, status)
    route_window(route).observe(duration, status)


F = TypeVar("F", bound=Callable[..., Any])


def record_operation(name: str) -> Callable[[F], F]:
    """Decorator form of :func:`track_operation` for whole-call timing.

    Prefer this for long functions so the body stays untouched; the emitted
    metrics (``zolai_analysis_operations_total``,
    ``zolai_analysis_duration_seconds``) are identical.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with track_operation(name):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def track_operation(name: str) -> Iterator[None]:
    """Context manager counting and timing a linguistic analysis operation.

    Usage::

        with track_operation("analyze_word"):
            result = analyzer.analyze_word("pasian")
    """
    ANALYSIS_OPS.labels(operation=name).inc()
    started = time.perf_counter()
    try:
        yield
    finally:
        ANALYSIS_LATENCY.labels(operation=name).observe(time.perf_counter() - started)

# --- Phase 8 Production Metrics (§32) ---

def record_pipeline_run(pipeline: str, success: bool, error_type: str | None = None) -> None:
    """Record a pipeline run for Phase 8 metrics."""
    from zolai.monitoring.production_metrics import record_pipeline_run as _record
    return _record(pipeline, success, error_type)

def observe_engine_call(engine: str, latency: float, success: bool) -> None:
    from zolai.monitoring.production_metrics import observe_engine_call as _observe
    return _observe(engine, latency, success)

def observe_rag_query(endpoint: str, latency: float, success: bool) -> None:
    from zolai.monitoring.production_metrics import observe_rag_query as _observe
    return _observe(endpoint, latency, success)

def observe_incremental_change(change_type: str) -> None:
    from zolai.monitoring.production_metrics import observe_incremental_change as _observe
    return _observe(change_type)

def observe_incremental_processing(latency: float) -> None:
    from zolai.monitoring.production_metrics import observe_incremental_processing as _observe
    return _observe(latency)

def observe_publish_artifact_size(size_bytes: int) -> None:
    from zolai.monitoring.production_metrics import observe_publish_artifact_size as _observe
    return _observe(size_bytes)

def observe_publish_sync(target: str, latency: float) -> None:
    from zolai.monitoring.production_metrics import observe_publish_sync as _observe
    return _observe(target, latency)

def record_publish_release(success: bool) -> None:
    from zolai.monitoring.production_metrics import record_publish_release as _record
    return _record(success)

# Re-export context managers and decorators (public surface of this module).
from zolai.monitoring.production_metrics import (  # noqa: F401, E402
    time_engine_call,
    time_incremental_processing,
    time_rag_query,
    track_engine_call,
    track_rag_query,
    track_rag_query_sync,
)
