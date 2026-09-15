"""Foundation Consensus — Pure Python majority vote + evidence weighting.

No network calls, no external dependencies.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from .evidence import Candidate, EvidenceTier

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    """Result of consensus decision."""
    fact_type: str
    fact_key: str
    decision: dict[str, Any]          # The agreed-upon canonical value
    confidence: float                 # Aggregated confidence (0.0–1.0)
    method: str                       # 'majority_vote' | 'weighted_evidence' | 'threshold_gate'
    threshold: float                  # Threshold used
    candidates_considered: int
    agreeing_candidates: int
    evidence_tiers_present: list[EvidenceTier]
    notes: tuple[str, ...] = ()


def majority_vote(
    candidates: list[Candidate],
    threshold: float = 0.5,
    key_fields: Optional[list[str]] = None,
) -> ConsensusResult:
    """Simple majority vote on candidate values.

    Compares candidate values by key_fields (default: all fields).
    Returns the value with majority support if it exceeds threshold.
    """
    if not candidates:
        raise ValueError("No candidates provided")
    fact_key = candidates[0].fact_key
    fact_type = candidates[0].fact_type

    # Normalize values for comparison
    def normalize_value(c: Candidate) -> tuple:
        if key_fields:
            return tuple(c.value.get(k) for k in key_fields)
        return tuple(sorted(c.value.items()))

    value_counts = Counter(normalize_value(c) for c in candidates)
    most_common, count = value_counts.most_common(1)[0]
    agreement_ratio = count / len(candidates)

    # Find the candidate with the winning value
    winning_candidate = next(c for c in candidates if normalize_value(c) == most_common)

    # Aggregate confidence from agreeing candidates
    agreeing = [c for c in candidates if normalize_value(c) == most_common]
    avg_confidence = sum(c.aggregate_confidence() for c in agreeing) / len(agreeing)

    # Collect evidence tiers
    tiers_present = set()
    for c in agreeing:
        for e in c.evidence:
            tiers_present.add(e.tier)

    notes = []
    if agreement_ratio < threshold:
        notes.append(f"Agreement ratio {agreement_ratio:.2f} below threshold {threshold}")

    return ConsensusResult(
        fact_type=fact_type,
        fact_key=fact_key,
        decision=winning_candidate.value,
        confidence=avg_confidence,
        method="majority_vote",
        threshold=threshold,
        candidates_considered=len(candidates),
        agreeing_candidates=count,
        evidence_tiers_present=sorted(tiers_present, key=lambda t: t.value),
        notes=tuple(notes),
    )


def weighted_evidence_consensus(
    candidates: list[Candidate],
    threshold: float = 0.7,
    min_tier_weight: float = 0.5,
) -> ConsensusResult:
    """Consensus by weighted evidence across candidates.

    Each candidate's evidence contributes weight = tier.weight() * confidence.
    The candidate with highest total evidence weight wins if it exceeds threshold.
    """
    if not candidates:
        raise ValueError("No candidates provided")
    fact_key = candidates[0].fact_key
    fact_type = candidates[0].fact_type

    # Score each candidate by evidence weight
    scored: list[tuple[float, Candidate]] = []
    for c in candidates:
        weight = sum(e.tier.weight() * e.confidence for e in c.evidence)
        scored.append((weight, c))

    scored.sort(reverse=True, key=lambda x: x[0])
    best_weight, best_candidate = scored[0]

    # Check if best candidate meets minimum evidence threshold
    total_possible_weight = sum(
        e.tier.weight() for c in candidates for e in c.evidence
    )
    weight_ratio = best_weight / total_possible_weight if total_possible_weight > 0 else 0.0

    # Also consider aggregate confidence
    agg_confidence = best_candidate.aggregate_confidence()

    # Collect evidence tiers from best candidate
    tiers_present = set(e.tier for e in best_candidate.evidence)

    notes = []
    if weight_ratio < min_tier_weight:
        notes.append(f"Evidence weight ratio {weight_ratio:.2f} below min {min_tier_weight}")
    if agg_confidence < threshold:
        notes.append(f"Aggregate confidence {agg_confidence:.2f} below threshold {threshold}")

    passed = weight_ratio >= min_tier_weight and agg_confidence >= threshold

    return ConsensusResult(
        fact_type=fact_type,
        fact_key=fact_key,
        decision=best_candidate.value if passed else {},
        confidence=agg_confidence if passed else 0.0,
        method="weighted_evidence",
        threshold=threshold,
        candidates_considered=len(candidates),
        agreeing_candidates=1,  # Only the winner "agrees" in this method
        evidence_tiers_present=sorted(tiers_present, key=lambda t: t.value),
        notes=tuple(notes),
    )


def threshold_gate_consensus(
    candidates: list[Candidate],
    threshold: float = 0.7,
    require_tiers: Optional[list[EvidenceTier]] = None,
) -> ConsensusResult:
    """Threshold-gate consensus: promotes candidate if it meets criteria.

    Criteria:
    - Aggregate confidence >= threshold
    - Has evidence from required tiers (default: any 2 tiers)
    - No contradictory higher-confidence candidate exists
    """
    if not candidates:
        raise ValueError("No candidates provided")
    fact_key = candidates[0].fact_key
    fact_type = candidates[0].fact_type

    require_tiers = require_tiers or [
        EvidenceTier.BIBLE_PARALLEL,
        EvidenceTier.DICTIONARY,
    ]

    # Evaluate each candidate
    qualified: list[Candidate] = []
    for c in candidates:
        conf = c.aggregate_confidence()
        if conf < threshold:
            continue

        tiers_present = set(e.tier for e in c.evidence)
        required_met = sum(1 for t in require_tiers if t in tiers_present)
        if required_met < 2:  # Need at least 2 required tiers
            continue

        qualified.append(c)

    if not qualified:
        # No candidate meets criteria — return empty decision
        tiers_all = set()
        for c in candidates:
            for e in c.evidence:
                tiers_all.add(e.tier)

        return ConsensusResult(
            fact_type=fact_type,
            fact_key=fact_key,
            decision={},
            confidence=0.0,
            method="threshold_gate",
            threshold=threshold,
            candidates_considered=len(candidates),
            agreeing_candidates=0,
            evidence_tiers_present=sorted(tiers_all, key=lambda t: t.value),
            notes=("No candidate met threshold criteria",),
        )

    # Among qualified, pick highest confidence
    best = max(qualified, key=lambda c: c.aggregate_confidence())
    best_conf = best.aggregate_confidence()

    # Check for contradictory high-confidence candidates
    contradictions = [
        c for c in qualified
        if c != best and c.aggregate_confidence() > best_conf * 0.9
        and c.value != best.value
    ]

    notes = []
    if contradictions:
        notes.append(f"Contradictory candidates: {len(contradictions)}")

    tiers_present = set(e.tier for e in best.evidence)

    return ConsensusResult(
        fact_type=fact_type,
        fact_key=fact_key,
        decision=best.value if not contradictions else {},
        confidence=best_conf if not contradictions else 0.0,
        method="threshold_gate",
        threshold=threshold,
        candidates_considered=len(candidates),
        agreeing_candidates=len(qualified),
        evidence_tiers_present=sorted(tiers_present, key=lambda t: t.value),
        notes=tuple(notes),
    )


def adaptive_consensus(
    candidates: list[Candidate],
    fact_type: str,
) -> ConsensusResult:
    """Adaptive consensus: selects method based on fact type and evidence.

    - word: weighted_evidence (threshold 0.75, needs Bible or Dictionary tier)
    - sentence: threshold_gate (threshold 0.7, needs 2 tiers)
    - paragraph: majority_vote (threshold 0.6, human review flagged if <0.8)
    - grammar: weighted_evidence (threshold 0.8, needs Grammar Patterns tier)
    """
    if not candidates:
        raise ValueError("No candidates provided")


    if fact_type == "word":
        # Words need strong evidence: Bible or Dictionary tier preferred
        has_bible = any(
            EvidenceTier.BIBLE_PARALLEL in {e.tier for e in c.evidence}
            for c in candidates
        )
        has_dict = any(
            EvidenceTier.DICTIONARY in {e.tier for e in c.evidence}
            for c in candidates
        )
        if has_bible or has_dict:
            return weighted_evidence_consensus(candidates, threshold=0.75, min_tier_weight=0.5)
        else:
            # Weak evidence — require higher agreement
            return majority_vote(candidates, threshold=0.7)

    elif fact_type == "sentence":
        # Sentences: threshold gate with tier requirements
        return threshold_gate_consensus(
            candidates,
            threshold=0.7,
            require_tiers=[EvidenceTier.BIBLE_PARALLEL, EvidenceTier.DICTIONARY, EvidenceTier.GRAMMAR_PATTERNS],
        )

    elif fact_type == "paragraph":
        # Paragraphs: majority vote, flag for human review if low confidence
        result = majority_vote(candidates, threshold=0.6)
        if result.confidence < 0.8:
            notes = list(result.notes) + ["FLAG_FOR_HUMAN_REVIEW: confidence < 0.8"]
            return ConsensusResult(
                fact_type=result.fact_type,
                fact_key=result.fact_key,
                decision=result.decision,
                confidence=result.confidence,
                method=result.method,
                threshold=result.threshold,
                candidates_considered=result.candidates_considered,
                agreeing_candidates=result.agreeing_candidates,
                evidence_tiers_present=result.evidence_tiers_present,
                notes=tuple(notes),
            )
        return result

    elif fact_type == "grammar":
        # Grammar patterns: high threshold, need grammar evidence
        has_grammar = any(
            EvidenceTier.GRAMMAR_PATTERNS in {e.tier for e in c.evidence}
            for c in candidates
        )
        if has_grammar:
            return weighted_evidence_consensus(candidates, threshold=0.8, min_tier_weight=0.6)
        else:
            return threshold_gate_consensus(
                candidates,
                threshold=0.8,
                require_tiers=[EvidenceTier.GRAMMAR_PATTERNS, EvidenceTier.BIBLE_PARALLEL],
            )

    else:
        # Default: majority vote
        return majority_vote(candidates, threshold=0.5)


def build_candidates_from_evidence(
    fact_type: str,
    fact_key: str,
    evidence_groups: dict[Any, list],  # value -> [evidence]
) -> list[Candidate]:
    """Build candidates from grouped evidence (e.g. after evidence collection).

    evidence_groups maps proposed values to their supporting evidence lists.
    """
    candidates = []
    for value, ev_list in evidence_groups.items():
        candidate = Candidate(
            fact_type=fact_type,
            fact_key=fact_key,
            value=value if isinstance(value, dict) else {"value": value},
            evidence=tuple(ev_list),
            source="evidence_collection",
        )
        candidates.append(candidate)
    return candidates


# ── Convenience: run consensus with default adaptive logic ──────────────────

def run_consensus(
    candidates: list[Candidate],
    fact_type: Optional[str] = None,
) -> ConsensusResult:
    """Run adaptive consensus on candidates."""
    if not candidates:
        raise ValueError("No candidates provided")
    ftype = fact_type or candidates[0].fact_type
    return adaptive_consensus(candidates, ftype)
