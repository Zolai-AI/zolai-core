"""Vector index builder for Phase 6 RAG.

Populates knowledge_vectors from all canonical sources.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Standard embedding model (384-dim)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIM = 384

# Sources to embed (in priority order)
SOURCES = [
    ("dictionary", "dictionary", "zolai", "headword", "definition", "Zolai→English dictionary"),
    ("dictionary", "dictionary_en_zo", "headword", "definition", "English→Zolai dictionary"),
    ("bible", "bible_verses", "zo_tdb77", "ref", "Tedim Bible (TDB77)"),
    ("bible", "bible_verses", "zo_tedim2010", "ref", "Tedim Bible (2010)"),
    ("phrases", "phrases", "zolai", "id", "Zolai phrases"),
    ("grammar_patterns", "grammar_patterns", "pattern", "pattern_id", "Grammar patterns"),
    ("observations", "word_observation_stats", "normalized_form", "id", "Word observations"),
    ("knowledge_claims", "knowledge_claims", "subject", "id", "Knowledge claims"),
]


def _get_embedder():
    """Lazy-load sentence transformer."""
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBEDDING_MODEL)
    except ImportError:
        log.warning("sentence-transformers not installed; vector build disabled")
        return None


def _build_text_for_source(table: str, row: dict[str, Any], source_config: tuple) -> str | None:
    """Build embeddable text from a row based on source type."""
    source_type = source_config[0]
    
    if source_type == "dictionary":
        zolai = row.get(source_config[2], "")
        definition = row.get(source_config[3], "")
        return f"{zolai}: {definition}" if zolai and definition else zolai
    
    elif source_type == "bible":
        text = row.get(source_config[2], "")
        ref = row.get(source_config[3], "")
        return f"{ref}: {text}" if text and ref else text
    
    elif source_type == "phrases":
        zolai = row.get(source_config[2], "")
        return zolai
    
    elif source_type == "grammar_patterns":
        pattern = row.get(source_config[2], "")
        pid = row.get(source_config[3], "")
        return f"{pid}: {pattern}" if pattern and pid else pattern
    
    elif source_type == "observations":
        word = row.get(source_config[2], "")
        freq = row.get("frequency", 0)
        return f"{word} (freq: {freq})" if word else None
    
    elif source_type == "knowledge_claims":
        subject = row.get("subject", "")
        predicate = row.get("predicate", "")
        obj = row.get("object", "")
        return f"{subject} {predicate} {obj}".strip()
    
    return None


def _chunk_text(text: str, max_tokens: int = 256) -> list[str]:
    """Simple chunking by sentence/word boundary."""
    if len(text) <= max_tokens * 4:  # Rough char estimate
        return [text]
    # Split by sentence-ish boundaries
    chunks = []
    current = ""
    for sent in text.replace("।", ".").replace("?", ".").replace("!", ".").split("."):
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) > max_tokens * 4:
            if current:
                chunks.append(current.strip())
            current = sent
        else:
            current += " " + sent if current else sent
    if current:
        chunks.append(current.strip())
    return chunks


def build_knowledge_vectors(
    engine,
    sources: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Build knowledge_vectors from canonical sources.

    Args:
        engine: SQLAlchemy engine.
        sources: List of source names to process (default: all SOURCES).
        limit: Max rows per source (for testing).
        dry_run: If True, don't write to DB.
        batch_size: Batch size for embedding.

    Returns:
        Summary dict with counts per source.
    """
    embedder = _get_embedder()
    if not embedder:
        return {"error": "sentence-transformers not available", "built": 0}

    source_filter = set(sources) if sources else None
    
    summary = {
        "built": 0,
        "skipped": 0,
        "errors": [],
        "by_source": {},
        "elapsed_seconds": 0.0,
    }
    start = time.time()

    with engine.begin() as conn:
        for source_config in SOURCES:
            source_name = source_config[1]  # table name
            if source_filter and source_name not in source_filter:
                continue

            table = source_config[1]
            pk_col = source_config[2]  # text column to embed
            id_col = source_config[3]  # ID column

            log.info("Building vectors for %s", source_name)

            # Check if table exists
            try:
                existing = conn.execute(
                    text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
                    {"t": table},
                ).first()
                if not existing:
                    log.warning("Table %s does not exist, skipping", table)
                    summary["by_source"][table] = {"status": "missing", "built": 0}
                    continue
            except Exception as e:
                log.warning("Failed to check table %s: %s", table, e)
                continue

            # Get rows to process
            lim = f"LIMIT {limit}" if limit else ""
            try:
                rows = conn.execute(
                    text(f"SELECT {id_col}, {pk_col} FROM {table} {lim}"),
                ).fetchall()
            except Exception as e:
                log.warning("Failed to query %s: %s", table, e)
                summary["errors"].append(f"{table}: {e}")
                continue

            if not rows:
                summary["by_source"][table] = {"status": "empty", "built": 0}
                continue

            built_count = 0
            skipped_count = 0

            # Process in batches
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i+batch_size]
                
                # Build texts
                texts = []
                ids = []
                for row in batch:
                    row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(zip([id_col, pk_col], row))
                    text = _build_text_for_source(table, row_dict, source_config)
                    if text:
                        texts.append(text)
                        ids.append(str(row_dict.get(id_col, '')))
                    else:
                        skipped_count += 1

                if not texts:
                    continue

                # Embed batch
                try:
                    embeddings = embedder.encode(texts, show_progress_bar=False, normalize_embeddings=True)
                except Exception as e:
                    log.error("Embedding failed for batch: %s", e)
                    summary["errors"].append(f"{table} batch {i}: {e}")
                    continue

                # Write to knowledge_vectors
                for idx, (text, emb) in enumerate(zip(texts, embeddings)):
                    vec_id = hashlib.sha256(f"{table}:{ids[idx]}:{text[:50]}".encode()).hexdigest()[:32]
                    
                    metadata = {
                        "source_table": table,
                        "source_id": ids[idx],
                        "source_config": source_config[0],
                    }

                    if not dry_run:
                        try:
                            conn.execute(
                                text("""
                                    INSERT OR REPLACE INTO knowledge_vectors 
                                    (id, text, metadata, embedding, source_type, source, version, imported_at)
                                    VALUES (:id, :text, :meta, :emb, :stype, :src, 1, datetime('now'))
                                """),
                                {
                                    "id": vec_id,
                                    "text": text,
                                    "meta": json.dumps({"source_table": table, "source_id": ids[idx]}),
                                    "emb": json.dumps(emb.tolist()),
                                    "stype": source_config[0],
                                    "src": table,
                                },
                            )
                        except Exception as e:
                            log.error("Insert failed: %s", e)
                            summary["errors"].append(f"{table} insert: {e}")
                            continue
                    
                    built_count += 1

            summary["built"] += built_count
            summary["skipped"] += skipped_count
            summary["by_source"][table] = {"built": built_count, "skipped": skipped_count}
            log.info("  %s: built=%d, skipped=%d", table, built_count, skipped_count)

    summary["elapsed_seconds"] = round(time.time() - start, 2)
    return summary
