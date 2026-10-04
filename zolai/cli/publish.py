"""CLI commands for Cloud Publishing (Phase 7)."""

from __future__ import annotations

import json
import logging

import typer
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.publishing import (
    build_knowledge_artifact,
    release_knowledge,
)

log = logging.getLogger(__name__)
app = typer.Typer(name="publish", help="Cloud Publishing commands (Phase 7)")


def _get_db_engine() -> Engine:
    return get_engine()


@app.command("build")
def build(
    version: str = typer.Argument(..., help="Version tag (e.g., 2026.10.0)"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help="Output directory"),
    pipeline_version: str = typer.Option("phase7", "--pipeline-version"),
    schema_version: str = typer.Option("1.0", "--schema-version"),
) -> None:
    """Build knowledge artifact (manifest.json + JSONL exports)."""
    _engine = _get_db_engine()
    output = output_dir or f"/tmp/zolai-artifact-{version}"
    manifest = build_knowledge_artifact(
        _get_db_engine(),
        version=version,
        output_dir=output,
        pipeline_version=pipeline_version,
        schema_version=schema_version,
    )
    print(json.dumps({
        "version": manifest.version,
        "git_commit": manifest.git_commit,
        "record_counts": manifest.record_counts,
        "quality_metrics": manifest.quality_metrics,
        "file_hashes": manifest.file_hashes,
        "created_at": manifest.created_at,
    }, indent=2, ensure_ascii=False))


@app.command("sync-r2")
def sync_r2(
    artifact_dir: str = typer.Argument(..., help="Artifact directory"),
    bucket: str = typer.Argument(..., help="R2 bucket name"),
    prefix: str = typer.Option("", "--prefix", help="Object key prefix"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Sync artifact to Cloudflare R2 bucket."""
    from zolai.publishing.sync import sync_to_r2
    result = sync_to_r2(artifact_dir, bucket, prefix=prefix, dry_run=dry_run)
    print(json.dumps({
        "success": result.success,
        "files_uploaded": result.files_uploaded,
        "bytes_uploaded": result.bytes_uploaded,
        "errors": result.errors,
        "details": result.details,
    }, indent=2))


@app.command("sync-d1")
def sync_d1(
    artifact_dir: str = typer.Argument(..., help="Artifact directory"),
    database_id: str = typer.Argument(..., help="D1 database ID"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Sync artifact to Cloudflare D1 database."""
    from zolai.publishing.sync import sync_to_d1
    result = sync_to_d1(artifact_dir, database_id, dry_run=dry_run)
    print(json.dumps({
        "success": result.success,
        "tables_created": result.tables_created,
        "rows_inserted": result.rows_inserted,
        "errors": result.errors,
        "details": result.details,
    }, indent=2))


@app.command("release")
def release(
    version: str = typer.Argument(..., help="Version tag (e.g., 2026.10.0)"),
    output_dir: str = typer.Option(None, "--output-dir"),
    bucket: str = typer.Option(None, "--bucket", help="R2 bucket name"),
    prefix: str = typer.Option(None, "--prefix", help="R2 prefix"),
    database_id: str = typer.Option(None, "--database-id", help="D1 database ID"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force", help="Skip validation errors"),
) -> None:
    """Execute full release pipeline: validate → build → sync → version → tag."""
    result = release_knowledge(
        _get_db_engine(),
        version=version,
        output_dir=output_dir,
        bucket=bucket,
        prefix=prefix,
        database_id=database_id,
        dry_run=dry_run,
        force=force,
    )
    print(json.dumps({
        "success": result.success,
        "version": result.version,
        "elapsed_seconds": result.elapsed_seconds,
        "artifact_manifest": {
            "version": result.artifact_manifest.version if result.artifact_manifest else None,
            "record_counts": result.artifact_manifest.record_counts if result.artifact_manifest else None,
        } if result.artifact_manifest else None,
        "r2_sync": {
            "success": result.r2_sync.success,
            "files_uploaded": result.r2_sync.files_uploaded,
        } if result.r2_sync else None,
        "d1_sync": {
            "success": result.d1_sync.success,
            "tables_created": result.d1_sync.tables_created,
        } if result.d1_sync else None,
        "knowledge_version_id": result.knowledge_version_id,
        "git_tag": result.git_tag,
        "errors": result.errors,
        "warnings": result.warnings,
    }, indent=2))


@app.command("status")
def status() -> None:
    """Show publishing status (recent versions, git tags)."""
    engine = _get_db_engine()
    with engine.connect() as conn:
        from sqlalchemy import text
        rows = conn.execute(
            text("SELECT version, git_commit, status, created_at FROM knowledge_versions ORDER BY id DESC LIMIT 10")
        ).fetchall()
    print(json.dumps([
        {"version": r[0], "git_commit": r[1], "status": r[2], "created_at": r[3]}
        for r in rows
    ], indent=2))
