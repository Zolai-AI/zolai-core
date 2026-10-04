"""Knowledge versioning & snapshots (Phase 4 §36).

Creates versioned knowledge artifacts with manifests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories.knowledge import KnowledgeVersionRepository

log = logging.getLogger(__name__)

# Canonical tables that participate in versioning
CANONICAL_TABLES = [
    "dictionary",
    "dictionary_en_zo",
    "bible_verses",
    "vocabulary",
    "phrases",
    "translations",
    "word_usage",
    "grammar_patterns",
    "word_collocations",
    "word_alignments",
    "training_exercises",
    "syllable_data",
    "proverbs",
    "knowledge_claims",
    "hypotheses",
    "foundation_evidence",
    "observations",
    "word_observation_stats",
    "attestation_index",
]


def _get_table_row_counts(engine: Engine) -> dict[str, int]:
    """Get row counts for all canonical tables."""
    counts = {}
    with engine.connect() as conn:
        for table in CANONICAL_TABLES:
            try:
                row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
                counts[table] = row[0] if row else 0
            except Exception:
                counts[table] = 0
    return counts


def _get_table_hash(engine: Engine, table: str) -> str:
    """Compute a hash of a table's content (for manifest)."""
    try:
        with engine.connect() as conn:
            # Use rowids for stable ordering, hash primary key + key columns
            row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
            count = row[0] if row else 0
            # For manifest, we use a simple count-based hash
            # Full content hash would be expensive for large tables
            return hashlib.sha256(f"{table}:{count}".encode()).hexdigest()[:16]
    except Exception:
        return "error"


def _compute_manifest_hash(engine: Engine) -> str:
    """Compute manifest hash for all canonical tables."""
    parts = []
    for table in CANONICAL_TABLES:
        parts.append(f"{table}:{_get_table_hash(engine, table)}")
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()


def create_knowledge_version(
    engine: Engine,
    version: str,
    source_versions: dict[str, str] | None = None,
    pipeline_version: str | None = None,
    schema_version: str | None = None,
    eval_run_id: int | None = None,
    manifest_hash: str | None = None,
    status: str = "OBSERVED",
    user: str = "system",
) -> int:
    """Create a knowledge version snapshot.

    Args:
        engine: SQLAlchemy engine.
        version: Version tag (e.g., "2026.10.0").
        source_versions: Dict of source → version (e.g., {"bible": "tdb77", "dictionary": "v2"}).
        pipeline_version: Pipeline version string.
        schema_version: Database schema version.
        eval_run_id: FK to eval_runs table.
        manifest_hash: Pre-computed manifest hash (computed if not provided).
        status: Version status (OBSERVED, SUPPORTED, VERIFIED, etc.).
        user: User/process identifier for audit.

    Returns:
        The version ID (row ID).
    """

    repo = KnowledgeVersionRepository(engine)

    if manifest_hash is None:
        manifest_hash = _compute_manifest_hash(engine)

    row_counts = _get_table_row_counts(engine)

    quality = {
        "total_rows": sum(row_counts.values()),
        "tables_checked": len(row_counts),
        "manifest_hash": manifest_hash,
    }

    data = {
        "version": version,
        "git_commit": _get_git_commit(),
        "source_versions": json.dumps(source_versions or {}, ensure_ascii=False),
        "pipeline_version": pipeline_version or "phase4",
        "schema_version": schema_version or "1.0",
        "row_counts": json.dumps(row_counts, ensure_ascii=False),
        "quality": json.dumps(quality, ensure_ascii=False),
        "eval_run_id": eval_run_id,
        "manifest_hash": manifest_hash,
        "status": status,
    }

    return repo.create(data, user=user)


def list_knowledge_versions(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    """List knowledge versions, newest first."""

    repo = KnowledgeVersionRepository(engine)
    return repo.find({}, limit=limit, offset=0, order_by="-id")


def get_knowledge_version(engine: Engine, version: str) -> dict[str, Any] | None:
    """Get a specific knowledge version by tag."""

    repo = KnowledgeVersionRepository(engine)
    return repo.get_by_version(version)


def _get_git_commit() -> str:
    """Get current git commit SHA."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core",
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"
