"""POS hypothesis discovery (Phase 3 §36).

Builds POSHypothesis rows (kind='pos') from multiple evidence sources:
1. Dictionary POS (dictionary.pos_canonical / pos) → DICTIONARY tier
2. Rule tagger (ZolaiPOSTagger) over attested sentences → CORPUS_ATTESTATION tier
3. word_usage per-book distribution → CORPUS_ATTESTATION tier
4. word_observation_stats neighbors (when available) → CORPUS_ATTESTATION tier
5. Morphological features (from EnhancedMorphologyAnalyzer) → CORPUS_ATTESTATION tier
6. Sentence-position histogram → CORPUS_ATTESTATION tier

Graceful fallback when partial data (word_observation_stats has only 2,075 rows).
Status: OBSERVED if single unambiguous source, else CANDIDATE.
Tagger scores → extras, NEVER contract confidence.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.pos_normalize import UPOS_ALLOWLIST, to_upos
from zolai.learning.discovery.evidence import upsert_evidence_bulk
from zolai.shared.contracts import POSHypothesis
from zolai.shared.contracts.base import confidence_from_evidence, require_discovery_status

log = logging.getLogger(__name__)

# Cap for POS hypotheses (plan default)
POS_CAP = 5000


def _get_dictionary_pos(engine: Engine, limit: int | None = None) -> list[dict[str, Any]]:
    """Get words with POS from dictionary tables."""
    lim = f"LIMIT {limit}" if limit else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT zolai AS word, pos_canonical, pos, pos_candidates, pos_evidence "
                f"FROM dictionary WHERE pos_canonical IS NOT NULL AND TRIM(pos_canonical) != '' {lim}"
            )
        ).fetchall()
    return [
        {
            "word": r[0],
            "pos_canonical": r[1],
            "pos_legacy": r[2],
            "pos_candidates": r[3],
            "pos_evidence": r[4],
        }
        for r in rows
    ]


def _get_vocabulary_pos(engine: Engine, limit: int | None = None) -> list[dict[str, Any]]:
    """Get words with POS from vocabulary tables."""
    lim = f"LIMIT {limit}" if limit else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT headword AS word, pos_canonical, pos, pos_candidates, pos_evidence "
                f"FROM vocabulary WHERE pos_canonical IS NOT NULL AND TRIM(pos_canonical) != '' {lim}"
            )
        ).fetchall()
    return [
        {
            "word": r[0],
            "pos_canonical": r[1],
            "pos_legacy": r[2],
            "pos_candidates": r[3],
            "pos_evidence": r[4],
        }
        for r in rows
    ]


def _get_zolai_vocabulary_pos(engine: Engine, limit: int | None = None) -> list[dict[str, Any]]:
    """Get words with POS from zolai_vocabulary table."""
    lim = f"LIMIT {limit}" if limit else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT zolai AS word, pos_canonical, pos, pos_candidates, pos_evidence "
                f"FROM zolai_vocabulary WHERE pos_canonical IS NOT NULL AND TRIM(pos_canonical) != '' {lim}"
            )
        ).fetchall()
    return [
        {
            "word": r[0],
            "pos_canonical": r[1],
            "pos_legacy": r[2],
            "pos_candidates": r[3],
            "pos_evidence": r[4],
        }
        for r in rows
    ]


def _get_word_usage_distribution(engine: Engine, limit: int | None = None) -> dict[str, Counter]:
    """Get per-book word frequency distribution from word_usage."""
    lim = f"LIMIT {limit}" if limit else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT word, book, total_freq FROM word_usage "
                f"WHERE total_freq > 0 {lim}"
            )
        ).fetchall()
    result: dict[str, Counter] = {}
    for word, book, freq in rows:
        if word not in result:
            result[word] = Counter()
        result[word][book] = freq
    return result


def _get_observation_neighbors(engine: Engine) -> dict[str, list[str]]:
    """Get left/right neighbors from word_observation_stats.neighbors (graceful fallback).

    Returns empty dict if table is empty or columns missing.
    """
    result: dict[str, list[str]] = {}
    try:
        with engine.connect() as conn:
            # Check if table has data
            count = conn.execute(text("SELECT COUNT(*) FROM word_observation_stats")).scalar()
            if not count:
                return result

            rows = conn.execute(
                text(
                    "SELECT normalized_form, neighbors FROM word_observation_stats "
                    "WHERE neighbors IS NOT NULL AND neighbors != '[]'"
                )
            ).fetchall()
        for form, neighbors_json in rows:
            try:
                neighbors = json.loads(neighbors_json)
                if isinstance(neighbors, list):
                    result[form] = [n.get("word", "") for n in neighbors if isinstance(n, dict)]
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        # Table missing or schema mismatch — graceful fallback
        pass
    return result


def _get_tagged_sentences(engine: Engine, limit: int | None = None) -> list[str]:
    """Get attested Zolai sentences for tagger (from bible_verses + translations)."""
    lim = f"LIMIT {limit}" if limit else ""
    sentences: list[str] = []
    with engine.connect() as conn:
        # Bible verses (zo_tdb77)
        rows = conn.execute(
            text(
                f"SELECT zo_tdb77 FROM bible_verses "
                f"WHERE zo_tdb77 IS NOT NULL AND TRIM(zo_tdb77) != '' {lim}"
            )
        ).fetchall()
        sentences.extend(r[0] for r in rows if r[0])

        # Translations (source/target)
        rows = conn.execute(
            text(
                f"SELECT source FROM translations "
                f"WHERE direction = 'zo_en' AND source IS NOT NULL AND TRIM(source) != '' {lim}"
            )
        ).fetchall()
        sentences.extend(r[0] for r in rows if r[0])
    return sentences


def _tagger_pos_distribution(
    tagger, sentences: list[str], limit: int | None = None
) -> dict[str, Counter]:
    """Run tagger over sentences, return word → Counter of UPOS tags."""
    from zolai.data.pos_normalize import to_upos

    dist: dict[str, Counter] = {}
    processed = 0
    for sent in sentences:
        if limit and processed >= limit:
            break
        try:
            tagged = tagger.tag_with_confidence(sent)
            for word, pos, _conf in tagged:
                upos = to_upos(pos)
                if upos:
                    if word not in dist:
                        dist[word] = Counter()
                    dist[word][upos] += 1
            processed += 1
        except Exception:
            continue
    return dist


def _get_morphology_features(engine: Engine, words: list[str]) -> dict[str, dict[str, Any]]:
    """Get morphological features for words using EnhancedMorphologyAnalyzer."""
    from zolai.foundation.morphology import get_enhanced_morphology

    analyzer = get_enhanced_morphology()
    result: dict[str, dict[str, Any]] = {}
    for word in words:
        try:
            analysis = analyzer.decompose(word)
            result[word] = {
                "directional": analysis.directional,
                "stem": analysis.stem,
                "aspect": analysis.aspect,
                "particle": analysis.particle,
                "compound_parts": list(analysis.compound_parts),
                "is_valid": analysis.is_valid,
            }
        except Exception:
            continue
    return result


def _get_sentence_position_histogram(engine: Engine, limit: int | None = None) -> dict[str, Counter]:
    """Get sentence position (first/middle/last) for words from bible_verses."""
    lim = f"LIMIT {limit}" if limit else ""
    result: dict[str, Counter] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT zo_tdb77 FROM bible_verses "
                f"WHERE zo_tdb77 IS NOT NULL AND TRIM(zo_tdb77) != '' {lim}"
            )
        ).fetchall()
    for (zo,) in rows:
        words = zo.split()
        if not words:
            continue
        n = len(words)
        for i, word in enumerate(words):
            pos_category = "first" if i == 0 else ("last" if i == n - 1 else "middle")
            if word not in result:
                result[word] = Counter()
            result[word][pos_category] += 1
    return result


def _decide_pos_status(sources: list[str]) -> str:
    """Decide status: OBSERVED if single unambiguous source, else CANDIDATE."""
    if len(sources) == 1:
        return "OBSERVED"
    return "CANDIDATE"


def build_pos_hypotheses(
    engine: Engine,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build POS hypotheses from multiple evidence sources.

    Returns summary dict with counts and status breakdown.
    """
    from zolai.data.repositories.knowledge import HypothesisRepository
    from zolai.pos_tagger import get_pos_tagger

    repo = HypothesisRepository(engine)
    tagger = get_pos_tagger()

    # Collect evidence from all sources
    dict_pos = _get_dictionary_pos(engine, limit)
    vocab_pos = _get_vocabulary_pos(engine, limit)
    zolai_vocab_pos = _get_zolai_vocabulary_pos(engine, limit)
    word_usage = _get_word_usage_distribution(engine, limit)
    obs_neighbors = _get_observation_neighbors(engine)
    sentences = _get_tagged_sentences(engine, limit)
    tagger_dist = _tagger_pos_distribution(tagger, sentences, limit)
    sent_pos = _get_sentence_position_histogram(engine, limit)

    # Union of all words seen
    all_words: set[str] = set()
    for src in (dict_pos, vocab_pos, zolai_vocab_pos):
        all_words.update(d["word"] for d in src)
    all_words.update(word_usage.keys())
    all_words.update(obs_neighbors.keys())
    all_words.update(tagger_dist.keys())
    all_words.update(sent_pos.keys())

    if limit:
        all_words = set(list(all_words)[:limit])

    # Get morphology for all words
    morph_features = _get_morphology_features(engine, list(all_words))

    hypotheses_written = 0
    status_counts: Counter = Counter()
    evidence_rows: list[dict[str, Any]] = []
    skipped_unmapped = 0

    for word in sorted(all_words):
        if hypotheses_written >= POS_CAP:
            break

        sources_used: list[str] = []
        upos_candidates: Counter = Counter()
        extras: dict[str, Any] = {"evidence_sources": [], "morphology": morph_features.get(word, {})}

        # 1. Dictionary POS (tier 2)
        for src_list, src_name in [
            (dict_pos, "dictionary"),
            (vocab_pos, "vocabulary"),
            (zolai_vocab_pos, "zolai_vocabulary"),
        ]:
            for d in src_list:
                if d["word"] == word and d["pos_canonical"]:
                    upos = to_upos(d["pos_canonical"])
                    if upos:
                        upos_candidates[upos] += 10  # weight dict higher
                        sources_used.append(src_name)
                        extras["evidence_sources"].append(
                            {"source": src_name, "pos_canonical": d["pos_canonical"], "pos_evidence": d["pos_evidence"]}
                        )
                        # Evidence row
                        evidence_rows.append({
                            "fact_type": "word",
                            "fact_key": f"word:{word}",
                            "tier": 2,
                            "source": src_name,
                            "method": "dictionary_pos_canonical",
                            "extractor": "pos_normalize_backfill",
                            "payload": {
                                "word": word,
                                "pos_canonical": d["pos_canonical"],
                                "pos_evidence": d["pos_evidence"],
                            },
                        })

        # 2. Tagger distribution (tier 4)
        if word in tagger_dist:
            for upos, count in tagger_dist[word].items():
                upos_candidates[upos] += count
            sources_used.append("tagger_corpus")
            extras["evidence_sources"].append({
                "source": "tagger_corpus",
                "distribution": dict(tagger_dist[word]),
            })
            # Evidence row
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4,
                "source": "bible_verses",
                "method": "rule_tagger_attestation",
                "extractor": "ZolaiPOSTagger",
                "payload": {"word": word, "tagger_distribution": dict(tagger_dist[word])},
            })

        # 3. Word usage distribution (tier 4)
        if word in word_usage:
            sources_used.append("word_usage")
            extras["evidence_sources"].append({
                "source": "word_usage",
                "per_book": dict(word_usage[word]),
            })
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4,
                "source": "word_usage",
                "method": "per_book_distribution",
                "extractor": "word_usage_table",
                "payload": {"word": word, "per_book": dict(word_usage[word])},
            })

        # 4. Observation neighbors (tier 4) — graceful fallback
        if word in obs_neighbors:
            sources_used.append("observation_neighbors")
            extras["evidence_sources"].append({
                "source": "observation_neighbors",
                "neighbors": obs_neighbors[word],
            })
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4,
                "source": "word_observation_stats",
                "method": "neighbor_context",
                "extractor": "observation_pipeline",
                "payload": {"word": word, "neighbors": obs_neighbors[word]},
            })

        # 5. Sentence position (tier 4)
        if word in sent_pos:
            sources_used.append("sentence_position")
            extras["evidence_sources"].append({
                "source": "sentence_position",
                "histogram": dict(sent_pos[word]),
            })
            evidence_rows.append({
                "fact_type": "word",
                "fact_key": f"word:{word}",
                "tier": 4,
                "source": "bible_verses",
                "method": "sentence_position_histogram",
                "extractor": "bible_verses",
                "payload": {"word": word, "histogram": dict(sent_pos[word])},
            })

        if not upos_candidates:
            continue

        # Choose best UPOS
        best_upos, best_count = upos_candidates.most_common(1)[0]
        if best_upos not in UPOS_ALLOWLIST:
            skipped_unmapped += 1
            continue

        # Confidence from evidence (tier-weighted)
        # Create minimal Evidence objects for confidence_from_evidence
        class _MiniEvidence:
            def __init__(self, tier: int):
                self.tier = tier

        evidence_items = []
        for src in set(sources_used):
            if src in ("dictionary", "vocabulary", "zolai_vocabulary"):
                evidence_items.append(_MiniEvidence(2))
            else:
                evidence_items.append(_MiniEvidence(4))
        confidence = confidence_from_evidence(evidence_items)

        # Extras with tagger score (never contract confidence)
        if word in tagger_dist:
            extras["tagger_distribution"] = dict(tagger_dist[word])

        status = require_discovery_status(_decide_pos_status(sources_used)).value

        if not dry_run:
            # Upsert evidence
            upsert_evidence_bulk(engine, evidence_rows)

            # Create hypothesis
            hypo = POSHypothesis(
                word=word,
                pos=best_upos,
                confidence=confidence,
                status=status,
                evidence_ids=[],  # Will be filled by evidence upsert but we don't have IDs back easily
                extras=extras,
            )
            repo.create(hypo.model_dump())
            hypotheses_written += 1
        else:
            hypotheses_written += 1

        status_counts[status] += 1
        evidence_rows.clear()

    return {
        "capability": "pos",
        "hypotheses_written": hypotheses_written,
        "status_counts": dict(status_counts),
        "skipped_unmapped": skipped_unmapped,
        "words_processed": len(all_words),
    }
class POSDiscovery:
    """Wrapper class for POS hypothesis discovery matching test expectations."""

    def __init__(self, engine, limit=100):
        self.engine = engine
        self.limit = limit

    def run(self, conn=None):
        from .pos import build_pos_hypotheses
        return build_pos_hypotheses(self.engine, limit=self.limit)

