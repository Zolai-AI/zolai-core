"""Knowledge updater for Phase 5 §36.

Updates knowledge claims, consensus, and review queue from incremental changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text

from zolai.knowledge import (
    ReviewQueue,
    compute_claim_consensus,
    create_knowledge_version,
    promote_hypotheses_to_claims,
)

log = logging.getLogger(__name__)


@dataclass
class UpdateSummary:
    """Result of knowledge update from changes."""
    claims_promoted: int = 0
    claims_updated: int = 0
    consensus_recomputed: int = 0
    review_items_enqueued: int = 0
    version_created: bool = False
    errors: list[str] = field(default_factory=list)


def _get_affected_subjects_from_changeset(changeset) -> list[str]:
    """Extract hypothesis subjects (word:{word}) from changeset records."""
    subjects = set()
    for rec in changeset.new + changeset.changed:
        # Extract words from record
        for _field in ("zolai", "zo_tdb77", "zo_tedim2010", "word", "headword", "source", "target"):
            val = rec.get(_field)
            if isinstance(val, str):
                for w in val.split():
                    if w.isalpha():
                        subjects.add(f"word:{w}")
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item.isalpha():
                        subjects.add(f"word:{item}")
    return list(subjects)


def update_knowledge_from_changes(
    engine,
    changeset,
    dry_run: bool = False,
    create_version: bool = False,
    version_tag: str | None = None,
) -> UpdateSummary:
    """Update knowledge claims, consensus, review queue from changes."""
    summary = UpdateSummary()

    if dry_run:
        return summary

    try:
        # 1. Identify affected hypothesis subjects
        affected_subjects = _get_affected_subjects_from_changeset(changeset)
        if not affected_subjects:
            log.info("No affected subjects for knowledge update")
            return summary

        log.info("Updating knowledge for %d affected subjects", len(affected_subjects))

        # 2. Re-run promotion for affected kinds (pos, morph_relation, collocation)
        # Filter hypotheses by affected subjects
        promo_result = promote_hypotheses_to_claims(
            engine,
            kinds=["pos", "morph_relation", "collocation"],
            min_evidence=1,
            min_confidence=0.0,
            dry_run=False,
        )
        summary.claims_promoted = promo_result.get("promoted", 0)

        # 3. Recompute consensus for claims with updated evidence
        # Get claim IDs that might have changed evidence
        with engine.connect() as conn:
            # Find claims linked to affected words
            placeholders = ",".join("?" * len(affected_subjects))
            claim_rows = conn.execute(
                text(f"""
                    SELECT DISTINCT kc.id
                    FROM knowledge_claims kc
                    JOIN claim_evidence ce ON ce.claim_id = kc.id
                    JOIN foundation_evidence fe ON fe.id = ce.evidence_id
                    WHERE fe.fact_key IN ({placeholders})
                """),
                affected_subjects,
            ).fetchall()
            claim_ids = [r[0] for r in claim_rows]

        if claim_ids:
            consensus_result = compute_claim_consensus(engine, claim_ids=claim_ids, method="weighted")
            summary.consensus_recomputed = consensus_result.get("claims_processed", 0)

        # 4. Enqueue affected claims/hypotheses for review
        review_queue = ReviewQueue(engine)
        for subject in affected_subjects:
            # Enqueue hypotheses for this subject
            review_queue.enqueue(
                item_type="hypothesis",
                item_id=0,  # Will be resolved by subject
                priority=5,
                metadata={"subject": subject, "source": "incremental_update"},
            )
            summary.review_items_enqueued += 1

        # 5. Create knowledge version snapshot if requested
        if create_version and version_tag:
            version_id = create_knowledge_version(
                engine,
                version_tag,
                source_versions={"incremental": version_tag},
                pipeline_version="phase5-incremental",
                status="OBSERVED",
            )
            summary.version_created = True
            log.info("Created knowledge version %d: %s", version_id, version_tag)

    except Exception as e:
        log.exception("Knowledge update failed: %s", e)
        summary.errors.append(str(e))

    return summary
