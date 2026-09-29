"""Pydantic models for the metrics REST API.

Request models accept Grafana's camelCase annotation fields (``dashboardId``,
``panelId``) alongside our snake_case spelling via ``populate_by_name``;
response models mirror the shapes documented in ``docs/MONITORING.md``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AnnotationKind = Literal["deploy", "eval", "manual"]

__all__ = [
    "AlertRuleState",
    "AlertsResponse",
    "AnnotationCreated",
    "AnnotationDeleted",
    "AnnotationIn",
    "AnnotationOut",
    "AnnotationUpdated",
    "BusinessSummary",
    "DbSummary",
    "DiskStatus",
    "EvalRunResponse",
    "HttpWindow",
    "IntegrityReport",
    "MetricsHealthResponse",
    "MetricsInfoResponse",
    "MetricsMeta",
    "PerformanceResponse",
    "RoutePerformance",
    "SummaryResponse",
]


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
class AnnotationIn(BaseModel):
    """Grafana-compatible annotation body (POST/PUT)."""

    model_config = ConfigDict(populate_by_name=True)

    time: str | int | None = None
    title: str = ""
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    kind: AnnotationKind = "manual"
    dashboard_id: int | None = Field(default=None, alias="dashboardId")
    panel_id: int | None = Field(default=None, alias="panelId")


class AnnotationOut(BaseModel):
    """Stored annotation as returned by GET/PUT/DELETE."""

    id: int
    time: str | int
    title: str = ""
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    kind: AnnotationKind = "manual"
    created_at: str | None = None


class AnnotationCreated(BaseModel):
    id: int
    grafana_id: int | None = None


class AnnotationUpdated(BaseModel):
    updated: int


class AnnotationDeleted(BaseModel):
    deleted: int


# ---------------------------------------------------------------------------
# Alerts / evaluation
# ---------------------------------------------------------------------------
class AlertRuleState(BaseModel):
    name: str
    severity: str
    threshold: float
    expr: str
    state: Literal["ok", "firing"]
    value: float
    since_s: float


class AlertsResponse(BaseModel):
    evaluated_at: str
    rules: list[AlertRuleState]
    active_count: int


class EvalRunResponse(BaseModel):
    set_name: str | None = None
    created_at: str | None = None
    case_count: int = 0
    duration_ms: float = 0.0
    gate_passed: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    #: ``"none"`` when no run is recorded, otherwise the stored source
    #: (``"db"`` / ``"jsonl"`` / …).
    source: str = "none"


# ---------------------------------------------------------------------------
# Summary / performance / info
# ---------------------------------------------------------------------------
class HttpWindow(BaseModel):
    requests: int = 0
    error_rate: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0


class DbSummary(BaseModel):
    query_p95_ms: float = 0.0
    rows_total: int = 0
    size_bytes: int = 0
    wal_bytes: int = 0


class BusinessSummary(BaseModel):
    words_translated: float = 0.0
    corpus_sentences: float = 0.0
    dictionary_entries: float = 0.0


class SummaryResponse(BaseModel):
    generated_at: str
    http: HttpWindow
    db: DbSummary
    business: BusinessSummary
    eval: EvalRunResponse


class RoutePerformance(BaseModel):
    count: int
    p95_s: float
    error_rate: float


class PerformanceResponse(BaseModel):
    window_s: float
    samples: int
    p50_s: float
    p90_s: float
    p95_s: float
    p99_s: float
    max_s: float
    by_route: dict[str, RoutePerformance]


class MetricsMeta(BaseModel):
    enabled: bool = True
    rules_url: str = "/api/metrics/alerts"


class MetricsInfoResponse(BaseModel):
    version: str
    python: str
    commit: str
    started_at: str
    db_backend: str
    metrics: MetricsMeta


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class IntegrityReport(BaseModel):
    ok: bool
    issues: list[Any] = Field(default_factory=list)
    checked_at: str | None = None


class HealthDb(BaseModel):
    writable: bool
    wal_mode: str
    foreign_keys: bool
    integrity: IntegrityReport


class DiskStatus(BaseModel):
    path: str | None = None
    free_bytes: int | None = None
    writable: bool = True


class MetricsHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: HealthDb
    disk: DiskStatus
    uptime_s: float
    version: str
