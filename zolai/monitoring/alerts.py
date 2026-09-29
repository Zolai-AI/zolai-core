"""Threshold alert rules and the in-process evaluator.

:data:`RULES` is the single source of truth for thresholds: the Prometheus
rule file ``ops/prometheus/rules.yml`` must declare identical numbers, and
``tests/test_alert_rules_parity.py`` fails the build when they drift.

The evaluator powers ``GET /api/metrics/alerts``.  It reads the same bounded
sample windows the exporter serves, so a rule fires on data the dashboards
also show (no hidden state).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .metrics import (
    ALERT_STATE,
    ALERTS_ACTIVE,
    DB_LATENCY,
    LATENCY,
    window_stats,
)

__all__ = ["RULES", "collect_metrics", "evaluate_alerts"]

#: Evaluation window (seconds) used for the local evaluator's percentiles.
EVAL_WINDOW_S = 300.0

RULES: dict[str, dict[str, Any]] = {
    "http_error_rate_high": {
        "severity": "critical",
        "threshold": 0.05,
        "metric": "error_rate",
        "comparison": ">",
        "for_s": 300,
        "expr": (
            'rate(zolai_http_requests_total{status=~"5.."}[5m]) '
            "/ rate(zolai_http_requests_total[5m])"
        ),
        "summary": "HTTP 5xx error rate above 5% for 5m",
    },
    "http_latency_p95_high": {
        "severity": "warning",
        "threshold": 1.0,
        "metric": "p95_s",
        "comparison": ">",
        "for_s": 300,
        "expr": (
            "histogram_quantile(0.95, sum by (le) "
            "(rate(zolai_http_request_duration_seconds_bucket[5m])))"
        ),
        "summary": "HTTP p95 latency above 1s for 5m",
    },
    "db_query_p95_high": {
        "severity": "warning",
        "threshold": 0.05,
        "metric": "db_query_p95_s",
        "comparison": ">",
        "for_s": 300,
        "expr": (
            "histogram_quantile(0.95, sum by (le) "
            "(rate(zolai_db_query_duration_seconds_bucket[5m])))"
        ),
        "summary": "DB p95 query latency above 50ms for 5m",
    },
}

#: Unix timestamp per firing rule (for ``since_s``); cleared when it recovers.
_FIRING_SINCE: dict[str, float] = {}


def collect_metrics(window_s: float = EVAL_WINDOW_S) -> dict[str, float]:
    """Snapshot the metric values the rules evaluate against.

    Args:
        window_s: Wall-clock window used for the percentiles/error rate.
    """
    http = window_stats(LATENCY.recent(window_s))
    db = window_stats(DB_LATENCY.recent(window_s))
    return {
        "error_rate": http["error_rate"],
        "p50_s": http["p50_s"],
        "p95_s": http["p95_s"],
        "p99_s": http["p99_s"],
        "db_query_p95_s": db["p95_s"],
        "db_query_p50_s": db["p50_s"],
        "requests": float(http["samples"]),
        "db_queries": float(db["samples"]),
    }


def _breached(value: float, threshold: float, comparison: str) -> bool:
    if comparison == ">=":
        return value >= threshold
    if comparison == "<":
        return value < threshold
    if comparison == "<=":
        return value <= threshold
    return value > threshold


def evaluate_alerts(
    metrics: Mapping[str, float] | None = None,
    *,
    window_s: float = EVAL_WINDOW_S,
) -> dict[str, Any]:
    """Evaluate every rule and publish ``zolai_alert_state`` / ``..._active``.

    Args:
        metrics: Optional pre-computed metric snapshot (used by tests to feed
            a synthetic window).  Defaults to :func:`collect_metrics`.
        window_s: Window used when computing the default snapshot.

    Returns:
        ``{evaluated_at, rules: [{name, severity, threshold, expr, state,
        value, since_s}], active_count}``.
    """
    snapshot = dict(metrics) if metrics is not None else collect_metrics(window_s)
    now = time.time()
    rules: list[dict[str, Any]] = []
    active = 0

    for name, rule in sorted(RULES.items()):
        value = float(snapshot.get(rule["metric"], 0.0))
        firing = _breached(value, float(rule["threshold"]), rule["comparison"])
        if firing:
            active += 1
            _FIRING_SINCE.setdefault(name, now)
            since_s = round(now - _FIRING_SINCE[name], 3)
        else:
            _FIRING_SINCE.pop(name, None)
            since_s = 0.0
        ALERT_STATE.labels(rule=name).set(1.0 if firing else 0.0)
        rules.append(
            {
                "name": name,
                "severity": rule["severity"],
                "threshold": rule["threshold"],
                "expr": rule["expr"],
                "state": "firing" if firing else "ok",
                "value": value,
                "since_s": since_s,
            }
        )

    ALERTS_ACTIVE.set(active)
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules": rules,
        "active_count": active,
    }
