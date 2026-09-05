"""Ingest Zolai knowledge sources (wiki MD/TXT, PDF-OCR text) into a JSONL index.

Records are one-per-line, newline-delimited (valid JSONL), each carrying its
dense embedding (all-MiniLM-L6-v2). No external vector DB is required.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import config

ROOT = config.paths.root
ART = config.paths.data_knowledge
WIKI_ROOT = config.paths.root.parent / "zolai-wiki"


def enum_list(items: list, limit: int) -> "list[tuple[int, object]]":
    """enumerate(items)[:limit] — explicit slice for typing/runtime clarity."""
    return list(enumerate(items))[: max(0, limit)]

def _chunk_by_headings(text: str, max_chars: int = 1200, min_chars: int = 60, hard_max: int = 10000) -> list[str]:
    """Split markdown text into chunks at heading/paragraph boundaries (O(n)).

    Added hard_max: if a single chunk exceeds this, force-split it at line
    boundaries to prevent huge single-chunk files (wordlists, etc.).
    """
    lines = text.splitlines()
    chunks: list[str] = []
    cur: list[str] = []
    cur_chars = 0

    def flush(*, force: bool) -> None:
        nonlocal cur, cur_chars
        body = "\n".join(cur).strip()
        if force or len(body) >= min_chars:
            chunks.append(body)
        cur, cur_chars = [], 0

    for line in lines:
        is_heading = re.match(r"^\s*(#{1,6})\s+", line) is not None
        if is_heading and cur:
            flush(force=False)
        cur.append(line)
        cur_chars += len(line) + 1
        # split at paragraph break when over budget
        if cur_chars >= max_chars and not line.strip():
            flush(force=False)
        # hard-split: if chunk is way too big, force flush every hard_max chars
        if cur_chars >= hard_max:
            flush(force=True)
    if cur:
        flush(force=False)

    # Second pass: split any remaining oversized chunks by character count
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= hard_max:
            final.append(chunk)
        else:
            # Split oversized chunk into hard_max-sized pieces
            for i in range(0, len(chunk), hard_max):
                piece = chunk[i:i+hard_max].strip()
                if len(piece) >= min_chars:
                    final.append(piece)
    return final


def iter_sources():
    """Yield (abs_path, kind) for wiki MD/TXT files (skip dot dirs).

    Looks for the zolai-wiki sibling repo at ``WIKI_ROOT``.
    """
    wiki = WIKI_ROOT
    if not wiki.exists():
        return
    for p in sorted(wiki.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() in {".md", ".txt"}:
            yield p, "wiki"


def embed_texts(model, texts: list[str], batch_size: int = 512) -> list[list[float]]:
    embs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    return [e.tolist() for e in embs]


def index_wiki(
    out_dir=ART, limit: int = 0, max_chunks: int = 200,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> Path:
    """Chunk + embed wiki sources into <out_dir>/knowledge_vectors.jsonl.

    Single-pass: loads existing index, chunks new files, embeds only new chunks,
    then writes the complete index (existing with embeddings + new with embeddings).
    """
    from sentence_transformers import SentenceTransformer

    out_dir.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(model_name)

    srcs = list(iter_sources())
    if limit:
        srcs = srcs[:limit]

    out_path = out_dir / "knowledge_vectors.jsonl"

    # ── Load existing index (rows already have embeddings) ──
    existing_ids: set[str] = set()
    existing_rows: list[dict] = []
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        row = json.loads(line)
                        existing_rows.append(row)
                        existing_ids.add(row.get("id", ""))
                    except json.JSONDecodeError:
                        continue

    # ── Chunk new wiki sources (skip already-indexed IDs) ──
    new_rows: list[dict] = []
    for abs_path, kind in srcs:
        relpath = abs_path.relative_to(WIKI_ROOT).as_posix()
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  ! skip {relpath}: {e}")
            continue
        for i, chunk in enumerate(_chunk_by_headings(text)):
            chunk_id = f"wiki/{relpath}#c{i}"
            if chunk_id in existing_ids:
                continue
            rec = {
                "id": chunk_id,
                "text": chunk,
                "metadata": {
                    "source": f"wiki/{relpath}",
                    "source_type": kind,
                    "heading": "",
                    "chunk_type": "wiki",
                },
            }
            new_rows.append(rec)

    # ── Embed only new chunks ──
    if new_rows:
        texts = [r["text"] for r in new_rows]
        embs = embed_texts(model, texts)
        for r, e in zip(new_rows, embs):
            r["embedding"] = e
            r["embeddingModel"] = model_name
            r["embeddingDim"] = len(e)
        print(f"wg: embedded {len(new_rows)} new chunks from {len(srcs)} sources")
    else:
        print(f"wg: no new chunks (all {len(existing_ids)} already indexed)")

    # ── Write complete index (existing + new, all with embeddings) ──
    # Write in batches so partial progress is saved if interrupted
    all_rows = existing_rows + new_rows
    batch_size = 5000
    with out_path.open("w", encoding="utf-8") as f:
        for i in range(0, len(all_rows), batch_size):
            batch = all_rows[i : i + batch_size]
            for r in batch:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            if (i // batch_size) % 10 == 0 and i > 0:
                print(f"wg: wrote {i + len(batch)}/{len(all_rows)} rows...")

    print(f"wg: total index rows = {len(all_rows)}, vectors -> {out_path}")
    return out_path


def index_pdfs(out_dir=ART, limit: int = 0, model_name: str = "...") -> Path:
    raise NotImplementedError(
        "PDF OCR ingest is a later backlog item (B). Pass parsed OCR text files "
        "via wiki-style .txt ingestion instead."
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    a = ap.parse_args()
    index_wiki(limit=a.limit, model_name=a.model)
