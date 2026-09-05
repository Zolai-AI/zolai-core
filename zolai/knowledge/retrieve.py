"""Retrieval over the Zolai knowledge vector index.

Primary path is an in-memory numpy cosine scan over knowledge_vectors.jsonl —
works fully offline with no external vector DB. Yields top-k chunks with their
source metadata so callers can inject them as RAG context for existing AIs.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import config

DEFAULT_INDEX = config.paths.data_knowledge / "knowledge_vectors.jsonl"

# HuggingFace dataset for auto-download
HF_REPO = "peterpausianlian/zolai-knowledge-vectors"
HF_FILENAME = "knowledge_vectors.jsonl"
HF_REPO_TYPE = "dataset"


@dataclass
class Index:
    texts: list[str] = field(default_factory=list)
    metas: list[dict] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    vectors: np.ndarray | None = None


def _ensure_index_file(path: Path) -> Path:
    """Download knowledge_vectors.jsonl from HuggingFace if missing or empty."""
    if path.exists() and path.stat().st_size > 1024:
        return path

    print(f"knowledge index not found at {path} — downloading from HuggingFace...")
    try:
        from huggingface_hub import hf_hub_download

        # Use HF_TOKEN from env if set (for private repos / rate limits)
        token = os.environ.get("HF_TOKEN")
        downloaded = hf_hub_download(
            repo_id=HF_REPO,
            filename=HF_FILENAME,
            repo_type=HF_REPO_TYPE,
            token=token,
            local_dir=str(path.parent),
            local_dir_use_symlinks=False,
        )
        # hf_hub_download saves as <local_dir>/<filename>
        src = Path(downloaded)
        if src != path:
            import shutil
            shutil.move(str(src), str(path))
        print(f"Downloaded {path.stat().st_size / 1024 / 1024:.1f} MB to {path}")
    except Exception as e:
        print(f"WARNING: Failed to download knowledge index from HF: {e}")
        print(f"  Repo: {HF_REPO}")
        print(f"  You can manually download: huggingface-cli download {HF_REPO} {HF_FILENAME} --repo-type dataset --local-dir {path.parent}")
    return path


def load_index(path: Path = DEFAULT_INDEX) -> Index:
    """Load a knowledge_vectors.jsonl index into memory. Missing -> empty Index."""
    idx = Index()
    path = _ensure_index_file(path)
    if not path.exists() or path.stat().st_size < 1024:
        return idx
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    for r in rows:
        idx.ids.append(r.get("id", ""))
        idx.texts.append(r.get("text", ""))
        idx.metas.append(r.get("metadata", {}))
    vecs = [r.get("embedding") for r in rows]
    if vecs and all(isinstance(v, list) for v in vecs):
        idx.vectors = np.asarray(vecs, dtype=np.float32)
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
    from sentence_transformers import SentenceTransformer

    if index is None:
        index = load_index()
    if index.vectors is None or len(index.vectors) == 0:
        print("retrieve: no vector index found (run zolai.knowledge.ingest first)")
        return []

    model = SentenceTransformer(model_name)
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
