"""Hypothesis → Claim promotion engine (Phase 4 §36).

Promotes machine-discovered hypotheses into audited, evidence-backed knowledge claims.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories.knowledge import ClaimRepository, HypothesisRepository
from zolai.shared.contracts.base import (
    GATED_STATUSES,
    KnowledgeStatus,
    confidence_from_evidence,
    require_discovery_status,
)

log = logging.getLogger(__name__)

# Kind → S/P/O mapping functions
KIND_MAPPERS = {
    "pos": lambda h: {
        "claim_type": "pos",
        "subject": h["subject"],
        "predicate": h["predicate"],
        "object": None,
    },
    "morph_relation": lambda h: {
        "claim_type": "morphology",
        "subject": h["subject"],
        "predicate": h["predicate"],
        "object": h.get("object"),
    },
    "collocation": lambda h: {
        "claim_type": "collocation",
        "subject": h["subject"],
        "predicate": h["predicate"],
        "object": h["object"],
    },
    "sentence_pattern": lambda h: {
        "claim_type": "sentence_pattern",
        "subject": h["subject"],
        "predicate": h["predicate"],
        "object": h.get("object"),
    },
    "grammar_phenomenon": lambda h: {
        "claim_type": "grammar_phenomenon",
        "subject": h["subject"],
        "predicate": h["predicate"],
        "object": h.get("object"),
    },
}


def _parse_evidence_ids(value: Any) -> list[int]:
    """Normalize evidence_ids (JSON string | list | None) → list[int]."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [int(v) for v in value]


def _get_evidence_tiers(engine: Engine, evidence_ids: list[int]) -> list[int]:
    """Fetch evidence tiers for given evidence IDs."""
    if not evidence_ids:
        return []
    placeholders = ",".join([f":id{i}" for i in range(len(evidence_ids))])
    params = {f"id{i}": eid for i, eid in enumerate(evidence_ids)}
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT tier FROM foundation_evidence WHERE id IN ({placeholders})"),
            params,
        ).fetchall()
    return [row[0] for row in rows]


def _map_hypothesis_to_claim(engine: Engine, hypothesis: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a hypothesis row into a claim dict for ClaimRepository."""
    kind = hypothesis["kind"]
    mapper = KIND_MAPPERS.get(kind)
    if not mapper:
        log.warning("Unknown hypothesis kind: %s", kind)
        return None

    claim_base = mapper(hypothesis)
    evidence_ids = _parse_evidence_ids(hypothesis.get("evidence_ids"))
    
    # Get evidence tiers for confidence calculation
    tiers = _get_evidence_tiers(engine, evidence_ids)
    if not tiers:
        # Fallback: infer from hypothesis extras/source info
        tiers = [4]  # CORPUS_ATTESTATION default
    
    # Build minimal evidence objects for confidence_from_evidence
    class _MiniEvidence:
        def __init__(self, tier: int):
            self.tier = tier
    
    evidence_items = [_MiniEvidence(t) for t in tiers]
    confidence = confidence_from_evidence(evidence_items)

    # Status: SUPPORTED if has evidence, else CANDIDATE (never OBSERVED for claims)
    status = "SUPPORTED" if evidence_ids else "CANDIDATE"
    
    # Extras from hypothesis
    extras = hypothesis.get("extras", {})
    if isinstance(extras, str):
        try:
            extras = json.loads(extras)
        except json.JSONDecodeError:
            extras = {}
    
    claim = {
        **claim_base,
        "confidence": confidence,
        "status": status,
        "evidence_ids": evidence_ids,
        "source_ids": [],  # Not tracked at hypothesis level
        "notes": "",
        "version": 1,
    }
    return claim


def promote_hypotheses_to_claims(
    engine: Engine,
    kinds: list[str] | None = None,
    min_evidence: int = 1,
    min_confidence: float = 0.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Promote eligible hypotheses to knowledge claims.

    Args:
        engine: SQLAlchemy engine.
        kinds: Hypothesis kinds to promote (default: all kinds in KIND_MAPPERS).
        min_evidence: Minimum number of evidence IDs required.
        min_confidence: Minimum confidence threshold.
        dry_run: If True, report what would be promoted without writing.

    Returns:
        Summary dict with counts per kind and status breakdown.
    """
    kinds = kinds or list(KIND_MAPPERS.keys())
    repo = HypothesisRepository(engine)
    claim_repo = ClaimRepository(engine)

    summary = {
        "promoted": 0,
        "skipped": 0,
        "by_kind": {},
        "status_counts": {"CANDIDATE": 0, "SUPPORTED": 0, "VERIFIED": 0},
        "errors": [],
    }

    for kind in kinds:
        if kind not in KIND_MAPPERS:
            summary["errors"].append(f"Unknown kind: {kind}")
            continue

        hypotheses = repo.find_by_kind(kind, limit=10000)  # Get all
        kind_promoted = 0
        kind_skipped = 0

        for hypo in hypotheses:
            evidence_ids = _parse_evidence_ids(hypo.get("evidence_ids"))
            if len(evidence_ids) < min_evidence:
                kind_skipped += 1
                continue

            claim = _map_hypothesis_to_claim(engine, hypo)
            if claim is None:
                kind_skipped += 1
                continue

            if claim["confidence"] < min_confidence:
                kind_skipped += 1
                continue

            if dry_run:
                kind_promoted += 1
                summary["status_counts"][claim["status"]] += 1
                continue

            try:
                claim_repo.create(claim)
                kind_promoted += 1
                summary["status_counts"][claim["status"]] += 1
            except Exception as exc:
                log.warning("Failed to promote hypothesis %s: %s", hypo.get("id"), exc)
                summary["errors"].append(f"Hypothesis {hypo.get('id')}: {exc}")
                kind_skipped += 1

        summary["by_kind"][kind] = {
            "promoted": kind_promoted,
            "skipped": kind_skipped,
        }
        summary["promoted"] += kind_promoted
        summary["skipped"] += kind_skipped

    return summary


def get_promotion_stats(engine: Engine) -> dict[str, Any]:
    """Get statistics on hypotheses available for promotion."""
    repo = HypothesisRepository(engine)
    stats = {}
    for kind in KIND_MAPPERS:
        rows = repo.find_by_kind(kind, limit=10000)
        stats[kind] = {
            "total": len(rows),
            "with_evidence": sum(1 for r in rows if _parse_evidence_ids(r.get("evidence_ids"))),
            "statuses": dict(Counter(r.get("status", "OBSERVED") for r in rows)),
        }
    return stats
