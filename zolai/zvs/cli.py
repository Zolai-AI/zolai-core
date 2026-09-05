"""Command-line interface for the ZVS 2018 validator.

Provides the ``zolai-zvs`` console entry point::

    zolai-zvs validate <paths...> [--json] [--noisy] [--categories dial,stem]

Reads plain text files, JSONL files (fields ``text``/``zolai``/``sentence``/
``corrected``/``original``), or strings on stdin, and writes a text or JSON
report. Exits non-zero when any violation is found, so it works in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence

from . import validate
from .exceptions import ExceptionRegistry
from .report import Report
from .rules_data import DEFAULT_EXCEPTIONS


def _categorise(text: str) -> str:
    """Return '(text | jsonl)' hint used only for CLI messaging."""
    return "text"


def _iter_texts(paths: Sequence[str], jsonl: bool) -> Iterable[tuple[str, str]]:
    """Yield ``(source, text)`` pairs from files or stdin."""
    sources = list(paths)
    if not sources:
        # Read from stdin when no paths are given.
        if not sys.stdin.isatty():
            for line in sys.stdin:
                line = line.rstrip("\n")
                if not line:
                    continue
                if jsonl:
                    yield from _yield_jsonl(line, "<stdin>")
                else:
                    yield "<stdin>", line
            return
        # Interactive help.
        _parser().parse_args(["--help"])  # prints help and exits
        return

    for path in sources:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    source = f"{path}:{lineno}"
                    if jsonl:
                        yield from _yield_jsonl(line, source)
                    else:
                        yield source, line
        except FileNotFoundError:
            print(f"File not found: {path}", file=sys.stderr)


def _yield_jsonl(line: str, source: str) -> Iterable[tuple[str, str]]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return
    if isinstance(data, dict):
        for field in ("text", "zolai", "sentence", "corrected", "original"):
            value = data.get(field)
            if isinstance(value, str) and value:
                yield f"{source}[{field}]", value


def _parse_categories(value: str | None) -> Iterable[str] | None:
    if not value:
        return None
    return tuple(c.strip() for c in value.split(",") if c.strip())


def _build_exceptions(args: argparse.Namespace) -> ExceptionRegistry:
    registry = ExceptionRegistry()
    if getattr(args, "use_default_exceptions", False):
        # Merge the seeded historical exceptions into the CLI's registry.
        for rule_id in DEFAULT_EXCEPTIONS.rule_ids:
            registry.add_rule(rule_id)
        for token in DEFAULT_EXCEPTIONS.tokens:
            registry.add_token(token)
        for phrase in DEFAULT_EXCEPTIONS.phrases:
            registry.add_phrase(phrase)
    for rule_id in args.exclude_rules or ():
        registry.add_rule(rule_id)
    for token in args.exclude_tokens or ():
        registry.add_token(token)
    for phrase in args.allow_phrase or ():
        registry.add_phrase(phrase)
    return registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zolai-zvs",
        description="ZVS 2018 compliance validator for the Zolai ecosystem.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    validate_p = sub.add_parser("validate", help="Validate files, JSONL, or stdin.")
    validate_p.add_argument("paths", nargs="*", help="Files to validate.")
    validate_p.add_argument("--jsonl", action="store_true", help="Treat input as JSONL.")
    validate_p.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON reports."
    )
    validate_p.add_argument(
        "--categories",
        default=None,
        help="Comma-separated rule categories (e.g. 'dialect,stem').",
    )
    validate_p.add_argument(
        "--noisy", action="store_true", help="Include phonotactic noise rules."
    )
    validate_p.add_argument(
        "--exclude-rules", nargs="*", help="Rule ids to disable (e.g. DIALECT_03)."
    )
    validate_p.add_argument(
        "--exclude-tokens", nargs="*", help="Forbidden tokens to ignore."
    )
    validate_p.add_argument(
        "--allow-phrase", nargs="*", help="Whole phrases allowed (historical quotes)."
    )
    validate_p.add_argument(
        "--use-default-exceptions",
        action="store_true",
        help="Merge the seeded historical DEFAULT_EXCEPTIONS into this run.",
    )
    return parser


def _run_validate(args: argparse.Namespace) -> int:
    registry = _build_exceptions(args)
    categories = _parse_categories(args.categories)
    reports: list[Report] = []

    for source, text in _iter_texts(args.paths, args.jsonl):
        report = validate(
            text,
            source=source,
            categories=categories,
            include_noisy=args.noisy,
            exceptions=registry,
            disabled_rules=args.exclude_rules,
        )
        reports.append(report)

    if args.json:
        payload = {"valid": all(r.is_valid for r in reports), "reports": [r.to_dict() for r in reports]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            print(report)

    return 0 if all(r.is_valid for r in reports) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _run_validate(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
