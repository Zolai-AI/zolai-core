"""Command-line interface for the offline evaluation package.

Entry point ``zolai-eval`` (also reachable as ``zolai evaluate``)::

    zolai-eval --set smoke
    zolai-eval --set smoke --json
    zolai-eval --set smoke --baseline report/eval-baseline.json --gate

Exit codes:
    - ``0`` — evaluation ok; with ``--gate`` every metric met its floor.
    - ``1`` — gate mode: at least one metric dropped below its floor.
    - ``2`` — gate mode requested without a ``--baseline``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .baseline import below_floor, load_baseline
from .datasets import SMOKE
from .metrics import (
    qa_term_recall,
    translation_bleu,
    translation_chrf,
    zvs_compliance_rate,
)

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
        help=f"Set specifier: '{SMOKE}' or a path/base prefix.",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

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
