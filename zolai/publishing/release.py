"""Release Orchestrator (Phase 7 §36).

Orchestrates: validate → build artifact → sync → version → tag.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.publishing.artifact import build_knowledge_artifact, ArtifactManifest
from zolai.publishing.sync import sync_to_r2, sync_to_d1, SyncResult
from zolai.learning.incremental.regression import run_regression_checks

log = logging.getLogger(__name__)


@dataclass
class ReleaseResult:
    """Result of a knowledge release."""
    success: bool
    version: str
    artifact_manifest: ArtifactManifest | None = None
    r2_sync: SyncResult | None = None
    d1_sync: SyncResult | None = None
    knowledge_version_id: int | None = None
    git_tag: str | None = None
    elapsed_seconds: float = 0.0
    errors: list[str] = None
    warnings: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


def _validate_pre_release(engine: Engine, version: str) -> tuple[bool, list[str], list[str]]:
    """Run pre-release validation checks.

    Returns:
        (passed, errors, warnings)
    """
    errors = []
    warnings = []

    # 1. Regression checks
    log.info("Running regression checks...")
    regression = run_regression_checks(engine)
    if not regression.passed:
        errors.append("Regression checks failed")
        for check, details in regression.checks.items():
            if isinstance(details, dict) and "count_diffs" in details:
                for table, diff in details["count_diffs"].items():
                    warnings.append(f"Row count diff in {table}: {diff['diff_pct']}%")

    # 2. Evidence coverage
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN evidence_ids != '[]' AND evidence_ids != '' THEN 1 ELSE 0 END) as with_ev
            FROM knowledge_claims
        """)).first()
        if row and row[0] > 0:
            coverage = row[1] / row[0]
            if coverage < 0.5:
                warnings.append(f"Evidence coverage {coverage:.1%} below 50% threshold")
        else:
            warnings.append("No knowledge claims found")

    # 3. ZVS compliance
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN zvs_compliance_status = 'compliant' THEN 1 ELSE 0 END) as ok
            FROM dictionary WHERE is_deleted = 0
        """)).first()
        if row and row[0] > 0:
            zvs_rate = row[1] / row[0]
            if zvs_rate < 1.0:
                warnings.append(f"ZVS compliance {zvs_rate:.1%} not 100%")

    # 4. At least one VERIFIED claim
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*) FROM knowledge_claims WHERE status = 'VERIFIED'")).first()
        if row and row[0] == 0:
            warnings.append("No VERIFIED knowledge claims (at least one recommended)")

    # 5. Knowledge version doesn't already exist
    with engine.connect() as conn:
        row = conn.execute(text("SELECT 1 FROM knowledge_versions WHERE version = :v"), {"v": version}).first()
        if row:
            errors.append(f"Knowledge version {version} already exists")

    passed = len(errors) == 0
    return passed, errors, warnings


def _create_knowledge_version_row(
    engine: Engine,
    version: str,
    manifest: ArtifactManifest,
    git_tag: str,
) -> int:
    """Create knowledge_versions row and return ID."""
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO knowledge_versions 
                (version, git_commit, source_versions, pipeline_version, schema_version,
                 row_counts, quality, eval_run_id, manifest_hash, status, created_at)
                VALUES (:v, :gc, :sv, :pv, :sc, :rc, :q, NULL, :mh, 'VERIFIED', datetime('now'))
            """),
            {
                "v": version,
                "gc": manifest.git_commit,
                "sv": json.dumps(manifest.source_versions),
                "pv": manifest.pipeline_version,
                "sc": manifest.schema_version,
                "rc": json.dumps(manifest.record_counts),
                "q": json.dumps(manifest.quality_metrics),
                "mh": manifest.file_hashes.get("manifest.json", ""),
            },
        )
        return result.lastrowid


def _create_git_tag(version: str, message: str) -> str:
    """Create annotated git tag."""
    tag = f"v{version}"
    try:
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", message],
            check=True,
            cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core",
        )
        return tag
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create git tag: {e}")


def release_knowledge(
    engine,
    version: str,
    output_dir: str | None = None,
    bucket: str | None = None,
    prefix: str | None = None,
    database_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> ReleaseResult:
    """Execute full knowledge release pipeline.

    Args:
        engine: SQLAlchemy engine.
        version: Version tag (e.g., "2026.10.0").
        output_dir: Artifact output directory (default: /tmp/zolai-artifact-{version}).
        bucket: R2 bucket name (optional).
        prefix: R2 prefix (default: releases/{version}/).
        database_id: D1 database ID (optional).
        dry_run: If True, run validation only.
        force: Skip validation errors (not recommended).

    Returns:
        ReleaseResult with all step results.
    """
    start = time.time()
    result = ReleaseResult(success=False, version=version)

    log.info("Starting knowledge release v%s", version)

    # Step 1: Pre-release validation
    log.info("Running pre-release validation...")
    passed, errors, warnings = _validate_pre_release(engine, version)
    result.errors.extend(errors)
    result.warnings.extend(warnings)

    if not passed and not force:
        result.errors.append("Pre-release validation failed (use --force to override)")
        result.elapsed_seconds = round(time.time() - start, 2)
        return result

    log.info("Validation passed (%d warnings)", len(warnings))

    if dry_run:
        result.success = True
        result.elapsed_seconds = round(time.time() - start, 2)
        result.warnings.append("DRY RUN - no changes made")
        return result

    # Step 2: Build artifact
    artifact_dir = output_dir or f"/tmp/zolai-artifact-{version}"
    log.info("Building artifact in %s...", artifact_dir)
    manifest = build_knowledge_artifact(
        engine,
        version=version,
        output_dir=artifact_dir,
    )
    result.artifact_manifest = manifest

    # Step 2b: Sync to R2 (if configured)
    if bucket:
        r2_prefix = prefix or f"releases/{version}/"
        log.info("Syncing to R2 bucket %s...", bucket)
        r2_result = sync_to_r2(artifact_dir, bucket, prefix=f"releases/{version}/")
        result.r2_sync = r2_result
        if not r2_result.success:
            result.errors.append("R2 sync failed")

    # Step 2c: Sync to D1 (if configured)
    if database_id:
        log.info("Syncing to D1 database %s...", database_id)
        d1_result = sync_to_d1(artifact_dir, database_id)
        result.d1_sync = d1_result
        if not d1_result.success:
            result.errors.append("D1 sync failed")

    # Step 3: Create knowledge version row
    git_tag = f"v{version}"
    log.info("Creating knowledge version row...")
    version_id = _create_knowledge_version_row(engine, version, result.artifact_manifest, f"v{version}")
    result.knowledge_version_id = version_id

    # Step 4: Create git tag
    log.info("Creating git tag %s...", git_tag)
    try:
        tag = _create_git_tag(version, f"Release v{version}")
        result.git_tag = tag
    except RuntimeError as e:
        result.errors.append(f"Git tag creation failed: {e}")

    # Finalize
    result.success = len(result.errors) == 0
    result.elapsed_seconds = round(time.time() - start, 2)

    if result.success:
        log.info("Release v%s completed successfully in %.2fs", version, result.elapsed_seconds)
    else:
        log.error("Release v%s failed: %s", version, result.errors)

    return result
