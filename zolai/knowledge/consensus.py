"""Consensus integration for knowledge claims (Phase 4 §36).

Adapter over foundation.consensus.adaptive_consensus for claim-level confidence aggregation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.foundation.consensus import adaptive_consensus, Candidate
from zolai.foundation.evidence import EvidenceTier

# Evidence tier weights (matching foundation/evidence.py)
EVIDENCE_TIER_WEIGHTS = {
    1: 1.0,   # BIBLE_PARALLEL
    2: 0.9,   # DICTIONARY
    3: 0.8,   # GRAMMAR_PATTERN
    4: 0.7,   # CORPUS_ATTESTATION
    5: 0.5,   # MODEL_GENERATED
}


def _parse_evidence_ids(value: Any) -> list[int]:
    import json
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [int(v) for v in value]


def _get_claim_evidence(engine: Engine, claim_id: int) -> list[dict[str, Any]]:
    """Fetch all evidence linked to a claim."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT fe.id, fe.fact_type, fe.fact_key, fe.tier, fe.source, fe.confidence, 
                       fe.method, fe.extractor, fe.payload
                FROM claim_evidence ce
                JOIN foundation_evidence fe ON fe.id = ce.evidence_id
                WHERE ce.claim_id = :claim_id
                ORDER BY fe.tier ASC, fe.id
            """),
            {"claim_id": claim_id},
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def _get_claims_with_multiple_evidence(
    engine: Engine,
    claim_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Get claims that have ≥2 evidence rows from different sources."""
    if claim_ids:
        placeholders = ",".join("?" * len(claim_ids))
        claim_filter = f"AND kc.id IN ({placeholders})"
        params = claim_ids
    else:
        claim_filter = ""
        params = []

    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT kc.id, kc.claim_type, kc.subject, kc.predicate, kc.object,
                       kc.confidence, kc.status, kc.evidence_ids,
                       COUNT(ce.evidence_id) as evidence_count
                FROM knowledge_claims kc
                JOIN claim_evidence ce ON ce.claim_id = kc.id
                GROUP BY kc.id
                HAVING COUNT(DISTINCT ce.evidence_id) >= 2
                {claim_filter}
            """),
            params,
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def compute_claim_consensus(
    engine: Engine,
    claim_ids: list[int] | None = None,
    method: str = "weighted",
) -> dict[str, Any]:
    """Compute consensus confidence for claims with multiple evidence sources.

    Args:
        engine: SQLAlchemy engine.
        claim_ids: Specific claim IDs to process (default: all with ≥2 evidence).
        method: Consensus method ("weighted" | "majority" | "unanimous").

    Returns:
        Dict with claim_id → {confidence, agreement_score, tier_breakdown, evidence_count}.
    """
    claims = _get_claims_with_multiple_evidence(engine, claim_ids)
    
    results = {}
    for claim in claims:
        claim_id = claim["id"]
        evidence_rows = _get_claim_evidence(engine, claim_id)
        
        # Build evidence list for adaptive_consensus
        evidence_list = []
        tier_breakdown = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        sources_seen = set()
        
        for ev in evidence_rows:
            tier = ev["tier"]
            tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
            sources_seen.add(ev["source"])
            evidence_list.append({
                "tier": tier,
                "source": ev["source"],
                "method": ev["method"],
                "extractor": ev["extractor"],
                "confidence": ev["confidence"],
            })
        
        if method == "weighted":
            # Use adaptive_consensus with proper Candidate objects
            from zolai.foundation.evidence import Evidence, EvidenceTier, Candidate
            candidates = []
            for ev in evidence_list:
                tier_enum = EvidenceTier(ev["tier"])
                evidence_obj = Evidence.create(
                    fact_type="word",
                    fact_key=f"claim:{claim_id}",
                    tier=tier_enum,
                    source=ev["source"],
                    confidence=ev["confidence"],
                    payload={"method": ev["method"], "extractor": ev["extractor"]},
                )
                candidates.append(Candidate(
                    fact_type="word",
                    fact_key=f"claim:{claim_id}",
                    value={"confidence": ev["confidence"]},
                    evidence=(evidence_obj,),
                    source="consensus",
                ))
            result = adaptive_consensus(candidates, fact_type="word")
            consensus_confidence = round(result.confidence, 2) if hasattr(result, "confidence") else 0.0
        elif method == "majority":
            # Simple majority: average of evidence confidences
            confs = [e["confidence"] for e in evidence_list if e["confidence"] is not None]
            consensus_confidence = round(sum(confs) / len(confs), 2) if confs else 0.0
        else:  # unanimous
            # All must agree (within 0.1)
            confs = [e["confidence"] for e in evidence_list if e["confidence"] is not None]
            if confs and max(confs) - min(confs) <= 0.1:
                consensus_confidence = round(sum(confs) / len(confs), 2)
            else:
                consensus_confidence = 0.0
        
        # Agreement score: fraction of evidence from distinct sources
        agreement_score = round(len(sources_seen) / len(evidence_rows), 2) if evidence_rows else 0.0
        
        results[claim_id] = {
            "confidence": consensus_confidence,
            "agreement_score": agreement_score,
            "tier_breakdown": tier_breakdown,
            "evidence_count": len(evidence_rows),
            "distinct_sources": len(sources_seen),
        }
    
    return {
        "claims_processed": len(results),
        "method": method,
        "results": results,
    }
