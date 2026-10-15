"""HTTP surface for the DB-driven learning tools.

The five subsystems built in ``fd9db31`` — websocket, consensus, metrics,
corpus and finetuning — are all SQLite-backed and all tested at the Python
level, but none of them had an HTTP entry point. This router mounts them under
the same ``/api/v1/learning`` prefix as
:mod:`zolai.api.learning_agents_router`, on sub-paths that do not collide with
the agent surface (``/consensus``, ``/metrics``, ``/corpus``, ``/finetuning``
and the ``/ws`` socket).

RBAC follows the existing public-read / admin-write split declared in
:mod:`zolai.api.rbac`:

* **public** — anything that only *computes* or *reads*: consensus tallies and
  discussion analysis (pure in-memory arithmetic), the metrics dashboard and
  its in-memory renderers, corpus health/stats and the per-verse quality
  probe. Same shape as ``GET /api/v1/learning/overview``.
* **admin** — anything that writes or mutates: starting a training job,
  registering/activating/deleting a model, a full corpus expansion (thousands
  of rows), a validation run, a metrics collection pass (new sample rows) and
  pushing an export to a remote InfluxDB.
* the **WebSocket** is admin-gated — see :func:`learning_websocket`.

Concurrency: everything here is SQLite/CPU-bound, so the endpoints are sync
``def`` and Starlette dispatches them to its threadpool. The few genuinely
async subsystems (metric collection, training jobs) are awaited directly from
``async def`` endpoints. ``asyncio.to_thread`` is never used inside a sync
endpoint — Starlette already runs those in a threadpool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import auth
from .auth_middleware import extract_key
from .rbac import ROLE_ADMIN, require_role, role_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/learning", tags=["learning-tools"])

# Anything that WRITES or mutates is admin-only. The same ``Depends`` guard the
# agent surface uses; ``require_role(strict=True)`` 401s an anonymous caller in
# both ``warn`` and ``enforce`` — only ``ZOLAI_API_AUTH=off`` bypasses it.
AdminAuth = Depends(require_role("admin", strict=True))


# ── Engine resolution ───────────────────────────────────────────────────────
#
# Every subsystem takes ``engine=None`` and falls back to ``get_engine()``. A
# test (or an embedder) can point the whole surface at a throwaway SQLite file
# by setting ``app.state.learning_engine``; production leaves it unset and gets
# the canonical database.


def _resolve_engine(app) -> Any:
    """The SQLAlchemy engine for ``app`` (``app.state.learning_engine`` or default)."""
    from ..data.repositories import get_engine

    engine = getattr(app.state, "learning_engine", None)
    return engine if engine is not None else get_engine()


def db_engine(request: Request):
    """Engine for an HTTP request — injected by the test suite via app.state."""
    return _resolve_engine(request.app)


def ws_engine(websocket: WebSocket):
    """Engine for the WebSocket route (same override, WebSocket variant)."""
    return _resolve_engine(websocket.app)


# ── Schemas ─────────────────────────────────────────────────────────────────


class VoteIn(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    agent_role: str = Field(default="", max_length=64)
    vote_type: str = Field(default="agree", max_length=32)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=2000)
    conditions: list[str] = Field(default_factory=list, max_length=50)


class ConsensusBuildIn(BaseModel):
    topic: str = Field(..., min_length=1, max_length=512)
    votes: list[VoteIn] = Field(default_factory=list, max_length=200)
    agent_weights: dict[str, float] | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DiscussionMessageIn(BaseModel):
    from_agent: str = Field(default="", max_length=64)
    content: str = Field(default="", max_length=4000)
    timestamp: str = Field(default="", max_length=64)


class DiscussionAnalyzeIn(BaseModel):
    messages: list[DiscussionMessageIn] = Field(default_factory=list, max_length=500)


class DecisionIn(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    agent_role: str = Field(default="", max_length=64)
    was_correct: bool
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    topic: str = Field(default="general", max_length=256)


class WeightsIn(BaseModel):
    decisions: list[DecisionIn] = Field(default_factory=list, max_length=500)


class MetricIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    value: float
    labels: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="api", max_length=64)


class MetricsRecordIn(BaseModel):
    metrics: list[MetricIn] = Field(..., min_length=1, max_length=1000)


class ExportIn(BaseModel):
    hours: int = Field(default=24, ge=1, le=720)
    url: str = Field(default="http://localhost:8086", max_length=512)
    token: str = Field(default="", max_length=1024)
    org: str = Field(default="zolai", max_length=128)
    bucket: str = Field(default="metrics", max_length=128)


class CorpusExpandIn(BaseModel):
    max_books: int | None = Field(default=None, ge=1, le=66)
    include_sentences: bool = True


class CorpusValidateIn(BaseModel):
    zvs_limit: int = Field(default=1000, ge=1, le=100_000)
    save: bool = True


class CorpusCheckIn(BaseModel):
    refs: list[str] = Field(..., min_length=1, max_length=200)


class ModelRegisterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(..., min_length=1, max_length=64)
    base_model: str = Field(default="", max_length=256)
    training_method: str = Field(default="lora", max_length=32)
    model_path: str = Field(default="", max_length=1024)
    metrics: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=50)


class ModelStatusIn(BaseModel):
    status: str = Field(default="registered", max_length=32)


class TrainingJobIn(BaseModel):
    method: str = Field(default="lora", max_length=32)
    base_model: str = Field(default="opencode/nemotron-3-ultra-free", max_length=256)
    lora_rank: int = Field(default=16, ge=1, le=512)
    lora_alpha: int = Field(default=32, ge=1, le=1024)
    learning_rate: float = Field(default=2e-4, gt=0.0, le=1.0)
    batch_size: int = Field(default=4, ge=1, le=256)
    max_steps: int = Field(default=1000, ge=1, le=5000)
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    status_filter: str = Field(default="approved", max_length=32)
    #: How long to wait for the queued job to leave ``pending`` before the
    #: response is sent. Bounded so the endpoint can never block on training.
    wait_seconds: float = Field(default=1.0, ge=0.0, le=10.0)


#: Bounded wait for a queued training job to pick up (see ``wait_seconds``).
_JOB_POLL_STEP = 0.05


# ── Per-engine instance caches ──────────────────────────────────────────────
#
# ``MetricsDashboard``, ``WebSocketServer`` and ``FineTuningPipeline`` all hold
# in-memory state (sockets, queued jobs). One instance per engine, mirroring the
# per-engine registry cache in ``agent_registry.get_agent_registry``.

_pipelines: dict[int, Any] = {}
_servers: dict[int, Any] = {}


def _pipeline(engine):
    """The fine-tuning pipeline for ``engine`` (jobs persist across requests)."""
    from ..learning.finetuning.pipeline import FineTuningPipeline

    cached = _pipelines.get(id(engine))
    if cached is not None and cached.engine is engine:
        return cached
    pipe = FineTuningPipeline(engine)
    _pipelines[id(engine)] = pipe
    return pipe


def _ws_server(engine):
    """The WebSocket server for ``engine``, with every handler registered."""
    from ..learning.agents.agent_registry import get_agent_registry
    from ..learning.websocket.handlers import (
        DiscussionHandler,
        MetricsHandler,
        TaskHandler,
    )
    from ..learning.websocket.server import WebSocketServer

    cached = _servers.get(id(engine))
    if cached is not None and cached[1] is engine:
        return cached[0]

    server = WebSocketServer()
    registry = get_agent_registry(engine)
    # ``_RegistryBackedHandler.handle_message`` dispatches on ``_<type>``, so the
    # registered message types are exactly the handler method suffixes. It takes
    # the live connection manager as a third argument while the server dispatches
    # ``handler(connection_id, message)`` — bind the manager per call.
    bindings = (
        (DiscussionHandler, ("start_discussion", "discussion_message",
                             "end_discussion", "get_consensus",
                             "get_discussion_status")),
        (TaskHandler, ("submit_task", "get_task_status", "cancel_task",
                       "subscribe_tasks")),
        (MetricsHandler, ("subscribe_metrics", "get_system_status",
                          "get_agent_metrics")),
    )
    for factory, message_types in bindings:
        handler = factory(engine=engine, registry=registry)

        async def dispatch(connection_id, message, _handler=handler):
            await _handler.handle_message(connection_id, message, server.manager)

        for message_type in message_types:
            server.register_handler(message_type, dispatch)

    _servers[id(engine)] = (server, engine)
    return server


# ── Consensus ───────────────────────────────────────────────────────────────
#
# Pure computation: the tallies never touch SQLite, so they are public reads in
# the same spirit as ``GET /api/v1/learning/overview``. The vote/message lists
# are bounded by the pydantic models above so an anonymous caller cannot turn a
# tally into a CPU denial of service.


@router.get("/consensus/vote-types")
def consensus_vote_types() -> dict[str, Any]:
    from ..learning.consensus.weighted_voting import VALID_VOTE_TYPES

    return {"vote_types": list(VALID_VOTE_TYPES)}


@router.post("/consensus/build")
def consensus_build(payload: ConsensusBuildIn) -> dict[str, Any]:
    """Weighted-vote tally over the supplied agent votes (no DB write)."""
    from ..learning.consensus.weighted_voting import ConsensusBuilder

    builder = ConsensusBuilder(consensus_threshold=payload.threshold or 0.66)
    for vote in payload.votes:
        builder.add_agent_vote(
            vote.agent_id,
            vote.agent_role,
            vote.vote_type,
            vote.confidence,
            vote.reasoning,
            vote.conditions,
        )
    result = builder.build(payload.topic, agent_weights=payload.agent_weights)
    return {"consensus": result, "votes_counted": len(payload.votes)}


@router.post("/consensus/analyze")
def consensus_analyze(payload: DiscussionAnalyzeIn) -> dict[str, Any]:
    """Turn a transcript into insights + action items (no DB write)."""
    from ..learning.consensus.discussion_analyzer import DiscussionAnalyzer

    analyzer = DiscussionAnalyzer()
    messages = [m.model_dump() for m in payload.messages]
    insights = analyzer.analyze_discussion(messages)
    return {
        "insights": insights,
        "action_items": analyzer.extract_action_items(messages),
    }


@router.post("/consensus/weights")
def consensus_weights(payload: WeightsIn) -> dict[str, Any]:
    """Re-score agents from a batch of decision outcomes (no DB write)."""
    from ..learning.consensus.confidence_scoring import ConfidenceScorer

    scorer = ConfidenceScorer()
    for decision in payload.decisions:
        scorer.record_decision(
            decision.agent_id,
            decision.agent_role,
            decision.was_correct,
            decision.confidence,
            decision.topic,
        )

    agents = sorted({d.agent_id for d in payload.decisions})
    return {
        "agents": [scorer.get_agent_stats(agent_id) for agent_id in agents],
        "weights": scorer.get_all_agent_weights(),
    }


# ── Metrics ─────────────────────────────────────────────────────────────────


def _dashboard(engine):
    from ..learning.metrics.dashboard import create_dashboard

    return create_dashboard(engine)


@router.get("/metrics")
def metrics_dashboard(hours: int = 24, engine=Depends(db_engine)) -> dict[str, Any]:
    """Windowed dashboard data — a read, like ``GET /learning/overview``."""
    if not 1 <= hours <= 720:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_hours", "hours": hours})
    return _dashboard(engine).get_dashboard_data(hours=hours)


@router.get("/metrics/names")
def metrics_names(engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.metrics.dashboard import create_collector

    return {"names": create_collector(engine).get_all_metric_names()}


@router.get("/metrics/stream/{name}")
def metrics_stream(name: str, limit: int = 100,
                   engine=Depends(db_engine)) -> dict[str, Any]:
    """Raw samples for one metric name, newest first."""
    from ..learning.metrics.dashboard import create_collector

    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_limit", "limit": limit})
    collector = create_collector(engine)
    return {
        "name": name,
        "samples": collector.query_metrics(name, limit=limit),
        "stats": collector.get_metric_stats(name),
    }


@router.get("/metrics/export/{fmt}")
def metrics_export(fmt: str, hours: int = 24,
                   engine=Depends(db_engine)) -> Response:
    """Render metrics in a wire format and return it in the response body.

    Nothing is written to disk and nothing is pushed anywhere — the rendered
    string travels back to the caller. The destination-pushing variant is
    ``POST /metrics/export/influxdb``, which is admin-only.
    """
    from ..learning.metrics.exporter import MetricsExporter

    if not 1 <= hours <= 720:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_hours", "hours": hours})

    exporter = MetricsExporter(_dashboard(engine))
    renderers = {
        "json": (exporter.export_json, "application/json"),
        "prometheus": (exporter.export_prometheus, "text/plain; version=0.0.4"),
        "csv": (exporter.export_csv, "text/csv"),
        "influxdb": (exporter.export_influxdb_line, "text/plain"),
    }
    if fmt == "grafana":
        import json as _json

        return Response(
            content=_json.dumps(exporter.export_grafana_dashboard(), indent=2),
            media_type="application/json",
        )
    if fmt not in renderers:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_format", "format": fmt,
            "supported": sorted({*renderers, "grafana"}),
        })
    render, media_type = renderers[fmt]
    return Response(content=render(hours=hours) if fmt != "prometheus" else render(),
                    media_type=media_type)


@router.post("/metrics/collect")
async def metrics_collect(_auth: dict[str, Any] = AdminAuth,
                        engine=Depends(db_engine)) -> dict[str, Any]:
    """Sample system/agent/learning/feedback metrics and store the rows.

    Writes one ``learning_metrics`` row per sample, so it is admin-only.
    """
    return {"collected": await _dashboard(engine).collect_all_metrics()}


@router.post("/metrics/export/influxdb")
def metrics_export_influxdb(payload: ExportIn, _auth: dict[str, Any] = AdminAuth,
                            engine=Depends(db_engine)) -> dict[str, Any]:
    """Push metrics to a remote InfluxDB — a write to an external destination."""
    from ..learning.metrics.exporter import MetricsExporter

    ok = MetricsExporter(_dashboard(engine)).export_to_influxdb(
        hours=payload.hours,
        url=payload.url,
        token=payload.token,
        org=payload.org,
        bucket=payload.bucket,
    )
    return {"exported": ok, "destination": {
        "url": payload.url, "org": payload.org, "bucket": payload.bucket,
    }}


# ── Corpus ──────────────────────────────────────────────────────────────────


@router.get("/corpus/health")
def corpus_health(engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.corpus.validator import QualityChecker

    return QualityChecker(engine).get_corpus_health()


@router.get("/corpus/stats")
def corpus_stats(engine=Depends(db_engine)) -> dict[str, Any]:
    """Parallel-corpus row counts (stored pairs + coverage)."""
    from ..learning.corpus.expander import ParallelCorpusBuilder

    builder = ParallelCorpusBuilder(engine)
    return {
        "stats": builder.get_stats(),
        "stored_expansions": _expansion_count(engine),
    }


@router.get("/corpus/reports/{report_id}")
def corpus_report(report_id: str, engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.corpus.validator import CorpusValidator

    validator = CorpusValidator(engine)
    report = validator.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_report",
                                                    "report_id": report_id})
    report["issue_count"] = validator.count_issues(report_id)
    return report


@router.post("/corpus/check")
def corpus_check(payload: CorpusCheckIn,
                 engine=Depends(db_engine)) -> dict[str, Any]:
    """Per-verse quality probe — a read, so it stays public."""
    from ..learning.corpus.validator import QualityChecker

    return {"checks": QualityChecker(engine).check_batch(payload.refs)}


@router.post("/corpus/expand")
def corpus_expand(payload: CorpusExpandIn, _auth: dict[str, Any] = AdminAuth,
                  engine=Depends(db_engine)) -> dict[str, Any]:
    """Expand the whole Bible corpus into SQLite.

    Writes one row per verse expansion plus every parallel pair, so it is
    admin-only even though it is idempotent on re-run.
    """
    from ..learning.corpus.expander import BibleCorpusExpander

    expander = BibleCorpusExpander(engine)
    try:
        return {"run": expander.expand_all(
            max_books=payload.max_books,
            include_sentences=payload.include_sentences,
        )}
    except Exception as exc:
        logger.exception("corpus expansion failed")
        raise HTTPException(status_code=500, detail={
            "error": "expansion_failed", "message": f"{type(exc).__name__}: {exc}",
        }) from exc


@router.post("/corpus/validate")
def corpus_validate(payload: CorpusValidateIn, _auth: dict[str, Any] = AdminAuth,
                    engine=Depends(db_engine)) -> dict[str, Any]:
    """Run the full quality validation (persists the report by default)."""
    from ..learning.corpus.validator import CorpusValidator

    validator = CorpusValidator(engine)
    try:
        report = validator.validate_all(zvs_limit=payload.zvs_limit)
    except Exception as exc:
        logger.exception("corpus validation failed")
        raise HTTPException(status_code=500, detail={
            "error": "validation_failed", "message": f"{type(exc).__name__}: {exc}",
        }) from exc

    body: dict[str, Any] = {"report": report.as_dict()}
    if payload.save:
        body["report_id"] = validator.save_report(report)
    return body


def _expansion_count(engine) -> int:
    from ..learning.corpus.expander import BibleCorpusExpander

    return BibleCorpusExpander(engine).count_expansions()


# ── Finetuning ──────────────────────────────────────────────────────────────
#
# The whole prefix is admin: training jobs spend real compute, model registry
# rows decide what serves traffic, and the stored datasets are raw feedback.


@router.get("/finetuning/models")
def list_models(status: str | None = None, engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.finetuning.model_manager import ModelManager

    return {"models": ModelManager(engine).list_models(status=status)}


@router.get("/finetuning/models/active")
def get_active_model(engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.finetuning.model_manager import ModelManager

    model = ModelManager(engine).get_active_model()
    if model is None:
        raise HTTPException(status_code=404, detail={"error": "no_active_model"})
    return model


@router.get("/finetuning/models/{model_id}")
def get_model(model_id: str, engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.finetuning.model_manager import ModelManager

    model = ModelManager(engine).get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_model",
                                                    "model_id": model_id})
    return model


@router.post("/finetuning/models", status_code=201)
def register_model(payload: ModelRegisterIn, _auth: dict[str, Any] = AdminAuth,
                   engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.finetuning.model_manager import ModelManager

    model_id = ModelManager(engine).register_model(
        name=payload.name,
        version=payload.version,
        base_model=payload.base_model,
        training_method=payload.training_method,
        model_path=payload.model_path,
        metrics=payload.metrics,
        tags=payload.tags,
    )
    return {"model_id": model_id}


@router.patch("/finetuning/models/{model_id}")
def update_model(model_id: str, payload: ModelStatusIn,
                 _auth: dict[str, Any] = AdminAuth,
                 engine=Depends(db_engine)) -> dict[str, Any]:
    """Set a model's lifecycle status (``registered``/``checkpoint``/``retired``).

    Promotion is a separate route (``POST …/{id}/activate``) so a status write
    can never silently change what serves traffic.
    """
    from ..learning.finetuning.model_manager import ModelManager

    manager = ModelManager(engine)
    if not manager.update_status(model_id, payload.status):
        raise HTTPException(status_code=404, detail={"error": "unknown_model",
                                                    "model_id": model_id})
    return {"model_id": model_id, "status": payload.status}


@router.post("/finetuning/models/{model_id}/activate")
def activate_model(model_id: str, _auth: dict[str, Any] = AdminAuth,
                   engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.finetuning.model_manager import ModelManager

    if not ModelManager(engine).set_active_model(model_id):
        raise HTTPException(status_code=404, detail={"error": "unknown_model",
                                                    "model_id": model_id})
    return {"model_id": model_id, "active": True}


@router.delete("/finetuning/models/{model_id}")
def delete_model(model_id: str, force: bool = False,
                 _auth: dict[str, Any] = AdminAuth,
                 engine=Depends(db_engine)) -> dict[str, Any]:
    from ..learning.finetuning.model_manager import ModelManager

    try:
        deleted = ModelManager(engine).delete_model(model_id, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail={"error": "model_conflict", "message": str(exc)}) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail={"error": "unknown_model",
                                                    "model_id": model_id})
    return {"model_id": model_id, "deleted": True}


@router.get("/finetuning/jobs")
def list_jobs(engine=Depends(db_engine)) -> dict[str, Any]:
    return {"jobs": _pipeline(engine).list_jobs()}


@router.get("/finetuning/jobs/{job_id}")
def get_job(job_id: str, engine=Depends(db_engine)) -> dict[str, Any]:
    job = _pipeline(engine).get_job_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_job",
                                                    "job_id": job_id})
    return job


@router.post("/finetuning/jobs", status_code=201)
async def create_job(payload: TrainingJobIn, _auth: dict[str, Any] = AdminAuth,
                     engine=Depends(db_engine)) -> dict[str, Any]:
    """Build a training dataset from feedback signals and queue a training job.

    This spends real compute and writes ``finetune_datasets`` rows, so it is
    admin-only.
    """
    from ..learning.finetuning.pipeline import TrainingConfig, TrainingMethod

    try:
        method = TrainingMethod(payload.method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_method", "method": payload.method,
            "supported": [m.value for m in TrainingMethod],
        }) from exc

    pipeline = _pipeline(engine)
    config = TrainingConfig(
        method=method,
        base_model=payload.base_model,
        lora_rank=payload.lora_rank,
        lora_alpha=payload.lora_alpha,
        learning_rate=payload.learning_rate,
        batch_size=payload.batch_size,
        max_steps=payload.max_steps,
    )
    try:
        job_id = await pipeline.create_training_job(
            config,
            min_confidence=payload.min_confidence,
            status_filter=payload.status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "no_feedback_signals", "message": str(exc),
        }) from exc

    if not pipeline.running:
        await pipeline.start()

    job = await _await_job(pipeline, job_id, payload.wait_seconds)
    return job or {"job_id": job_id, "status": "pending"}


@router.post("/finetuning/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, _auth: dict[str, Any] = AdminAuth,
                     engine=Depends(db_engine)) -> dict[str, Any]:
    pipeline = _pipeline(engine)
    cancelled = await pipeline.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail={
            "error": "not_cancellable", "job_id": job_id,
            "status": (pipeline.get_job_status(job_id) or {}).get("status"),
        })
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/finetuning/datasets/{dataset_id}/items")
def dataset_items(dataset_id: str, limit: int = 100,
                  engine=Depends(db_engine)) -> dict[str, Any]:
    pipeline = _pipeline(engine)
    pipeline.ensure_schema()
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400,
                            detail={"error": "invalid_limit", "limit": limit})
    return {
        "dataset_id": dataset_id,
        "items": pipeline.get_dataset_items(dataset_id, limit=limit),
    }


async def _await_job(pipeline, job_id: str, wait_seconds: float) -> dict[str, Any] | None:
    """Poll a queued job for at most ``wait_seconds`` (never blocks forever)."""
    if wait_seconds <= 0:
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while loop.time() < deadline:
        job = pipeline.get_job_status(job_id)
        if job and job.get("status") not in ("pending", "queued"):
            return job
        await asyncio.sleep(_JOB_POLL_STEP)
    return None


# ── WebSocket ───────────────────────────────────────────────────────────────
#
# Gating decision: **admin-only**, checked here rather than in middleware.
# ``ApiKeyMiddleware`` returns early for every non-HTTP scope
# (``if scope.get("type") != "http"``), so a WebSocket handshake never reaches
# ``PUBLIC_ROUTES``/``ADMIN_METHOD_ROUTES`` and never gets the per-IP rate
# bucket. A public socket would therefore be an *ungated* socket — strictly
# weaker than the public ``POST /api/v1/assistant/chat`` — and the handlers
# wired above (``submit_task``, ``discussion_start``) write to SQLite. So the
# endpoint resolves the key itself and requires the admin role; with
# ``ZOLAI_API_AUTH=off`` the whole app is open anyway and the socket is too.

#: Query-parameter fallback — browsers cannot set headers on a WebSocket.
WS_KEY_PARAM = "api_key"

#: RFC 6455 application code for "credential missing or not admin".
WS_FORBIDDEN = 4401


async def _ws_authorize(websocket: WebSocket) -> dict[str, Any] | None:
    """Resolve the caller and require admin; ``None`` means rejected."""
    mode = auth.api_auth_mode()
    if mode == "off":
        return {}

    token = extract_key(websocket.scope.get("headers"))
    if not token:
        token = websocket.query_params.get(WS_KEY_PARAM) or None

    record = None
    if token:
        record = await run_in_threadpool(auth.resolve_key, token)
        if record is None:
            # Imported lazily: ``session_auth`` pulls in argon2.
            from . import session_auth

            record = await run_in_threadpool(session_auth.resolve_session, token)

    if role_for(record) != ROLE_ADMIN:
        logger.info("rejected learning websocket handshake: %s", token or "anonymous")
        await websocket.close(code=WS_FORBIDDEN, reason="admin role required")
        return None
    return record


@router.websocket("/ws")
async def learning_websocket(websocket: WebSocket,
                             engine=Depends(ws_engine)) -> None:
    """Real-time agent discussions, tasks and metrics (admin-gated socket).

    Frames: ``ping``/``subscribe``/``unsubscribe`` (built in) plus
    ``start_discussion``, ``discussion_message``, ``end_discussion``,
    ``get_consensus``, ``get_discussion_status``, ``submit_task``,
    ``get_task_status``, ``cancel_task``, ``subscribe_tasks``,
    ``subscribe_metrics``, ``get_system_status``, ``get_agent_metrics``.
    """
    record = await _ws_authorize(websocket)
    if record is None:
        return

    # ``WebSocketServer.handle_connection`` performs the accept itself (via
    # ``ConnectionManager.connect``) — accepting here too would be a second
    # handshake frame.
    server = _ws_server(engine)
    agent_id = websocket.query_params.get("agent_id") or None
    user_id = websocket.query_params.get("user_id") or (
        record.get("key_prefix") if record else None
    )
    await server.handle_connection(websocket, agent_id=agent_id, user_id=user_id)