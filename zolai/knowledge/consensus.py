"""Consensus integration for knowledge claims (Phase 4 §36).

Read-only adapter over ``zolai.foundation.consensus``: one ``Candidate`` per
claim carrying every linked ``claim_evidence`` row, then adaptive or weighted
consensus.  Nothing here writes — status, confidence, links, and the audit
log are never touched (contract: ``tests/test_knowledge_consensus.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.foundation.consensus import adaptive_consensus, weighted_evidence_consensus
from zolai.foundation.evidence import Candidate, Evidence
from zolai.shared.contracts import confidence_from_evidence

from .promotion import evidence_from_row

#: Methods accepted by :func:`compute_claim_consensus`.
CONSENSUS_METHODS: frozenset[str] = frozenset({"adaptive", "weighted"})

# Source label stamped on every claim-level candidate.
_CONSENSUS_SOURCE = "consensus"


def claim_consensus_fact_type(claim_type: str) -> str:
    """Map a claim's ``claim_type`` to a foundation fact type.

    ``lexicon``/``morphology`` claims are word-level; ``grammar`` claims stay
    grammar; anything else passes through unchanged.
    """
    if claim_type in ("lexicon", "morphology"):
        return "word"
    if claim_type == "grammar":
        return "grammar"
    return claim_type


@dataclass(frozen=True)
class ClaimConsensus:
    """Consensus verdict for one claim (JSON-safe via ``to_dict``)."""

    claim_id: int
    fact_type: str
    fact_key: str
    evidence_count: int
    source_count: int
    confidence: float              # contract confidence_from_evidence (tier weights, 2 dp)
    agreement_score: float
    tier_breakdown: dict[str, dict[str, Any]]
    decision: dict[str, Any]
    threshold_met: bool
    consensus_confidence: float    # foundation aggregate (2 dp); 0.0 below threshold
    method: str
    threshold: float
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view — deterministic and ``json.dumps``-safe."""
        return {
            "claim_id": self.claim_id,
            "fact_type": self.fact_type,
            "fact_key": self.fact_key,
            "evidence_count": self.evidence_count,
            "source_count": self.source_count,
            "confidence": self.confidence,
            "agreement_score": self.agreement_score,
            "tier_breakdown": {name: dict(entry) for name, entry in self.tier_breakdown.items()},
            "decision": dict(self.decision),
            "threshold_met": self.threshold_met,
            "consensus_confidence": self.consensus_confidence,
            "method": self.method,
            "threshold": self.threshold,
            "notes": list(self.notes),
        }


def select_consensus_claim_ids(
    engine: Engine,
    claim_ids: list[int] | None = None,
    min_evidence_rows: int = 2,
) -> list[int]:
    """Claim ids eligible for consensus, ordered by id.

    A claim is selected when it has at least ``min_evidence_rows`` linked
    ``claim_evidence`` rows (default 2 — a single source cannot corroborate
    itself).  Explicit ``claim_ids`` are filtered by the same threshold;
    unknown ids simply do not match.  Claims with no links at all are never
    selected (inner join).
    """
    params: dict[str, Any] = {"min_rows": int(min_evidence_rows)}
    id_filter = ""
    if claim_ids is not None:
        unique = list(dict.fromkeys(int(cid) for cid in claim_ids))
        if not unique:
            return []
        placeholders = ",".join(f":cid{i}" for i in range(len(unique)))
        id_filter = f"AND kc.id IN ({placeholders})"
        params.update({f"cid{i}": cid for i, cid in enumerate(unique)})

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT kc.id FROM knowledge_claims kc "
                "JOIN claim_evidence ce ON ce.claim_id = kc.id "
                f"{id_filter} "
                "GROUP BY kc.id "
                "HAVING COUNT(ce.evidence_id) >= :min_rows "
                "ORDER BY kc.id"
            ),
            params,
        ).fetchall()
    return [int(row[0]) for row in rows]


def load_claim_evidence(engine: Engine, claim_id: int) -> list[dict[str, Any]]:
    """All ``foundation_evidence`` rows linked to a claim, in link order."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT fe.*, ce.role FROM claim_evidence ce "
                "JOIN foundation_evidence fe ON fe.id = ce.evidence_id "
                "WHERE ce.claim_id = :claim_id "
                "ORDER BY ce.id"
            ),
            {"claim_id": int(claim_id)},
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def build_consensus_candidate(
    claim: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> Candidate | None:
    """Pool every resolvable evidence row into one claim-level candidate.

    Out-of-range tiers are dropped; when nothing resolves the candidate is
    ``None`` (the claim is skipped, never fatal).
    """
    evidence: list[Evidence] = []
    for row in evidence_rows:
        item = evidence_from_row(row)
        if item is not None:
            evidence.append(item)
    if not evidence:
        return None

    claim_id = claim.get("id")
    claim_type = claim.get("claim_type")
    return Candidate(
        fact_type=claim_consensus_fact_type(str(claim_type or "")),
        fact_key=f"claim:{claim_id}",
        value={
            "claim_type": claim_type,
            "subject": claim.get("subject"),
            "predicate": claim.get("predicate"),
            "object": claim.get("object"),
        },
        evidence=tuple(evidence),
        source=_CONSENSUS_SOURCE,
    )


def tier_breakdown(candidate: Candidate) -> dict[str, dict[str, Any]]:
    """Per-tier evidence counts, that tier's weight, and its weight total.

    ``weight_total`` is ``sum(tier_weight * confidence)`` per tier, rounded to
    strip float noise (products like ``0.9 * 0.8`` are 0.7200000000000001).
    """
    out: dict[str, dict[str, Any]] = {}
    for item in candidate.evidence:
        name = item.tier.name
        entry = out.setdefault(
            name,
            {"count": 0, "tier_weight": item.tier.weight(), "weight_total": 0.0},
        )
        entry["count"] += 1
        entry["weight_total"] += item.tier.weight() * item.confidence
    for entry in out.values():
        entry["weight_total"] = round(entry["weight_total"], 10)
    return out


def compute_claim_consensus(
    engine: Engine,
    claim_ids: list[int] | None = None,
    method: str = "adaptive",
    min_evidence_rows: int = 2,
) -> dict[int, ClaimConsensus]:
    """Compute consensus for eligible claims — strictly read-only.

    Args:
        engine: SQLAlchemy engine.
        claim_ids: Specific claim ids (same threshold applies as default).
        method: ``adaptive`` (foundation routing per fact type) or
            ``weighted`` (always weighted evidence, threshold 0.7).
        min_evidence_rows: Minimum linked evidence rows for a claim.

    Returns:
        ``{claim_id: ClaimConsensus}`` in claim-id order.

    Raises:
        ValueError: unknown ``method``.
    """
    if method not in CONSENSUS_METHODS:
        raise ValueError(
            f"unknown consensus method {method!r}; known: {sorted(CONSENSUS_METHODS)}"
        )

    results: dict[int, ClaimConsensus] = {}
    claim_id_list = select_consensus_claim_ids(
        engine, claim_ids=claim_ids, min_evidence_rows=min_evidence_rows
    )
    for claim_id in claim_id_list:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM knowledge_claims WHERE id = :id"),
                {"id": claim_id},
            ).first()
        if row is None:
            continue  # deleted between selection and fetch — skip, never fatal
        claim = dict(row._mapping)

        candidate = build_consensus_candidate(claim, load_claim_evidence(engine, claim_id))
        if candidate is None:
            continue

        if method == "adaptive":
            verdict = adaptive_consensus([candidate], fact_type=candidate.fact_type)
        else:
            verdict = weighted_evidence_consensus([candidate])

        notes = tuple(verdict.notes)
        threshold_met = bool(verdict.decision) and not any("below" in note for note in notes)
        results[claim_id] = ClaimConsensus(
            claim_id=claim_id,
            fact_type=candidate.fact_type,
            fact_key=candidate.fact_key,
            evidence_count=len(candidate.evidence),
            source_count=len({ev.source for ev in candidate.evidence}),
            confidence=confidence_from_evidence([ev.tier for ev in candidate.evidence]),
            agreement_score=(
                round(verdict.agreeing_candidates / verdict.candidates_considered, 2)
                if verdict.candidates_considered
                else 0.0
            ),
            tier_breakdown=tier_breakdown(candidate),
            decision=dict(verdict.decision),
            threshold_met=threshold_met,
            consensus_confidence=round(verdict.confidence, 2),
            method=verdict.method,
            threshold=verdict.threshold,
            notes=notes,
        )
    return results
