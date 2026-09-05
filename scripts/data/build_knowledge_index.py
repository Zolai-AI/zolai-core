#!/usr/bin/env python3
"""Build knowledge index with embeddings — GPU-optimized for Kaggle T4×2.

Usage:
    # Kaggle T4 GPU (~70 seconds for 104k chunks):
    python scripts/data/build_knowledge_index.py

    # CPU fallback (~87 minutes):
    python scripts/data/build_knowledge_index.py --device cpu

    # Resume from partial index (incremental):
    python scripts/data/build_knowledge_index.py --resume

    # Limit files for testing:
    python scripts/data/build_knowledge_index.py --limit 50

Requires: pip install sentence-transformers
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

REPO_ROOT = Path(__file__).resolve().parents[3]  # scripts/data/ → zolai-core
WIKI_ROOT = REPO_ROOT.parent / "zolai-wiki"
DATA_KNOWLEDGE = REPO_ROOT.parent / "data" / "knowledge"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE_GPU = 2048
BATCH_SIZE_CPU = 256


def chunk_text(text: str, max_chars: int = 1200, min_chars: int = 60, hard_max: int = 10000) -> list[str]:
    """Split markdown into chunks at heading/paragraph boundaries."""
    import re
    lines = text.splitlines()
    chunks: list[str] = []
    cur: list[str] = []
    cur_chars = 0

    def flush(*, force: bool = False) -> None:
        nonlocal cur, cur_chars
        body = "\n".join(cur).strip()
        if force or len(body) >= min_chars:
            chunks.append(body)
        cur, cur_chars = [], 0

    for line in lines:
        if re.match(r"^\s*#{1,6}\s+", line) and cur:
            flush()
        cur.append(line)
        cur_chars += len(line) + 1
        if cur_chars >= max_chars and not line.strip():
            flush()
        if cur_chars >= hard_max:
            flush(force=True)
    if cur:
        flush()

    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= hard_max:
            final.append(chunk)
        else:
            for i in range(0, len(chunk), hard_max):
                piece = chunk[i:i + hard_max].strip()
                if len(piece) >= min_chars:
                    final.append(piece)
    return final


def iter_wiki_files():
    """Yield (abs_path, kind) for wiki MD/TXT files."""
    if not WIKI_ROOT.exists():
        return
    for p in sorted(WIKI_ROOT.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() in {".md", ".txt"}:
            yield p, "wiki"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build knowledge index with embeddings")
    ap.add_argument("--device", default=None, help="cuda or cpu (auto-detect)")
    ap.add_argument("--limit", type=int, default=0, help="Max wiki files (0=all)")
    ap.add_argument("--resume", action="store_true", help="Skip already-indexed chunks")
    ap.add_argument("--batch-size", type=int, default=0, help="Override batch size")
    ap.add_argument("--model", default=MODEL_NAME, help="Sentence-transformer model")
    args = ap.parse_args()

    import torch
    from sentence_transformers import SentenceTransformer

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    batch_size = args.batch_size or (BATCH_SIZE_GPU if device == "cuda" else BATCH_SIZE_CPU)

    print(f"Device: {device}, Batch: {batch_size}, Model: {args.model}")
    print(f"Wiki: {WIKI_ROOT} (exists: {WIKI_ROOT.exists()})")
    print(f"Output: {DATA_KNOWLEDGE}")

    t0 = time.time()
    model = SentenceTransformer(args.model, device=device)
    print(f"Model loaded in {time.time()-t0:.1f}s")

    # Load existing IDs for resume
    out_path = DATA_KNOWLEDGE / "knowledge_vectors.jsonl"
    existing_ids: set[str] = set()
    existing_count = 0
    if args.resume and out_path.exists():
        with open(out_path) as f:
            for line in f:
                if line.strip():
                    try:
                        row = json.loads(line)
                        existing_ids.add(row.get("id", ""))
                        existing_count += 1
                    except json.JSONDecodeError:
                        pass
        print(f"Resume: {existing_count} existing rows, {len(existing_ids)} unique IDs")

    # Chunk wiki files
    print("Chunking wiki...", flush=True)
    t1 = time.time()
    pending: list[dict] = []
    file_count = 0
    for abs_path, kind in iter_wiki_files():
        file_count += 1
        if args.limit and file_count > args.limit:
            break
        relpath = abs_path.relative_to(WIKI_ROOT).as_posix()
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, chunk in enumerate(chunk_text(text)):
            chunk_id = f"wiki/{relpath}#c{i}"
            if chunk_id in existing_ids:
                continue
            pending.append({
                "id": chunk_id,
                "text": chunk,
                "metadata": {"source": f"wiki/{relpath}", "source_type": kind, "heading": "", "chunk_type": "wiki"},
            })

    print(f"New chunks: {len(pending)} from {file_count} files in {time.time()-t1:.1f}s", flush=True)

    if not pending:
        print("Nothing new to index.")
        return

    # Embed + write
    print("Embedding + writing...", flush=True)
    t2 = time.time()
    mode = "a" if args.resume and existing_count > 0 else "w"
    total = 0
    with open(out_path, mode) as f:
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            texts = [r["text"][:512] for r in batch]
            embs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
            for r, e in zip(batch, embs):
                r["embedding"] = e.tolist() if hasattr(e, "tolist") else list(e)
                r["embeddingModel"] = args.model
                r["embeddingDim"] = len(e)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            total += len(batch)
            elapsed = time.time() - t2
            rate = total / elapsed if elapsed > 0 else 0
            eta = (len(pending) - total) / rate if rate > 0 else 0
            print(f"  {total}/{len(pending)} ({rate:.0f}/s, ETA {eta:.0f}s)", flush=True)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"=== DONE in {time.time()-t0:.1f}s ===", flush=True)
    print(f"File: {out_path} ({size_mb:.1f} MB)", flush=True)
    print(f"New rows: {total}, Existing: {existing_count}", flush=True)


if __name__ == "__main__":
    main()
