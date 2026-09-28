"""Command-line interface for the offline evaluation package.

Entry point ``zolai-eval`` (also reachable as ``zolai evaluate``)::

    zolai-eval --set smoke
    zolai-eval --set smoke --json
    zolai-eval --set smoke --baseline report/eval-baseline.json --gate
    zolai-eval --set db:eval_v1 --baseline report/eval-baseline.json --gate

Sets live in the DB (``eval_sets`` / ``eval_cases``); the bundled JSONL files
are interchange only::

    zolai-eval --import zolai/eval/sets/smoke_qa.jsonl --as smoke
    zolai-eval --set db:smoke --export report/smoke.jsonl

Exit codes:
    - ``0`` — evaluation ok; with ``--gate`` every metric met its floor.
    - ``1`` — gate mode: at least one metric dropped below its floor.
    - ``2`` — gate mode requested without a ``--baseline``, or an import/export
      argument error (missing ``--as``/``db:<set>``, unreadable file).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .baseline import below_floor, load_baseline
from .datasets import DB_SPEC, SMOKE
from .metrics import (
    qa_term_recall,
    translation_bleu,
    translation_chrf,
    zvs_compliance_rate,
)
from .store import export_set_to_jsonl, import_jsonl_to_set

_ALL_METRICS = {
    "zvs_compliance_rate": zvs_compliance_rate,
    "translation_bleu": translation_bleu,
    "translation_chrf": translation_chrf,
    "qa_term_recall": qa_term_recall,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zolai-eval",
        description="Offline, dependency-free evaluation of Zolai model outputs.",
    )
    parser.add_argument(
        "--set",
        default=SMOKE,
        help=f"Set specifier: '{SMOKE}', '{DB_SPEC}', 'db:<name>' or a path/base prefix.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Path to a baseline JSON mapping metric -> minimum score.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero when any metric is below its baseline floor.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output."
    )
    parser.add_argument(
        "--import",
        dest="import_path",
        default=None,
        metavar="PATH",
        help="Import a JSONL file into an eval set (with --as) and exit.",
    )
    parser.add_argument(
        "--as",
        dest="as_set",
        default=None,
        metavar="SET",
        help="Target set name for --import.",
    )
    parser.add_argument(
        "--export",
        dest="export_path",
        default=None,
        metavar="PATH",
        help="Export the set named by --set db:<name> to JSONL and exit.",
    )
    return parser


def _run_import(args: argparse.Namespace) -> int:
    """Handle ``--import <path> --as <set>`` (short-circuits metrics)."""
    if not args.as_set:
        print("error: --import requires --as <set>", file=sys.stderr)
        return 2
    try:
        count = import_jsonl_to_set(args.import_path, args.as_set)
    except (OSError, ValueError) as exc:
        print(f"error: import failed: {exc}", file=sys.stderr)
        return 2
    print(f"imported {count} cases into set '{args.as_set}'")
    return 0


def _run_export(args: argparse.Namespace) -> int:
    """Handle ``--export <path>`` for ``--set db:<name>`` (short-circuits metrics)."""
    spec = args.set
    if not spec.startswith(f"{DB_SPEC}:"):
        print(f"error: --export requires --set {DB_SPEC}:<set>", file=sys.stderr)
        return 2
    set_name = spec.split(":", 1)[1]
    if not set_name:
        print(f"error: --export requires --set {DB_SPEC}:<set>", file=sys.stderr)
        return 2
    try:
        count = export_set_to_jsonl(set_name, args.export_path)
    except OSError as exc:
        print(f"error: export failed: {exc}", file=sys.stderr)
        return 2
    if count == 0:
        print(f"error: no cases in eval set '{set_name}'", file=sys.stderr)
        return 2
    print(f"exported {count} cases from set '{set_name}' to {args.export_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.import_path is not None:
        return _run_import(args)
    if args.export_path is not None:
        return _run_export(args)

    # Imported lazily inside evaluate to keep the import graph light.
    from . import evaluate

    scores = evaluate(sets=args.set)

    floors = None
    if args.baseline:
        try:
            floors = load_baseline(args.baseline)
        except FileNotFoundError:
            print(f"error: baseline not found: {args.baseline}", file=sys.stderr)
            return 2

    regressed = below_floor(scores, floors) if floors else []

    if args.gate and floors is None:
        print("error: --gate requires --baseline", file=sys.stderr)
        return 2

    if args.json:
        payload = {"metrics": scores}
        if floors is not None:
            payload["floors"] = floors
            payload["below_floor"] = regressed
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for name in sorted(scores):
            print(f"{name}: {scores[name]:.4f}")
        if regressed:
            print("regressed:")
            for name in regressed:
                print(f"  {name}: {scores[name]:.4f} < floor {floors[name]:.4f}")

    if args.gate and regressed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
