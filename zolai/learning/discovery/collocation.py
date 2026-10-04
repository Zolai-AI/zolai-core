"""Collocation hypothesis discovery (Phase 3 §36).

Builds CollocationHypothesis rows (kind='collocation') from:
1. Phase 2 cooccurrence.py stats.collocations JSON (top-K, min_pmi/min_freq)
2. Static word_collocations table (high-PMI pairs)

Promotion policy (R8): top-K by PMI, min_freq threshold.
Zero DDL — uses existing hypotheses table with kind='collocation'.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.learning.discovery.evidence import EVIDENCE_TIER_MAP, upsert_evidence_bulk
from zolai.shared.contracts import CollocationHypothesis
from zolai.shared.contracts.base import require_discovery_status

log = logging.getLogger(__name__)

# Cap for collocation hypotheses (plan default)
COLLOC_CAP = 1000

# Phase 2 stats file location
STATS_COLLOCATIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "stats" / "collocations.json"
)

# PMI/frequency thresholds (plan R8)
MIN_PMI = 3.0
MIN_FREQ = 5
TOP_K = 2000  # Process top-K, then cap at COLLOC_CAP


def _load_stats_collocations() -> list[dict[str, Any]]:
    """Load collocations from Phase 2 stats JSON."""
    if not STATS_COLLOCATIONS_PATH.exists():
        log.warning("Phase 2 collocations stats not found at %s", STATS_COLLOCATIONS_PATH)
        return []

    try:
        with open(STATS_COLLOCATIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Expected format: list of {word1, word2, pmi, count, window} or dict with 'collocations' key
        if isinstance(data, dict) and "collocations" in data:
            return data["collocations"]
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load collocations stats: %s", exc)
    return []


def _get_word_collocations(engine: Engine, min_pmi: float = MIN_PMI, limit: int | None = None) -> list[dict[str, Any]]:
    """Get high-PMI collocations from word_collocations table."""
    lim = f"LIMIT {limit}" if limit else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT word1, word2, frequency, pmiproxy FROM word_collocations "
                f"WHERE pmiproxy >= :min_pmi AND frequency >= :min_freq "
                f"ORDER BY pmiproxy DESC {lim}"
            ),
            {"min_pmi": min_pmi, "min_freq": MIN_FREQ},
        ).fetchall()
    return [
        {"word1": r[0], "word2": r[1], "count": r[2], "pmi": r[3], "window": 2}
        for r in rows
    ]


def _filter_and_rank(
    stats_collocs: list[dict[str, Any]],
    table_collocs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge, deduplicate, filter by thresholds, rank by PMI."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}

    for colloc in stats_collocs + table_collocs:
        w1 = colloc.get("word1", "").lower().strip()
        w2 = colloc.get("word2", "").lower().strip()
        if not w1 or not w2 or w1 == w2:
            continue
        # Canonical order: alphabetical
        key = tuple(sorted((w1, w2)))
        pmi = float(colloc.get("pmi", 0))
        count = int(colloc.get("count", 0))
        window = int(colloc.get("window", 2))

        if pmi < MIN_PMI or count < MIN_FREQ:
            continue

        if key not in seen or pmi > seen[key]["pmi"]:
            seen[key] = {"word1": key[0], "word2": key[1], "pmi": pmi, "count": count, "window": window}

    # Sort by PMI descending
    merged = sorted(seen.values(), key=lambda x: x["pmi"], reverse=True)
    return merged[:TOP_K]


def build_collocation_hypotheses(
    engine: Engine,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build collocation hypotheses from Phase 2 stats + word_collocations table."""
    from zolai.data.repositories.knowledge import HypothesisRepository

    repo = HypothesisRepository(engine)

    stats_collocs = _load_stats_collocations()
    table_collocs = _get_word_collocations(engine, limit=limit)
    merged = _filter_and_rank(stats_collocs, table_collocs)

    if limit:
        merged = merged[:limit]

    collocations_written = 0
    status_counts: dict[str, int] = {"OBSERVED": 0, "CANDIDATE": 0}

    for colloc in merged:
        if collocations_written >= COLLOC_CAP:
            break

        word1 = colloc["word1"]
        word2 = colloc["word2"]
        pmi = colloc["pmi"]
        count = colloc["count"]
        window = colloc["window"]

        # Evidence rows: one for stats source, one for word_collocations table if present
        evidence_rows = [
            {
                "fact_type": "collocation",
                "fact_key": f"colloc:{word1}|{word2}",
                "tier": 4,  # CORPUS_ATTESTATION
                "source": "cooccurrence_stats",
                "method": "pmi_extraction",
                "extractor": "Phase2_cooccurrence_pipeline",
                "payload": {"word1": word1, "word2": word2, "pmi": pmi, "count": count, "window": window},
            }
        ]

        # Check if also in word_collocations table
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM word_collocations "
                    "WHERE (word1 = :w1 AND word2 = :w2) OR (word1 = :w2 AND word2 = :w1)"
                ),
                {"w1": word1, "w2": word2},
            ).first()
            if row:
                evidence_rows.append({
                    "fact_type": "collocation",
                    "fact_key": f"colloc:{word1}|{word2}",
                    "tier": 4,
                    "source": "word_collocations",
                    "method": "pmi_table_lookup",
                    "extractor": "WordCollocationRepository",
                    "payload": {"word1": word1, "word2": word2, "pmi": pmi, "count": count},
                })

        # Status: CANDIDATE for corpus-only (no dictionary/Bible attestation)
        status = require_discovery_status("CANDIDATE").value

        # Confidence from evidence (tier 4 = 0.7)
        class _MiniEvidence:
            def __init__(self, t: int):
                self.tier = t

        confidence = EVIDENCE_TIER_MAP[4][1]  # 0.7

        if not dry_run:
            upsert_evidence_bulk(engine, evidence_rows)

            hypo = CollocationHypothesis(
                word1=word1,
                word2=word2,
                pmi=pmi,
                count=count,
                window=window,
                confidence=confidence,
                status=status,
                evidence_ids=[],
                extras={"source": "corpus", "promotion_policy": "R8"},
            )
            repo.create(hypo.model_dump())
            collocations_written += 1
        else:
            collocations_written += 1

        status_counts[status] += 1

    return {
        "capability": "collocation",
        "collocations_written": collocations_written,
        "status_counts": status_counts,
        "candidates_considered": len(merged),
    }
class CollocationDiscovery:
    """Wrapper class for collocation hypothesis discovery matching test expectations."""

    def __init__(self, engine, limit=100):
        self.engine = engine
        self.limit = limit

    def run(self, conn=None):
        from .collocation import build_collocation_hypotheses
        return build_collocation_hypotheses(self.engine, limit=self.limit)

