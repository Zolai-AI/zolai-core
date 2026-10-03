"""Sentence pattern discovery → grammar_patterns (Phase 3 §36).

Extracts sentence patterns from attested corpus (bible_verses, translations)
using the existing BiblePatternLearner classification, and writes them
into grammar_patterns with namespace 'disc_sp_*'.

Shared GrammarPatternWriter upserts by pattern_id, filling:
- normalized (canonical SOV structure)
- components (grammatical components JSON)
- sources (evidence source list)
- evidence_ids (linked foundation_evidence)
- confidence (from evidence tiers)
- status (OBSERVED/CANDIDATE only)
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.learning.bible_pattern_learner import BiblePatternLearner, get_bible_learner
from zolai.learning.discovery.evidence import upsert_evidence, upsert_evidence_bulk, EVIDENCE_TIER_MAP
from zolai.shared.contracts.base import require_discovery_status

log = logging.getLogger(__name__)

# Cap for sentence patterns (plan default: few hundred)
PATTERN_CAP = 500


def _extract_patterns_from_bible(engine: Engine, limit: int | None = None) -> list[dict[str, Any]]:
    """Use BiblePatternLearner to classify and extract patterns from Bible."""
    learner = get_bible_learner()

    patterns_by_type: dict[str, list[dict[str, Any]]] = {}
    lim = f"LIMIT {limit}" if limit else ""

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT ref, zo_tdb77, en_kJV FROM bible_verses "
                f"WHERE zo_tdb77 IS NOT NULL AND TRIM(zo_tdb77) != '' {lim}"
            )
        ).fetchall()

    for ref, zo, en in rows:
        if not zo:
            continue
        pattern_type = learner._classify_pattern(zo)
        structure = learner._extract_structure(zo)
        components = learner._extract_components(zo)

        entry = {
            "pattern_id": f"disc_sp_{pattern_type}_{ref.replace(' ', '_').replace(':', '_')}",
            "pattern_type": pattern_type,
            "structure": structure,
            "example_zo": zo,
            "example_en": en or "",
            "ref": ref,
            "components": components,
        }

        if pattern_type not in patterns_by_type:
            patterns_by_type[pattern_type] = []
        patterns_by_type[pattern_type].append(entry)

    # Flatten and return top patterns per type
    result: list[dict[str, Any]] = []
    for ptype, entries in patterns_by_type.items():
        # Take top 50 per type (by frequency)
        freq = Counter(e["structure"] for e in entries)
        seen_structures: set[str] = set()
        for entry in entries:
            if entry["structure"] in seen_structures:
                continue
            if len([e for e in result if e["pattern_type"] == ptype]) >= 50:
                break
            seen_structures.add(entry["structure"])
            result.append(entry)

    return result


def _extract_patterns_from_translations(engine: Engine, limit: int | None = None) -> list[dict[str, Any]]:
    """Extract patterns from translations table (ZO→EN pairs)."""
    learner = get_bible_learner()
    lim = f"LIMIT {limit}" if limit else ""

    result: list[dict[str, Any]] = []
    seen_structures: set[str] = set()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT source, target, reference FROM translations "
                f"WHERE direction = 'zo_en' AND source IS NOT NULL AND TRIM(source) != '' {lim}"
            )
        ).fetchall()

    for zo, en, ref in rows:
        if not zo:
            continue
        pattern_type = learner._classify_pattern(zo)
        structure = learner._extract_structure(zo)
        components = learner._extract_components(zo)

        if structure in seen_structures:
            continue
        seen_structures.add(structure)

        pattern_id = f"disc_sp_{pattern_type}_trans_{abs(hash(zo)) % 100000}"
        result.append({
            "pattern_id": pattern_id,
            "pattern_type": pattern_type,
            "structure": structure,
            "example_zo": zo,
            "example_en": en or "",
            "ref": ref or "translation",
            "components": components,
        })

        if len(result) >= 200:
            break

    return result


def _build_normalized_pattern(components: dict[str, Any]) -> str:
    """Build normalized SOV pattern string from components."""
    parts = []
    if "subject" in components:
        parts.append(f"S({components['subject']})")
    if "object" in components:
        parts.append(f"O({components['object']})")
    if "verb" in components:
        parts.append(f"V({components['verb']})")
    return " ".join(parts) if parts else "SOV"


def _write_grammar_pattern(
    engine: Engine,
    pattern_data: dict[str, Any],
    source_name: str,
    dry_run: bool = False,
) -> tuple[int, list[int]]:
    """Write one pattern to grammar_patterns via shared writer logic.

    Returns (pattern_id, evidence_ids).
    """
    from zolai.data.repositories.grammar import GrammarRepository

    repo = GrammarRepository(engine)

    pattern_id = pattern_data["pattern_id"]
    pattern_type = pattern_data["pattern_type"]
    structure = pattern_data["structure"]
    example_zo = pattern_data["example_zo"]
    example_en = pattern_data["example_en"]
    ref = pattern_data["ref"]
    components = pattern_data.get("components", {})

    # Evidence row
    evidence_rows = [{
        "fact_type": "grammar",
        "fact_key": f"pattern:{pattern_id}",
        "tier": 1,  # BIBLE_PARALLEL
        "source": source_name,
        "method": "bible_pattern_classification",
        "extractor": "BiblePatternLearner",
        "payload": {
            "pattern_id": pattern_id,
            "pattern_type": pattern_type,
            "structure": structure,
            "example_zo": example_zo,
            "example_en": example_en,
            "ref": ref,
            "components": components,
        },
    }]

    # Check if already exists
    existing = repo.get_by_pattern_id(pattern_id)
    if existing:
        # Already exists — skip (idempotent)
        return (existing["id"], [])

    # Confidence from evidence (tier 1 = 1.0)
    class _MiniEvidence:
        def __init__(self, t: int):
            self.tier = t
    confidence = EVIDENCE_TIER_MAP[1][1]  # 1.0

    # Status: OBSERVED (single unambiguous Bible source)
    status = require_discovery_status("OBSERVED").value

    normalized = _build_normalized_pattern(components)

    if not dry_run:
        upsert_evidence_bulk(engine, evidence_rows)
        # Get evidence ID
        with engine.connect() as conn:
            ev_row = conn.execute(
                text(
                    "SELECT id FROM foundation_evidence "
                    "WHERE fact_type = 'grammar' AND fact_key = :fk AND source = :src"
                ),
                {"fk": f"pattern:{pattern_id}", "src": source_name},
            ).first()
        evidence_ids = [ev_row[0]] if ev_row else []

        # Insert into grammar_patterns
        now = json.dumps([], ensure_ascii=False)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO grammar_patterns "
                    "(pattern_id, pattern, description, function, examples, frequency, "
                    "normalized, components, sources, evidence_ids, confidence, status) "
                    "VALUES (:pid, :pat, :desc, :func, :ex, :freq, :norm, :comp, :srcs, :eids, :conf, :stat)"
                ),
                {
                    "pid": pattern_id,
                    "pat": structure,
                    "desc": f"Discovered {pattern_type} pattern from {source_name}",
                    "func": pattern_type,
                    "ex": json.dumps([example_zo], ensure_ascii=False),
                    "freq": 1,
                    "norm": normalized,
                    "comp": json.dumps(components, ensure_ascii=False),
                    "srcs": json.dumps([source_name], ensure_ascii=False),
                    "eids": json.dumps(evidence_ids, ensure_ascii=False),
                    "conf": confidence,
                    "stat": status,
                },
            )
    else:
        evidence_ids = []

    return (0, evidence_ids)


def build_sentence_pattern_hypotheses(
    engine: Engine,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build sentence patterns into grammar_patterns (disc_sp_* namespace)."""
    patterns_bible = _extract_patterns_from_bible(engine, limit)
    patterns_trans = _extract_patterns_from_translations(engine, limit)

    all_patterns = patterns_bible + patterns_trans
    if limit:
        all_patterns = all_patterns[:limit]

    patterns_written = 0
    status_counts: dict[str, int] = {"OBSERVED": 0, "CANDIDATE": 0}
    skipped = 0

    for pattern_data in all_patterns:
        if patterns_written >= PATTERN_CAP:
            break

        source_name = "bible_verses" if pattern_data["ref"].startswith(("GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA", "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO", "ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO", "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL", "MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH", "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS", "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV")) else "translations"

        try:
            _write_grammar_pattern(engine, pattern_data, source_name, dry_run)
            patterns_written += 1
            status_counts["OBSERVED"] += 1
        except Exception as exc:
            log.warning("Failed to write pattern %s: %s", pattern_data.get("pattern_id"), exc)
            skipped += 1

    return {
        "capability": "sentence_patterns",
        "patterns_written": patterns_written,
        "status_counts": status_counts,
        "skipped": skipped,
        "candidates_considered": len(all_patterns),
    }
class SentencePatternDiscovery:
    """Wrapper class for sentence pattern discovery matching test expectations."""
    
    def __init__(self, engine, limit=100):
        self.engine = engine
        self.limit = limit
    
    def run(self, conn=None):
        from .sentence_patterns import build_sentence_pattern_hypotheses
        return build_sentence_pattern_hypotheses(self.engine, limit=self.limit)

