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
from sqlalchemy.exc import OperationalError

from zolai.rag.vectors import encode_embedding
from zolai.shared.clock import stamp

log = logging.getLogger(__name__)

# Standard embedding model (384-dim)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIM = 384

# Per-document ceiling for chunked sources. A document above it is a generated
# aggregation (two master wordlists in the wiki: 12,200 and 4,595 chunks) and is
# skipped whole rather than embedded as an arbitrary prefix; the file stays
# reachable through the ``wiki_content_fts`` lexical pass.
MAX_CHUNKS_PER_DOC = 2048

# Ceiling for one chunk. The model truncates far below this (256 tokens), so a
# larger piece wastes text and can stall a build outright.
MAX_CHUNK_CHARS = 2000

# Text budget per embed call. A row-batch is a poor flush unit for chunked
# sources: 256 documents can expand to a quarter-million chunks, and one
# ``encode`` call that size runs for hours without committing anything.
MAX_TEXTS_PER_EMBED = 1024


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
    chunk: bool = False


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
    SourceSpec(
        "wiki",
        "wiki_content",
        "content",
        "id",
        "Zolai wiki (grammar, vocabulary, culture)",
        extra_cols=("title", "wiki_category"),
        chunk=True,
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

    elif source_type == "wiki":
        body = row.get(spec.text_col) or ""
        if not body:
            return None
        title = row.get("title") or ""
        category = row.get("wiki_category") or ""
        header = " — ".join(p for p in (title, category) if p)
        return f"{header}\n{body}" if header else body

    return None


def _split_oversized(piece: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Hard-split a boundary-less run so no single chunk can be huge.

    ``_chunk_text`` only breaks on ``.``; a generated wordlist with no periods
    becomes one multi-hundred-KB "sentence", which the tokenizer then grinds
    through (one 746k-char piece stalled a build for 20+ minutes).
    """
    if len(piece) <= limit:
        return [piece]
    parts = []
    start = 0
    while start < len(piece):
        end = min(start + limit, len(piece))
        if end < len(piece):
            window = piece[start:end]
            cut = max(window.rfind("\n"), window.rfind(" "), window.rfind(","))
            if cut > limit // 2:
                end = start + cut + 1
        parts.append(piece[start:end].strip())
        start = end
    return [p for p in parts if p]


def _chunk_text(text: str, max_tokens: int = 256) -> list[str]:
    """Simple chunking by sentence/word boundary, with a hard size ceiling."""
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
    bounded = []
    for chunk in chunks:
        bounded.extend(_split_oversized(chunk))
    return bounded


def _insert_vectors(conn, records: list[dict[str, Any]], attempts: int = 6) -> None:
    """``INSERT OR REPLACE`` one flush, backing off while another writer holds the lock.

    A 30 s ``busy_timeout`` is not enough on a shared DB: the learning cycle and
    the API commit while a build runs, and a lock held past the timeout used to
    discard the whole 64-chunk flush with only an ``errors`` entry. The records
    are regenerated from the same text on every attempt, so retrying is safe.
    """
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            conn.execute(
                text("""
                    INSERT OR REPLACE INTO knowledge_vectors
                    (id, text, metadata, embedding, source_type, source, version, imported_at)
                    VALUES (:id, :text, :meta, :emb, :stype, :src, 1, :now)
                """),
                records,
            )
            conn.commit()
            return
        except OperationalError as exc:
            conn.rollback()
            if "locked" not in str(exc).lower() or attempt == attempts:
                raise
            log.warning(
                "knowledge_vectors locked (attempt %d/%d); retrying in %.0fs",
                attempt, attempts, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


def _vector_id(
    table: str, source_id: str, chunk_idx: int, piece: str, *, chunked: bool
) -> str:
    """Stable 32-hex id covering the model and the **full** text.

    The model is part of the key so switching embedding models re-embeds
    instead of silently mixing vector spaces; the full text is hashed so an
    edit past the first 50 characters is not mistaken for an existing vector.
    """
    key = (
        f"{EMBEDDING_MODEL}:{table}:{source_id}:{chunk_idx}:{piece}"
        if chunked
        else f"{EMBEDDING_MODEL}:{table}:{source_id}:{piece}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _purge_foreign_vectors(conn, sources: set[str] | None = None) -> int:
    """Delete vectors embedded by a different model; they cannot be compared.

    The index is derived data, so dropping rows is safe — but only for the
    sources this run will rebuild. A ``--source grammar_patterns`` build that
    purges the whole table leaves every other source unembedded, because it
    does not touch their rows afterwards. ``sources`` is the same filter the
    build loop applies (table name or source_type); ``None`` means a full
    build, which re-embeds everything it purges.

    Rows without an ``embedding_model`` predate the field and are equally
    unusable next to current vectors, so they are purged with the rest.
    """
    where = "json_extract(metadata, '$.embedding_model') IS NOT :m"
    params: dict[str, object] = {"m": EMBEDDING_MODEL}
    if sources is not None:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(knowledge_vectors)"))
        }
        if "source_type" not in columns:
            return 0
        types = sorted(
            {
                spec.source_type
                for spec in SOURCES
                if spec.table in sources or spec.source_type in sources
            }
        )
        if not types:
            return 0
        placeholders = ", ".join(f":s{i}" for i in range(len(types)))
        where += f" AND source_type IN ({placeholders})"
        params.update({f"s{i}": value for i, value in enumerate(types)})
    try:
        count = int(
            conn.execute(
                text(f"SELECT COUNT(*) FROM knowledge_vectors WHERE {where}"), params
            ).scalar()
            or 0
        )
        if count:
            conn.execute(
                text(f"DELETE FROM knowledge_vectors WHERE {where}"), params
            )
            conn.commit()
    except OperationalError as exc:
        # No JSON1 support (or an older row shape): leave the index alone and
        # say so rather than failing a multi-hour build.
        conn.rollback()
        log.warning("Cannot check embedding-model identity (%s); not purging", exc)
        return 0
    return count


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
        if not dry_run:
            purged = _purge_foreign_vectors(conn, source_filter)
            if purged:
                summary["purged_foreign_model"] = purged
                log.warning(
                    "Purged %d vectors embedded by another model; re-embedding",
                    purged,
                )
        for spec in SOURCES:
            table = spec.table
            # Accept either the table name or the spec name (``--source wiki``
            # and ``--source wiki_content`` both mean the same source, matching
            # what the retriever accepts).
            if source_filter and table not in source_filter and spec.source_type not in source_filter:
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
            # Hard ceiling: ``--batch-size`` may not raise it, only lower it.
            flush_at = min(MAX_TEXTS_PER_EMBED, max(batch_size, 1))
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i+batch_size]

                # Build texts. Rows already in the index are skipped before
                # embedding, so an interrupted multi-hour build resumes where
                # it stopped instead of re-encoding everything.
                texts: list[str] = []
                ids: list[str] = []
                vec_ids: list[str] = []

                def flush() -> None:
                    """Embed and commit the pending pieces."""
                    nonlocal built_count, texts, ids, vec_ids
                    if not texts:
                        return
                    # ``batch_size`` must be passed to encode():
                    # sentence-transformers otherwise defaults to 32 no matter
                    # how many texts are handed over.
                    try:
                        embeddings = embedder.encode(
                            texts,
                            batch_size=batch_size,
                            show_progress_bar=False,
                            normalize_embeddings=True,
                        )
                    except Exception as e:
                        log.error("Embedding failed for %s batch %d: %s", table, i, e)
                        summary["errors"].append(f"{table} batch {i}: {e}")
                        texts, ids, vec_ids = [], [], []
                        return

                    records = []
                    imported_at = stamp()
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
                                        # Without the model identity a later
                                        # switch silently compares vectors from
                                        # two different spaces.
                                        "embedding_model": EMBEDDING_MODEL,
                                        "embedding_dim": VECTOR_DIM,
                                    }
                                ),
                                # base64 float32 instead of a JSON float array:
                                # ~2.3x smaller and decoded in milliseconds, which
                                # is what keeps the retriever's index load short.
                                "emb": encode_embedding(emb),
                                "stype": spec.source_type,
                                "src": table,
                                "now": imported_at,
                            }
                        )

                    if not dry_run:
                        try:
                            _insert_vectors(conn, records)
                        except Exception as e:
                            log.error("Insert failed for %s batch %d: %s", table, i, e)
                            summary["errors"].append(f"{table} insert: {e}")
                            texts, ids, vec_ids = [], [], []
                            return

                    # Stable sha256 ids make a re-run a no-op, so committing
                    # this slice early is always safe.
                    built_count += len(records)
                    texts, ids, vec_ids = [], [], []

                for row in batch:
                    row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(zip(select_cols, row))
                    content = _build_text_for_source(spec, row_dict)
                    if not content:
                        skipped_count += 1
                        continue
                    source_id = str(row_dict.get(spec.id_col, ''))
                    pieces = _chunk_text(content) if spec.chunk else [content]
                    if len(pieces) > MAX_CHUNKS_PER_DOC:
                        # A document this large is a generated aggregation (e.g. a
                        # 1.8M-word master wordlist). Embedding its first 2,048
                        # chunks would index an arbitrary prefix of a table that is
                        # already embedded, so skip it whole and say so.
                        log.warning(
                            "Skipping %s:%s — %d chunks exceeds MAX_CHUNKS_PER_DOC=%d"
                            " (generated aggregation; its source table is embedded)",
                            table, source_id, len(pieces), MAX_CHUNKS_PER_DOC,
                        )
                        skipped_count += 1
                        continue
                    pieces_skipped = 0
                    for chunk_idx, piece in enumerate(pieces):
                        vec_id = _vector_id(
                            table, source_id, chunk_idx, piece, chunked=spec.chunk
                        )
                        if vec_id in existing_ids:
                            pieces_skipped += 1
                            continue
                        texts.append(piece)
                        ids.append(source_id)
                        vec_ids.append(vec_id)
                        if len(texts) >= flush_at:
                            flush()
                    if pieces_skipped == len(pieces):
                        skipped_count += 1

                flush()

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
