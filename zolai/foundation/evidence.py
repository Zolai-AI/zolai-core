"""Foundation Evidence — Candidate, Evidence, Confidence, Verifier.

Pure Python — no network calls, no LLM dependencies.
LLM verifiers can be injected via Verifier ABC for batch jobs.
"""
from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

log = logging.getLogger(__name__)


class EvidenceTier(Enum):
    """Evidence strength tiers (strongest first)."""
    BIBLE_PARALLEL = 1      # T1: Bible parallel verses (31,102)
    DICTIONARY = 2          # T2: Community dictionaries
    GRAMMAR_PATTERNS = 3    # T3: Grammar patterns (5,482)
    CORPUS_ATTESTATION = 4  # T4: Web corpus frequency >=5
    LLM_GENERATION = 5      # T5: LLM with RAG context

    def weight(self) -> float:
        """Return numeric weight for consensus calculation."""
        weights = {
            EvidenceTier.BIBLE_PARALLEL: 1.0,
            EvidenceTier.DICTIONARY: 0.9,
            EvidenceTier.GRAMMAR_PATTERNS: 0.8,
            EvidenceTier.CORPUS_ATTESTATION: 0.7,
            EvidenceTier.LLM_GENERATION: 0.4,
        }
        return weights[self]


@dataclass(frozen=True, slots=True)
class Evidence:
    """Single piece of evidence for a candidate fact."""
    fact_type: str                    # 'word' | 'sentence' | 'paragraph' | 'grammar'
    fact_key: str                     # e.g. 'word:pasian', 'sentence:GEN 1:1'
    tier: EvidenceTier
    source: str                       # Source table/system: 'bible_verses', 'dictionary', etc.
    confidence: float                 # 0.0–1.0 (source-internal confidence)
    provenance_hash: str              # SHA256 of source record(s)
    payload: dict[str, Any]           # Source-specific detail
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def create(
        cls,
        fact_type: str,
        fact_key: str,
        tier: EvidenceTier,
        source: str,
        confidence: float,
        payload: dict[str, Any],
        provenance_data: Optional[bytes] = None,
    ) -> Evidence:
        """Factory: compute provenance_hash from payload if not provided."""
        if provenance_data is None:
            provenance_data = json.dumps(payload, sort_keys=True).encode()
        provenance_hash = hashlib.sha256(provenance_data).hexdigest()[:16]
        return cls(
            fact_type=fact_type,
            fact_key=fact_key,
            tier=tier,
            source=source,
            confidence=confidence,
            provenance_hash=provenance_hash,
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    """A candidate fact awaiting verification/consensus."""
    fact_type: str                    # 'word' | 'sentence' | 'paragraph' | 'grammar'
    fact_key: str                     # Unique key for this fact
    value: dict[str, Any]             # The proposed canonical value
    evidence: tuple[Evidence, ...]    # Supporting evidence
    source: str                       # Origin: 'analyzer', 'llm_batch', 'human', etc.
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def aggregate_confidence(self) -> float:
        """Compute weighted confidence from evidence."""
        if not self.evidence:
            return 0.0
        total_weight = sum(e.tier.weight() * e.confidence for e in self.evidence)
        max_weight = sum(e.tier.weight() for e in self.evidence)
        return total_weight / max_weight if max_weight > 0 else 0.0

    def evidence_by_tier(self) -> dict[EvidenceTier, list[Evidence]]:
        """Group evidence by tier."""
        result: dict[EvidenceTier, list[Evidence]] = {}
        for e in self.evidence:
            result.setdefault(e.tier, []).append(e)
        return result

    def has_tier(self, tier: EvidenceTier) -> bool:
        """Check if candidate has evidence from a given tier."""
        return any(e.tier == tier for e in self.evidence)


@dataclass(frozen=True, slots=True)
class Confidence:
    """Confidence assessment with breakdown."""
    overall: float                    # 0.0–1.0
    by_tier: dict[EvidenceTier, float]  # Per-tier confidence
    evidence_count: int
    threshold_met: bool               # Whether overall >= threshold
    threshold: float                  # Threshold used
    notes: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_candidate(cls, candidate: Candidate, threshold: float = 0.7) -> Confidence:
        """Build Confidence from a Candidate."""
        by_tier: dict[EvidenceTier, float] = {}
        for tier in EvidenceTier:
            tier_evidence = [e for e in candidate.evidence if e.tier == tier]
            if tier_evidence:
                by_tier[tier] = sum(e.confidence for e in tier_evidence) / len(tier_evidence)
            else:
                by_tier[tier] = 0.0

        overall = candidate.aggregate_confidence()
        return cls(
            overall=overall,
            by_tier=by_tier,
            evidence_count=len(candidate.evidence),
            threshold_met=overall >= threshold,
            threshold=threshold,
        )


class Verifier(ABC):
    """Abstract verifier interface — injectable for batch jobs.

    Default implementation is NullVerifier (no-op).
    LLM-based verifiers can be injected for batch verification runs.
    """

    @abstractmethod
    def verify(self, candidate: Candidate) -> tuple[bool, float, str]:
        """Verify a candidate.

        Returns:
            (passed, confidence, notes)
        """
        ...

    @abstractmethod
    def verify_batch(self, candidates: list[Candidate]) -> list[tuple[bool, float, str]]:
        """Verify multiple candidates (batch optimization)."""
        ...

    def name(self) -> str:
        """Verifier identifier for audit logging."""
        return self.__class__.__name__


class NullVerifier(Verifier):
    """No-op verifier — always passes with neutral confidence.

    Used as default when no external verifier is configured.
    """

    def verify(self, candidate: Candidate) -> tuple[bool, float, str]:
        return True, 0.5, "null_verifier: no verification performed"

    def verify_batch(self, candidates: list[Candidate]) -> list[tuple[bool, float, str]]:
        return [(True, 0.5, "null_verifier: no verification performed") for _ in candidates]


class EvidenceThresholdVerifier(Verifier):
    """Verifier that checks evidence tier requirements.

    Passes if candidate has evidence from required tiers.
    """

    def __init__(
        self,
        required_tiers: Optional[list[EvidenceTier]] = None,
        min_tier_count: int = 2,
        min_confidence: float = 0.7,
    ) -> None:
        self.required_tiers = required_tiers or [
            EvidenceTier.BIBLE_PARALLEL,
            EvidenceTier.DICTIONARY,
        ]
        self.min_tier_count = min_tier_count
        self.min_confidence = min_confidence

    def verify(self, candidate: Candidate) -> tuple[bool, float, str]:
        tiers_present = set(e.tier for e in candidate.evidence)
        required_met = sum(1 for t in self.required_tiers if t in tiers_present)
        agg_conf = candidate.aggregate_confidence()

        passed = (
            required_met >= self.min_tier_count
            and agg_conf >= self.min_confidence
        )
        notes = f"tiers_present={len(tiers_present)}, required_met={required_met}, agg_conf={agg_conf:.2f}"
        return passed, agg_conf, notes

    def verify_batch(self, candidates: list[Candidate]) -> list[tuple[bool, float, str]]:
        return [self.verify(c) for c in candidates]


# ── Helper functions ─────────────────────────────────────────────────────────

def create_word_candidate(
    word: str,
    analysis: dict[str, Any],
    evidence_list: list[Evidence],
) -> Candidate:
    """Create a word candidate from FoundationAnalyzer output."""
    return Candidate(
        fact_type="word",
        fact_key=f"word:{word.lower()}",
        value={
            "form": word,
            "syllables": analysis.get("syllables", []),
            "pos": analysis.get("pos", "X"),
            "morphology": analysis.get("morphology", {}),
            "meanings": analysis.get("meanings", []),
        },
        evidence=tuple(evidence_list),
        source="analyzer",
    )


def create_sentence_candidate(
    sentence: str,
    analysis: dict[str, Any],
    evidence_list: list[Evidence],
) -> Candidate:
    """Create a sentence candidate from FoundationAnalyzer output."""
    import hashlib
    key_hash = hashlib.sha256(sentence.encode()).hexdigest()[:12]
    return Candidate(
        fact_type="sentence",
        fact_key=f"sentence:{key_hash}",
        value={
            "text": sentence,
            "tokens": analysis.get("tokens", []),
            "pos_tags": analysis.get("pos_tags", []),
            "translation": analysis.get("translation"),
            "grammar": analysis.get("grammar", {}),
        },
        evidence=tuple(evidence_list),
        source="analyzer",
    )


def create_paragraph_candidate(
    paragraph: str,
    analysis: dict[str, Any],
    evidence_list: list[Evidence],
) -> Candidate:
    """Create a paragraph candidate from FoundationAnalyzer output."""
    import hashlib
    key_hash = hashlib.sha256(paragraph.encode()).hexdigest()[:12]
    return Candidate(
        fact_type="paragraph",
        fact_key=f"paragraph:{key_hash}",
        value={
            "text": paragraph,
            "sentences": analysis.get("sentences", []),
            "style_profile": analysis.get("style_profile", {}),
            "register": analysis.get("register", "unknown"),
        },
        evidence=tuple(evidence_list),
        source="analyzer",
    )


# ── Default verifier instance ────────────────────────────────────────────────
DEFAULT_VERIFIER: Verifier = NullVerifier()
EVIDENCE_VERIFIER: Verifier = EvidenceThresholdVerifier()
