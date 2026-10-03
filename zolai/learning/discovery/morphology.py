"""Morphological relation discovery (Phase 3 §36).

Builds MorphologicalRelation rows (kind='morph_relation') from:
- Top-N attested surface forms → decompose via EnhancedMorphologyAnalyzer
- Evidence tier: DICTIONARY if root in _KNOWN_ROOTS else CORPUS_ATTESTATION
- CANDIDATE for heuristic-only splits (no dictionary root match)
- Relation types: prefix, suffix, compound, stem
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.foundation.evidence import EvidenceTier
from zolai.foundation.morphology import _KNOWN_ROOTS, get_enhanced_morphology
from zolai.learning.discovery.evidence import upsert_evidence, upsert_evidence_bulk, EVIDENCE_TIER_MAP
from zolai.morphology import _KNOWN_ROOTS as MORPH_KNOWN_ROOTS
from zolai.shared.contracts import MorphologicalRelation
from zolai.shared.contracts.base import require_discovery_status

log = logging.getLogger(__name__)

# Cap for morphology hypotheses (plan default)
MORPH_CAP = 5000


def _get_top_words(engine: Engine, limit: int | None = None) -> list[str]:
    """Get top words by frequency from vocabulary + word_usage."""
    words: set[str] = set()
    lim = f"LIMIT {limit}" if limit else ""

    with engine.connect() as conn:
        # From vocabulary
        rows = conn.execute(
            text(
                f"SELECT headword FROM vocabulary "
                f"WHERE frequency > 0 ORDER BY frequency DESC {lim}"
            )
        ).fetchall()
        words.update(r[0] for r in rows)

        # From word_usage
        rows = conn.execute(
            text(
                f"SELECT word FROM word_usage "
                f"WHERE total_freq > 0 ORDER BY total_freq DESC {lim}"
            )
        ).fetchall()
        words.update(r[0] for r in rows)

        # From bible_verses (tokenized)
        rows = conn.execute(
            text(
                f"SELECT zo_tdb77 FROM bible_verses "
                f"WHERE zo_tdb77 IS NOT NULL AND TRIM(zo_tdb77) != '' {lim}"
            )
        ).fetchall()
        for (zo,) in rows:
            for w in zo.split():
                words.add(w)

    # Sort by combined frequency (approximate)
    word_freq: dict[str, int] = {}
    with engine.connect() as conn:
        for word in words:
            row = conn.execute(
                text("SELECT COALESCE(SUM(total_freq), 0) FROM word_usage WHERE word = :w"),
                {"w": word},
            ).first()
            if row and row[0]:
                word_freq[word] = row[0]
            else:
                row = conn.execute(
                    text("SELECT frequency FROM vocabulary WHERE headword = :w"),
                    {"w": word},
                ).first()
                if row and row[0]:
                    word_freq[word] = row[0]
                else:
                    word_freq[word] = 1

    sorted_words = sorted(words, key=lambda w: word_freq.get(w, 0), reverse=True)
    if limit:
        sorted_words = sorted_words[:limit]
    return sorted_words


def _known_roots() -> set[str]:
    """Union of _KNOWN_ROOTS from both morphology modules."""
    return set(_KNOWN_ROOTS) | set(MORPH_KNOWN_ROOTS)


def build_morphology_hypotheses(
    engine: Engine,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build morphological relation hypotheses.

    Returns summary dict with counts and status breakdown.
    """
    from zolai.data.repositories.knowledge import HypothesisRepository

    repo = HypothesisRepository(engine)
    analyzer = get_enhanced_morphology()
    known_roots = _known_roots()

    words = _get_top_words(engine, limit)
    relations_written = 0
    status_counts: dict[str, int] = {"OBSERVED": 0, "CANDIDATE": 0}
    skipped = 0

    for word in words:
        if relations_written >= MORPH_CAP:
            break

        try:
            analysis = analyzer.decompose(word)
        except Exception:
            skipped += 1
            continue

        # No meaningful decomposition found
        if not analysis.directional and not analysis.aspect and not analysis.particle and not analysis.compound_parts:
            continue

        evidence_rows: list[dict[str, Any]] = []
        relation_entries: list[tuple[str, str, str, str]] = []  # (relation_type, root, function, evidence_tier)

        # Directional prefix
        if analysis.directional:
            relation_entries.append((
                "prefix",
                analysis.directional,
                "directional",
                4 if analysis.directional not in known_roots else 2,
            ))
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4 if analysis.directional not in known_roots else 2,
                "source": "morphology_analyzer" if analysis.directional not in known_roots else "dictionary",
                "method": "directional_detection",
                "extractor": "EnhancedMorphologyAnalyzer",
                "payload": {"surface": word, "directional": analysis.directional, "stem": analysis.stem},
            })

        # Aspect suffix
        if analysis.aspect:
            relation_entries.append((
                "suffix",
                analysis.aspect,
                "aspect",
                4 if analysis.aspect not in known_roots else 2,
            ))
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4 if analysis.aspect not in known_roots else 2,
                "source": "morphology_analyzer" if analysis.aspect not in known_roots else "dictionary",
                "method": "aspect_detection",
                "extractor": "EnhancedMorphologyAnalyzer",
                "payload": {"surface": word, "aspect": analysis.aspect, "stem": analysis.stem},
            })

        # Particle
        if analysis.particle:
            relation_entries.append((
                "suffix",
                analysis.particle,
                "particle",
                4 if analysis.particle not in known_roots else 2,
            ))
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4 if analysis.particle not in known_roots else 2,
                "source": "morphology_analyzer" if analysis.particle not in known_roots else "dictionary",
                "method": "particle_detection",
                "extractor": "EnhancedMorphologyAnalyzer",
                "payload": {"surface": word, "particle": analysis.particle, "stem": analysis.stem},
            })

        # Compound parts
        if analysis.compound_parts:
            for part in analysis.compound_parts:
                relation_entries.append((
                    "compound",
                    part,
                    "compound_component",
                    4 if part not in known_roots else 2,
                ))
                evidence_rows.append({
                    "fact_type": "word",
                    "fact_key": f"word:{word}",
                    "tier": 4 if part not in known_roots else 2,
                    "source": "morphology_analyzer" if part not in known_roots else "dictionary",
                    "method": "compound_decomposition",
                    "extractor": "EnhancedMorphologyAnalyzer",
                    "payload": {"surface": word, "compound_part": part, "all_parts": list(analysis.compound_parts)},
                })

        if not relation_entries:
            continue

        if not dry_run:
            upsert_evidence_bulk(engine, evidence_rows)

        for relation_type, root, function, tier in relation_entries:
            # Determine status
            status = "OBSERVED" if tier == 2 else "CANDIDATE"  # DICTIONARY tier = OBSERVED, heuristic = CANDIDATE

            # Confidence from evidence
            class _MiniEvidence:
                def __init__(self, t: int):
                    self.tier = t

            confidence = EVIDENCE_TIER_MAP.get(tier, (None, 0.0, ""))[1]

            if not dry_run:
                hypo = MorphologicalRelation(
                    surface=word,
                    root=root,
                    relation_type=relation_type,
                    function=function,
                    confidence=confidence,
                    status=status,
                    evidence_ids=[],
                    extras={"analysis_tier": tier, "is_valid": analysis.is_valid},
                )
                repo.create(hypo.model_dump())
                relations_written += 1
            else:
                relations_written += 1

            status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "capability": "morphology",
        "relations_written": relations_written,
        "status_counts": status_counts,
        "skipped": skipped,
        "words_processed": len(words),
    }
class MorphologyDiscovery:
    """Wrapper class for morphology hypothesis discovery matching test expectations."""
    
    def __init__(self, engine, limit=100):
        self.engine = engine
        self.limit = limit
    
    def run(self, conn=None):
        from .morphology import build_morphology_hypotheses
        return build_morphology_hypotheses(self.engine, limit=self.limit)

