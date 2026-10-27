#!/usr/bin/env python3
"""Rewrite ``knowledge_vectors.embedding`` from JSON arrays to base64 float32.

Idempotent: only rows whose value starts with ``[`` (the legacy encoding) are
touched. Safe to run while readers are live — both encodings decode.

    python scripts/maintenance/compact_embeddings.py --db ../data/zolai.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zolai.rag.vectors import encode_embedding  # noqa: E402

BATCH = 500


def compact(db_path: Path, *, dry_run: bool = False) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path), timeout=60, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        pending = conn.execute(
            "SELECT COUNT(*) FROM knowledge_vectors WHERE substr(embedding, 1, 1) = '['"
        ).fetchone()[0]
        if dry_run:
            return {"pending": pending, "rewritten": 0}

        rewritten = 0
        while True:
            rows = conn.execute(
                "SELECT id, embedding FROM knowledge_vectors "
                "WHERE substr(embedding, 1, 1) = '[' LIMIT ?",
                (BATCH,),
            ).fetchall()
            if not rows:
                break
            conn.execute("BEGIN IMMEDIATE")
            for vid, emb_json in rows:
                conn.execute(
                    "UPDATE knowledge_vectors SET embedding = ? WHERE id = ?",
                    (encode_embedding(json.loads(emb_json)), vid),
                )
            conn.execute("COMMIT")
            rewritten += len(rows)
            print(f"  rewritten {rewritten}/{pending}", flush=True)
        return {"pending": pending, "rewritten": rewritten}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to the canonical SQLite DB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result = compact(Path(args.db), dry_run=args.dry_run)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
