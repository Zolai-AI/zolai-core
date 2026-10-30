"""Evidence ranking and packaging for RAG (Phase 6 §36)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Evidence tier weights (matching foundation/evidence.py)
EVIDENCE_TIER_WEIGHTS = {
    1: 1.0,   # BIBLE_PARALLEL
    2: 0.9,   # DICTIONARY
    3: 0.8,   # GRAMMAR_PATTERNS
    4: 0.7,   # CORPUS_ATTESTATION
    5: 0.5,   # MODEL_GENERATED
}

SOURCE_TIER_MAP = {
    "bible_verses": 1,
    "dictionary": 2,
    "dictionary_en_zo": 2,
    "grammar_patterns": 3,
    "word_usage": 4,
    "word_observation_stats": 4,
    "attestation_index": 4,
    "hypotheses": 4,
    "knowledge_claims": 3,
    "kg_nodes": 3,
}


def _dedupe_pos(values: list[Any]) -> list[str]:
    """Collapse POS tags that differ only by case (``Noun`` + ``NOUN``).

    The dictionary column spells the label (``Noun``), the claims/KG tiers spell
    the tag (``NOUN``), and both describe one part of speech — so the word
    payload used to carry two chips for it (explorer#23). The readable label
    wins; an all-uppercase tag is kept only when no label spelling exists.
    """
    by_key: dict[str, str] = {}
    for value in values:
        text_value = str(value)
        key = text_value.casefold()
        kept = by_key.get(key)
        if kept is None or (kept.isupper() and not text_value.isupper()):
            by_key[key] = text_value
    return list(by_key.values())


@dataclass
class EvidenceItem:
    """Single piece of evidence."""
    source_type: str          # 'dictionary', 'bible', 'grammar', 'corpus', 'kg', etc.
    source: str               # specific table/source name
    fact_key: str             # e.g., 'word:pasian', 'pattern:disc_sp_1'
    tier: int                 # 1-5
    confidence: float         # 0.0-1.0
    text: str                 # evidence text snippet
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidencePack:
    """Structured evidence pack for a word/query (§20)."""
    word: str
    forms: list[str] = field(default_factory=list)
    frequency: int = 0
    document_frequency: int = 0
    sentence_frequency: int = 0
    pos: list[str] = field(default_factory=list)
    morphology: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    collocations: list[dict[str, Any]] = field(default_factory=list)
    grammar_usage: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_items: list[EvidenceItem] = field(default_factory=list)


def get_tier_for_source(source: str) -> int:
    """Get evidence tier for a source."""
    return SOURCE_TIER_MAP.get(source, 4)


def rank_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Rank evidence by tier weight + confidence (highest first)."""
    def score(item: EvidenceItem) -> float:
        tier_weight = EVIDENCE_TIER_WEIGHTS.get(item.tier, 0.5)
        return tier_weight * item.confidence

    return sorted(items, key=score, reverse=True)


def evidence_to_pack(word: str, evidence_items: list[EvidenceItem]) -> EvidencePack:
    """Convert ranked evidence items into structured EvidencePack (§20)."""
    if not evidence_items:
        return EvidencePack(word=word)

    ranked = rank_evidence(evidence_items)

    # Aggregate
    pack = EvidencePack(word=word)
    # Keep the exact items the pack was aggregated from: the evidence chain
    # stays available in one pass (wire models deliberately drop this field —
    # see ``agent.tools.language._word_detail``).
    pack.evidence_items = list(evidence_items)
    seen_sources = set()
    tier_weights = []

    for item in ranked:
        pack.sources.append(item.source)
        seen_sources.add(item.source_type)
        tier_weights.append(EVIDENCE_TIER_WEIGHTS.get(item.tier, 0.5))

        # Extract structured info from metadata
        meta = item.metadata

        # Forms
        if "forms" in meta:
            pack.forms.extend(meta["forms"])

        # Frequency
        #
        # A frequency is a *count of the same corpus made by one source*, not an
        # addend. Summing let a KG node's weight inflate the entry: pasian's
        # ``word_observation_stats`` row says 5123, its ``kg_nodes`` properties
        # say 100, and the entry answered 5223 — a number no source reports
        # (explorer#16 / explorer#37). Best-supported count wins, as it already
        # does for document_frequency.
        freq = meta.get("frequency")
        if freq is not None:
            pack.frequency = max(pack.frequency, freq)
        doc_freq = meta.get("document_frequency")
        if doc_freq is not None:
            pack.document_frequency = max(pack.document_frequency, doc_freq)
        # The observation stats carry this one and the aggregator used to drop
        # it on the floor, so every entry answered sentence_frequency 0 while
        # its own evidence metadata said 4366 (explorer#37).
        sent_freq = meta.get("sentence_frequency")
        if sent_freq is not None:
            pack.sentence_frequency = max(pack.sentence_frequency, sent_freq)

        # POS
        if "pos" in meta and meta["pos"] is not None:
            pos_val = meta["pos"]
            if isinstance(pos_val, list):
                pack.pos.extend([p for p in pos_val if p is not None])
            else:
                pack.pos.append(pos_val)

        # Morphology
        if "morphology" in meta:
            pack.morphology.update(meta["morphology"])

        # Examples
        if item.text:
            pack.examples.append(item.text)

        # Collocations \u2014 normalize to {word1, word2, pmi, freq}
        for coll in meta.get("collocations") or []:
            if not isinstance(coll, dict):
                continue
            if "token" in coll:
                pack.collocations.append({
                    "word1": word,
                    "word2": coll.get("token", ""),
                    "pmi": coll.get("pmi", 0.0),
                    "freq": coll.get("count", 0),
                })
            elif "word1" in coll:
                pack.collocations.append({
                    "word1": coll.get("word1", ""),
                    "word2": coll.get("word2", ""),
                    "pmi": coll.get("pmi", 0.0),
                    "freq": coll.get("freq", coll.get("frequency", 0)),
                })

        # Grammar usage
        if "grammar" in meta:
            pack.grammar_usage.extend(meta["grammar"])

    # Deduplicate (lists may contain dicts -> not hashable, so key on JSON)
    def _dedupe(seq):
        seen, uniq = set(), []
        for v in seq:
            try:
                k = json.dumps(v, sort_keys=True, default=str) if isinstance(v, (dict, list)) else str(v)
            except (TypeError, ValueError):
                k = repr(v)
            if k not in seen:
                seen.add(k)
                uniq.append(v)
        return uniq

    pack.forms = _dedupe(pack.forms)
    pack.pos = _dedupe_pos(pack.pos)
    pack.examples = _dedupe(pack.examples)
    pack.sources = _dedupe(pack.sources)
    pack.collocations = _dedupe(pack.collocations)
    pack.grammar_usage = _dedupe(pack.grammar_usage)


    # Confidence = weighted average of evidence confidences
    if tier_weights:
        weighted = sum(w * item.confidence for w, item in zip(tier_weights, ranked))
        pack.confidence = round(weighted / sum(tier_weights), 2)

    pack.evidence_items = ranked
    return pack


def grammar_pattern_rows(engine: Engine, word: str, limit: int = 20) -> list[dict[str, Any]]:
    """Grammar-pattern rows naming a word — the one query behind both
    ``GET /word/{w}/patterns`` and the entry's own ``grammar_usage`` field.

    Single source on purpose: the Patterns tab listed three OBSERVED rows for
    ``pasian`` while the entry's Grammar card said "no patterns", because the
    entry never consulted ``grammar_patterns`` at all (explorer#16 / #37).

    A corpus without the table answers empty rather than raising: the other
    sources here assume their tables exist, but this one is new, and a lean
    database must not start 500ing on the word entry it used to serve.
    """
    if not sa_inspect(engine).has_table("grammar_patterns"):
        return []
    rows: list[dict[str, Any]] = []
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT pattern_id, pattern, description, function, examples, confidence, status
                FROM grammar_patterns
                WHERE pattern LIKE :w OR description LIKE :w
                ORDER BY confidence DESC
                LIMIT :lim
            """),
            {"w": f"%{word}%", "lim": limit},
        ).fetchall()
        for r in result:
            rows.append({
                "pattern_id": r[0],
                "pattern": r[1],
                "description": r[2],
                "function": r[3],
                "examples": json.loads(r[4]) if r[4] else [],
                "confidence": r[5],
                "status": r[6],
            })
    return rows


def build_word_evidence_items(engine: Engine, word: str, limit: int = 50) -> list[EvidenceItem]:
    """Build evidence items for a word from all canonical sources."""
    items = []

    # 1. Dictionary
    #
    # ``confidence`` is a 0..1 lexicon score, NOT a count. It used to be
    # aliased ``frequency`` here, so every dictionary row added its confidence
    # to the pack's corpus frequency: ``word_observation_stats`` said 5223 and
    # the API answered 5223.9 (a float in an int field, which the wire model
    # rightly rejects). Frequencies come from the observation stats only.
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT zolai, english as definition, pos_canonical, pos_evidence, confidence
                FROM dictionary WHERE zolai = :word
                UNION ALL
                SELECT headword, translations_clean as definition, pos, NULL as pos_evidence, NULL
                FROM dictionary_en_zo WHERE headword = :word
            """),
            {"word": word},
        ).fetchall()

        for row in rows:
            items.append(EvidenceItem(
                source_type="dictionary",
                source="dictionary",
                fact_key=f"word:{word}",
                tier=2,
                confidence=row[4] if row[4] is not None else 0.9,
                text=row[1] if row[1] else "",
                metadata={
                    "word": row[0],
                    "definition": row[1],
                    "pos": row[2] if row[2] else None,
                },
            ))

    # 2. Bible attestations (attestation_index)
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ai.word, ai.source, bv.zo_tdb77, bv.ref
                FROM attestation_index ai
                JOIN bible_verses bv ON bv.ref = ai.source
                WHERE ai.word = :word
                LIMIT 20
            """),
            {"word": word},
        ).fetchall()

        for row in rows:
            items.append(EvidenceItem(
                source_type="bible",
                source="bible_verses",
                fact_key=f"word:{word}",
                tier=1,
                confidence=1.0,
                text=f"{row[3]}: {row[2]}",
                metadata={
                    "word": row[0],
                    "source": row[1],
                    "example_zo": row[2],
                    "ref": row[3],
                },
            ))

    # 3. Word observation stats (contexts, collocations, frequency)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM word_observation_stats WHERE normalized_form = :word"),
            {"word": word},
        ).first()

        if row:
            r = dict(row._mapping)

            def _jload(val, default):
                if isinstance(val, str):
                    try:
                        return json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        return default
                return val if val is not None else default

            items.append(EvidenceItem(
                source_type="corpus",
                source="word_observation_stats",
                fact_key=f"word:{word}",
                tier=4,
                confidence=0.7,
                text=f"Freq: {r.get('frequency', 0)}",
                metadata={
                    "frequency": r.get("frequency") or 0,
                    "document_frequency": r.get("doc_freq") or 0,
                    "sentence_frequency": r.get("sent_freq") or 0,
                    "contexts": _jload(r.get("contexts"), {}),
                    "collocations": _jload(r.get("collocations"), []),
                    "surface_forms": _jload(r.get("surface_forms"), []),
                    "attestation": _jload(r.get("attestation"), {}),
                },
            ))

    # 4. Knowledge claims
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT subject, predicate, object, confidence, status, evidence_ids
                FROM knowledge_claims
                WHERE subject LIKE :word_pat
            """),
            {"word_pat": f"word:{word}%"},
        ).fetchall()

        for row in rows:
            items.append(EvidenceItem(
                source_type="knowledge",
                source="knowledge_claims",
                fact_key=f"claim:{row[0]}",
                tier=3,
                confidence=row[3] if row[3] else 0.5,
                text=f"{row[1]} {row[2]}",
                metadata={
                    "claim_type": "knowledge",
                    "predicate": row[1],
                    "object": row[2],
                    "status": row[4],
                },
            ))

    # 5. KG nodes
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT label, node_type, properties, confidence
                FROM kg_nodes WHERE label = :word
            """),
            {"word": word},
        ).fetchall()

        for row in rows:
            items.append(EvidenceItem(
                source_type="kg",
                source="kg_nodes",
                fact_key=f"kg:{row[0]}",
                tier=3,
                confidence=row[3] if row[3] else 0.5,
                text=f"KG node: {row[0]} ({row[1]})",
                metadata=json.loads(row[2]) if isinstance(row[2], str) else row[2],
            ))

    # 6. Grammar patterns (the entry's grammar_usage field)
    for row in grammar_pattern_rows(engine, word, limit):
        items.append(EvidenceItem(
            source_type="grammar",
            source="grammar_patterns",
            fact_key=f"pattern:{row['pattern_id']}",
            tier=3,
            confidence=row["confidence"] if row["confidence"] is not None else 0.5,
            # Deliberately no excerpt: the pattern string ("ciangin-Pasian") is a
            # template, not a usage sentence, and ``pack.examples`` is built from
            # item text — a common word like "hi" matches the pattern limit, which
            # would bury the definitions and verses under template names.
            text="",
            metadata={"grammar": [row]},
        ))

    return items
