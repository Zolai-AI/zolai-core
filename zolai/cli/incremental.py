"""CLI commands for Incremental Learning (Phase 5)."""

from __future__ import annotations

import json
import logging
from typing import Any

import typer
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.learning.incremental import (
    ChangeSet,
    detect_changes,
    process_changeset,
    run_incremental_pipeline,
    run_regression_checks,
    update_knowledge_from_changes,
)

log = logging.getLogger(__name__)
app = typer.Typer(name="incremental", help="Incremental Learning pipeline (Phase 5)")


def _get_db_engine() -> Engine:
    return get_engine()


def _serialize_changeset(cs: ChangeSet) -> dict[str, Any]:
    """Serialize ChangeSet for JSON output."""
    return {
        "summary": cs.summary(),
        "new_count": len(cs.new),
        "changed_count": len(cs.changed),
        "unchanged_count": len(cs.unchanged),
        "removed_count": len(cs.removed),
    }


@app.command("detect")
def detect(
    source_file: str = typer.Argument(..., help="JSONL source file"),
    batch_id: str = typer.Option(None, "--batch-id", help="Import batch ID"),
) -> None:
    """Detect changes between JSONL and canonical tables."""
    engine = _get_db_engine()
    changeset = detect_changes(engine, source_file, batch_id)
    print(json.dumps(_serialize_changeset(changeset), indent=2, ensure_ascii=False))


@app.command("process")
def process(
    source_file: str = typer.Argument(..., help="JSONL source file"),
    batch_id: str = typer.Option(None, "--batch-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Process changeset: upsert canonical, run discovery, update stats."""
    engine = _get_db_engine()
    changeset = detect_changes(engine, source_file, batch_id)
    result = process_changeset(engine, changeset, dry_run=dry_run)
    print(json.dumps({
        "changeset": _serialize_changeset(changeset),
        "processing": {
            "upserted": result.upserted,
            "hypotheses_built": result.hypotheses_built,
            "stats_updated": result.stats_updated,
            "attestation_refreshed": result.attestation_refreshed,
            "errors": result.errors,
        },
    }, indent=2, ensure_ascii=False))


@app.command("update-knowledge")
def update_knowledge(
    source_file: str = typer.Argument(..., help="JSONL source file"),
    batch_id: str = typer.Option(None, "--batch-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    create_version: bool = typer.Option(False, "--create-version"),
    version_tag: str = typer.Option(None, "--version-tag"),
) -> None:
    """Update knowledge claims, consensus, review queue from changes."""
    engine = _get_db_engine()
    changeset = detect_changes(engine, source_file, batch_id)
    result = update_knowledge_from_changes(
        engine,
        changeset,
        dry_run=dry_run,
        create_version=create_version,
        version_tag=version_tag,
    )
    print(json.dumps({
        "changeset": _serialize_changeset(changeset),
        "knowledge_update": {
            "claims_promoted": result.claims_promoted,
            "claims_updated": result.claims_updated,
            "consensus_recomputed": result.consensus_recomputed,
            "review_items_enqueued": result.review_items_enqueued,
            "version_created": result.version_created,
            "errors": result.errors,
        },
    }, indent=2, ensure_ascii=False))


@app.command("regression")
def regression(
    baseline_version_id: int = typer.Option(None, "--baseline-version"),
    row_threshold: float = typer.Option(0.05, "--row-threshold"),
) -> None:
    """Run regression checks against baseline version."""
    engine = _get_db_engine()
    result = run_regression_checks(engine, baseline_version_id=baseline_version_id)
    print(json.dumps({
        "passed": result.passed,
        "checks": result.checks,
        "errors": result.errors,
    }, indent=2, ensure_ascii=False))


@app.command("run")
def run(
    source_file: str = typer.Argument(..., help="JSONL source file"),
    batch_id: str = typer.Option(None, "--batch-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_regression: bool = typer.Option(False, "--no-regression"),
    create_version: bool = typer.Option(False, "--create-version"),
    version_tag: str = typer.Option(None, "--version-tag"),
) -> None:
    """Run full incremental pipeline."""
    engine = _get_db_engine()
    result = run_incremental_pipeline(
        engine,
        source_file,
        batch_id=batch_id,
        dry_run=dry_run,
        run_regression=not no_regression,
        create_version=create_version,
        version_tag=version_tag,
    )
    print(json.dumps({
        "success": result.success,
        "elapsed_seconds": result.elapsed_seconds,
        "changeset": _serialize_changeset(result.changeset) if result.changeset else None,
        "processing": {
            "upserted": result.processing.upserted if result.processing else 0,
            "hypotheses_built": result.processing.hypotheses_built if result.processing else {},
            "stats_updated": result.processing.stats_updated if result.processing else 0,
            "attestation_refreshed": result.processing.attestation_refreshed if result.processing else 0,
        } if result.processing else None,
        "knowledge_update": {
            "claims_promoted": result.knowledge_update.claims_promoted if result.knowledge_update else 0,
            "consensus_recomputed": result.knowledge_update.consensus_recomputed if result.knowledge_update else 0,
            "review_items_enqueued": result.knowledge_update.review_items_enqueued if result.knowledge_update else 0,
            "version_created": result.knowledge_update.version_created if result.knowledge_update else False,
        } if result.knowledge_update else None,
        "regression": {
            "passed": result.regression.passed if result.regression else None,
        } if result.regression else None,
        "version_id": result.version_id,
        "errors": result.errors,
    }, indent=2, ensure_ascii=False))


@app.command("status")
def status(
    batch_id: str = typer.Option(None, "--batch-id"),
) -> None:
    """Show status of incremental runs."""
    print(json.dumps({"status": "not implemented"}, indent=2))
