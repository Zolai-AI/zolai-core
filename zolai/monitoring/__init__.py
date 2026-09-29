"""Zolai monitoring — Prometheus instrumentation, alerting and metric storage.

Modules:

``metrics``
    All ``zolai_*`` metric families plus bounded sample windows used for
    exact p50/p95/p99 percentiles.
``middleware``
    Pure-ASGI HTTP middleware (request counter, latency histogram,
    in-flight gauge) labelled with **route templates only**.
``db_metrics``
    SQLAlchemy ``before/after_cursor_execute`` listeners — query latency and
    error counters labelled with an **operation class only** (never SQL).
``alerts``
    Threshold rules shared verbatim with ``ops/prometheus/rules.yml`` plus a
    local evaluator used by ``/api/metrics/alerts``.
``store``
    Annotation / eval-run / integrity persistence and health aggregation.
``background``
    300s daemon sampler for DB size, WAL size and corpus gauges.

Usage::

    from zolai.monitoring import record_operation, track_operation

    @record_operation("translate")
    def translate(...): ...

    with track_operation("analyze_phonology"):
        analysis = analyzer.analyze_phonology("pasian")
"""

from __future__ import annotations

from .metrics import record_operation, track_operation  # noqa: F401

__all__ = ["record_operation", "track_operation"]
