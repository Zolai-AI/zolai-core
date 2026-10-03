"""Grammar phenomena discovery → grammar_patterns (Phase 3 §36).

Declarative phenomena specification (Master Prompt §10):
- Tense/aspect markers (ta, zo, khin, lai, ding, tak, ah)
- Coordination (leh, banah, tua, te, huna, ciangin, panin, kumin)
- Subordination (ci hi, ci-in, huna, panin)
- Relative clauses
- Question formation (hiam, diam, bang hang)
- Negation patterns (kei, lo, kei ding)
- Particles (hi, hen, un, in, vo, ding, ci, aw, ni, lah, te, teh)
- Ergative construction (in)

Reuses BiblePatternLearner classification — NOT a new engine.
Writes to grammar_patterns with namespace 'disc_g_*'.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.learning.bible_pattern_learner import get_bible_learner
from zolai.learning.discovery.evidence import upsert_evidence, upsert_evidence_bulk, EVIDENCE_TIER_MAP
from zolai.shared.contracts.base import require_discovery_status

log = logging.getLogger(__name__)

# Cap for grammar phenomena (plan default: few hundred)
GRAMMAR_CAP = 500


@dataclass
class GrammarPhenomenon:
    """A discovered grammar phenomenon."""
    phenomenon_id: str
    phenomenon_type: str
    pattern: str
    description: str
    example_zo: str
    example_en: str
    ref: str
    components: dict[str, Any]
    normalized: str


# ── Phenomena specifications (declarative, per §10) ────────────────────────────

PHENOMENA_SPECS: list[dict[str, Any]] = [
    {
        "type": "tense_aspect",
        "markers": ["ta", "zo", "khin", "lai", "ding", "tak", "ah"],
        "description": "Tense/aspect marking on verbs",
    },
    {
        "type": "coordination",
        "markers": ["leh", "banah", "tua", "te", "huna", "ciangin", "panin", "kumin"],
        "description": "Clause/phrase coordination",
    },
    {
        "type": "subordination",
        "markers": ["ci hi", "ci-in", "huna", "panin"],
        "description": "Subordinate clause markers",
    },
    {
        "type": "question_formation",
        "markers": ["hiam", "diam", "bang hang"],
        "description": "Question formation patterns",
    },
    {
        "type": "negation",
        "markers": ["kei", "lo", "kei ding"],
        "description": "Negation patterns",
    },
    {
        "type": "particles",
        "markers": ["hi", "hen", "un", "in", "vo", "ding", "ci", "aw", "ni", "lah", "te", "teh"],
        "description": "Sentence-final and clause particles",
    },
    {
        "type": "ergative",
        "markers": ["in"],
        "description": "Ergative agent marking",
    },
]


def _extract_phenomena_from_text(
    text: str,
    ref: str,
    en_text: str,
    learner,
) -> list[GrammarPhenomenon]:
    """Extract all matching phenomena from a Zolai text."""
    phenomena: list[GrammarPhenomenon] = []
    lower = text.lower()

    for spec in PHENOMENA_SPECS:
        ptype = spec["type"]
        for marker in spec["markers"]:
            if marker in lower:
                # Find the position/context
                pattern_match = re.search(rf".{{0,30}}{re.escape(marker)}.{{0,30}}", text)
                context = pattern_match.group(0) if pattern_match else text[:100]

                phenomenon_id = f"disc_g_{ptype}_{marker.replace(' ', '_')}_{abs(hash(text)) % 100000}"
                normalized = f"{ptype}:{marker}"

                # Components: use learner's extraction + marker position
                components = learner._extract_components(text)
                components["phenomenon_marker"] = marker
                components["phenomenon_type"] = ptype
                components["context"] = context

                phenomena.append(GrammarPhenomenon(
                    phenomenon_id=phenomenon_id,
                    phenomenon_type=ptype,
                    pattern=f"{ptype}:{marker}",
                    description=spec["description"],
                    example_zo=text,
                    example_en=en_text or "",
                    ref=ref,
                    components=components,
                    normalized=normalized,
                ))

    return phenomena


def _extract_phenomena_from_bible(engine: Engine, limit: int | None = None) -> list[GrammarPhenomenon]:
    """Extract grammar phenomena from bible_verses."""
    learner = get_bible_learner()
    phenomena: list[GrammarPhenomenon] = []
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
        phenomena.extend(_extract_phenomena_from_text(zo, ref, en or "", learner))

    return phenomena


def _extract_phenomena_from_translations(engine: Engine, limit: int | None = None) -> list[GrammarPhenomenon]:
    """Extract grammar phenomena from translations table."""
    learner = get_bible_learner()
    phenomena: list[GrammarPhenomenon] = []
    lim = f"LIMIT {limit}" if limit else ""

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
        phenomena.extend(_extract_phenomena_from_text(zo, ref or "translation", en or "", learner))

    return phenomena


def _deduplicate_phenomena(phenomena: list[GrammarPhenomenon]) -> list[GrammarPhenomenon]:
    """Deduplicate by (phenomenon_type, marker, normalized pattern)."""
    seen: dict[tuple[str, str, str], GrammarPhenomenon] = {}
    for p in phenomena:
        marker = p.components.get("phenomenon_marker", "")
        key = (p.phenomenon_type, marker, p.normalized)
        if key not in seen:
            seen[key] = p
    return list(seen.values())


def _write_grammar_phenomenon(
    engine: Engine,
    phenomenon: GrammarPhenomenon,
    source_name: str,
    dry_run: bool = False,
) -> tuple[int, list[int]]:
    """Write one grammar phenomenon to grammar_patterns."""
    from zolai.data.repositories.grammar import GrammarRepository

    repo = GrammarRepository(engine)

    phenomenon_id = phenomenon.phenomenon_id

    # Check if already exists
    existing = repo.get_by_pattern_id(phenomenon_id)
    if existing:
        return (existing["id"], [])

    # Evidence row
    evidence_rows = [{
        "fact_type": "grammar",
        "fact_key": f"pattern:{phenomenon_id}",
        "tier": 1,  # BIBLE_PARALLEL
        "source": source_name,
        "method": "grammar_phenomenon_extraction",
        "extractor": "BiblePatternLearner",
        "payload": {
            "phenomenon_id": phenomenon_id,
            "phenomenon_type": phenomenon.phenomenon_type,
            "pattern": phenomenon.pattern,
            "example_zo": phenomenon.example_zo,
            "example_en": phenomenon.example_en,
            "ref": phenomenon.ref,
            "components": phenomenon.components,
            "normalized": phenomenon.normalized,
        },
    }]

    # Confidence from evidence (tier 1 = 1.0)
    class _MiniEvidence:
        def __init__(self, t: int):
            self.tier = t
    confidence = EVIDENCE_TIER_MAP[1][1]  # 1.0

    # Status: OBSERVED (single unambiguous Bible source)
    status = require_discovery_status("OBSERVED").value

    if not dry_run:
        upsert_evidence_bulk(engine, evidence_rows)
        with engine.connect() as conn:
            ev_row = conn.execute(
                text(
                    "SELECT id FROM foundation_evidence "
                    "WHERE fact_type = 'grammar' AND fact_key = :fk AND source = :src"
                ),
                {"fk": f"pattern:{phenomenon_id}", "src": source_name},
            ).first()
        evidence_ids = [ev_row[0]] if ev_row else []

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO grammar_patterns "
                    "(pattern_id, pattern, description, function, examples, frequency, "
                    "normalized, components, sources, evidence_ids, confidence, status) "
                    "VALUES (:pid, :pat, :desc, :func, :ex, :freq, :norm, :comp, :srcs, :eids, :conf, :stat)"
                ),
                {
                    "pid": phenomenon_id,
                    "pat": phenomenon.pattern,
                    "desc": phenomenon.description,
                    "func": phenomenon.phenomenon_type,
                    "ex": json.dumps([phenomenon.example_zo], ensure_ascii=False),
                    "freq": 1,
                    "norm": phenomenon.normalized,
                    "comp": json.dumps(phenomenon.components, ensure_ascii=False),
                    "srcs": json.dumps([source_name], ensure_ascii=False),
                    "eids": json.dumps(evidence_ids, ensure_ascii=False),
                    "conf": confidence,
                    "stat": status,
                },
            )
    else:
        evidence_ids = []

    return (0, evidence_ids)


def build_grammar_hypotheses(
    engine: Engine,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build grammar phenomena into grammar_patterns (disc_g_* namespace)."""
    phenomena_bible = _extract_phenomena_from_bible(engine, limit)
    phenomena_trans = _extract_phenomena_from_translations(engine, limit)

    all_phenomena = _deduplicate_phenomena(phenomena_bible + phenomena_trans)
    if limit:
        all_phenomena = all_phenomena[:limit]

    patterns_written = 0
    status_counts: dict[str, int] = {"OBSERVED": 0, "CANDIDATE": 0}
    skipped = 0

    for phenomenon in all_phenomena:
        if patterns_written >= GRAMMAR_CAP:
            break

        source_name = "bible_verses" if not phenomenon.ref.startswith("translation") else "translations"

        try:
            _write_grammar_phenomenon(engine, phenomenon, source_name, dry_run)
            patterns_written += 1
            status_counts["OBSERVED"] += 1
        except Exception as exc:
            log.warning("Failed to write phenomenon %s: %s", phenomenon.phenomenon_id, exc)
            skipped += 1

    return {
        "capability": "grammar",
        "patterns_written": patterns_written,
        "status_counts": status_counts,
        "skipped": skipped,
        "candidates_considered": len(all_phenomena),
    }
class GrammarDiscovery:
    """Wrapper class for grammar discovery matching test expectations."""
    
    def __init__(self, engine, limit=100):
        self.engine = engine
        self.limit = limit
    
    def run(self, conn=None):
        from .grammar import build_grammar_hypotheses
        return build_grammar_hypotheses(self.engine, limit=self.limit)

