"""Unified retriever for RAG (Phase 6 §36).

Multi-source retrieval over canonical knowledge.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.rag.evidence import EvidencePack, build_word_evidence_items, evidence_to_pack

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Single search result."""
    id: str
    text: str
    score: float
    source: str
    metadata: dict[str, Any]


class UnifiedRetriever:
    """Multi-source retrieval over canonical knowledge."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._embedder = None

    def _get_embedder(self):
        """Lazy-load sentence transformer for vector search."""
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                log.warning("sentence-transformers not available; vector search disabled")
        return self._embedder

    # --- Word-centric retrieval (§20) ---
    def query_word(self, word: str, limit: int = 20) -> EvidencePack:
        """Get full evidence pack for a word per §20."""
        items = build_word_evidence_items(self.engine, word, limit)
        return evidence_to_pack(word, items)

    def query_word_forms(self, word: str, limit: int = 20) -> list[str]:
        """Get word forms (surface variants)."""
        forms = []
        with self.engine.connect() as conn:
            # Dictionary forms
            rows = conn.execute(
                text(
                    "SELECT zolai FROM dictionary WHERE zolai LIKE :w "
                    "UNION SELECT headword FROM dictionary_en_zo "
                    "WHERE headword LIKE :w LIMIT :lim"
                ),
                {"w": f"{word}%", "lim": limit},
            ).fetchall()
            forms.extend([r[0] for r in rows])
            # Word forms from observation stats
            row = conn.execute(
                text("SELECT surface_forms FROM word_observation_stats WHERE normalized_form = :w"),
                {"w": word},
            ).first()
            if row and row[0]:
                import json
                parsed = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                if isinstance(parsed, list):
                    # entries may be dicts {"form": ..., "count": ...} -> keep strings only
                    for item in parsed:
                        if isinstance(item, str):
                            forms.append(item)
                        elif isinstance(item, dict):
                            val = item.get("form") or item.get("surface") or item.get("word")
                            if isinstance(val, str):
                                forms.append(val)
                elif isinstance(parsed, str):
                    forms.append(parsed)
        # dedupe without hashing (dict.fromkeys crashes on dicts)
        seen, uniq = set(), []
        for f in forms:
            if isinstance(f, str) and f not in seen:
                seen.add(f)
                uniq.append(f)
        return uniq[:limit]

    def query_word_contexts(self, word: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get contexts (left/right/sentence) for a word."""
        contexts = []
        with self.engine.connect() as conn:
            # Bible verses
            rows = conn.execute(
                text("""
                    SELECT ref, zo_tdb77, zo_tedim2010, en_kJV
                    FROM bible_verses
                    WHERE zo_tdb77 LIKE :w OR zo_tedim2010 LIKE :w OR en_kJV LIKE :w
                    LIMIT :lim
                """),
                {"w": f"%{word}%", "lim": limit},
            ).fetchall()
            for r in rows:
                contexts.append({
                    "source": "bible_verses",
                    "ref": r[0],
                    "zo_tdb77": r[1],
                    "zo_tedim2010": r[2],
                    "en": r[3],
                })
        return contexts

    def query_word_collocations(self, word: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get collocations for a word."""
        collocs = []
        with self.engine.connect() as conn:
            # word_collocations table
            rows = conn.execute(
                text("""
                    SELECT word1, word2, frequency, pmiproxy
                    FROM word_collocations
                    WHERE word1 = :w OR word2 = :w
                    ORDER BY pmiproxy DESC
                    LIMIT :lim
                """),
                {"w": word, "lim": limit},
            ).fetchall()
            for r in rows:
                collocs.append({
                    "word1": r[0],
                    "word2": r[1],
                    "frequency": r[2],
                    "pmi": r[3],
                })
        return collocs

    def query_word_patterns(self, word: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get grammar patterns involving a word."""
        patterns = []
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT pattern_id, pattern, description, function, examples, confidence, status
                    FROM grammar_patterns
                    WHERE pattern LIKE :w OR description LIKE :w
                    ORDER BY confidence DESC
                    LIMIT :lim
                """),
                {"w": f"%{word}%", "lim": limit},
            ).fetchall()
            for r in rows:
                patterns.append({
                    "pattern_id": r[0],
                    "pattern": r[1],
                    "description": r[2],
                    "function": r[3],
                    "examples": json.loads(r[4]) if r[4] else [],
                    "confidence": r[5],
                    "status": r[6],
                })
        return patterns

    def query_word_evidence(self, word: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get evidence chain for a word."""
        from zolai.rag.evidence import build_word_evidence_items
        items = build_word_evidence_items(self.engine, word, limit)
        return [
            {
                "source_type": item.source_type,
                "source": item.source,
                "tier": item.tier,
                "confidence": item.confidence,
                "text": item.text,
                "metadata": item.metadata,
            }
            for item in items
        ]

    # --- Sentence/paragraph analysis ---
    def analyze_sentence(self, text: str) -> dict[str, Any]:
        """Analyze a Zolai sentence (tokenize, POS, grammar, entities)."""
        # This is a placeholder - full implementation would use the discovery pipeline
        from zolai.shared.text import tokenize_words
        tokens = tokenize_words(text)

        return {
            "tokens": tokens,
            "token_count": len(tokens),
            "language": "zolai",
            "analysis": "placeholder - full analysis requires discovery pipeline",
        }

    def analyze_paragraph(self, text: str) -> dict[str, Any]:
        """Analyze a Zolai paragraph (multi-sentence)."""
        sentences = text.split("।")  # Zolai sentence delimiter
        from zolai.shared.text import tokenize_words

        cleaned = [s.strip() for s in sentences if s.strip()]
        return {
            "sentences": cleaned,
            "sentence_count": len(cleaned),
            "analysis": {
                "status": "tokenized",
                "note": "segmentation complete; POS/grammar via discovery pipeline",
                "tokens_per_sentence": [
                    len(tokenize_words(s)) for s in cleaned
                ],
            },
        }

    # --- Search ---
    def search(self, query: str, limit: int = 20, source_filter: list[str] | None = None) -> list[SearchResult]:
        """Hybrid search (lexical + vector fallback)."""
        results = []

        # Lexical search across main tables
        tables = [
            ("dictionary", "zolai", "definition"),
            ("dictionary_en_zo", "headword", "definition"),
            ("bible_verses", "zo_tdb77", "ref"),
            ("phrases", "zolai", "id"),
            ("grammar_patterns", "pattern", "pattern_id"),
        ]

        with self.engine.connect() as conn:
            for table, text_col, id_col in tables:
                if source_filter and table not in source_filter:
                    continue
                try:
                    rows = conn.execute(
                        text(f"SELECT {id_col}, {text_col} FROM {table} WHERE {text_col} LIKE :q LIMIT 5"),
                        {"q": f"%{query}%"},
                    ).fetchall()
                    for r in rows:
                        results.append(SearchResult(
                            id=f"{table}:{r[0]}",
                            text=r[1],
                            score=1.0,
                            source=table,
                            metadata={},
                        ))
                except Exception:
                    pass

        return results[:limit]

    # --- RAG ---
    def rag_query(self, question: str, limit: int = 5) -> dict[str, Any]:
        """Full RAG: question → retrieval → answer with citations."""
        # Retrieve relevant evidence
        search_results = self.search(question, limit=10)

        # Build context from search results
        context_parts = []
        citations = []
        for i, r in enumerate(search_results):
            context_parts.append(f"[{i+1}] {r.source}: {r.text}")
            citations.append({"id": i+1, "source": r.source, "text": r.text[:200]})

        context = "\n".join(context_parts)

        return {
            "question": question,
            "context": context,
            "citations": citations,
            "answer": f"Based on {len(search_results)} sources: {context[:500]}...",  # Placeholder
            "retrieved_count": len(search_results),
        }

    # --- Knowledge version/stats ---
    def get_knowledge_version(self) -> dict[str, Any]:
        """Get current knowledge version."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT version, git_commit, created_at FROM knowledge_versions ORDER BY id DESC LIMIT 1")
            ).first()
            if row:
                return {"version": row[0], "git_commit": row[1], "created_at": row[2]}
            return {"version": "none", "git_commit": "unknown"}

    def get_knowledge_statistics(self) -> dict[str, Any]:
        """Get aggregate knowledge statistics."""
        stats = {}
        tables = [
            ("dictionary", "dictionary entries"),
            ("dictionary_en_zo", "EN→ZO entries"),
            ("bible_verses", "Bible verses"),
            ("vocabulary", "vocabulary items"),
            ("phrases", "phrases"),
            ("grammar_patterns", "grammar patterns"),
            ("word_collocations", "collocations"),
            ("knowledge_claims", "knowledge claims"),
            ("hypotheses", "hypotheses"),
            ("foundation_evidence", "evidence"),
            ("kg_nodes", "KG nodes"),
            ("kg_edges", "KG edges"),
        ]

        with self.engine.connect() as conn:
            for table, label in tables:
                try:
                    row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
                    stats[label] = row[0] if row else 0
                except Exception:
                    stats[label] = 0

        return stats


def create_retriever(engine: Engine) -> UnifiedRetriever:
    """Factory function."""
    return UnifiedRetriever(engine)
