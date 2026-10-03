"""CLI commands for Knowledge Engine (Phase 4)."""

from __future__ import annotations

import json
import logging
from typing import Any

import typer
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.knowledge import (
    promote_hypotheses_to_claims,
    compute_claim_consensus,
    ReviewQueue,
    create_knowledge_version,
    list_knowledge_versions,
    get_knowledge_version,
)

log = logging.getLogger(__name__)
app = typer.Typer(name="knowledge", help="Knowledge Engine commands (Phase 4)")


def _get_db_engine() -> Engine:
    return get_engine()


@app.command("promote")
def promote(
    kinds: list[str] = typer.Option(None, "--kind", "-k", help="Hypothesis kinds to promote"),
    min_evidence: int = typer.Option(1, "--min-evidence", help="Minimum evidence count"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", help="Minimum confidence"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing"),
) -> None:
    """Promote hypotheses to knowledge claims."""
    engine = _get_db_engine()
    kinds_list = kinds if kinds else None
    
    result = promote_hypotheses_to_claims(
        engine,
        kinds=kinds_list,
        min_evidence=min_evidence,
        min_confidence=min_confidence,
        dry_run=dry_run,
    )
    
    print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("consensus")
def consensus(
    claim_ids: list[int] = typer.Option(None, "--claim-id", help="Specific claim IDs"),
    method: str = typer.Option("weighted", "--method", help="Consensus method"),
) -> None:
    """Compute consensus confidence for claims."""
    engine = _get_db_engine()
    claim_ids_list = claim_ids if claim_ids else None
    
    result = compute_claim_consensus(engine, claim_ids=claim_ids_list, method=method)
    print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("review")
def review_queue(
    status: str = typer.Option(None, "--status", help="Filter by queue status"),
    item_type: str = typer.Option(None, "--type", help="Filter by item type"),
    limit: int = typer.Option(50, "--limit", help="Max items"),
    offset: int = typer.Option(0, "--offset", help="Offset"),
) -> None:
    """List review queue items."""
    engine = get_engine()
    queue = ReviewQueue(engine)
    items = queue.get_queue(status=status, item_type=item_type, limit=limit, offset=offset)
    print(json.dumps(items, indent=2, ensure_ascii=False))


@app.command("review-action")
def review_action(
    item_type: str = typer.Argument(..., help="Item type: claim/hypothesis/pattern"),
    item_id: int = typer.Argument(..., help="Item ID"),
    action: str = typer.Argument(..., help="Action: approve/reject/edit/merge/split/mark_uncertain/add_evidence"),
    payload: str = typer.Option("{}", "--payload", help="JSON payload"),
    user: str = typer.Option("system", "--user", help="User performing action"),
) -> None:
    """Process a review action."""
    engine = get_engine()
    queue = ReviewQueue(engine)
    payload_dict = json.loads(payload)
    
    result = queue.process_action(item_type, item_id, action, payload_dict, user=user)
    print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("version")
def version_create(
    tag: str = typer.Argument(..., help="Version tag (e.g., 2026.10.0)"),
    source_versions: str = typer.Option("{}", "--source-versions", help="JSON source versions"),
    pipeline_version: str = typer.Option(None, "--pipeline-version"),
    schema_version: str = typer.Option(None, "--schema-version"),
    eval_run_id: int = typer.Option(None, "--eval-run-id"),
    manifest_hash: str = typer.Option(None, "--manifest-hash"),
    status: str = typer.Option("OBSERVED", "--status"),
    user: str = typer.Option("system", "--user"),
) -> None:
    """Create a knowledge version snapshot."""
    engine = get_engine()
    source_dict = json.loads(source_versions)
    
    version_id = create_knowledge_version(
        get_engine(),
        tag,
        source_versions=source_dict,
        pipeline_version=pipeline_version,
        schema_version=schema_version,
        eval_run_id=eval_run_id,
        manifest_hash=manifest_hash,
        status=status,
        user=user,
    )
    print(f"Created knowledge version {version_id}")


@app.command("list-versions")
def list_versions(
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List knowledge versions."""
    engine = get_engine()
    versions = list_knowledge_versions(engine, limit=limit)
    print(json.dumps(versions, indent=2, ensure_ascii=False))


@app.command("stats")
def stats() -> None:
    """Show knowledge engine statistics."""
    from zolai.knowledge.promotion import get_promotion_stats
    
    engine = get_engine()
    promo_stats = get_promotion_stats(engine)
    
    # Claim stats
    with get_engine().connect() as conn:
        claim_counts = conn.execute(
            text("SELECT status, COUNT(*) FROM knowledge_claims GROUP BY status")
        ).fetchall()
        review_counts = conn.execute(
            text("SELECT status, COUNT(*) FROM foundation_review_queue GROUP BY status")
        ).fetchall()
    
    result = {
        "promotion": promo_stats,
        "claims_by_status": {r[0]: r[1] for r in claim_counts},
        "review_queue_by_status": {r[0]: r[1] for r in review_counts},
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


from sqlalchemy import text  # noqa: E402
