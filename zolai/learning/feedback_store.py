"""Feedback storage and learning signal extraction (Phase 5).

Captures every user interaction as a learning signal:
- Query + response + citations
- User feedback (thumbs up/down, corrections)
- Implicit signals (dwell time, follow-up queries)
- Model self-evaluation (retrieval quality, citation coverage)

All signals flow to a review queue for human validation, then to
fine-tuning datasets. Pure additive schema — never mutates canonical data.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..data.repositories import get_engine

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """Types of learning signals we capture."""
    EXPLICIT_UP = "explicit_up"          # User thumbs up
    EXPLICIT_DOWN = "explicit_down"      # User thumbs down
    CORRECTION = "correction"            # User provided corrected answer
    IMPLICIT_DWELL = "implicit_dwell"    # Long dwell time (engagement)
    IMPLICIT_REFINE = "implicit_refine"  # User refined query
    IMPLICIT_ABANDON = "implicit_abandon" # User left without feedback
    MODEL_SELF_EVAL = "model_self_eval"  # Model rated its own confidence


class FeedbackStatus(str, Enum):
    """Review status of a learning signal."""
    PENDING = "pending"      # Awaiting human review
    APPROVED = "approved"    # Human approved for training
    REJECTED = "rejected"    # Human rejected
    AUTO_APPROVED = "auto_approved"  # High-confidence auto-approval


@dataclass
class LearningSignal:
    """A single learning signal from a user interaction."""
    # Core identity
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user_id: Optional[str] = None  # None for anonymous

    # Interaction context
    query: str = ""
    response: str = ""
    citations: list[dict] = field(default_factory=list)
    retrieval_only: bool = False
    provider: str = ""
    model: str = ""
    engine_mode: str = "rule"

    # Feedback
    feedback_type: FeedbackType = FeedbackType.IMPLICIT_DWELL
    feedback_value: Optional[str] = None  # For corrections: corrected text
    confidence: float = 0.5  # Model's self-reported confidence

    # Metadata
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


class FeedbackStore:
    """Persists and manages learning signals."""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create learning tables if they don't exist."""
        with self.engine.begin() as conn:
            # Learning signals table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS learning_signals (
                    signal_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT,
                    query TEXT NOT NULL,
                    response TEXT NOT NULL,
                    citations TEXT NOT NULL DEFAULT '[]',
                    retrieval_only INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    engine_mode TEXT NOT NULL DEFAULT 'rule',
                    feedback_type TEXT NOT NULL,
                    feedback_value TEXT,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewer_id TEXT,
                    reviewed_at TEXT,
                    review_notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_learning_signals_session
                ON learning_signals(session_id)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_learning_signals_status
                ON learning_signals(status)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_learning_signals_created
                ON learning_signals(created_at)
            """))

            # Review queue for human validation
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS learning_review_queue (
                    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 5,
                    assigned_to TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(signal_id) REFERENCES learning_signals(signal_id)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_review_queue_status
                ON learning_review_queue(status)
            """))

            # Training dataset exports (versioned)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS training_datasets (
                    dataset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    signal_count INTEGER NOT NULL,
                    format TEXT NOT NULL,  -- 'jsonl', 'chatml', 'alpaca'
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
            """))

    def store_signal(self, signal: LearningSignal) -> str:
        """Store a learning signal, return signal_id."""
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO learning_signals (
                    signal_id, session_id, user_id, query, response,
                    citations, retrieval_only, provider, model, engine_mode,
                    feedback_type, feedback_value, confidence,
                    status, created_at, updated_at, metadata
                ) VALUES (
                    :signal_id, :session_id, :user_id, :query, :response,
                    :citations, :retrieval_only, :provider, :model, :engine_mode,
                    :feedback_type, :feedback_value, :confidence,
                    :status, :created_at, :updated_at, :metadata
                )
            """), {
                "signal_id": signal.signal_id,
                "session_id": signal.session_id,
                "user_id": signal.user_id,
                "query": signal.query,
                "response": signal.response,
                "citations": json.dumps(signal.citations),
                "retrieval_only": 1 if signal.retrieval_only else 0,
                "provider": signal.provider,
                "model": signal.model,
                "engine_mode": signal.engine_mode,
                "feedback_type": signal.feedback_type.value,
                "feedback_value": signal.feedback_value,
                "confidence": signal.confidence,
                "status": FeedbackStatus.PENDING.value,
                "created_at": signal.timestamp.isoformat(),
                "updated_at": signal.timestamp.isoformat(),
                "metadata": json.dumps(signal.metadata),
            })

            # Add to review queue based on priority
            priority = self._calculate_priority(signal)
            conn.execute(text("""
                INSERT INTO learning_review_queue (signal_id, priority, status, created_at)
                VALUES (:signal_id, :priority, 'pending', :created_at)
            """), {
                "signal_id": signal.signal_id,
                "priority": priority,
                "created_at": signal.timestamp.isoformat(),
            })

        return signal.signal_id

    def _calculate_priority(self, signal: LearningSignal) -> int:
        """Calculate review priority (1=highest, 10=lowest)."""
        # Explicit negative feedback = highest priority
        if signal.feedback_type == FeedbackType.EXPLICIT_DOWN:
            return 1
        # Corrections = high priority
        if signal.feedback_type == FeedbackType.CORRECTION:
            return 2
        # Low confidence model responses = medium priority
        if signal.confidence < 0.4:
            return 3
        # Retrieval-only responses = medium priority
        if signal.retrieval_only:
            return 4
        # Explicit up = lower priority (validation)
        if signal.feedback_type == FeedbackType.EXPLICIT_UP:
            return 5
        # Implicit signals = lowest priority
        return 8

    def get_pending_review(self, limit: int = 50, status: str = "pending") -> list[dict]:
        """Get signals pending review."""
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT ls.*, lrq.queue_id, lrq.priority
                FROM learning_signals ls
                JOIN learning_review_queue lrq ON ls.signal_id = lrq.signal_id
                WHERE lrq.status = :status
                ORDER BY lrq.priority ASC, ls.created_at ASC
                LIMIT :limit
            """), {"status": status, "limit": limit}).fetchall()
            return [dict(r._mapping) for r in rows]

    def approve_signal(self, signal_id: str, reviewer_id: str, notes: str = "") -> bool:
        """Approve a signal for training dataset inclusion."""
        with self.engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE learning_signals
                SET status = 'approved', reviewer_id = :reviewer_id,
                    reviewed_at = :reviewed_at, review_notes = :notes,
                    updated_at = :updated_at
                WHERE signal_id = :signal_id AND status = 'pending'
            """), {
                "signal_id": signal_id,
                "reviewer_id": reviewer_id,
                "reviewed_at": datetime.utcnow().isoformat(),
                "notes": notes,
                "updated_at": datetime.utcnow().isoformat(),
            })
            if result.rowcount:
                conn.execute(text("""
                    UPDATE learning_review_queue
                    SET status = 'completed', completed_at = :completed_at
                    WHERE signal_id = :signal_id
                """), {
                    "signal_id": signal_id,
                    "completed_at": datetime.utcnow().isoformat(),
                })
                return True
        return False

    def reject_signal(self, signal_id: str, reviewer_id: str, notes: str = "") -> bool:
        """Reject a signal."""
        with self.engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE learning_signals
                SET status = 'rejected', reviewer_id = :reviewer_id,
                    reviewed_at = :reviewed_at, review_notes = :notes,
                    updated_at = :updated_at
                WHERE signal_id = :signal_id AND status = 'pending'
            """), {
                "signal_id": signal_id,
                "reviewer_id": reviewer_id,
                "reviewed_at": datetime.utcnow().isoformat(),
                "notes": notes,
                "updated_at": datetime.utcnow().isoformat(),
            })
            if result.rowcount:
                conn.execute(text("""
                    UPDATE learning_review_queue
                    SET status = 'completed', completed_at = :completed_at
                    WHERE signal_id = :signal_id
                """), {
                    "signal_id": signal_id,
                    "completed_at": datetime.utcnow().isoformat(),
                })
                return True
        return False

    def export_training_dataset(
        self,
        name: str,
        version: str,
        format: str = "jsonl",
        min_confidence: float = 0.7,
        status_filter: str = "approved"
    ) -> str:
        """Export approved signals as a training dataset."""
        from pathlib import Path

        dataset_id = str(uuid.uuid4())
        export_dir = Path("/tmp/training_datasets")
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"{name}_v{version}.{format}"

        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT query, response, citations, metadata
                FROM learning_signals
                WHERE status = :status AND confidence >= :min_conf
            """), {"status": status_filter, "min_conf": min_confidence}).fetchall()

        # Format as JSONL (one JSON object per line)
        with open(export_path, "w") as f:
            for row in rows:
                record = {
                    "prompt": row.query,
                    "completion": row.response,
                    "citations": json.loads(row.citations) if row.citations else [],
                    "metadata": json.loads(row.metadata) if row.metadata else {},
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Record dataset metadata
        dataset_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO training_datasets (
                    dataset_id, name, version, signal_count, format, path, created_at, metadata
                ) VALUES (
                    :dataset_id, :name, :version, :count, :format, :path, :created_at, :metadata
                )
            """), {
                "dataset_id": dataset_id,
                "name": name,
                "version": version,
                "count": len(rows),
                "format": format,
                "path": str(export_path),
                "created_at": datetime.utcnow().isoformat(),
                "metadata": json.dumps({"min_confidence": min_confidence, "status_filter": status_filter}),
            })

        return str(export_path)


# Global instance
_feedback_store: FeedbackStore | None = None


def get_feedback_store() -> FeedbackStore:
    global _feedback_store
    if _feedback_store is None:
        _feedback_store = FeedbackStore()
    return _feedback_store
