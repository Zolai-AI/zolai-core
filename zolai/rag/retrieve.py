"""Unified retriever for RAG (Phase 6 §36).

Multi-source retrieval over canonical knowledge.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.rag.evidence import (
    EvidencePack,
    build_word_evidence_items,
    evidence_to_pack,
    grammar_pattern_rows,
)
from zolai.rag.vectors import decode_embedding

log = logging.getLogger(__name__)


#: Query tokens that carry no lexical signal (frozen — retrieval must stay stable).
STOPWORDS = frozenset({
    "a", "an", "and", "are", "at", "by", "can", "do", "does", "for", "how", "i",
    "in", "is", "it", "me", "mean", "meaning", "my", "of", "on", "or", "please",
    "that", "the", "this", "to", "translate", "translation", "what", "when",
    "where", "which", "who", "why", "word", "you",
})

#: Upper bound on tokens fed to the per-table token-OR pass (bounds SQL size).
MAX_SEARCH_TOKENS = 6

#: Per-table LIMIT for every lexical pass (one LIMIT per table, not per token).
SEARCH_PASS_LIMIT = 10

#: FTS5 score mapping.  ``bm25()`` is negative and lower is better; ``-bm25``
#: is mapped monotonically into [floor, floor + span] so FTS hits outrank the
#: token-OR pass (0.6) and sit below whole-string exact matches (1.0).
FTS_SCORE_FLOOR = 0.75
FTS_SCORE_SPAN = 0.2

#: Vector fallback: minimum cosine for a hit, and the process-level cache.
#: The matrix is ~380 MB over the full corpus, so it is loaded lazily and only
#: when lexical results are thin; ``ZOLAI_VECTOR_SEARCH=0`` disables it.
VECTOR_MIN_SCORE = 0.35
_VECTOR_CACHE: dict[str, Any] | None = None
_VECTOR_CACHE_KEY: tuple[Any, ...] | None = None


def _vector_search_enabled() -> bool:
    return os.environ.get("ZOLAI_VECTOR_SEARCH", "1").lower() not in {"0", "false", "no"}

#: ``get_knowledge_statistics`` is twelve ``COUNT(*)`` scans of a 2.4 GB corpus:
#: 7.9s cold / 2.8s warm in production, which is why the Studio Data page sat on
#: "Loading…" with TOTAL ROWS 0 (explorer#35). Counts only move on import, so the
#: payload is served from a short TTL keyed by database URL — same idiom as
#: ``DatabaseManager.counts`` (core#24), and ``force=True`` still rescans.
STATS_CACHE_TTL_S = 15.0
_STATS_CACHE: dict[str, tuple[float, dict[str, int]]] = {}


def invalidate_statistics_cache() -> None:
    """Drop the cached counts (used by an import that must show up at once)."""
    _STATS_CACHE.clear()


#: (table, id_col, primary_col, matchable_cols, context_cols)
#:
#: ``primary_col`` drives prefix/contains scoring; ``matchable_cols`` (primary
#: first) drive exact matching and the token-OR pass, so English/parallel
#: columns such as ``dictionary.english_clean`` or ``bible_verses.en_kJV``
#: answer English queries. ``context_cols`` are (column, metadata_field) pairs
#: copied into result metadata.
SEARCH_TABLES: list[tuple[str, str, str, list[str], list[tuple[str, str]]]] = [
    (
        "dictionary",
        "zolai",
        "zolai",
        ["zolai", "english_clean", "english"],
        [("english_clean", "definition"), ("pos", "pos")],
    ),
    (
        "dictionary_en_zo",
        "headword",
        "headword",
        ["headword", "translations_clean"],
        [("translations_clean", "definition"), ("pos", "pos")],
    ),
    (
        "bible_verses",
        "ref",
        "zo_tdb77",
        ["zo_tdb77", "zo_tedim2010", "en_kJV"],
        [("en_kJV", "context"), ("zo_tedim2010", "alt_zo")],
    ),
    (
        "phrases",
        "id",
        "zolai",
        ["zolai", "english"],
        [("english", "definition"), ("frequency", "frequency")],
    ),
    (
        "grammar_patterns",
        "pattern_id",
        "pattern",
        ["pattern", "description"],
        [("description", "description"), ("function", "function")],
    ),
]


def tokenize_query(query: str) -> list[str]:
    """Lowercase alphanumeric tokens, stopwords dropped, order preserved.

    Empty result means "no usable token" — callers fall back to whole-string
    matching instead of issuing an empty SQL token list.
    """
    tokens = re.findall(r"[a-z0-9]+", (query or "").lower())
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out[:MAX_SEARCH_TOKENS]


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
        """Lazy-load sentence transformer for vector search.

        The local HF cache is tried first. Loading from the hub issues a HEAD
        for ``video_preprocessor_config.json`` even when every weight is already
        on disk, so an engine declared ``network=False`` reached out to
        huggingface.co purely to confirm a file it had -- and the offline
        contract tests recorded that as an outbound attempt.

        Network is touched only when nothing is cached yet, so a fresh machine
        still downloads the model instead of silently losing vector search.
        """
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                log.warning("sentence-transformers not available; vector search disabled")
                return None
            try:
                self._embedder = SentenceTransformer(
                    "all-MiniLM-L6-v2", local_files_only=True
                )
            except OSError:
                # Not cached: allow the one-time download.
                try:
                    self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
                except Exception as exc:  # ImportError / OSError / guard raising
                    log.warning(
                        "sentence-transformers unavailable; vector search disabled: %s", exc
                    )
            except Exception as exc:
                # Cached but unusable -- an incomplete download, a model/config
                # mismatch, a corrupt blob.  This raises something other than
                # OSError (an unfinished cache produced ``TypeError: Pooling
                # .__init__() missing ... embedding_dimension``), and it used to
                # escape this guard and fail the whole tool call.  A download
                # would not repair a local artifact and would break the
                # ``network=False`` contract, so degrade to lexical search and
                # say so instead of failing the caller.
                log.warning(
                    "cached sentence-transformer is unusable; vector search disabled: %s", exc
                )
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
        """Get grammar patterns involving a word.

        Same rows the entry's ``grammar_usage`` field is built from — one query
        in ``evidence.grammar_pattern_rows``, so the Patterns tab and the entry
        can no longer disagree about whether patterns exist (explorer#16).
        """
        return grammar_pattern_rows(self.engine, word, limit)

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
        """Hybrid search (lexical passes + vector fallback).

        Lexical passes run best-score-first and every result is deduped by
        ``(source, id)``, so the first hit for a row wins:

        1. ``exact`` (1.0) — primary column equals the query
        2. ``english_exact`` (1.0) — an English/parallel column equals the query
        3. ``prefix`` (0.9) — primary column starts with the query
        4. ``contains`` (0.7) — primary column contains the whole query
        5. ``token_or`` (0.6) — any token (stopwords dropped, max
           :data:`MAX_SEARCH_TOKENS`) matches any matchable column via LIKE
        6. ``fts5`` (0.75–0.95) — wiki content ranked by ``bm25()``

        A query with no usable tokens skips the token-OR pass and keeps the
        whole-string behaviour above.
        """
        results: list[SearchResult] = []
        seen: set[tuple[str, str]] = set()
        tokens = tokenize_query(query)

        with self.engine.connect() as conn:
            for table, id_col, primary_col, matchable_cols, context_cols in SEARCH_TABLES:
                if source_filter and table not in source_filter:
                    continue
                select_clause = self._select_clause(id_col, primary_col, context_cols)

                # Every strong pass below used to compare the **whole query
                # string** against the column, so any multi-token input ("what is
                # pasian", "pasian is god") failed all of them and every hit fell
                # through to the weak, unordered token_or pass — which returned
                # the head of an alphabetical index scan (abba, adonai, allah…)
                # instead of pasian. Each pass now runs for the whole string first
                # and then per meaningful token, with token matches scored just
                # below their whole-string equivalents so ranking is unchanged for
                # single-word queries.
                terms = [(query, 1.0)] + [(tok, 0.95) for tok in tokens]

                # 1. Exact match on the primary (identity) column.
                for term, decay in terms:
                    self._extend(
                        results,
                        seen,
                        table,
                        context_cols,
                        self._execute_like(
                            conn,
                            f"SELECT {select_clause} FROM {table} WHERE {primary_col} = :q "
                            f"ORDER BY {primary_col} LIMIT {SEARCH_PASS_LIMIT}",
                            {"q": term},
                        ),
                        match_type="exact",
                        score=1.0 * decay,
                    )

                # 2. Exact match on English / parallel columns.
                secondary_cols = [c for c in matchable_cols if c != primary_col]
                if secondary_cols:
                    for term, decay in terms:
                        predicate = " OR ".join(f"{col} = :q" for col in secondary_cols)
                        self._extend(
                            results,
                            seen,
                            table,
                            context_cols,
                            self._execute_like(
                                conn,
                                f"SELECT {select_clause} FROM {table} WHERE ({predicate}) "
                                f"ORDER BY {primary_col} LIMIT {SEARCH_PASS_LIMIT}",
                                {"q": term},
                            ),
                            match_type="english_exact",
                            score=1.0 * decay,
                        )

                # 3. Prefix match on the primary column.
                for term, decay in terms:
                    self._extend(
                        results,
                        seen,
                        table,
                        context_cols,
                        self._execute_like(
                            conn,
                            f"SELECT {select_clause} FROM {table} WHERE {primary_col} LIKE :q "
                            f"ORDER BY {primary_col} LIMIT {SEARCH_PASS_LIMIT}",
                            {"q": f"{term}%"},
                        ),
                        match_type="prefix",
                        score=0.9 * decay,
                    )

                # 4. Contains on the primary column.
                for term, decay in terms:
                    self._extend(
                        results,
                        seen,
                        table,
                        context_cols,
                        self._execute_like(
                            conn,
                            f"SELECT {select_clause} FROM {table} WHERE {primary_col} LIKE :q "
                            f"ORDER BY {primary_col} LIMIT {SEARCH_PASS_LIMIT}",
                            {"q": f"%{term}%"},
                        ),
                        match_type="contains",
                        score=0.7 * decay,
                    )

                # 5. Token-OR across every matchable column (skipped when the
                #    query tokenizes to nothing — never an empty token list).
                if tokens:
                    params = {f"t{i}": f"%{token}%" for i, token in enumerate(tokens)}
                    predicate = " OR ".join(
                        f"{col} LIKE :t{i}"
                        for i in range(len(tokens))
                        for col in matchable_cols
                    )
                    self._extend(
                        results,
                        seen,
                    table,
                        context_cols,
                        self._execute_like(
                            conn,
                            f"SELECT {select_clause} FROM {table} WHERE ({predicate}) "
                            f"ORDER BY {primary_col} LIMIT {SEARCH_PASS_LIMIT}",
                            params,
                        ),
                        match_type="token_or",
                        score=0.6,
                    )

            # 6. FTS5 enhanced search over the wiki content index.
            #    ``wiki_content_fts`` is the only FTS5 table present in the
            #    shipped database; the old ``dictionary_fts``/``phrases_fts``
            #    targets were never created, so this pass silently did nothing.
            #    Its schema is (title, content) over ``content=wiki_content``.
            #    Every row used to get a constant 0.95, and the final stable
            #    sort then kept rowid order — FTS relevance was thrown away.
            #    ``bm25()`` is negative (more negative = better); it is negated
            #    and scaled against the best hit in this pass, because its
            #    absolute magnitude depends on corpus size (tiny on small
            #    corpora) and a fixed transform would flatten every score.
            if not source_filter or {"wiki", "wiki_content"} & set(source_filter):
                try:
                    rows = conn.execute(
                        text(
                            "SELECT rowid, title, content, bm25(wiki_content_fts) AS rank "
                            "FROM wiki_content_fts "
                            f"WHERE wiki_content_fts MATCH :q ORDER BY rank LIMIT {SEARCH_PASS_LIMIT}"
                        ),
                        {"q": query},
                    ).fetchall()
                    relevance = [max(-float(r[3] or 0.0), 0.0) for r in rows]
                    best = max(relevance, default=0.0) or 1.0
                    for r, rel in zip(rows, relevance):
                        if any(res.id == f"wiki:{r[0]}" for res in results):
                            continue
                        score = FTS_SCORE_FLOOR + FTS_SCORE_SPAN * (rel / best)
                        results.append(SearchResult(
                            id=f"wiki:{r[0]}",
                            text=r[1] or "",
                            score=round(score, 4),
                            source="wiki",
                            metadata={"match_type": "fts5", "content": r[2]},
                        ))
                except Exception:
                    pass  # FTS5 table may not exist, that's OK

        # 7. Vector fallback — when lexical evidence is weak (no exact/prefix/
        #    FTS hit) or the query is sentence-like, where per-token matches
        #    fill the list without answering the question. The row-count guard
        #    applies only to single words: a sentence query is exactly the case
        #    the semantic pass exists for, so it runs even when lexical already
        #    filled the list (the merge below re-sorts and trims).
        best_lexical = max((r.score for r in results), default=0.0)
        sentence_like = len(tokens) >= 2
        if sentence_like or (len(results) < limit and best_lexical < 0.9):
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

    @staticmethod
    def _select_clause(id_col: str, primary_col: str, context_cols: list[tuple[str, str]]) -> str:
        """Build the SELECT column list shared by every lexical pass."""
        cols = [id_col, primary_col] + [col for col, _ in context_cols]
        return ", ".join(cols)

    @staticmethod
    def _execute_like(conn: Any, sql: str, params: dict[str, Any]) -> list[Any]:
        """Run one lexical pass, tolerating tables missing from a given schema."""
        try:
            return conn.execute(text(sql), params).fetchall()
        except Exception:
            return []

    @staticmethod
    def _extend(
        results: list[SearchResult],
        seen: set[tuple[str, str]],
        table: str,
        context_cols: list[tuple[str, str]],
        rows: list[Any],
        match_type: str,
        score: float,
    ) -> None:
        """Append rows as results, deduping by (source, id) — first hit wins."""
        for row in rows:
            key = (table, str(row[0]))
            if key in seen:
                continue
            seen.add(key)
            meta: dict[str, Any] = {"match_type": match_type}
            for idx, (_, field_name) in enumerate(context_cols):
                meta[field_name] = row[2 + idx] if 2 + idx < len(row) else None
            results.append(SearchResult(
                id=f"{table}:{row[0]}",
                text=row[1] or "",
                score=score,
                source=table,
                metadata=meta,
            ))

    def _load_vector_index(self) -> dict[str, Any] | None:
        """Load ``knowledge_vectors`` into a normalized float32 matrix, cached.

        The cache key is ``(db_url, row_count, last_import)``, so a rebuild —
        or a different database in the same process — is picked up on the next
        call without reloading on every search.
        """
        global _VECTOR_CACHE, _VECTOR_CACHE_KEY

        import numpy as np

        with self.engine.connect() as conn:
            key_row = conn.execute(
                text("SELECT COUNT(*), MAX(imported_at) FROM knowledge_vectors")
            ).first()
            # The DB URL is part of the key: tests use several databases and a
            # count-only key would hand one process another database's matrix.
            key = (str(self.engine.url), key_row[0], key_row[1]) if key_row else ("", 0, None)
            if not key[1]:
                return None
            if _VECTOR_CACHE is not None and _VECTOR_CACHE_KEY == key:
                return _VECTOR_CACHE

            # Stream the rows and fill one preallocated matrix. ``fetchall()``
            # held every JSON/base64 string (~0.5 GB at the built corpus) next
            # to the matrix it was about to become; iterating frees each row as
            # it is decoded, and preallocating avoids a second full-size array.
            result = conn.execute(
                text(
                    "SELECT id, text, metadata, embedding, source "
                    "FROM knowledge_vectors WHERE embedding IS NOT NULL"
                )
            )
            expected = int(key_row[0] or 0)
            matrix = None
            count = 0
            unreadable = 0

            ids: list[str] = []
            texts: list[str] = []
            sources: list[str] = []
            metas: list[dict[str, Any]] = []

            for vid, txt, meta_json, emb_json, src in result:
                emb = decode_embedding(emb_json)
                if emb is None or not emb.size:
                    unreadable += 1
                    continue
                if matrix is None:
                    matrix = np.empty((expected, emb.shape[0]), dtype=np.float32)
                elif emb.shape[0] != matrix.shape[1]:
                    unreadable += 1
                    continue
                if count == matrix.shape[0]:
                    matrix = np.vstack(
                        [matrix, np.empty((1024, matrix.shape[1]), dtype=np.float32)]
                    )
                matrix[count] = emb
                count += 1
                ids.append(vid)
                texts.append(txt or "")
                sources.append(src or "")
                try:
                    metas.append(json.loads(meta_json or "{}"))
                except (TypeError, ValueError):
                    metas.append({})

        if not count or matrix is None:
            return None
        if unreadable:
            log.warning("vector index: skipped %d unreadable embeddings", unreadable)
        _VECTOR_CACHE = {
            "ids": ids,
            "texts": texts,
            "sources": sources,
            "metas": metas,
            "matrix": matrix[:count],
        }
        _VECTOR_CACHE_KEY = key
        log.info("vector index loaded: %d rows", count)
        return _VECTOR_CACHE

    def _vector_search(self, query: str, limit: int, source_filter: list[str] | None = None) -> list[SearchResult]:
        """Semantic search over ``knowledge_vectors`` (normalized cosine)."""
        if not _vector_search_enabled():
            return []
        embedder = self._get_embedder()
        if embedder is None:
            return []

        index = self._load_vector_index()
        if not index:
            return []

        import numpy as np

        query_vec = np.asarray(
            embedder.encode(query, normalize_embeddings=True), dtype=np.float32
        )
        matrix = index["matrix"]
        if matrix.shape[1] != query_vec.shape[0]:
            log.warning(
                "vector dimension mismatch: index=%d query=%d",
                matrix.shape[1],
                query_vec.shape[0],
            )
            return []

        scores = matrix @ query_vec
        allowed = set(source_filter) if source_filter else None
        if allowed is not None:
            if "wiki" in allowed:
                allowed.add("wiki_content")
            mask = np.fromiter(
                (s in allowed for s in index["sources"]), dtype=bool, count=len(scores)
            )
            scores = np.where(mask, scores, -1.0)

        take = min(len(scores), max(limit * 5, limit))
        top = np.argpartition(-scores, take - 1)[:take]
        top = top[np.argsort(-scores[top])]

        results: list[SearchResult] = []
        for pos in top:
            score = float(scores[pos])
            if score < VECTOR_MIN_SCORE:
                break
            results.append(
                SearchResult(
                    id=str(index["ids"][pos]),
                    text=index["texts"][pos],
                    score=round(score, 4),
                    source=index["sources"][pos],
                    metadata={**index["metas"][pos], "match_type": "vector"},
                )
            )
            if len(results) >= limit:
                break
        return results

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
    @staticmethod
    def _evidence_line(index: int, result: SearchResult) -> str:
        """One grounded evidence line, carrying the row's *content*.

        The old context was ``[1] dictionary: pasian`` — the bare word and
        nothing else, so a model reading it had no proposition to work from.
        Every search table publishes context columns (``definition``, ``pos``,
        ``context``); those are the answerable text.

        For Bible verses, we format with Zolai + English side by side for clarity.
        """
        source = result.source

        # Special handling for Bible verses - show Zolai + English side by side
        if source == "bible_verses":
            zo_text = result.text
            en_text = result.metadata.get("context", "")  # en_kJV
            alt_zo = result.metadata.get("alt_zo", "")   # zo_tedim2010
            ref = result.metadata.get("ref", result.id)

            # Format: [n] REF - Zo text | EN translation
            parts = [f"[{index}] {ref}"]
            if zo_text:
                parts.append(f"Zo: {zo_text[:300]}")
            if en_text and en_text != zo_text:
                parts.append(f"EN: {en_text[:300]}")
            if alt_zo and alt_zo != zo_text:
                parts.append(f"Alt: {alt_zo[:200]}")
            return " | ".join(parts)

        # Special handling for dictionary entries
        if source in ("dictionary", "dictionary_en_zo"):
            parts = [f"[{index}] {source}: {result.text}"]
            details = []
            for field in ("definition", "pos", "alt_zo"):
                value = result.metadata.get(field)
                if value is None:
                    continue
                text_value = str(value).strip()
                if text_value and text_value.lower() not in {"unknown", "n", "none", ""}:
                    details.append(f"{field}={text_value}")
            if details:
                parts.append(f" ({'; '.join(details)})")
            return "".join(parts)

        # Default handling for other sources
        parts = [f"[{index}] {result.source}: {result.text}"]
        details = []
        for field in ("definition", "context", "pos", "alt_zo", "frequency"):
            value = result.metadata.get(field)
            if value is None:
                continue
            text_value = str(value).strip()
            if text_value and text_value.lower() not in {"unknown", "n", "none", ""}:
                details.append(f"{field}={text_value}")
        if details:
            parts.append(f" ({'; '.join(details)})")
        return "".join(parts)

    def rag_query(
        self,
        question: str,
        limit: int = 5,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Full RAG: question → retrieval → a **model-written** answer.

        The answer used to be a hardcoded template::

            f"Based on {len(search_results)} sources: {context[:500]}..."

        — no model was ever called, so ``/rag`` could not answer anything. It
        now resolves the deployment's configured provider through the same
        :func:`~zolai.agent.synthesis.make_chat_fn` the assistant uses, so there
        is one provider-resolution path rather than two that can disagree.

        When no provider resolves (or the call fails), the response says so via
        ``retrieval_only: true`` and returns the retrieved evidence instead of a
        fabricated answer. Callers can therefore never mistake the two.
        """
        search_results = self.search(question, limit=10)

        context_parts: list[str] = []
        citations: list[dict[str, Any]] = []
        for i, r in enumerate(search_results):
            context_parts.append(self._evidence_line(i + 1, r))
            citation: dict[str, Any] = {
                "id": i + 1,
                "source": r.source,
                "text": r.text[:200],
            }
            definition = r.metadata.get("definition") or r.metadata.get("context")
            if definition:
                citation["definition"] = str(definition)[:400]
            citations.append(citation)

        context = "\n".join(context_parts)

        base: dict[str, Any] = {
            "question": question,
            "context": context,
            "citations": citations,
            "retrieved_count": len(search_results),
        }

        # Imported lazily, and *inside* the try: the retriever is a low-level
        # dependency of the agent tooling so a module-level import risks a
        # cycle, and an import failure must degrade to the evidence fallback
        # rather than 500 the endpoint.
        try:
            from ..agent.synthesis import make_chat_fn

            chat, meta = make_chat_fn(assistant="public", provider=provider, model=model)
        except Exception as exc:  # no provider configured / row disabled
            log.debug("rag: no provider resolved: %s", exc)
            return {
                **base,
                "answer": self._fallback_answer(question, search_results, "no_active_provider"),
                "generated": False,
                "retrieval_only": True,
                "provider": "",
                "model": "",
                "provider_error": str(exc)[:200],
            }

        system = (
            "You answer questions about the Zolai (Zomi) language using ONLY the "
            "evidence given below. The evidence is retrieved corpus rows, each "
            "tagged [n] with its source table. Cite the bracketed numbers you "
            "rely on. If the evidence does not answer the question, say so "
            "plainly and name what is missing — never fill the gap with "
            "general knowledge or invented translations."
        )
        user = (
            f"Evidence:\n{context or '(no evidence retrieved)'}\n\n"
            f"Question: {question}\n\nAnswer using the evidence above."
        )

        try:
            reply = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                None,
            )
        except Exception as exc:
            log.warning("rag: provider call failed: %s", exc)
            return {
                **base,
                "answer": self._fallback_answer(question, search_results, "call_failed"),
                "generated": False,
                "retrieval_only": True,
                "provider": str(meta.get("catalog_id") or ""),
                "model": str(meta.get("model") or ""),
                "provider_error": str(exc)[:200],
            }

        # ChatFn returns {"text", "tool_calls", "latency_ms"} - not "content".
        # Accepting both keeps this resilient if the adapter ever normalises.
        answer = ""
        try:
            payload = reply or {}
            raw = payload.get("text") or payload.get("content") or ""
            answer = str(raw).strip()
        except Exception:
            answer = ""

        if not answer:
            return {
                **base,
                "answer": self._fallback_answer(question, search_results, "empty_reply"),
                "generated": False,
                "retrieval_only": True,
                "provider": str(meta.get("catalog_id") or ""),
                "model": str(meta.get("model") or ""),
                "provider_error": "empty_reply",
            }

        return {
            **base,
            "answer": answer,
            "generated": True,
            "retrieval_only": False,
            "provider": str(meta.get("catalog_id") or ""),
            "model": str(meta.get("model") or ""),
        }

    @staticmethod
    def _fallback_answer(
        question: str,
        results: list[SearchResult],
        reason: str = "no_active_provider",
    ) -> str:
        """Honest no-model answer: the evidence, not a synthesised claim.

        ``reason`` distinguishes "nothing was configured" from "the call was
        made and failed" — collapsing the two into "no active LLM provider"
        would send operators hunting for a missing key while the real problem
        was a timeout or an empty completion.
        """
        if not results:
            return (
                "No evidence was retrieved for this question, so there is "
                "nothing to answer from. The corpus has no row matching it."
            )
        if reason == "empty_reply":
            lead = "The model returned an empty answer"
        elif reason == "no_active_provider":
            lead = "No active LLM provider"
        else:
            lead = "The model call failed"
        lines = [
            f"{lead}, so this is the retrieved evidence for "
            f"{question!r} — not a written answer:"
        ]
        for i, r in enumerate(results[:8], 1):
            detail = ""
            definition = r.metadata.get("definition") or r.metadata.get("context")
            if definition:
                detail = f" — {str(definition).strip()[:160]}"
            lines.append(f"[{i}] {r.source}: {r.text}{detail}")
        return "\n".join(lines)

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

    def get_knowledge_statistics(self, *, force: bool = False) -> dict[str, Any]:
        """Aggregate knowledge statistics (TTL-cached — see ``STATS_CACHE_TTL_S``)."""
        key = str(self.engine.url)
        now = time.monotonic()
        cached = _STATS_CACHE.get(key)
        if not force and cached is not None and now - cached[0] < STATS_CACHE_TTL_S:
            return dict(cached[1])

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

        _STATS_CACHE[key] = (now, dict(stats))
        return stats


def create_retriever(engine: Engine) -> UnifiedRetriever:
    """Factory function."""
    return UnifiedRetriever(engine)
