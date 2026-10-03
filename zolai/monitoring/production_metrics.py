"""Phase 8 Production Metrics — Language Intelligence, RAG, Engine, Incremental, Publishing.

Extends the core monitoring.metrics with Phase 8 §32 language-intelligence metrics.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from functools import wraps
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

# --- Language Intelligence Metrics (§32) ---
ZOLAI_WORDS_TOTAL = Gauge(
    "zolai_words_total",
    "Total words in vocabulary",
    ["status"],  # attested, candidate, verified
)

ZOLAI_POS_HYPOTHESES_TOTAL = Gauge(
    "zolai_pos_hypotheses_total",
    "Total POS hypotheses",
    ["status"],  # OBSERVED, CANDIDATE, SUPPORTED, VERIFIED, REJECTED, DEPRECATED
)

ZOLAI_MORPHOLOGY_HYPOTHESES_TOTAL = Gauge(
    "zolai_morphology_hypotheses_total",
    "Total morphology hypotheses",
    ["status"],
)

ZOLAI_GRAMMAR_PATTERNS_TOTAL = Gauge(
    "zolai_grammar_patterns_total",
    "Total grammar patterns",
    ["status"],
)

ZOLAI_KNOWLEDGE_CLAIMS_TOTAL = Gauge(
    "zolai_knowledge_claims_total",
    "Total knowledge claims",
    ["status"],
)

ZOLAI_REVIEW_QUEUE_TOTAL = Gauge(
    "zolai_review_queue_total",
    "Items in review queue",
    ["status"],  # pending, in_progress, approved, rejected, deferred
)

ZOLAI_CONFLICTING_CLAIMS_TOTAL = Gauge(
    "zolai_conflicting_claims_total",
    "Claims with conflicting evidence",
)

ZOLAI_PIPELINE_RUNS_TOTAL = Counter(
    "zolai_pipeline_runs_total",
    "Total pipeline runs",
    ["pipeline", "status"],  # incremental, release, discovery, etc.; success, failure
)

ZOLAI_PIPELINE_FAILURES_TOTAL = Counter(
    "zolai_pipeline_failures_total",
    "Total pipeline failures",
    ["pipeline", "error_type"],
)

ZOLAI_KNOWLEDGE_VERSION = Gauge(
    "zolai_knowledge_version",
    "Current knowledge version (Unix timestamp)",
)

ZOLAI_CORPUS_SIZE = Gauge(
    "zolai_corpus_size",
    "Total corpus size in bytes",
)

ZOLAI_SOURCE_COUNT = Gauge(
    "zolai_source_count",
    "Number of data sources",
)

# --- RAG/Engine Metrics ---
ZOLAI_RAG_QUERIES_TOTAL = Counter(
    "zolai_rag_queries_total",
    "Total RAG queries",
    ["endpoint", "status"],  # word, search, rag, analyze; success, error
)

ZOLAI_RAG_LATENCY_SECONDS = Histogram(
    "zolai_rag_latency_seconds",
    "RAG query latency",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

ZOLAI_ENGINE_CALLS_TOTAL = Counter(
    "zolai_engine_calls_total",
    "Total engine calls",
    ["engine", "status"],  # discovery, rag, incremental, etc.
)

ZOLAI_ENGINE_LATENCY_SECONDS = Histogram(
    "zolai_engine_latency_seconds",
    "Engine call latency",
    ["engine"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

# --- Incremental Metrics ---
ZOLAI_INCREMENTAL_CHANGES_TOTAL = Counter(
    "zolai_incremental_changes_total",
    "Total incremental changes processed",
    ["change_type"],  # new, changed, removed
)

ZOLAI_INCREMENTAL_PROCESSING_SECONDS = Histogram(
    "zolai_incremental_processing_seconds",
    "Incremental processing latency",
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)

# --- Publishing Metrics ---
ZOLAI_PUBLISH_ARTIFACT_SIZE_BYTES = Histogram(
    "zolai_publish_artifact_size_bytes",
    "Published artifact size",
    buckets=[1024, 10240, 102400, 1048576, 10485760, 104857600],
)

ZOLAI_PUBLISH_SYNC_SECONDS = Histogram(
    "zolai_publish_sync_seconds",
    "R2/D1 sync duration",
    ["target"],  # r2, d1
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)

ZOLAI_PUBLISH_RELEASE_TOTAL = Counter(
    "zolai_publish_release_total",
    "Total releases",
    ["status"],  # success, failure
)


# --- Helper Functions ---
def record_pipeline_run(pipeline: str, success: bool, error_type: str | None = None) -> None:
    """Record a pipeline run."""
    status = "success" if success else "failure"
    ZOLAI_PIPELINE_RUNS_TOTAL.labels(pipeline=pipeline, status=status).inc()
    if not success and error_type:
        ZOLAI_PIPELINE_FAILURES_TOTAL.labels(pipeline=pipeline, error_type=error_type).inc()


def observe_engine_call(engine: str, latency: float, success: bool) -> None:
    """Observe an engine call."""
    ZOLAI_ENGINE_CALLS_TOTAL.labels(engine=engine, status="success" if success else "failure").inc()
    ZOLAI_ENGINE_LATENCY_SECONDS.labels(engine=engine).observe(latency)


def observe_rag_query(endpoint: str, latency: float, success: bool) -> None:
    """Observe a RAG query."""
    ZOLAI_RAG_QUERIES_TOTAL.labels(endpoint=endpoint, status="success" if success else "error").inc()
    ZOLAI_RAG_LATENCY_SECONDS.labels(endpoint=endpoint).observe(latency)


def observe_incremental_change(change_type: str) -> None:
    """Observe an incremental change."""
    ZOLAI_INCREMENTAL_CHANGES_TOTAL.labels(change_type=change_type).inc()


def observe_incremental_processing(latency: float) -> None:
    """Observe incremental processing time."""
    ZOLAI_INCREMENTAL_PROCESSING_SECONDS.observe(latency)


def observe_publish_artifact_size(size_bytes: int) -> None:
    """Observe artifact size."""
    ZOLAI_PUBLISH_ARTIFACT_SIZE_BYTES.observe(size_bytes)


def observe_publish_sync(target: str, latency: float) -> None:
    """Observe R2/D1 sync time."""
    ZOLAI_PUBLISH_SYNC_SECONDS.labels(target=target).observe(latency)


def record_publish_release(success: bool) -> None:
    """Record a release attempt."""
    ZOLAI_PUBLISH_RELEASE_TOTAL.labels(status="success" if success else "failure").inc()


# --- Context Managers ---
@contextmanager
def time_engine_call(engine: str):
    """Context manager to time an engine call."""
    start = time.perf_counter()
    success = True
    try:
        yield
    except Exception:
        success = False
        raise
    finally:
        latency = time.perf_counter() - start
        observe_engine_call(engine, latency, success)


@contextmanager
def time_rag_query(endpoint: str):
    """Context manager to time a RAG query."""
    start = time.perf_counter()
    success = True
    try:
        yield
    except Exception:
        success = False
        raise
    finally:
        latency = time.perf_counter() - start
        observe_rag_query(endpoint, latency, success)


@contextmanager
def time_incremental_processing():
    """Context manager to time incremental processing."""
    start = time.perf_counter()
    try:
        yield
    finally:
        latency = time.perf_counter() - start
        observe_incremental_processing(latency)


# --- Decorators ---
def track_engine_call(engine: str):
    """Decorator to track engine calls."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            success = True
            try:
                return func(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                latency = time.perf_counter() - start
                observe_engine_call(engine, latency, success)
        return wrapper
    return decorator


def track_rag_query(endpoint: str):
    """Decorator to track RAG queries."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            success = True
            try:
                return await func(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                latency = time.perf_counter() - start
                observe_rag_query(endpoint, latency, success)
        return async_wrapper
    return decorator


def track_rag_query_sync(endpoint: str):
    """Decorator to track sync RAG queries."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            success = True
            try:
                return func(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                latency = time.perf_counter() - start
                observe_rag_query(endpoint, latency, success)
        return wrapper
    return decorator


# Imports at bottom for contextmanager, wraps
from contextlib import contextmanager
from functools import wraps
