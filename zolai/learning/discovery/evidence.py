"""Evidence writer for discovery (Phase 3 §36).

Upserts into ``foundation_evidence`` by (fact_type, fact_key, source, method)
composite key — idempotent, no new unique index required (plan deviation noted).
Method + extractor are always set so downstream can trace provenance.

Tiers per plan: Bible 1.0 (tier=1) → dict 0.9 (tier=2) → grammar 0.8 (tier=3)
→ corpus 0.7 (tier=4). Confidence is tier weight rounded to 2 dp via
``confidence_from_evidence()``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.shared.contracts.base import confidence_from_evidence

# Evidence tier mapping: internal tier int → (source table name, weight, tier name)
EVIDENCE_TIER_MAP: dict[int, tuple[str, float, str]] = {
    1: ("bible_verses", 1.0, "BIBLE_PARALLEL"),
    2: ("dictionary", 0.9, "DICTIONARY"),
    3: ("grammar_patterns", 0.8, "GRAMMAR_PATTERNS"),
    4: ("corpus", 0.7, "CORPUS_ATTESTATION"),
}


def _compute_provenance_hash(payload: dict[str, Any]) -> str:
    """SHA256 of deterministic JSON payload (16-char prefix)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _normalize_fact_key(fact_type: str, fact_key: str) -> str:
    """Ensure fact_key has the canonical prefix (word:, colloc:, etc.)."""
    if fact_key.startswith(f"{fact_type}:"):
        return fact_key
    if fact_type == "collocation" and fact_key.startswith("word:"):
        # Already encoded as word:xxx|word:yyy in some paths
        return fact_key
    return f"{fact_type}:{fact_key}"


def upsert_evidence(
    engine: Engine,
    fact_type: str,
    fact_key: str,
    tier: int,
    source: str,
    method: str,
    extractor: str,
    payload: dict[str, Any],
    confidence: float | None = None,
) -> int:
    """Upsert one evidence row by (fact_type, fact_key, source, method).

    Returns the evidence row id (existing or newly inserted).
    """
    fact_key = _normalize_fact_key(fact_type, fact_key)
    provenance_hash = _compute_provenance_hash(payload)
    now = datetime.now(timezone.utc).isoformat()

    # Confidence = tier weight if not provided
    if confidence is None:
        confidence = round(EVIDENCE_TIER_MAP.get(tier, (None, 0.0, ""))[1], 2)

    with engine.begin() as conn:
        # Try to find existing
        existing = conn.execute(
            text(
                "SELECT id FROM foundation_evidence "
                "WHERE fact_type = :ft AND fact_key = :fk AND source = :src AND method = :mth"
            ),
            {"ft": fact_type, "fk": fact_key, "src": source, "mth": method},
        ).first()

        if existing:
            evidence_id = existing[0]
            # Update confidence, payload, extractor (other fields stable)
            conn.execute(
                text(
                    "UPDATE foundation_evidence SET "
                    "confidence = :conf, payload = :payload, extractor = :ext, "
                    "provenance_hash = :phash WHERE id = :id"
                ),
                {
                    "conf": confidence,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "ext": extractor,
                    "phash": provenance_hash,
                    "id": evidence_id,
                },
            )
            return evidence_id

        # Insert new
        result = conn.execute(
            text(
                "INSERT INTO foundation_evidence "
                "(fact_type, fact_key, tier, source, confidence, provenance_hash, "
                "payload, method, extractor, created_at) "
                "VALUES (:ft, :fk, :tier, :src, :conf, :phash, :payload, :mth, :ext, :now)"
            ),
            {
                "ft": fact_type,
                "fk": fact_key,
                "tier": tier,
                "src": source,
                "conf": confidence,
                "phash": provenance_hash,
                "payload": json.dumps(payload, ensure_ascii=False),
                "mth": method,
                "ext": extractor,
                "now": now,
            },
        )
        return result.inserted_primary_key[0]


def upsert_evidence_bulk(
    engine: Engine,
    rows: list[dict[str, Any]],
) -> list[int]:
    """Bulk upsert evidence rows (same composite key logic as upsert_evidence).

    Each row dict must contain: fact_type, fact_key, tier, source, method,
    extractor, payload. Optional: confidence.

    Returns list of evidence IDs (in same order as input).
    """
    if not rows:
        return []

    ids: list[int] = []
    now = datetime.now(timezone.utc).isoformat()

    with engine.begin() as conn:
        for row in rows:
            fact_type = row["fact_type"]
            fact_key = _normalize_fact_key(fact_type, row["fact_key"])
            tier = row["tier"]
            source = row["source"]
            method = row["method"]
            extractor = row["extractor"]
            payload = row["payload"]
            confidence = row.get("confidence")

            if confidence is None:
                confidence = round(EVIDENCE_TIER_MAP.get(tier, (None, 0.0, ""))[1], 2)

            provenance_hash = _compute_provenance_hash(payload)

            existing = conn.execute(
                text(
                    "SELECT id FROM foundation_evidence "
                    "WHERE fact_type = :ft AND fact_key = :fk AND source = :src AND method = :mth"
                ),
                {"ft": fact_type, "fk": fact_key, "src": source, "mth": method},
            ).first()

            if existing:
                evidence_id = existing[0]
                conn.execute(
                    text(
                        "UPDATE foundation_evidence SET "
                        "confidence = :conf, payload = :payload, extractor = :ext, "
                        "provenance_hash = :phash WHERE id = :id"
                    ),
                    {
                        "conf": confidence,
                        "payload": json.dumps(payload, ensure_ascii=False),
                        "ext": extractor,
                        "phash": provenance_hash,
                        "id": evidence_id,
                    },
                )
                ids.append(evidence_id)
            else:
                result = conn.execute(
                    text(
                        "INSERT INTO foundation_evidence "
                        "(fact_type, fact_key, tier, source, confidence, provenance_hash, "
                        "payload, method, extractor, created_at) "
                        "VALUES (:ft, :fk, :tier, :src, :conf, :phash, :payload, :mth, :ext, :now)"
                    ),
                    {
                        "ft": fact_type,
                        "fk": fact_key,
                        "tier": tier,
                        "src": source,
                        "conf": confidence,
                        "phash": provenance_hash,
                        "payload": json.dumps(payload, ensure_ascii=False),
                        "mth": method,
                        "ext": extractor,
                        "now": now,
                    },
                )
                ids.append(result.inserted_primary_key[0])

    return ids