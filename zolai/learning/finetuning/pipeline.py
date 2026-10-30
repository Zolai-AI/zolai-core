"""
Fine-Tuning Pipeline - Orchestrates model training from feedback signals.

Training datasets live in SQLite (``finetune_datasets`` /
``finetune_dataset_items``) and trained-model metadata in
``finetune_model_registry`` — no JSON/JSONL files are written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.shared.clock import now, stamp

from ...data.repositories import get_engine
from ..feedback_store import FeedbackStore, FeedbackType
from .model_manager import ModelManager

logger = logging.getLogger(__name__)


class TrainingMethod(str, Enum):
    """Training methods supported."""

    LORA = "lora"
    QLORA = "qlora"
    FULL = "full"
    CONTINUOUS = "continuous"  # Incremental learning


class TrainingStatus(str, Enum):
    """Training job status."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TrainingConfig:
    """Configuration for a training job."""

    method: TrainingMethod = TrainingMethod.LORA
    base_model: str = "opencode/nemotron-3-ultra-free"
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    learning_rate: float = 2e-4
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    max_steps: int = 1000
    warmup_steps: int = 100
    save_steps: int = 500
    eval_steps: int = 500
    logging_steps: int = 10
    max_seq_length: int = 2048
    use_4bit: bool = True
    use_8bit: bool = False
    output_dir: str = "./finetuned_models"
    seed: int = 42


@dataclass
class TrainingJob:
    """A training job."""

    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: TrainingConfig = field(default_factory=TrainingConfig)
    status: TrainingStatus = TrainingStatus.PENDING
    dataset_id: str = ""
    feedback_signals: list = field(default_factory=list)
    created_at: datetime = field(default_factory=now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metrics: dict = field(default_factory=dict)
    error: str | None = None
    model_id: str | None = None


class FineTuningPipeline:
    """Main pipeline for fine-tuning models from feedback signals."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()
        self.feedback_store = FeedbackStore(engine=self.engine)
        self.model_manager = ModelManager(engine=self.engine)
        self.jobs: dict = {}
        self.job_queue: asyncio.Queue = asyncio.Queue()
        self.running = False
        self.current_job: TrainingJob | None = None

    # ── Dataset storage ────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create the dataset tables if missing (additive, idempotent)."""
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS finetune_datasets (
                    dataset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    format TEXT NOT NULL,
                    signal_count INTEGER NOT NULL,
                    min_confidence REAL NOT NULL,
                    status_filter TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS finetune_dataset_items (
                    item_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    instruction TEXT NOT NULL,
                    input TEXT NOT NULL,
                    output TEXT NOT NULL,
                    signal_id TEXT,
                    feedback_type TEXT,
                    confidence REAL,
                    metadata TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_finetune_dataset_items_dataset
                ON finetune_dataset_items (dataset_id, seq)
            """))

    async def create_training_job(
        self,
        config: TrainingConfig,
        feedback_signals: list = None,
        min_confidence: float = 0.7,
        status_filter: str = "approved",
    ) -> str:
        """Create a training job from feedback signals."""
        # Get feedback signals if not provided
        if feedback_signals is None:
            feedback_signals = self._collect_feedback_signals(min_confidence, status_filter)

        if not feedback_signals:
            raise ValueError("No feedback signals available for training")

        # Build the dataset into SQLite
        dataset_id = await self._build_training_dataset(
            feedback_signals,
            min_confidence=min_confidence,
            status_filter=status_filter,
        )

        # Create job
        job = TrainingJob(
            config=config,
            dataset_id=dataset_id,
            feedback_signals=feedback_signals,
        )

        self.jobs[job.job_id] = job
        await self.job_queue.put(job)

        logger.info(f"Created training job {job.job_id} with {len(feedback_signals)} signals")
        return job.job_id

    def _collect_feedback_signals(self, min_confidence: float, status_filter: str) -> list:
        """Collect feedback signals from the store."""
        signals = self.feedback_store.get_pending_review(limit=10000, status=status_filter)

        # Filter by type and confidence
        usable_types = {
            FeedbackType.EXPLICIT_UP.value,
            FeedbackType.EXPLICIT_DOWN.value,
            FeedbackType.CORRECTION.value,
        }
        filtered = [
            signal for signal in signals
            if signal.get("feedback_type") in usable_types
            and signal.get("confidence", 0) >= min_confidence
        ]
        return filtered

    async def _build_training_dataset(
        self,
        signals: list,
        min_confidence: float = 0.7,
        status_filter: str = "approved",
    ) -> str:
        """Build a training dataset from feedback signals into SQLite.

        Returns the dataset id. The dataset id is derived from the signal set,
        so rebuilding the same signals reuses the existing dataset row.
        """
        self.ensure_schema()

        rows = []
        for seq, signal in enumerate(signals):
            rows.append({
                "item_id": f"{seq}:{signal.get('signal_id', '')}",
                "seq": seq,
                "instruction": signal.get("query", "") or "",
                "input": "",
                "output": signal.get("feedback_value") or signal.get("response", "") or "",
                "signal_id": signal.get("signal_id"),
                "feedback_type": signal.get("feedback_type"),
                "confidence": float(signal.get("confidence", 1.0) or 0.0),
            })

        dataset_id = self._dataset_id_for(rows)
        now = stamp()
        metadata = json.dumps(
            {"min_confidence": min_confidence, "status_filter": status_filter},
            sort_keys=True,
        )

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO finetune_datasets
                        (dataset_id, name, format, signal_count, min_confidence,
                         status_filter, created_at, metadata)
                    VALUES (:dataset_id, :name, 'alpaca', :signal_count,
                            :min_confidence, :status_filter, :created_at, :metadata)
                """),
                {
                    "dataset_id": dataset_id,
                    "name": f"feedback_{dataset_id}",
                    "signal_count": len(rows),
                    "min_confidence": min_confidence,
                    "status_filter": status_filter,
                    "created_at": now,
                    "metadata": metadata,
                },
            )
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO finetune_dataset_items
                        (item_id, dataset_id, seq, instruction, input, output,
                         signal_id, feedback_type, confidence, metadata)
                    VALUES (:item_id, :dataset_id, :seq, :instruction, :input,
                            :output, :signal_id, :feedback_type, :confidence, :metadata)
                """),
                [dict(row, dataset_id=dataset_id, metadata=metadata) for row in rows],
            )

        logger.info(f"Stored training dataset {dataset_id} with {len(rows)} items")
        return dataset_id

    @staticmethod
    def _dataset_id_for(rows: list) -> str:
        """Content-derived dataset id (same signals → same dataset)."""
        digest = hashlib.sha256()
        for row in rows:
            digest.update(f"{row['item_id']}|{row['instruction']}|{row['output']}\n".encode())
        return f"ds_{digest.hexdigest()[:16]}"

    def get_dataset_items(self, dataset_id: str, limit: int = 1000) -> list:
        """Read stored dataset items back."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT item_id, seq, instruction, input, output,
                           signal_id, feedback_type, confidence
                    FROM finetune_dataset_items
                    WHERE dataset_id = :dataset_id
                    ORDER BY seq LIMIT :limit
                """),
                {"dataset_id": dataset_id, "limit": limit},
            ).fetchall()
        return [dict(row._mapping) for row in rows]

    def count_dataset_items(self, dataset_id: str = None) -> int:
        """Count stored dataset items."""
        sql = "SELECT COUNT(*) FROM finetune_dataset_items"
        params: dict = {}
        if dataset_id:
            sql += " WHERE dataset_id = :dataset_id"
            params["dataset_id"] = dataset_id
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    # ── Job lifecycle ──────────────────────────────────────────────────────

    async def start(self):
        """Start the pipeline processor."""
        self.running = True
        asyncio.create_task(self._process_queue())
        logger.info("Fine-tuning pipeline started")

    async def stop(self):
        """Stop the pipeline."""
        self.running = False
        if self.current_job:
            self.current_job.status = TrainingStatus.CANCELLED
        logger.info("Fine-tuning pipeline stopped")

    async def _process_queue(self):
        """Process training job queue."""
        while self.running:
            try:
                job = await asyncio.wait_for(self.job_queue.get(), timeout=1.0)
                await self._execute_job(job)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Queue processor error: {e}")

    async def _execute_job(self, job: TrainingJob):
        """Execute a training job."""
        job.status = TrainingStatus.RUNNING
        job.started_at = now()
        self.current_job = job

        try:
            if job.config.method == TrainingMethod.LORA:
                await self._run_lora_training(job)
            elif job.config.method == TrainingMethod.QLORA:
                await self._run_qlora_training(job)
            elif job.config.method == TrainingMethod.FULL:
                await self._run_full_training(job)
            elif job.config.method == TrainingMethod.CONTINUOUS:
                await self._run_continuous_training(job)

            job.status = TrainingStatus.COMPLETED
            job.completed_at = now()
            logger.info(f"Training job {job.job_id} completed successfully")

        except Exception as e:
            job.status = TrainingStatus.FAILED
            job.error = str(e)
            job.completed_at = now()
            logger.error(f"Training job {job.job_id} failed: {e}")
        finally:
            self.current_job = None

    async def _run_lora_training(self, job: TrainingJob):
        """Run LoRA fine-tuning (simulated loop, DB-backed bookkeeping)."""
        logger.info(f"Starting LoRA training for job {job.job_id}")

        config = job.config
        for step in range(config.max_steps):
            if job.status == TrainingStatus.CANCELLED:
                break

            job.metrics["current_step"] = step + 1
            job.metrics[f"step_{step}"] = {
                "loss": 1.0 / (step + 1),
                "learning_rate": config.learning_rate,
            }

            if config.save_steps and step % config.save_steps == 0:
                job.metrics[f"checkpoint_{step}"] = "saved"

        # Register the resulting model in SQLite (no artifact files).
        job.model_id = self.model_manager.register_model(
            name=f"{config.method.value}_model",
            version=f"v{job.job_id[:8]}",
            base_model=config.base_model,
            training_method=config.method.value,
            metrics={"steps": config.max_steps, "dataset_id": job.dataset_id},
            tags=["lora"] if config.method == TrainingMethod.LORA else [],
        )
        job.metrics["final_model_id"] = job.model_id

    async def _run_qlora_training(self, job: TrainingJob):
        """Run QLoRA fine-tuning."""
        logger.info(f"Starting QLoRA training for job {job.job_id}")
        await self._run_lora_training(job)  # Same schedule as LoRA for now

    async def _run_full_training(self, job: TrainingJob):
        """Run full fine-tuning."""
        logger.info(f"Starting full training for job {job.job_id}")
        await self._run_lora_training(job)  # Placeholder

    async def _run_continuous_training(self, job: TrainingJob):
        """Run continuous/incremental training."""
        logger.info(f"Starting continuous training for job {job.job_id}")
        await self._run_lora_training(job)  # Placeholder

    # ── Introspection ──────────────────────────────────────────────────────

    def get_job_status(self, job_id: str) -> dict | None:
        """Get status of a training job."""
        job = self.jobs.get(job_id)
        if not job:
            return None

        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "config": job.config.__dict__,
            "progress": job.metrics.get("current_step", 0) / max(job.config.max_steps, 1),
            "metrics": job.metrics,
            "error": job.error,
            "dataset_id": job.dataset_id,
            "model_id": job.model_id,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def list_jobs(self, status: TrainingStatus = None) -> list:
        """List training jobs."""
        return [
            self.get_job_status(job_id)
            for job_id, job in self.jobs.items()
            if status is None or job.status == status
        ]

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a training job."""
        job = self.jobs.get(job_id)
        if job and job.status in (TrainingStatus.PENDING, TrainingStatus.QUEUED, TrainingStatus.RUNNING):
            was_running = job.status == TrainingStatus.RUNNING and self.current_job is job
            job.status = TrainingStatus.CANCELLED
            if was_running:
                # The training loop checks for CANCELLED on each step.
                logger.info(f"Job {job_id} flagged for cancellation")
            return True
        return False


# Global instance
_pipeline: FineTuningPipeline | None = None


def get_finetuning_pipeline(engine: Engine = None) -> FineTuningPipeline:
    """Get or create the global fine-tuning pipeline."""
    global _pipeline
    if _pipeline is None:
        _pipeline = FineTuningPipeline(engine)
    return _pipeline
