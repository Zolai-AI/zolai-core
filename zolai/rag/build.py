"""Vector index builder for Phase 6 RAG.

Populates knowledge_vectors from all canonical sources.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, NamedTuple

from sqlalchemy import text

from zolai.rag.vectors import encode_embedding

log = logging.getLogger(__name__)

# Standard embedding model (384-dim)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIM = 384


class SourceSpec(NamedTuple):
    """One embeddable source, in the single schema every consumer reads.

    The old list mixed a 6-wide entry with 5-wide ones (the extra duplicated
    ``source_type`` shifted every later field), so the row loop and
    ``_build_text_for_source`` disagreed about which entry was the id column
    and which was the text. ``dictionary_en_zo`` was queried for a
    ``definition`` column that does not exist and was silently skipped, and
    ``word_observation_stats`` was queried for an ``id`` column it lacks.

    Named positional fields keep that drift from recurring. ``definition_col``
    is an optional parallel/translation column folded into the embedded text
    for dictionary sources; it stays ``None`` everywhere else.
    """

    source_type: str
    table: str
    text_col: str
    id_col: str
    label: str
    definition_col: str | None = None
    extra_cols: tuple[str, ...] = ()


# Sources to embed (in priority order)
SOURCES: list[SourceSpec] = [
    SourceSpec("dictionary", "dictionary", "zolai", "id", "Zolai→English dictionary", "english_clean"),
    SourceSpec("dictionary", "dictionary_en_zo", "headword", "id", "English→Zolai dictionary", "translations_clean"),
    SourceSpec("bible", "bible_verses", "zo_tdb77", "ref", "Tedim Bible (TDB77)"),
    SourceSpec("bible", "bible_verses", "zo_tedim2010", "ref", "Tedim Bible (2010)"),
    SourceSpec("phrases", "phrases", "zolai", "id", "Zolai phrases"),
    SourceSpec("grammar_patterns", "grammar_patterns", "pattern", "pattern_id", "Grammar patterns"),
    SourceSpec(
        "observations",
        "word_observation_stats",
        "normalized_form",
        "normalized_form",
        "Word observations",
        extra_cols=("frequency",),
    ),
    SourceSpec(
        "knowledge_claims",
        "knowledge_claims",
        "subject",
        "id",
        "Knowledge claims",
        extra_cols=("predicate", "object"),
    ),
]


def _get_embedder():
    """Lazy-load sentence transformer.

    A missing package and an unusable model are both non-fatal: the caller
    falls back to lexical search instead of failing the whole build.  An
    incomplete local HF cache fails *inside* the loader (the model's pooling
    module raises ``TypeError``), which an ``ImportError``-only guard would let
    escape.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        log.warning("sentence-transformers not installed; vector build disabled")
        return None
    try:
        return SentenceTransformer(EMBEDDING_MODEL)
    except Exception as exc:
        log.warning("sentence-transformer unusable; vector build disabled: %s", exc)
        return None


def _build_text_for_source(spec: SourceSpec, row: dict[str, Any]) -> str | None:
    """Build embeddable text from a row based on the source type."""
    source_type = spec.source_type

    if source_type == "dictionary":
        zolai = row.get(spec.text_col) or ""
        definition = (row.get(spec.definition_col) or "") if spec.definition_col else ""
        return f"{zolai}: {definition}" if zolai and definition else (zolai or None)

    elif source_type == "bible":
        text = row.get(spec.text_col) or ""
        ref = row.get(spec.id_col) or ""
        return f"{ref}: {text}" if text and ref else (text or None)

    elif source_type == "phrases":
        return row.get(spec.text_col) or None

    elif source_type == "grammar_patterns":
        pattern = row.get(spec.text_col) or ""
        pid = row.get(spec.id_col) or ""
        return f"{pid}: {pattern}" if pattern and pid else (pattern or None)

    elif source_type == "observations":
        word = row.get(spec.text_col) or ""
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
    batch_size: int = 256,
    force: bool = False,
) -> dict[str, Any]:
    """Build knowledge_vectors from canonical sources.

    Args:
        engine: SQLAlchemy engine.
        sources: List of source names to process (default: all SOURCES).
        limit: Max rows per source (for testing).
        dry_run: If True, don't write to DB.
        batch_size: Rows per embed call (passed through to ``encode``).
        force: Re-embed rows whose id is already present.  Without it an
            interrupted build resumes instead of starting over.

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

    # A plain connection with one commit per embed batch: the whole build is
    # tens of minutes, and a single transaction around it would hold the write
    # lock (and lose every completed batch on failure).
    with engine.connect() as conn:
        for spec in SOURCES:
            table = spec.table
            if source_filter and table not in source_filter:
                continue

            log.info("Building vectors for %s", table)

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

            # Only the columns the text builder reads are pulled (id + text +
            # optional parallel/definition column), so every source uses the
            # same row shape and no nonexistent column is ever queried.
            select_cols = [spec.id_col, spec.text_col]
            if spec.definition_col:
                select_cols.append(spec.definition_col)
            select_cols.extend(spec.extra_cols)
            # de-dup while keeping order (observations use one column for both id and text)
            select_clause = ", ".join(dict.fromkeys(select_cols))

            lim = f"LIMIT {limit}" if limit else ""
            try:
                rows = conn.execute(
                    text(f"SELECT {select_clause} FROM {table} {lim}"),
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
            existing_ids: set[str] = set()
            if not force and not dry_run:
                existing_ids = {
                    r[0]
                    for r in conn.execute(
                        text("SELECT id FROM knowledge_vectors WHERE source = :s"),
                        {"s": table},
                    ).fetchall()
                }

            # Process in batches
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i+batch_size]

                # Build texts. Rows already in the index are skipped before
                # embedding, so an interrupted multi-hour build resumes where
                # it stopped instead of re-encoding everything.
                texts = []
                ids = []
                vec_ids = []
                for row in batch:
                    row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(zip(select_cols, row))
                    content = _build_text_for_source(spec, row_dict)
                    if not content:
                        skipped_count += 1
                        continue
                    source_id = str(row_dict.get(spec.id_col, ''))
                    vec_id = hashlib.sha256(
                        f"{table}:{source_id}:{content[:50]}".encode()
                    ).hexdigest()[:32]
                    if vec_id in existing_ids:
                        skipped_count += 1
                        continue
                    texts.append(content)
                    ids.append(source_id)
                    vec_ids.append(vec_id)

                if not texts:
                    continue

                # Embed batch. ``batch_size`` must be passed to encode():
                # sentence-transformers otherwise defaults to 32 no matter how
                # many texts are handed over.
                try:
                    embeddings = embedder.encode(
                        texts,
                        batch_size=batch_size,
                        show_progress_bar=False,
                        normalize_embeddings=True,
                    )
                except Exception as e:
                    log.error("Embedding failed for batch: %s", e)
                    summary["errors"].append(f"{table} batch {i}: {e}")
                    continue

                # Write the whole embed batch with one INSERT OR REPLACE
                # (executemany) instead of one statement per row. Membership
                # keys are stable sha256 ids, so re-runs stay idempotent.
                records = []
                for idx, (content, emb) in enumerate(zip(texts, embeddings)):
                    records.append(
                        {
                            "id": vec_ids[idx],
                            "text": content,
                            # source_type/source sit in metadata too: the
                            # retrieval filters read metadata, not just columns.
                            "meta": json.dumps(
                                {
                                    "source_type": spec.source_type,
                                    "source": table,
                                    "source_table": table,
                                    "source_id": ids[idx],
                                }
                            ),
                            # base64 float32 instead of a JSON float array:
                            # ~2.3x smaller and decoded in milliseconds, which
                            # is what keeps the retriever's index load short.
                            "emb": encode_embedding(emb),
                            "stype": spec.source_type,
                            "src": table,
                        }
                    )

                if not dry_run:
                    try:
                        conn.execute(
                            text("""
                                INSERT OR REPLACE INTO knowledge_vectors
                                (id, text, metadata, embedding, source_type, source, version, imported_at)
                                VALUES (:id, :text, :meta, :emb, :stype, :src, 1, datetime('now'))
                            """),
                            records,
                        )
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        log.error("Insert failed for %s batch %d: %s", table, i, e)
                        summary["errors"].append(f"{table} insert: {e}")
                        continue

                built_count += len(records)

            summary["built"] += built_count
            summary["skipped"] += skipped_count
            # Two specs can share a table (bible_verses has a TDB77 and a 2010
            # text column); accumulate instead of letting the second overwrite.
            entry = summary["by_source"].setdefault(table, {"built": 0, "skipped": 0})
            entry["built"] = entry.get("built", 0) + built_count
            entry["skipped"] = entry.get("skipped", 0) + skipped_count
            log.info("  %s: built=%d, skipped=%d", table, built_count, skipped_count)

    summary["elapsed_seconds"] = round(time.time() - start, 2)
    return summary
