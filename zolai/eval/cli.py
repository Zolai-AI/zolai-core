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
import logging
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone

from .baseline import below_floor, load_baseline
from .datasets import DB_SPEC, SMOKE
from .metrics import (
    qa_term_recall,
    translation_bleu,
    translation_chrf,
    zvs_compliance_rate,
)
from .store import export_set_to_jsonl, import_jsonl_to_set

logger = logging.getLogger(__name__)

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


def _run_identity(spec: str) -> tuple[str, str] | None:
    """Resolve ``(set_name, source)`` for a ``--set`` specifier.

    Returns ``None`` when the run cannot be attributed to a single eval set
    (``db`` merges every active set; arbitrary JSONL paths have no stable
    identity in ``eval_sets``).
    """
    if spec == DB_SPEC:
        return None
    if spec.startswith(f"{DB_SPEC}:"):
        name = spec.split(":", 1)[1]
        return (name, "db") if name else None
    if spec == SMOKE:
        return (SMOKE, "jsonl")
    return None


def _persist_eval_run(
    set_name: str,
    source: str,
    scores: dict[str, float],
    *,
    duration_ms: float,
    gate_passed: bool,
) -> bool:
    """Persist one evaluation run to ``eval_runs`` (best-effort).

    Creates the monitoring tables and the ``eval_sets`` parent row when they
    are missing, then inserts the run.  Never raises: the CLI's exit-code
    contract must not depend on the monitoring store being reachable.

    Returns:
        ``True`` when the row was written.
    """
    try:
        from sqlalchemy import inspect, text

        from ..data.database import get_manager

        mgr = get_manager()
        if not inspect(mgr.engine).has_table("eval_runs"):
            from ..data.migrations import create_monitoring_tables

            create_monitoring_tables(mgr)

        with mgr.write_session() as session:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if (
                session.execute(
                    text("SELECT 1 FROM eval_sets WHERE set_name = :n"),
                    {"n": set_name},
                ).first()
                is None
            ):
                session.execute(
                    text(
                        "INSERT INTO eval_sets"
                        "(set_name, description, created_at, updated_at) "
                        "VALUES (:n, :d, :t, :t)"
                    ),
                    {"n": set_name, "d": "auto-created by zolai-eval run", "t": now},
                )
            case_count = (
                session.execute(
                    text(
                        "SELECT COUNT(*) FROM eval_cases "
                        "WHERE set_name = :n AND is_active = 1"
                    ),
                    {"n": set_name},
                ).scalar()
                or 0
            )
            session.execute(
                text(
                    "INSERT INTO eval_runs"
                    "(set_name, created_at, case_count, duration_ms, gate_passed, "
                    "metrics, source) "
                    "VALUES (:set_name, :created_at, :case_count, :duration_ms, "
                    ":gate_passed, :metrics, :source)"
                ),
                {
                    "set_name": set_name,
                    "created_at": now,
                    "case_count": int(case_count),
                    "duration_ms": round(float(duration_ms), 3),
                    "gate_passed": 1 if gate_passed else 0,
                    "metrics": json.dumps(
                        {k: round(float(v), 6) for k, v in sorted(scores.items())}
                    ),
                    "source": source,
                },
            )
    except Exception as exc:  # noqa: BLE001 — observability must not break the CLI
        logger.debug("eval run persistence skipped: %s", exc)
        return False
    return True


def _publish_eval_metrics(
    set_name: str,
    scores: dict[str, float],
    *,
    duration_ms: float,
) -> bool:
    """Publish Prometheus gauges and a Grafana annotation for this run.

    Best-effort: observability must never change the CLI's exit code.
    """
    try:
        from ..monitoring.metrics import EVAL_LAST_RUN, EVAL_METRIC_VALUE, EVAL_RUNS

        EVAL_RUNS.labels(set_name=set_name).inc()
        for name, value in sorted(scores.items()):
            EVAL_METRIC_VALUE.labels(set_name=set_name, metric=str(name)).set(float(value))
        EVAL_LAST_RUN.set(time.time())
    except Exception as exc:  # noqa: BLE001 — metrics must never break the CLI
        logger.debug("eval metric publishing skipped: %s", exc)
        return False

    try:
        from ..monitoring.store import create_annotation

        create_annotation(
            {
                "title": f"eval:{set_name}",
                "text": json.dumps(
                    {
                        "duration_ms": round(float(duration_ms), 3),
                        "metrics": {
                            k: round(float(v), 6) for k, v in sorted(scores.items())
                        },
                    },
                    ensure_ascii=False,
                ),
                "tags": ["eval", set_name],
                "kind": "eval",
            }
        )
    except Exception as exc:  # noqa: BLE001 — annotation is optional
        logger.debug("eval annotation skipped: %s", exc)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.import_path is not None:
        return _run_import(args)
    if args.export_path is not None:
        return _run_export(args)

    # Imported lazily inside evaluate to keep the import graph light.
    from . import evaluate

    started = time.perf_counter()
    scores = evaluate(sets=args.set)
    duration_ms = (time.perf_counter() - started) * 1000

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

    identity = _run_identity(args.set)
    if identity is not None:
        _persist_eval_run(
            identity[0],
            identity[1],
            scores,
            duration_ms=duration_ms,
            gate_passed=not (args.gate and regressed),
        )
        _publish_eval_metrics(identity[0], scores, duration_ms=duration_ms)

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
