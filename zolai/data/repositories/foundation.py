"""Foundation Layer Repositories — Raw, Staging, Canonical, Evidence, Consensus, Meta.

Follows the same pattern as dictionary.py and base.py.
Each repository provides domain-specific queries for its table.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.engine import Engine

from .base import BaseRepository

# =============================================================================
# RAW LAYER REPOSITORIES
# =============================================================================

class FoundationRawCorpusRepository(BaseRepository):
    """Repository for raw corpus imports (foundation_raw_corpus)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_raw_corpus")

    def get_by_source_type(self, source_type: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get raw corpus entries by source type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.source_type == source_type)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_content_hash(self, content_hash: str) -> dict[str, Any] | None:
        """Get raw corpus entry by content hash."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(self.table.c.content_hash == content_hash)
            ).first()
        return self._row_to_dict(row) if row else None

    def count_by_source_type(self) -> dict[str, int]:
        """Count entries per source type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_type, COUNT(*) as cnt FROM foundation_raw_corpus "
                    "GROUP BY source_type ORDER BY cnt DESC"
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_unprocessed(self, limit: int = 0) -> list[dict[str, Any]]:
        """Get raw corpus entries that haven't been processed to staging yet."""
        with self._engine.connect() as conn:
            query = self.table.select().order_by(self.table.c.id)
            if limit > 0:
                query = query.limit(limit)
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(row) for row in rows]


class FoundationRawLLMRepository(BaseRepository):
    """Repository for raw LLM outputs (foundation_raw_llm)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_raw_llm")

    def get_by_model(self, model: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get LLM outputs by model name."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.model == model)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_prompt_hash(self, prompt_hash: str) -> list[dict[str, Any]]:
        """Get LLM outputs by prompt hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.prompt_hash == prompt_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


# =============================================================================
# STAGING LAYER REPOSITORIES
# =============================================================================

class FoundationStagingWordsRepository(BaseRepository):
    """Repository for staged word candidates (foundation_staging_words)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_staging_words")

    def get_by_form(self, form: str) -> list[dict[str, Any]]:
        """Get staged words by exact form match."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.form) == form.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_pos(self, pos: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get staged words by part of speech."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.pos == pos)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_source_hash(self, source_hash: str) -> list[dict[str, Any]]:
        """Get staged words by source hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source_hash == source_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_zvs_compliant(self, compliant: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        """Get staged words filtered by ZVS compliance."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.zvs_compliant == (1 if compliant else 0))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_high_frequency(self, min_freq: int = 5, limit: int = 100) -> list[dict[str, Any]]:
        """Get staged words with frequency above threshold."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.frequency >= min_freq)
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_hash(self, source_hash: str) -> list[dict[str, Any]]:
        """Get staged words by source hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source_hash == source_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_unpromoted(self, limit: int = 0) -> list[dict[str, Any]]:
        """Get staged words that haven't been promoted to canonical yet."""
        with self._engine.connect() as conn:
            query = self.table.select().order_by(self.table.c.id)
            if limit > 0:
                query = query.limit(limit)
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(row) for row in rows]


class FoundationStagingSentencesRepository(BaseRepository):
    """Repository for staged sentence analyses (foundation_staging_sentences)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_staging_sentences")

    def get_by_text(self, text: str) -> list[dict[str, Any]]:
        """Get staged sentences by exact text match."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.text) == text.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_source_hash(self, source_hash: str) -> list[dict[str, Any]]:
        """Get staged sentences by source hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source_hash == source_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_text(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search staged sentences by text substring."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.text.ilike(f"%{query}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_hash(self, source_hash: str) -> list[dict[str, Any]]:
        """Get staged sentences by source hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source_hash == source_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


class FoundationStagingParagraphsRepository(BaseRepository):
    """Repository for staged paragraph analyses (foundation_staging_paragraphs)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_staging_paragraphs")

    def get_by_source_hash(self, source_hash: str) -> list[dict[str, Any]]:
        """Get staged paragraphs by source hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source_hash == source_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_hash(self, source_hash: str) -> list[dict[str, Any]]:
        """Get staged paragraphs by source hash."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source_hash == source_hash)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


class FoundationStagingEvidenceRepository(BaseRepository):
    """Repository for staged evidence bundles (foundation_staging_evidence)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_staging_evidence")

    def get_by_fact(self, fact_type: str, fact_key: str) -> list[dict[str, Any]]:
        """Get evidence by fact type and key."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.fact_type == fact_type)
                    & (self.table.c.fact_key == fact_key)
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_tier(self, tier: int, limit: int = 100) -> list[dict[str, Any]]:
        """Get evidence by tier."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.tier == tier)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


# =============================================================================
# CANONICAL LAYER REPOSITORIES
# =============================================================================

class CanonicalWordsRepository(BaseRepository):
    """Repository for verified canonical words (canonical_words)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "canonical_words", id_column="id", version_column="version")

    def get_by_form(self, form: str) -> list[dict[str, Any]]:
        """Get canonical words by exact form match."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.form) == form.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_pos(self, pos: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get canonical words by part of speech."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.pos == pos)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_verified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get verified canonical words."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verified_at.isnot(None))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_unverified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get unverified canonical words."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verified_at.is_(None))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_version(self, version: int, limit: int = 100) -> list[dict[str, Any]]:
        """Get canonical words by version."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.version == version)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_high_frequency(self, min_freq: int = 5, limit: int = 100) -> list[dict[str, Any]]:
        """Get canonical words with frequency above threshold."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.frequency >= min_freq)
                .order_by(self.table.c.frequency.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


class CanonicalSentencesRepository(BaseRepository):
    """Repository for verified canonical sentences (canonical_sentences)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "canonical_sentences", id_column="id", version_column="version")

    def get_by_text(self, text: str) -> list[dict[str, Any]]:
        """Get canonical sentences by exact text match."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(
                    func.lower(self.table.c.text) == text.lower()
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_verified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get verified canonical sentences."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verified_at.isnot(None))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_unverified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get unverified canonical sentences."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verified_at.is_(None))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search_text(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search canonical sentences by text substring."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.text.ilike(f"%{query}%"))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


class CanonicalParagraphsRepository(BaseRepository):
    """Repository for verified canonical paragraphs (canonical_paragraphs)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "canonical_paragraphs", id_column="id", version_column="version")

    def get_verified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get verified canonical paragraphs."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verified_at.isnot(None))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_unverified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get unverified canonical paragraphs."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verified_at.is_(None))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


# =============================================================================
# EVIDENCE / CONSENSUS / VERIFICATION REPOSITORIES
# =============================================================================

class FoundationEvidenceRepository(BaseRepository):
    """Repository for evidence records (foundation_evidence)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_evidence")

    def get_by_fact(self, fact_type: str, fact_key: str) -> list[dict[str, Any]]:
        """Get evidence by fact type and key."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.fact_type == fact_type)
                    & (self.table.c.fact_key == fact_key)
                )
                .order_by(self.table.c.tier)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_tier(self, tier: int, limit: int = 100) -> list[dict[str, Any]]:
        """Get evidence by tier."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.tier == tier)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_source(self, source: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get evidence by source table."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.source == source)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_high_confidence(self, min_confidence: float = 0.8, limit: int = 100) -> list[dict[str, Any]]:
        """Get high-confidence evidence."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.confidence >= min_confidence)
                .order_by(self.table.c.confidence.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


class FoundationVerificationsRepository(BaseRepository):
    """Repository for verification results (foundation_verifications)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_verifications")

    def get_by_candidate(self, candidate_id: int) -> list[dict[str, Any]]:
        """Get verifications for a candidate."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.candidate_id == candidate_id)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_verifier(self, verifier: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get verifications by verifier name."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verifier == verifier)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_passed(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get passed verifications."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.passed == 1)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_failed(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get failed verifications."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.passed == 0)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


class FoundationConsensusRepository(BaseRepository):
    """Repository for consensus decisions (foundation_consensus)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_consensus")

    def get_by_fact(self, fact_type: str, fact_key: str) -> list[dict[str, Any]]:
        """Get consensus by fact type and key."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.fact_type == fact_type)
                    & (self.table.c.fact_key == fact_key)
                )
                .order_by(self.table.c.created_at.desc())
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_method(self, method: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get consensus by method."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.method == method)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_high_confidence(self, min_confidence: float = 0.8, limit: int = 100) -> list[dict[str, Any]]:
        """Get high-confidence consensus decisions."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.confidence >= min_confidence)
                .order_by(self.table.c.confidence.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


# =============================================================================
# META REPOSITORIES
# =============================================================================

class FoundationBatchesRepository(BaseRepository):
    """Repository for batch job runs (foundation_batches)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_batches")

    def get_by_type(self, batch_type: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get batches by type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.batch_type == batch_type)
                .order_by(self.table.c.started_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_status(self, status: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get batches by status."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.status == status)
                .order_by(self.table.c.started_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_latest(self, batch_type: str) -> dict[str, Any] | None:
        """Get latest batch of a given type."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select()
                .where(self.table.c.batch_type == batch_type)
                .order_by(self.table.c.started_at.desc())
                .limit(1)
            ).first()
        return self._row_to_dict(row) if row else None

    def create_batch(
        self,
        batch_type: str,
        metadata: dict | str | None = None,
        user: str = "system",
    ) -> int:
        """Create a new pending batch.

        ``metadata`` may be a dict (stored in stats) or a legacy user string.
        """
        if isinstance(metadata, str):
            user = metadata
            metadata = None
        stats = "{}" if not metadata else __import__("json").dumps(metadata, ensure_ascii=False)
        return self.create(
            {
                "batch_type": batch_type,
                "status": "pending",
                "stats": stats,
                "started_at": self._get_timestamp(),
            },
            user=user,
        )

    def update_batch_status(
        self, batch_id: int, status: str,
        stats: dict[str, Any] | None = None, user: str = "system"
    ) -> bool:
        """Update batch status and stats."""
        data = {"status": status}
        if stats:
            data["stats"] = json.dumps(stats, ensure_ascii=False)
        if status in ("completed", "failed"):
            data["completed_at"] = self._get_timestamp()
        return self.update(batch_id, data, user=user)


class FoundationReviewQueueRepository(BaseRepository):
    """Repository for human review queue (foundation_review_queue)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_review_queue")

    def get_by_fact(self, fact_type: str, fact_key: str) -> list[dict[str, Any]]:
        """Get review queue entries by fact."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.fact_type == fact_type)
                    & (self.table.c.fact_key == fact_key)
                )
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get pending review items ordered by priority."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.status == "pending")
                .order_by(self.table.c.priority.desc(), self.table.c.created_at)
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_assignee(self, assignee: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get review items by assignee."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.assignee == assignee)
                .order_by(self.table.c.priority.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def add_to_queue(
        self, fact_type: str, fact_key: str, priority: int = 0,
        assignee: str | None = None, user: str = "system"
    ) -> int:
        """Add item to review queue."""
        return self.create(
            {
                "fact_type": fact_type,
                "fact_key": fact_key,
                "priority": priority,
                "assignee": assignee,
                "status": "pending",
                "created_at": self._get_timestamp(),
            },
            user=user,
        )

    def update_status(self, queue_id: int, status: str, user: str = "system") -> bool:
        """Update review queue item status."""
        data = {"status": status}
        if status in ("approved", "rejected"):
            data["resolved_at"] = self._get_timestamp()
        return self.update(queue_id, data, user=user)


class FoundationMetricsRepository(BaseRepository):
    """Repository for evaluation metrics (foundation_metrics)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_metrics")

    def get_by_run(self, run_id: str) -> list[dict[str, Any]]:
        """Get metrics for a run."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.run_id == run_id)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_metric(self, metric: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get all values for a metric across runs."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.metric == metric)
                .order_by(self.table.c.created_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def record_metric(
        self, run_id: str, metric: str, value: float,
        baseline: float | None = None, user: str = "system"
    ) -> int:
        """Record a metric value."""
        data = {
            "run_id": run_id,
            "metric": metric,
            "value": value,
            "baseline": baseline,
            "delta": value - baseline if baseline is not None else None,
            "created_at": self._get_timestamp(),
        }
        return self.create(data, user=user)


# =============================================================================
# COST TRACKING REPOSITORY
# =============================================================================

class FoundationCostTrackingRepository(BaseRepository):
    """Repository for LLM cost tracking (foundation_cost_tracking)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "foundation_cost_tracking")

    def log_request(
        self,
        request_id: str,
        task_type: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        extra_info: str | None = None,
    ) -> int:
        """Log an LLM API request with cost data.

        Args:
            request_id: UUID for this request.
            task_type: Type of task ('word', 'sentence', 'paragraph', 'grammar', 'batch').
            model: Model name used.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cost_usd: Cost in USD.
            extra_info: Optional JSON metadata.

        Returns:
            The ID of the created record.
        """
        data = {
            "request_id": request_id,
            "task_type": task_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "extra_info": extra_info,
            "created_at": self._get_timestamp(),
        }
        return self.create(data)

    def get_summary(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """Get cost summary for a date range.

        Args:
            start_date: ISO date string for start of range (inclusive).
            end_date: ISO date string for end of range (inclusive).

        Returns:
            Dict with total_cost, by_task, by_model, daily breakdown.
        """
        conditions = []
        if start_date:
            conditions.append(f"created_at >= '{start_date}'")
        if end_date:
            conditions.append(f"created_at <= '{end_date}'")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._engine.connect() as conn:
            # Total cost
            total_row = conn.execute(
                text(f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM foundation_cost_tracking {where_clause}")
            ).first()
            total_cost = float(total_row[0])

            # By task type
            task_rows = conn.execute(
                text(
                    f"SELECT task_type, SUM(cost_usd) as cost, COUNT(*) as cnt "
                    f"FROM foundation_cost_tracking {where_clause} "
                    f"GROUP BY task_type ORDER BY cost DESC"
                )
            ).fetchall()
            by_task = {row[0]: {"cost": float(row[1]), "count": row[2]} for row in task_rows}

            # By model
            model_rows = conn.execute(
                text(
                    f"SELECT model, SUM(cost_usd) as cost, COUNT(*) as cnt "
                    f"FROM foundation_cost_tracking {where_clause} "
                    f"GROUP BY model ORDER BY cost DESC"
                )
            ).fetchall()
            by_model = {row[0]: {"cost": float(row[1]), "count": row[2]} for row in model_rows}

            # Daily breakdown
            daily_rows = conn.execute(
                text(
                    f"SELECT DATE(created_at) as day, SUM(cost_usd) as cost, COUNT(*) as cnt "
                    f"FROM foundation_cost_tracking {where_clause} "
                    f"GROUP BY DATE(created_at) ORDER BY day DESC"
                )
            ).fetchall()
            daily = [{"date": row[0], "cost": float(row[1]), "count": row[2]} for row in daily_rows]

        return {
            "total_cost": total_cost,
            "by_task": by_task,
            "by_model": by_model,
            "daily": daily,
        }

    def check_budget(self, monthly_budget_usd: float) -> tuple[bool, float]:
        """Check if current month's spend is within budget.

        Args:
            monthly_budget_usd: Monthly budget in USD.

        Returns:
            Tuple of (within_budget, current_spend).
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) FROM foundation_cost_tracking "
                    "WHERE created_at >= :month_start"
                ),
                {"month_start": month_start},
            ).first()
        current_spend = float(row[0])
        return current_spend <= monthly_budget_usd, current_spend

    def get_daily_breakdown(self, days: int = 30) -> list[dict[str, Any]]:
        """Get daily cost breakdown for the last N days.

        Args:
            days: Number of days to look back.

        Returns:
            List of dicts with date, cost, count keys.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DATE(created_at) as day, SUM(cost_usd) as cost, COUNT(*) as cnt "
                    "FROM foundation_cost_tracking "
                    "WHERE created_at >= DATE('now', :offset) "
                    "GROUP BY DATE(created_at) ORDER BY day DESC"
                ),
                {"offset": f"-{days} days"},
            ).fetchall()
        return [{"date": row[0], "cost": float(row[1]), "count": row[2]} for row in rows]

    def get_by_task_type(self) -> dict[str, float]:
        """Get total cost per task type.

        Returns:
            Dict mapping task_type to total cost.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT task_type, SUM(cost_usd) FROM foundation_cost_tracking "
                    "GROUP BY task_type ORDER BY SUM(cost_usd) DESC"
                )
            ).fetchall()
        return {row[0]: float(row[1]) for row in rows}


# =============================================================================
# REGISTRY
# =============================================================================

FOUNDATION_REPOSITORIES: dict[str, type[BaseRepository]] = {
    # Raw Layer
    "foundation_raw_corpus": FoundationRawCorpusRepository,
    "foundation_raw_llm": FoundationRawLLMRepository,
    # Staging Layer
    "foundation_staging_words": FoundationStagingWordsRepository,
    "foundation_staging_sentences": FoundationStagingSentencesRepository,
    "foundation_staging_paragraphs": FoundationStagingParagraphsRepository,
    "foundation_staging_evidence": FoundationStagingEvidenceRepository,
    # Canonical Layer
    "canonical_words": CanonicalWordsRepository,
    "canonical_sentences": CanonicalSentencesRepository,
    "canonical_paragraphs": CanonicalParagraphsRepository,
    # Evidence / Consensus / Verification
    "foundation_evidence": FoundationEvidenceRepository,
    "foundation_verifications": FoundationVerificationsRepository,
    "foundation_consensus": FoundationConsensusRepository,
    # Meta
    "foundation_batches": FoundationBatchesRepository,
    "foundation_review_queue": FoundationReviewQueueRepository,
    "foundation_metrics": FoundationMetricsRepository,
    # Cost Tracking
    "foundation_cost_tracking": FoundationCostTrackingRepository,
}


def get_foundation_repositories(engine: Engine) -> dict[str, BaseRepository]:
    """Instantiate all foundation repositories with a shared engine."""
    return {name: repo_cls(engine) for name, repo_cls in FOUNDATION_REPOSITORIES.items()}
