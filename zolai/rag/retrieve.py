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
            except (ImportError, OSError):
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
        """Hybrid search (lexical + vector fallback) with FTS5 enhancement for definitional queries."""
        results = []

        # Enhanced table definitions for definitional queries
        # Each tuple: (table, text_col, id_col, extra_cols_for_context)
        # extra_cols_for_context: list of (col_name, field_name_in_metadata)
        tables = [
            ("dictionary", "zolai", "zolai", [("english_clean", "definition"), ("pos", "pos")]),
            ("dictionary_en_zo", "headword", "headword", [("translations_clean", "definition"), ("pos", "pos")]),
            ("bible_verses", "zo_tdb77", "ref", [("en_kJV", "context"), ("zo_tedim2010", "alt_zo")]),
            ("phrases", "zolai", "id", [("english", "definition"), ("category", "category")]),
            ("grammar_patterns", "pattern", "pattern_id", [("description", "description"), ("function", "function")]),
        ]

        with self.engine.connect() as conn:
            # 1. Exact match priority (for definitional queries like "pasian", "tapa", "vantung")
            for table, text_col, id_col, extra_cols in tables:
                if source_filter and table not in source_filter:
                    continue
                try:
                    # Build SELECT with extra columns
                    extra_select = ", ".join([f"{col}" for col, _ in extra_cols])
                    select_clause = f"{id_col}, {text_col}"
                    if extra_select:
                        select_clause += f", {extra_select}"

                    rows = conn.execute(
                        text(f"SELECT {select_clause} FROM {table} WHERE {text_col} = :q LIMIT 10"),
                        {"q": query},
                    ).fetchall()
                    for r in rows:
                        meta = {"match_type": "exact"}
                        # Add extra columns to metadata
                        for idx, (_, field_name) in enumerate(extra_cols):
                            meta[field_name] = r[2 + idx] if 2 + idx < len(r) else None
                        results.append(SearchResult(
                            id=f"{table}:{r[0]}",
                            text=r[1],
                            score=1.0,  # Exact match = highest score
                            source=table,
                            metadata=meta,
                        ))
                except Exception:
                    pass

            # 2. Prefix match (starts with query)
            for table, text_col, id_col, extra_cols in tables:
                if source_filter and table not in source_filter:
                    continue
                try:
                    extra_select = ", ".join([f"{col}" for col, _ in extra_cols])
                    select_clause = f"{id_col}, {text_col}"
                    if extra_select:
                        select_clause += f", {extra_select}"

                    rows = conn.execute(
                        text(f"SELECT {select_clause} FROM {table} WHERE {text_col} LIKE :q LIMIT 10"),
                        {"q": f"{query}%"},
                    ).fetchall()
                    for r in rows:
                        # Avoid duplicates from exact match
                        if not any(res.text == r[1] and res.source == table for res in results):
                            meta = {"match_type": "prefix"}
                            for idx, (_, field_name) in enumerate(extra_cols):
                                meta[field_name] = r[2 + idx] if 2 + idx < len(r) else None
                            results.append(SearchResult(
                                id=f"{table}:{r[0]}",
                                text=r[1],
                                score=0.9,  # Prefix match = high score
                                source=table,
                                metadata=meta,
                            ))
                except Exception:
                    pass

            # 3. Contains match (substring) — expanded for definitional queries
            for table, text_col, id_col, extra_cols in tables:
                if source_filter and table not in source_filter:
                    continue
                try:
                    extra_select = ", ".join([f"{col}" for col, _ in extra_cols])
                    select_clause = f"{id_col}, {text_col}"
                    if extra_select:
                        select_clause += f", {extra_select}"

                    rows = conn.execute(
                        text(f"SELECT {select_clause} FROM {table} WHERE {text_col} LIKE :q LIMIT 10"),
                        {"q": f"%{query}%"},
                    ).fetchall()
                    for r in rows:
                        # Avoid duplicates
                        if not any(res.text == r[1] and res.source == table for res in results):
                            meta = {"match_type": "contains"}
                            for idx, (_, field_name) in enumerate(extra_cols):
                                meta[field_name] = r[2 + idx] if 2 + idx < len(r) else None
                            results.append(SearchResult(
                                id=f"{table}:{r[0]}",
                                text=r[1],
                                score=0.7,  # Contains match = medium score
                                source=table,
                                metadata=meta,
                            ))
                except Exception:
                    pass

            # 4. FTS5 enhanced search for dictionary and phrases (if FTS5 tables exist)
            fts_tables = [
                ("dictionary_fts", "zolai", "rowid", [("english_clean", "definition")]),
                ("phrases_fts", "zolai", "rowid", [("english", "definition")]),
            ]
            for table, text_col, id_col, extra_cols in fts_tables:
                if source_filter and table not in source_filter:
                    continue
                try:
                    extra_select = ", ".join([f"{col}" for col, _ in extra_cols])
                    select_clause = f"{id_col}, {text_col}"
                    if extra_select:
                        select_clause += f", {extra_select}"

                    # FTS5 MATCH query
                    rows = conn.execute(
                        text(f"SELECT {select_clause} FROM {table} WHERE {text_col} MATCH :q LIMIT 10"),
                        {"q": query},
                    ).fetchall()
                    for r in rows:
                        if not any(res.text == r[1] and res.source == table for res in results):
                            meta = {"match_type": "fts5"}
                            for idx, (_, field_name) in enumerate(extra_cols):
                                meta[field_name] = r[2 + idx] if 2 + idx < len(r) else None
                            results.append(SearchResult(
                                id=f"{table}:{r[0]}",
                                text=r[1],
                                score=0.95,  # FTS5 = very high relevance
                                source=table,
                                metadata=meta,
                            ))
                except Exception:
                    pass  # FTS5 tables may not exist, that's OK

        # 5. Vector search fallback (if embedder available)
        embedder = self._get_embedder()
        if embedder is not None:
            try:
                vector_results = self._vector_search(query, limit, source_filter)
                for vr in vector_results:
                    # Avoid duplicates from lexical search
                    if not any(res.text == vr.text and res.source == vr.source for res in results):
                        results.append(vr)
            except Exception as e:
                log.debug(f"Vector search failed: {e}")

        # Sort by score descending and return top results
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _vector_search(self, query: str, limit: int, source_filter: list[str] | None = None) -> list[SearchResult]:
        """Vector similarity search using sentence-transformers."""
        embedder = self._get_embedder()
        if embedder is None:
            return []

        # Encode query for future use when vector index is available
        _ = embedder.encode(query, normalize_embeddings=True)

        # Tables with vector embeddings (if available)
        # Note: This requires a vectors table or embeddings stored in the DB
        # For now, we use the embedder to re-rank lexical results semantically
        # A full vector search would need a dedicated vector index (FAISS, pgvector, etc.)
        return []

    # --- Related words (ranked) ---
    def related_words(self, word: str, limit: int = 12) -> list[dict[str, Any]]:
        """Words related to `word`, for display *after* the exact match.

        Ordered by relationship strength: same word family first (shared
        prefix/extension), then same-POS neighbours. Never includes the
        query word itself.
        """
        w = (word or "").strip().lower()
        if not w:
            return []

        out: list[dict[str, Any]] = []
        seen: set[str] = {w}

        with self.engine.connect() as conn:
            # 1. Same word family: shared prefix (>=2 chars) or extension.
            try:
                # Fast prefix lookup on dictionary (uses the zolai index).
                rows = conn.execute(
                    text(
                        """
                        SELECT zolai, pos_canonical
                        FROM dictionary
                        WHERE zolai LIKE :prefix COLLATE NOCASE
                          AND zolai <> :exact
                        ORDER BY zolai ASC
                        LIMIT 50
                        """
                    ),
                    {"prefix": f"{w[:max(2, len(w) - 1)]}%", "exact": w},
                ).fetchall()
            except Exception:
                rows = []

            if rows:
                # Fast frequency lookup for these specific words only.
                freq_query = f"""
                    SELECT LOWER(normalized_form), frequency
                    FROM word_observation_stats
                    WHERE LOWER(normalized_form) IN (
                        {",".join([f":w{i}" for i in range(len(rows))])}
                    )
                """
                freq_params = {f"w{i}": str(r[0]).lower() for i, r in enumerate(rows)}
                try:
                    freq_rows = conn.execute(text(freq_query), freq_params).fetchall()
                    freq_map = {k: v for k, v in freq_rows}
                except Exception:
                    freq_map = {}

                for word_, pos in rows:
                    lw = word_.lower()
                    if lw in seen:
                        continue
                    seen.add(lw)
                    # Calculate shared prefix length against the query word.
                    shared = 0
                    for a, b in zip(w, lw):
                        if a != b:
                            break
                        shared += 1
                    out.append({
                        "word": word_,
                        "pos": pos,
                        "frequency": freq_map.get(lw, 0),
                        "relation": "prefix",
                        "shared_prefix": shared,
                    })

            # 2. Same-POS fallback (neighbours sharing the query's POS).
            if len(out) < limit:
                try:
                    pos_rows = conn.execute(
                        text("""
                            SELECT v.headword, v.pos_canonical, v.frequency
                            FROM vocabulary v
                            WHERE v.pos_canonical = (
                                SELECT pos_canonical FROM dictionary
                                WHERE zolai = :w COLLATE NOCASE LIMIT 1
                            )
                              AND v.headword <> :w
                              AND v.headword LIKE :prefix COLLATE NOCASE
                            ORDER BY v.frequency DESC
                            LIMIT :cap
                        """),
                        {"w": w, "prefix": f"{w}%", "cap": limit - len(out)},
                    ).fetchall()

                    for headword, pos, freq in pos_rows:
                        if headword.lower() in seen:
                            continue
                        seen.add(headword.lower())
                        out.append({
                            "word": headword,
                            "pos": pos,
                            "frequency": freq or 0,
                            "relation": "same_pos",
                            "shared_prefix": 0,
                        })
                except Exception:
                    pass

        out.sort(key=lambda r: (-r["shared_prefix"], -(r["frequency"] or 0), r["word"]))
        return out[:limit]

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
