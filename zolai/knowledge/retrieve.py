"""Retrieval over the Zolai knowledge vector index.

Primary path is an in-memory numpy cosine scan. Vectors are stored in the
``knowledge_vectors`` table (DB-first) and hydrated into an :class:`Index` for
fast offline cosine lookup — no external vector DB, no runtime JSONL reads.

Yields top-k chunks with their source metadata so callers can inject them as
RAG context for existing AIs.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import config
from ..data.repositories import get_engine
from ..data.repositories.extended import KnowledgeVectorRepository
from ..rag.vectors import decode_embedding

DEFAULT_INDEX = config.paths.data_knowledge / "knowledge_vectors.jsonl"

# HuggingFace dataset for auto-download
HF_REPO = "peterpausianlian/zolai-knowledge-vectors"
HF_FILENAME = "knowledge_vectors.jsonl"
HF_REPO_TYPE = "dataset"
#: Pinned so two machines cannot end up with different vector files under the
#: same name. Override with ``ZOLAI_KNOWLEDGE_INDEX_REVISION`` (tag or SHA).
HF_REVISION = os.environ.get("ZOLAI_KNOWLEDGE_INDEX_REVISION", "main")


@dataclass
class Index:
    texts: list[str] = field(default_factory=list)
    metas: list[dict] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    vectors: np.ndarray | None = None


#: Process-level cache: hydrating the full table costs ~1 min and ~380 MB.
#: The key is ``(db_url, row_count, last_import)`` so a rebuild or a different
#: database invalidates it.
_INDEX_CACHE: tuple[tuple[Any, ...], "Index"] | None = None


def _index_cache_key() -> tuple[Any, ...]:
    from sqlalchemy import text as sql_text

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text("SELECT COUNT(*), MAX(imported_at) FROM knowledge_vectors")
        ).first()
    return (str(engine.url), int(row[0]), row[1]) if row else ("", 0, None)


def _vector_table_exists() -> bool:
    """True when ``knowledge_vectors`` exists in the canonical DB.

    A database that never ran the RAG build (fresh checkout, test fixture,
    partially migrated deployment) has no such table at all. Reading it then
    raised ``OperationalError: no such table: knowledge_vectors`` straight out
    of ``load_index`` — a 500 on ``POST /knowledge/search`` (core#21). The
    caller only needs to know there is no index, so the absence is checked
    explicitly instead of surfacing as a driver error.
    """
    from sqlalchemy import inspect as sa_inspect

    return sa_inspect(get_engine()).has_table("knowledge_vectors")


def _ingest_jsonl_to_db(path: Path) -> int:
    """One-time ingest of a knowledge_vectors.jsonl file into the table."""
    repo = KnowledgeVectorRepository(get_engine())
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not records:
        return 0
    return repo.upsert_batch(records, batch_id="hf-knowledge-index")


def _ensure_index_file(path: Path) -> Path:
    """Download knowledge_vectors.jsonl from HuggingFace if missing or empty."""
    if path.exists() and path.stat().st_size > 1024:
        return path

    print(f"knowledge index not found at {path} — downloading from HuggingFace...")
    try:
        from huggingface_hub import hf_hub_download

        token = os.environ.get("HF_TOKEN")
        downloaded = hf_hub_download(
            repo_id=HF_REPO,
            filename=HF_FILENAME,
            repo_type=HF_REPO_TYPE,
            revision=HF_REVISION,
            token=token,
            local_dir=str(path.parent),
        )
        src = Path(downloaded)
        if src != path:
            shutil.move(str(src), str(path))
        print(f"Downloaded {path.stat().st_size / 1024 / 1024:.1f} MB to {path}")
    except Exception as e:
        print(f"WARNING: Failed to download knowledge index from HF: {e}")
        print(f"  Repo: {HF_REPO}")
    return path


def load_index(path: Path = DEFAULT_INDEX) -> Index:
    """Load the knowledge vector index from the ``knowledge_vectors`` table.

    If the table is empty and a local ``knowledge_vectors.jsonl`` exists (or can
    be downloaded), it is first ingested into the table (build step). Missing or
    empty data returns an empty :class:`Index`.

    The hydrated index is cached per process: once the corpus is populated the
    table holds hundreds of thousands of rows, and re-reading it on every call
    turned each retrieval into a minute of parsing.
    """
    global _INDEX_CACHE

    if not _vector_table_exists():
        # No table -> no index. Deliberately *before* the JSONL/HF path: that
        # path ingests into ``knowledge_vectors``, so it cannot succeed either,
        # and it would spend the request on a network download first.
        print("retrieve: no knowledge_vectors table in this DB — knowledge index unavailable")
        return Index()

    repo = KnowledgeVectorRepository(get_engine())
    key = _index_cache_key()
    if key[1] and _INDEX_CACHE is not None and _INDEX_CACHE[0] == key:
        return _INDEX_CACHE[1]

    idx = Index()
    records = repo.all_rows()
    if not records:
        # One-time ingest from a local/downloaded JSONL bundle, if present.
        path = _ensure_index_file(path)
        if path.exists() and path.stat().st_size >= 1024:
            n = _ingest_jsonl_to_db(path)
            if n:
                records = repo.all_rows()
                key = _index_cache_key()

    if not records:
        return idx

    # Decode alongside the text/metadata so a row with an unreadable embedding
    # is dropped from all four lists together. Two separate passes would leave
    # ids/texts longer than the matrix and shift every later hit. The matrix is
    # preallocated and each ``embedding`` popped so the encoded strings do not
    # stay resident next to the float arrays.
    matrix = None
    count = 0
    for r in records:
        emb = decode_embedding(r.pop("embedding", None))
        if emb is None or not emb.size:
            continue
        if matrix is None:
            matrix = np.empty((len(records), emb.shape[0]), dtype=np.float32)
        elif emb.shape[0] != matrix.shape[1]:
            continue
        if count == matrix.shape[0]:
            matrix = np.vstack(
                [matrix, np.empty((1024, matrix.shape[1]), dtype=np.float32)]
            )
        matrix[count] = emb
        count += 1
        try:
            meta = json.loads(r.get("metadata") or "{}")
        except json.JSONDecodeError:
            meta = {}
        idx.ids.append(r.get("id", "") or "")
        idx.texts.append(r.get("text", "") or "")
        idx.metas.append(meta)
    if matrix is not None and count:
        idx.vectors = matrix[:count]
    if key[1]:
        _INDEX_CACHE = (key, idx)
    return idx


def _embed_query(model, query: str) -> np.ndarray:
    vec = model.encode([query], normalize_embeddings=True)[0]
    return np.asarray(vec, dtype=np.float32)


def retrieve(
    query: str,
    index: Index | None = None,
    *,
    top_k: int = 5,
    threshold: float = 0.85,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    source_type: str | None = None,
    source: str | None = None,
) -> list[dict]:
    """Return top-k chunks relevant to the query (cosine >= threshold).

    Lazily loads the default index when `index` is None and embeds the query on
    the fly with all-MiniLM-L6-v2. Logs and returns [] if no index exists.

    Args:
        source_type: Filter results by metadata.source_type (e.g. "wiki", "pdf").
        source: Filter results by metadata.source (exact match).
    """
    # Index first, model second: an empty index is the common case on a
    # deployment that has not built the knowledge layer, and answering it must
    # not depend on the ``ml`` extra being installed — importing the embedder
    # up front turned "no index" into ``ModuleNotFoundError`` (500) on a
    # base-only install (core#21).
    if index is None:
        index = load_index()
    if index.vectors is None or len(index.vectors) == 0:
        print("retrieve: no vector index found (run zolai.knowledge.ingest first)")
        return []

    from sentence_transformers import SentenceTransformer

    # Offline-first (core#12): the embedder already sits in the local HF cache
    # on the deployment host, so read it from disk and skip the Hub revision
    # check — that check is what logged "You are sending unauthenticated
    # requests to the HF Hub" on every embed and burned the anonymous rate
    # limit.  ``zolai/rag/retrieve.py`` loads the same model this way.
    try:
        model = SentenceTransformer(model_name, local_files_only=True)
    except OSError:
        model = SentenceTransformer(model_name)  # not cached: one-time download

    q = _embed_query(model, query)
    # cosine = dot product since vectors are normalized
    sims = index.vectors @ q
    # most-similar ranking with threshold
    order = np.argsort(-sims)
    hits = []
    for pos in order:
        if sims[pos] < threshold:
            break
        meta = index.metas[pos]
        # Apply metadata filters
        if source_type and meta.get("source_type") != source_type:
            continue
        if source and meta.get("source") != source:
            continue
        hits.append(
            {
                "id": index.ids[pos],
                "text": index.texts[pos],
                "score": round(float(sims[pos]), 4),
                "metadata": meta,
            }
        )
        if len(hits) >= top_k:
            break
    return hits


def format_context(hits: list[dict]) -> str:
    """Render hits as RAG context text for injection into an existing AI prompt."""
    blocks = []
    for i, h in enumerate(hits, 1):
        src = h["metadata"].get("source", "")
        blocks.append(f"[{i}] ({src})\n{h['text']}")
    return "\n\n".join(blocks)
