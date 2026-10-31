#!/usr/bin/env python3
"""Create group-aware, seeded train/val/test splits from the consolidated corpus.

Rows that share a source item — a verse reference, a pair id, or (when neither
exists) the same normalized text — land in one split, so a verse cannot be
trained on and tested on at once. For a plain row-by-row shuffle, see git
history before 2026-10-31: it straddled paired directions and the leakage gate
then failed on its own output.

Run: python3 scripts/training/create_training_splits.py
Output must still pass ``scripts/training/check_split_leakage.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from zolai.trainer.dataset import _split_by_group  # noqa: E402

DATA = ROOT / "data" / "training"
INPUT_FILE = DATA / "master_train_complete_all_resources.jsonl"
SPLIT_FILES = {
    "train": "llm_train.jsonl",
    "val": "llm_val.jsonl",
    "test": "llm_test.jsonl",
}


def _log(path: Path, msg: str) -> None:
    print(msg, flush=True)
    with open(path, "a") as f:
        f.write(msg + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_FILE)
    parser.add_argument("--out-dir", type=Path, default=DATA)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: {args.input} not found", file=sys.stderr)
        return 2

    records = []
    with open(args.input, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"ERROR: {args.input}:{line_no}: {e}", file=sys.stderr)
                return 2

    try:
        train, val, test = _split_by_group(records, args.seed, val_ratio=0.1, test_ratio=0.1)
    except ValueError as e:
        print(f"ERROR: cannot split {len(records):,} records: {e}", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "training_splits.log"
    log_path.write_text("")
    _log(log_path, f"CREATING TRAINING SPLITS (80/10/10, seed={args.seed})\n")
    _log(log_path, f"Input: {args.input} ({len(records):,} records)\n")

    stats = {"train": len(train), "val": len(val), "test": len(test)}
    for name, rows in (("train", train), ("val", val), ("test", test)):
        out = args.out_dir / SPLIT_FILES[name]
        with open(out, "w", encoding="utf-8") as f:
            for rec in rows:
                rec["split"] = name
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _log(log_path, f"Saved {name}: {out} ({len(rows):,})")

    _log(log_path, f"\nTotals: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
