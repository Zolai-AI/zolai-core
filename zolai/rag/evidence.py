"""Evidence ranking and packaging for RAG (Phase 6 §36)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

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
        freq = meta.get("frequency")
        if freq is not None:
            pack.frequency += freq
        doc_freq = meta.get("document_frequency")
        if doc_freq is not None:
            pack.document_frequency = max(pack.document_frequency, doc_freq)
        
        # POS
        if "pos" in meta:
            pack.pos.append(meta["pos"])
        
        # Morphology
        if "morphology" in meta:
            pack.morphology.update(meta["morphology"])
        
        # Examples
        if item.text:
            pack.examples.append(item.text)
        
        # Collocations
        if "collocations" in meta:
            pack.collocations.extend(meta["collocations"])
        
        # Grammar usage
        if "grammar" in meta:
            pack.grammar_usage.extend(meta["grammar"])
    
    # Deduplicate
    pack.forms = list(dict.fromkeys(pack.forms))
    pack.pos = list(dict.fromkeys(pack.pos))
    pack.examples = list(dict.fromkeys(pack.examples))
    pack.sources = list(dict.fromkeys(pack.sources))
    
    # Confidence = weighted average of evidence confidences
    if tier_weights:
        pack.confidence = round(sum(w * item.confidence for w, item in zip(tier_weights, ranked)) / sum(tier_weights), 2)
    
    pack.evidence_items = ranked
    return pack


def build_word_evidence_items(engine: Engine, word: str, limit: int = 50) -> list[EvidenceItem]:
    """Build evidence items for a word from all canonical sources."""
    items = []
    
    # 1. Dictionary
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT zolai, english as definition, pos_canonical, pos_evidence, confidence as frequency
                FROM dictionary WHERE zolai = :word
                UNION ALL
                SELECT headword, translations_clean as definition, pos, NULL as pos_evidence, 0 as frequency
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
                confidence=0.9,
                text=row[1] if row[1] else "",
                metadata={
                    "word": row[0],
                    "definition": row[1],
                    "pos": row[2] if row[2] else None,
                    "frequency": row[4] if row[4] is not None else 0,
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
            items.append(EvidenceItem(
                source_type="corpus",
                source="word_observation_stats",
                fact_key=f"word:{word}",
                tier=4,
                confidence=0.7,
                text=f"Freq: {r.get('frequency', 0)}",
                metadata={
                    "frequency": r.get("frequency", 0),
                    "document_frequency": r.get("document_frequency", 0),
                    "sentence_frequency": r.get("sentence_frequency", 0),
                    "contexts": r.get("contexts"),
                    "collocations": r.get("collocations"),
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
    
    return items
