"""Incremental pipeline orchestrator (Phase 5 §36).

Orchestrates: detect changes → process → update knowledge → regression → version.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

from zolai.learning.incremental import (
    ChangeSet,
    detect_changes,
    process_changeset,
    run_regression_checks,
    update_knowledge_from_changes,
)

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of incremental pipeline run."""
    changeset: ChangeSet | None = None
    processing: Any = None
    knowledge_update: Any = None
    regression: Any = None
    version_id: int | None = None
    elapsed_seconds: float = 0.0
    success: bool = False
    errors: list[str] = field(default_factory=list)


def run_incremental_pipeline(
    engine: Engine,
    source_file: str,
    batch_id: str | None = None,
    dry_run: bool = False,
    run_regression: bool = True,
    create_version: bool = False,
    version_tag: str | None = None,
) -> PipelineResult:
    """Run the full incremental pipeline.

    Args:
        engine: SQLAlchemy engine.
        source_file: Path to JSONL source file.
        batch_id: Import batch ID (generated if not provided).
        dry_run: If True, only detect changes.
        run_regression: If True, run regression checks.
        create_version: If True, create knowledge version snapshot.
        version_tag: Version tag for snapshot.

    Returns:
        PipelineResult with all step outputs.
    """
    start = time.time()
    result = PipelineResult()
    batch_id = batch_id or f"inc_{int(time.time())}"

    try:
        log.info("Starting incremental pipeline: %s (batch=%s)", source_file, batch_id)

        # Step 1: Detect changes
        changeset = detect_changes(engine, source_file, batch_id)
        result.changeset = changeset
        log.info("Change detection: %s", changeset.summary())

        if dry_run:
            result.success = True
            result.elapsed_seconds = round(time.time() - start, 2)
            return result

        # Step 2: Process changes
        processing = process_changeset(engine, changeset, dry_run=False)
        result.processing = processing
        log.info("Processing complete: %d upserted", processing.upserted)

        # Step 3: Update knowledge
        knowledge_update = update_knowledge_from_changes(
            engine,
            changeset,
            dry_run=False,
            create_version=create_version,
            version_tag=version_tag,
        )
        result.knowledge_update = knowledge_update
        log.info("Knowledge update: %d claims promoted", knowledge_update.claims_promoted)

        # Step 4: Regression checks
        if run_regression:
            baseline_version = None  # Use latest
            regression = run_regression_checks(engine, baseline_version_id=baseline_version)
            result.regression = regression
            if not regression.passed:
                result.errors.append("Regression checks failed")
                log.warning("Regression checks failed: %s", regression.checks)

        # Step 5: Version snapshot (if requested and not already created)
        if create_version and version_tag and not knowledge_update.version_created:
            from zolai.knowledge import create_knowledge_version
            version_id = create_knowledge_version(
                engine,
                version_tag,
                source_versions={"incremental": batch_id},
                pipeline_version="phase5-incremental",
                status="OBSERVED",
            )
            result.version_id = version_id
            log.info("Created version snapshot: %d", version_id)

        result.success = True

    except Exception as e:
        log.exception("Pipeline failed: %s", e)
        result.errors.append(str(e))
        result.success = False

    result.elapsed_seconds = round(time.time() - start, 2)
    return result
