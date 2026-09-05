#!/usr/bin/env python3
"""Build knowledge index with embeddings — GPU-optimized for Kaggle T4×2.

Usage:
    # All sources, auto-detect device:
    python scripts/data/build_knowledge_index.py

    # Kaggle T4 GPU with Kaggle dataset input:
    python scripts/data/build_knowledge_index.py --data-dir /kaggle/input/zolai-rag-data --sources wiki,dictionary,parallel,bible

    # CPU fallback (~87 minutes):
    python scripts/data/build_knowledge_index.py --device cpu

    # Resume from partial index (incremental):
    python scripts/data/build_knowledge_index.py --resume

    # Only specific sources:
    python scripts/data/build_knowledge_index.py --sources wiki,dictionary

    # Limit wiki files for testing:
    python scripts/data/build_knowledge_index.py --limit 50

Requires: pip install sentence-transformers
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # scripts/data/ → zolai-core
WIKI_ROOT = REPO_ROOT.parent / "zolai-wiki"
DATA_ROOT = REPO_ROOT.parent / "data"
DATA_KNOWLEDGE = DATA_ROOT / "knowledge"
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


def iter_wiki_files(data_dir: Path | None = None):
    """Yield (abs_path, kind) for wiki MD/TXT files."""
    wiki = data_dir / "wiki" if data_dir else WIKI_ROOT
    # Also check if WIKI_ROOT itself has the wiki files (default behavior)
    if not wiki.exists():
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


def iter_dictionary_files(data_dir: Path):
    """Yield (entry_dict, 'dictionary') for dictionary JSONL entries."""
    dict_dir = data_dir / "dictionary" / "processed"
    if not dict_dir.exists():
        # Fallback: check flat structure (Kaggle dataset layout)
        dict_dir = data_dir
    patterns = ["dict_*v*.jsonl", "zvs_*_v*.jsonl"]
    files: list[Path] = []
    for pat in patterns:
        files.extend(sorted(dict_dir.glob(pat)))
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    hw = entry.get("headword") or entry.get("zolai", "")
                    trans = entry.get("translations") or ([entry["english"]] if entry.get("english") else [])
                    pos = entry.get("pos", [])
                    examples = entry.get("example_zo") or entry.get("explanations", [])
                    text = f"{hw}"
                    if pos:
                        text += f" ({', '.join(pos)})"
                    if trans:
                        text += f": {', '.join(str(t) for t in trans)}"
                    if examples:
                        ex = examples[0] if isinstance(examples, list) else examples
                        text += f" — {ex}"
                    yield {
                        "text": text,
                        "metadata": {"source": f"dictionary/{p.name}", "source_type": "dictionary", "heading": hw, "chunk_type": "dictionary"},
                    }, "dictionary"
                except Exception:
                    pass


def iter_parallel_files(data_dir: Path):
    """Yield (entry_dict, 'parallel') for bilingual pair JSONLs."""
    par_dir = data_dir / "parallel"
    if not par_dir.exists():
        # Fallback: check flat structure (Kaggle dataset layout)
        par_dir = data_dir
    patterns = ["zo_en_pairs_*.jsonl", "bible_parallel_*.jsonl"]
    files: list[Path] = []
    for pat in patterns:
        files.extend(sorted(par_dir.glob(pat)))
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    en = entry.get("input") or entry.get("english", "")
                    zo = entry.get("output") or entry.get("zolai", "")
                    ref = entry.get("metadata", {}).get("reference", "")
                    text = f"{en}\n→ {zo}"
                    if ref:
                        text += f" ({ref})"
                    yield {
                        "text": text,
                        "metadata": {"source": f"parallel/{p.name}", "source_type": "parallel", "heading": ref, "chunk_type": "parallel"},
                    }, "parallel"
                except Exception:
                    pass


def iter_bible_study_files(data_dir: Path):
    """Yield (entry_dict, 'bible') for bible study JSONLs."""
    bible_dir = data_dir / "dictionary" / "bible_study"
    if not bible_dir.exists():
        # Fallback: check flat structure (Kaggle dataset layout)
        bible_dir = data_dir
    for p in sorted(bible_dir.glob("*_study.jsonl")):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "vocab":
                        text = f"{entry.get('word', '')}: {entry.get('gloss', '')} (freq: {entry.get('freq', '')})"
                    elif entry.get("type") == "grammar":
                        text = f"{entry.get('pattern', '')}: {entry.get('example', '')} ({entry.get('explanation', '')})"
                    elif entry.get("type") == "book_summary":
                        top = entry.get("top_words", [])[:5]
                        top_str = ", ".join(f"{w}({c})" for w, c in top) if top else ""
                        text = f"{entry.get('book', '')}: {entry.get('verses', 0)} verses, {entry.get('new_vocab', 0)} vocab — {top_str}"
                    else:
                        parts = [f"{k}: {v}" for k, v in entry.items() if isinstance(v, str) and v]
                        text = " — ".join(parts[:4]) if parts else str(entry)[:500]
                    ref = entry.get("reference") or entry.get("book", p.stem)
                    yield {
                        "text": text,
                        "metadata": {"source": f"bible/{p.name}", "source_type": "bible", "heading": ref, "chunk_type": "bible"},
                    }, "bible"
                except Exception:
                    pass


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build knowledge index with embeddings")
    ap.add_argument("--device", default=None, help="cuda or cpu (auto-detect)")
    ap.add_argument("--limit", type=int, default=0, help="Max wiki files (0=all)")
    ap.add_argument("--resume", action="store_true", help="Skip already-indexed chunks")
    ap.add_argument("--batch-size", type=int, default=0, help="Override batch size")
    ap.add_argument("--model", default=MODEL_NAME, help="Sentence-transformer model")
    ap.add_argument("--data-dir", default=None, help="Data root (default: ../data or /kaggle/input/zolai-rag-data)")
    ap.add_argument("--sources", default="wiki,dictionary,parallel,bible", help="Comma-separated sources to include")
    ap.add_argument("--output-dir", default=None, help="Override output directory (default: <repo>/data/knowledge)")
    args = ap.parse_args()

    import torch
    from sentence_transformers import SentenceTransformer

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    batch_size = args.batch_size or (BATCH_SIZE_GPU if device == "cuda" else BATCH_SIZE_CPU)
    sources = set(args.sources.split(","))

    # Resolve data directory
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        data_dir = DATA_ROOT

    # Resolve output directory
    out_dir = Path(args.output_dir) if args.output_dir else DATA_KNOWLEDGE

    print(f"Device: {device}, Batch: {batch_size}, Model: {args.model}")
    print(f"Data dir: {data_dir}")
    print(f"Sources: {', '.join(sorted(sources))}")
    print(f"Output: {out_dir}")

    t0 = time.time()
    model = SentenceTransformer(args.model, device=device)
    print(f"Model loaded in {time.time()-t0:.1f}s")

    # Load existing IDs for resume
    out_path = out_dir / "knowledge_vectors.jsonl"
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

    # Collect chunks from all enabled sources
    pending: list[dict] = []
    source_counts: dict[str, int] = {}

    # --- Wiki ---
    if "wiki" in sources:
        print("Chunking wiki...", flush=True)
        t1 = time.time()
        count = 0
        file_count = 0
        for abs_path, kind in iter_wiki_files(data_dir):
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
                count += 1
        source_counts["wiki"] = count
        print(f"  Wiki: {count} chunks from {file_count} files in {time.time()-t1:.1f}s", flush=True)

    # --- Dictionary ---
    if "dictionary" in sources:
        print("Ingesting dictionary...", flush=True)
        t1 = time.time()
        count = 0
        for i, (entry, _kind) in enumerate(iter_dictionary_files(data_dir)):
            chunk_id = f"dictionary/{i}"
            if chunk_id in existing_ids:
                continue
            entry["id"] = chunk_id
            pending.append(entry)
            count += 1
        source_counts["dictionary"] = count
        print(f"  Dictionary: {count} entries in {time.time()-t1:.1f}s", flush=True)

    # --- Parallel ---
    if "parallel" in sources:
        print("Ingesting parallel pairs...", flush=True)
        t1 = time.time()
        count = 0
        for i, (entry, _kind) in enumerate(iter_parallel_files(data_dir)):
            chunk_id = f"parallel/{i}"
            if chunk_id in existing_ids:
                continue
            entry["id"] = chunk_id
            pending.append(entry)
            count += 1
        source_counts["parallel"] = count
        print(f"  Parallel: {count} entries in {time.time()-t1:.1f}s", flush=True)

    # --- Bible study ---
    if "bible" in sources:
        print("Ingesting bible study...", flush=True)
        t1 = time.time()
        count = 0
        for i, (entry, _kind) in enumerate(iter_bible_study_files(data_dir)):
            chunk_id = f"bible/{i}"
            if chunk_id in existing_ids:
                continue
            entry["id"] = chunk_id
            pending.append(entry)
            count += 1
        source_counts["bible"] = count
        print(f"  Bible: {count} entries in {time.time()-t1:.1f}s", flush=True)

    # Summary
    total_chunks = len(pending)
    print(f"\nTotal new chunks: {total_chunks}", flush=True)
    for src, cnt in sorted(source_counts.items()):
        print(f"  {src}: {cnt}", flush=True)

    if not pending:
        print("Nothing new to index.")
        return

    # Embed + write
    print("Embedding + writing...", flush=True)
    t2 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
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
