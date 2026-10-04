"""Hypothesis → Claim promotion engine (Phase 4 §36).

Promotes machine-discovered hypotheses into audited, evidence-backed knowledge
claims.  The expression encoding is idempotent: the normalized
``(claim_type, subject, predicate, object)`` key is protected by the
expression-unique index ``ux_kc_claim_key``, so a re-run is a no-op.

Contract (``tests/test_knowledge_promotion.py``):

- confidence comes only from ``confidence_from_evidence`` (tier weights, ≤ 2 dp)
- ``SUPPORTED`` requires ≥ 1 resolved evidence link, else ``CANDIDATE``
  (never ``OBSERVED``)
- ``dry_run`` plans without writing; unsupported kinds are refused, unmappable
  rows are counted (never fatal); hypothesis rows are never mutated
- expression encoding is stable across runs (``word:word:x`` → ``word:x``)

Live-DB compatibility: the legacy ``KIND_MAPPERS`` encoding created ``pos`` /
``collocation`` claim types; both coexist in ``data/zolai.db`` with the new
``lexicon`` encoding, so the duplicate pre-check matches either generation.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from zolai.data.repositories.knowledge import ClaimRepository, HypothesisRepository
from zolai.foundation.evidence import Evidence, EvidenceTier
from zolai.shared.contracts import KnowledgeStatus, confidence_from_evidence

log = logging.getLogger(__name__)

# Batch size for paging through hypotheses (tests monkeypatch this attribute).
PAGE_SIZE = 500

# Kinds eligible for promotion and their claim_type encoding.
KIND_TO_CLAIM_TYPE: dict[str, str] = {
    "pos": "lexicon",
    "morph_relation": "morphology",
    "collocation": "lexicon",
}
DEFAULT_KINDS: list[str] = list(KIND_TO_CLAIM_TYPE)

# Claim types a previous generation of this mapper may have written for the
# same kind — matched during the duplicate pre-check so live re-runs stay
# idempotent across the encoding migration.
_LEGACY_CLAIM_TYPES: dict[str, tuple[str, ...]] = {
    "pos": ("lexicon", "pos"),
    "collocation": ("lexicon", "collocation"),
    "morph_relation": ("morphology",),
}

# Collapse a repeated ``word:`` prefix so ``word:word:dup`` collides with
# ``word:dup`` at the expression-unique index.
_WORD_PREFIX = re.compile(r"^(word:)+")


@dataclass(frozen=True)
class ClaimExpression:
    """Normalized claim identity (subject/predicate/object slots)."""

    claim_type: str
    subject: str
    predicate: str
    object: str | None

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Unique-index key (``object`` defaults to ``''`` like COALESCE)."""
        return (self.claim_type, self.subject, self.predicate, self.object or "")


@dataclass
class LinkedEvidence:
    """One ``foundation_evidence`` row resolved for a hypothesis."""

    evidence_id: int
    evidence: Evidence


@dataclass
class PromotionSummary:
    """Counts produced by one promotion run (JSON-safe via ``to_dict``)."""

    kinds: list[str]
    dry_run: bool
    hypotheses_considered: int = 0
    claims_planned: int = 0
    claims_created: int = 0
    claims_skipped_duplicate: int = 0
    claims_skipped_no_evidence: int = 0
    claims_skipped_low_confidence: int = 0
    claims_skipped_unmappable: int = 0
    unsupported_kinds: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view (stable field order, ``json.dumps``-safe)."""
        return asdict(self)


def parse_id_list(value: Any) -> list[int]:
    """Normalize evidence ids (JSON string | list | junk | None) → ``list[int]``.

    Unparsable entries are dropped rather than raising: a stale id must never
    fail a whole promotion run.
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    out: list[int] = []
    for item in value:
        if item is None:
            continue
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _parse_extras(value: Any) -> dict[str, Any]:
    """Hypothesis ``extras`` (JSON string | dict | junk) → dict."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def evidence_from_row(row: Mapping[str, Any]) -> Evidence | None:
    """Convert a ``foundation_evidence`` row into foundation ``Evidence``.

    Out-of-range or malformed tiers return ``None`` (the row is dropped, never
    fatal).  Also used by ``zolai.knowledge.consensus``.
    """
    try:
        tier = EvidenceTier(int(row["tier"]))
    except (KeyError, TypeError, ValueError):
        return None
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        confidence = float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return Evidence.create(
        fact_type=str(row.get("fact_type") or "word"),
        fact_key=str(row.get("fact_key") or ""),
        tier=tier,
        source=str(row.get("source") or "unknown"),
        confidence=confidence,
        payload=payload,
    )


def build_claim_expression(hypothesis: Mapping[str, Any]) -> ClaimExpression | None:
    """Encode a hypothesis row as a claim expression.

    Returns ``None`` when the kind is unsupported or a required slot is
    missing (the caller counts those as ``claims_skipped_unmappable``).
    """
    kind = hypothesis.get("kind")
    claim_type = KIND_TO_CLAIM_TYPE.get(kind if isinstance(kind, str) else "")
    if claim_type is None:
        return None

    subject = (hypothesis.get("subject") or "").strip()
    if not subject:
        return None
    subject = _WORD_PREFIX.sub("word:", subject)

    predicate = (hypothesis.get("predicate") or "").strip()
    object_raw = hypothesis.get("object")
    object_ = object_raw.strip() if isinstance(object_raw, str) and object_raw.strip() else None
    extras = _parse_extras(hypothesis.get("extras"))

    if kind == "pos":
        if not predicate:
            pos = extras.get("pos") or extras.get("upos")
            predicate = f"pos:{pos}" if isinstance(pos, str) and pos.strip() else ""
        if not predicate:
            return None
        return ClaimExpression(claim_type, subject, predicate, None)

    if kind == "morph_relation":
        if not predicate:
            mtype = extras.get("type")
            predicate = f"morph:{mtype}" if isinstance(mtype, str) and mtype.strip() else ""
        if not predicate:
            return None
        root = object_ or extras.get("root")
        if not isinstance(root, str) or not root.strip():
            return None
        root = root.strip()
        object_ = root if root.startswith("root:") else f"root:{root}"
        return ClaimExpression(claim_type, subject, predicate, object_)

    # collocation — both S/P/O slots are mandatory.
    if not predicate or object_ is None:
        return None
    return ClaimExpression(claim_type, subject, predicate, object_)


def load_linked_evidence(engine: Engine, evidence_ids: list[int]) -> list[LinkedEvidence]:
    """Resolve ``foundation_evidence`` rows in requested order.

    Missing ids, duplicates, and out-of-range tiers are dropped silently.
    """
    unique = list(dict.fromkeys(int(eid) for eid in evidence_ids))
    if not unique:
        return []
    placeholders = ",".join(f":e{i}" for i in range(len(unique)))
    params = {f"e{i}": eid for i, eid in enumerate(unique)}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, fact_type, fact_key, tier, source, confidence, "
                f"provenance_hash, payload, created_at FROM foundation_evidence "
                f"WHERE id IN ({placeholders})"
            ),
            params,
        ).fetchall()
    by_id = {int(row.id): dict(row._mapping) for row in rows}
    resolved: list[LinkedEvidence] = []
    for evidence_id in unique:
        row = by_id.get(evidence_id)
        if row is None:
            continue
        evidence = evidence_from_row(row)
        if evidence is None:
            continue
        resolved.append(LinkedEvidence(evidence_id=evidence_id, evidence=evidence))
    return resolved


def _expression_exists(engine: Engine, expression: ClaimExpression, kind: str) -> bool:
    """True when this expression (or its legacy-encoding twin) is already a claim."""
    types = _LEGACY_CLAIM_TYPES.get(kind, (expression.claim_type,))
    placeholders = ",".join(f":t{i}" for i in range(len(types)))
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM knowledge_claims "
                f"WHERE claim_type IN ({placeholders}) "
                "AND subject = :subject AND predicate = :predicate "
                "AND COALESCE(object, '') = :object "
                "LIMIT 1"
            ),
            {
                **{f"t{i}": t for i, t in enumerate(types)},
                "subject": expression.subject,
                "predicate": expression.predicate,
                "object": expression.object or "",
            },
        ).first()
    return row is not None


def _promote_one(
    engine: Engine,
    claim_repo: ClaimRepository,
    hypothesis: Mapping[str, Any],
    summary: PromotionSummary,
    min_evidence: int,
    min_confidence: float,
    dry_run: bool,
) -> None:
    """Gate one hypothesis row and (maybe) write the claim — never raises."""
    summary.hypotheses_considered += 1

    # 1. Expression first: an unmappable row is counted regardless of evidence.
    kind = str(hypothesis.get("kind") or "")
    expression = build_claim_expression(hypothesis)
    if expression is None:
        summary.claims_skipped_unmappable += 1
        return

    # 2. Evidence gate (resolved rows only — dangling ids do not count).
    linked = load_linked_evidence(engine, parse_id_list(hypothesis.get("evidence_ids")))
    if len(linked) < min_evidence:
        summary.claims_skipped_no_evidence += 1
        return

    # 3. Confidence gate — evidence-derived only (tier weights, ≤ 2 dp).
    confidence = confidence_from_evidence([item.evidence for item in linked])
    if confidence < min_confidence:
        summary.claims_skipped_low_confidence += 1
        return

    summary.claims_planned += 1
    if dry_run:
        return

    # 4. Idempotency: pre-check the unique index (incl. legacy encodings).
    if _expression_exists(engine, expression, kind):
        summary.claims_skipped_duplicate += 1
        return

    claim: dict[str, Any] = {
        "claim_type": expression.claim_type,
        "subject": expression.subject,
        "predicate": expression.predicate,
        "object": expression.object,
        "confidence": confidence,
        "status": (
            KnowledgeStatus.SUPPORTED.value if linked else KnowledgeStatus.CANDIDATE.value
        ),
        "evidence_ids": [item.evidence_id for item in linked],
        "source_ids": [],
        "notes": f"kind={kind}",
        "version": 1,
    }
    try:
        claim_repo.create(claim)
    except IntegrityError:
        # Lost a race (or hit an encoding the pre-check could not see): the
        # expression-unique index already holds the claim — count duplicate.
        summary.claims_skipped_duplicate += 1
    except Exception as exc:
        log.warning("Failed to promote hypothesis %s: %s", hypothesis.get("id"), exc)
        summary.errors.append(f"Hypothesis {hypothesis.get('id')}: {exc}")
    else:
        summary.claims_created += 1


def promote_hypotheses_to_claims(
    engine: Engine,
    kinds: list[str] | None = None,
    min_evidence: int = 1,
    min_confidence: float = 0.0,
    dry_run: bool = False,
) -> PromotionSummary:
    """Promote eligible hypotheses to knowledge claims.

    Args:
        engine: SQLAlchemy engine.
        kinds: Hypothesis kinds to promote (default: ``DEFAULT_KINDS``).
        min_evidence: Minimum number of *resolved* evidence rows per claim.
        min_confidence: Minimum contract confidence (tier-weighted, 2 dp).
        dry_run: Plan and count without writing anything.

    Returns:
        ``PromotionSummary`` with per-gate counts (use ``to_dict()`` for JSON).
    """
    requested = list(dict.fromkeys(kinds)) if kinds else list(DEFAULT_KINDS)
    supported = [k for k in requested if k in KIND_TO_CLAIM_TYPE]
    unsupported = [k for k in requested if k not in KIND_TO_CLAIM_TYPE]
    summary = PromotionSummary(kinds=supported, dry_run=dry_run, unsupported_kinds=unsupported)

    repo = HypothesisRepository(engine)
    claim_repo = ClaimRepository(engine)

    for kind in supported:
        offset = 0
        while True:
            batch = repo.find_by_kind(kind, limit=PAGE_SIZE, offset=offset)
            if not batch:
                break
            for hypothesis in batch:
                _promote_one(
                    engine,
                    claim_repo,
                    hypothesis,
                    summary,
                    min_evidence=min_evidence,
                    min_confidence=min_confidence,
                    dry_run=dry_run,
                )
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

    return summary


def get_promotion_stats(engine: Engine) -> dict[str, Any]:
    """Statistics on hypotheses available for promotion, per supported kind."""
    repo = HypothesisRepository(engine)
    stats: dict[str, Any] = {}
    for kind in KIND_TO_CLAIM_TYPE:
        rows = repo.find_by_kind(kind, limit=10000)
        stats[kind] = {
            "total": len(rows),
            "with_evidence": sum(1 for r in rows if parse_id_list(r.get("evidence_ids"))),
            "statuses": dict(Counter(r.get("status", "OBSERVED") for r in rows)),
        }
    return stats
