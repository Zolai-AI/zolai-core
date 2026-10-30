#!/usr/bin/env python3
"""Fail the build when a record appears in more than one data split.

Defaults to ``data/training/llm_{train,val,test}.jsonl`` and also compares every
``data/eval/*.jsonl`` set against train (eval fixtures must be unseen).

Exit codes: 0 clean or nothing to check, 1 leakage found, 2 unreadable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from zolai.trainer.leakage import check_leakage  # noqa: E402


def _discover(training_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for name in ("train", "val", "test"):
        for candidate in (
            training_dir / f"llm_{name}.jsonl",
            training_dir / f"{name}.jsonl",
        ):
            if candidate.exists():
                found[name] = candidate
                break
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path)
    parser.add_argument("--val", type=Path)
    parser.add_argument("--test", type=Path)
    parser.add_argument(
        "--training-dir", type=Path, default=ROOT / "data" / "training"
    )
    parser.add_argument(
        "--eval-dir", type=Path, default=ROOT / "data" / "eval",
        help="every *.jsonl here is checked against train",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    explicit = {name: getattr(args, name) for name in ("train", "val", "test")}
    splits = {name: path for name, path in explicit.items() if path is not None}
    if not splits:
        splits = _discover(args.training_dir)

    eval_sets: dict[str, Path] = {}
    if any(name == "train" for name in splits) and args.eval_dir.exists():
        eval_sets = {p.stem: p for p in sorted(args.eval_dir.glob("*.jsonl"))}

    if not splits:
        message = f"no split files found under {args.training_dir} — nothing to check"
        print(json.dumps({"ok": True, "skipped": message}) if args.json else message)
        return 0

    try:
        report = check_leakage(splits, eval_vs=eval_sets)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for name, path in splits.items():
            print(f"  {name:<5} {report['splits'][name]:>9,} rows  {path}")
        if report["ok"]:
            print("no leakage: splits are disjoint")
        else:
            print("LEAKAGE DETECTED:")
            for overlap in report["overlaps"]:
                print(
                    f"  {overlap['left']} ∩ {overlap['right']}: "
                    f"{overlap['count']:,} shared rows (e.g. {overlap['examples'][:1]})"
                )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
