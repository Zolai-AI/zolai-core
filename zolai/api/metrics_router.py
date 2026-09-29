"""Metrics REST API — Prometheus exposition plus JSON metric/health endpoints.

Every route here is registered on ``create_app()`` **before** the catch-all
``/{path:path}`` route, so these paths always win.

Endpoints:

=================================  ======  ==============================================
Path                               Method  Purpose
=================================  ======  ==============================================
``/metrics``                       GET     Prometheus text exposition (0.0.4)
``/api/metrics/summary``           GET     HTTP + DB + business + latest eval snapshot
``/api/metrics/health``            GET     DB/disk/integrity health (503 when degraded)
``/api/metrics/eval``              GET     Latest ``eval_runs`` row
``/api/metrics/performance``       GET     Percentiles over ``?window=5m``, by route
``/api/metrics/alerts``            GET     Local threshold-rule evaluation
``/api/metrics/info``              GET     Build metadata mirroring ``zolai_build_info``
``/api/metrics/annotations``       GET/POST  Grafana-compatible annotations
``/api/metrics/annotations/{id}``  PUT/DELETE
=================================  ======  ==============================================
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from prometheus_client import generate_latest

from ..monitoring import store
from ..monitoring.alerts import evaluate_alerts
from ..monitoring.metrics import (
    CORPUS_SENTENCES,
    DICTIONARY_ENTRIES,
    LATENCY,
    ROUTE_WINDOWS,
    WORDS_TRANSLATED,
    metric_value,
    parse_window,
    window_stats,
)
from .metrics_schemas import (
    AlertRuleState,
    AlertsResponse,
    AnnotationCreated,
    AnnotationDeleted,
    AnnotationIn,
    AnnotationOut,
    AnnotationUpdated,
    EvalRunResponse,
    MetricsHealthResponse,
    MetricsInfoResponse,
    PerformanceResponse,
    RoutePerformance,
    SummaryResponse,
)

router = APIRouter(tags=["metrics"])

#: Default window for summary/performance percentiles.
DEFAULT_WINDOW_S = 300.0

#: Prometheus exposition media type.
EXPOSITION_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Prometheus exposition
# ---------------------------------------------------------------------------
@router.get("/metrics", response_class=Response, summary="Prometheus metrics")
def metrics_exposition() -> Response:
    """Serve every registered metric in the Prometheus text format.

    Refreshes the throttled background gauges first (at most once per minute),
    then serializes the default registry — ``process_*`` and ``python_*``
    included.
    """
    from ..monitoring.background import sample_now

    sample_now()
    return Response(content=generate_latest(), media_type=EXPOSITION_MEDIA_TYPE)


# ---------------------------------------------------------------------------
# JSON metric views
# ---------------------------------------------------------------------------
@router.get("/api/metrics/summary", response_model=SummaryResponse)
def metrics_summary(
    window: Annotated[str | None, Query(description="e.g. 5m, 30s, 3600")] = None,
) -> SummaryResponse:
    """Aggregate HTTP, DB, business and evaluation metrics."""
    window_s = parse_window(window, DEFAULT_WINDOW_S)
    http = window_stats(LATENCY.recent(window_s))
    sampled = store.db_stats()
    latest = store.latest_eval_run() or {"source": "none"}
    return {
        "generated_at": _now(),
        "http": {
            "requests": http["samples"],
            "error_rate": http["error_rate"],
            "p50": http["p50_s"],
            "p95": http["p95_s"],
            "p99": http["p99_s"],
        },
        "db": {
            "query_p95_ms": sampled["query_p95_ms"],
            "rows_total": sampled["rows_total"],
            "size_bytes": sampled["size_bytes"],
            "wal_bytes": sampled["wal_bytes"],
        },
        "business": {
            "words_translated": metric_value(WORDS_TRANSLATED),
            "corpus_sentences": metric_value(CORPUS_SENTENCES),
            "dictionary_entries": metric_value(DICTIONARY_ENTRIES),
        },
        "eval": latest,
    }


@router.get("/api/metrics/health", response_model=MetricsHealthResponse)
def metrics_health(response: Response) -> MetricsHealthResponse:
    """Database + disk + process health; answers **503** when degraded."""
    report = store.check_health()
    if report["status"] != "ok":
        response.status_code = 503
    return report


@router.get("/api/metrics/eval", response_model=EvalRunResponse)
def metrics_eval() -> EvalRunResponse:
    """Most recent evaluation run (``source: none`` when nothing is recorded)."""
    run = store.latest_eval_run()
    if run is None:
        return EvalRunResponse(source="none")
    return run


@router.get(
    "/api/metrics/performance",
    response_model=PerformanceResponse,
    summary="Latency percentiles by route",
)
def metrics_performance(
    window: Annotated[str | None, Query(description="e.g. 5m, 30s, 1h")] = None,
) -> PerformanceResponse:
    """Exact percentiles over the retained ring buffer, globally and per route."""
    window_s = parse_window(window, DEFAULT_WINDOW_S)
    stats = window_stats(LATENCY.recent(window_s))
    by_route: dict[str, RoutePerformance] = {}
    for route, route_window in ROUTE_WINDOWS.items():
        route_stats = window_stats(route_window.recent(window_s))
        if not route_stats["samples"]:
            continue
        by_route[route] = {
            "count": route_stats["samples"],
            "p95_s": route_stats["p95_s"],
            "error_rate": route_stats["error_rate"],
        }
    return {
        "window_s": window_s,
        "samples": stats["samples"],
        "p50_s": stats["p50_s"],
        "p90_s": stats["p90_s"],
        "p95_s": stats["p95_s"],
        "p99_s": stats["p99_s"],
        "max_s": stats["max_s"],
        "by_route": by_route,
    }


@router.get(
    "/api/metrics/alerts",
    response_model=AlertsResponse,
    summary="Threshold alert evaluation",
)
def metrics_alerts() -> AlertsResponse:
    """Evaluate the local rule set (mirrors ``ops/prometheus/rules.yml``)."""
    result: dict[str, Any] = evaluate_alerts()
    return {
        "evaluated_at": result["evaluated_at"],
        "active_count": result["active_count"],
        "rules": [AlertRuleState(**rule) for rule in result["rules"]],
    }


@router.get("/api/metrics/info", response_model=MetricsInfoResponse)
def metrics_info() -> MetricsInfoResponse:
    """Build metadata — the JSON twin of the ``zolai_build_info`` metric."""
    return store.build_info()


# ---------------------------------------------------------------------------
# Annotations (Grafana-compatible)
# ---------------------------------------------------------------------------
@router.get("/api/metrics/annotations", response_model=list[AnnotationOut])
def list_annotations(
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[AnnotationOut]:
    """Stored annotations, newest first."""
    return store.list_annotations(limit)


@router.post(
    "/api/metrics/annotations",
    response_model=AnnotationCreated,
    status_code=201,
    summary="Create an annotation",
)
async def create_annotation(body: AnnotationIn) -> AnnotationCreated:
    """Insert an annotation and, when configured, mirror it into Grafana.

    ``GRAFANA_URL`` / ``GRAFANA_API_KEY`` come from the environment only —
    never from the request.
    """
    data = body.model_dump(by_alias=True)
    try:
        payload = store.annotation_payload(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = store.create_annotation(data)

    grafana_body = {
        **data,
        "time": payload["time"],
        "title": payload["title"],
        "text": payload["text"],
        "tags": json.loads(payload["tags"]),
    }
    pushed = await store.push_annotation_to_grafana(grafana_body)
    grafana_id = pushed.get("id") if isinstance(pushed, dict) else None
    if grafana_id is not None:
        store.record_grafana_id(result["id"], grafana_id)
        result["grafana_id"] = int(grafana_id)
    return AnnotationCreated(**result)


@router.put(
    "/api/metrics/annotations/{annotation_id}",
    response_model=AnnotationUpdated,
)
def update_annotation(annotation_id: int, body: AnnotationIn) -> AnnotationUpdated:
    """Replace an existing annotation.

    Only fields the client actually sent are changed — unspecified fields keep
    their stored values (the body model applies defaults, so ``exclude_unset``
    is what distinguishes "not sent" from "sent as null").
    """
    try:
        store.update_annotation(annotation_id, body.model_dump(by_alias=True, exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="annotation not found") from exc
    return AnnotationUpdated(updated=annotation_id)


@router.delete(
    "/api/metrics/annotations/{annotation_id}",
    response_model=AnnotationDeleted,
)
def delete_annotation(annotation_id: int) -> AnnotationDeleted:
    """Delete an annotation."""
    try:
        store.delete_annotation(annotation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="annotation not found") from exc
    return AnnotationDeleted(deleted=annotation_id)
