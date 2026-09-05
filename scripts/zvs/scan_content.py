#!/usr/bin/env python3
"""Report-only ZVS 2018 content scanner.

Scans repository text content (wiki Markdown files and/or corpus JSONL
records) against the ZVS 2018 validator and writes a non-blocking report.

This is a **report-only** tool:

- It NEVER writes to any source file (wiki / corpus content is read-only).
- It exits ``0`` ALWAYS, even when violations are found — scans are
  informational and non-blocking for CI.
- Only the **contents** of files are scanned. File names and directory names
  are never tokenized (so a file named ``bawipa.md`` cannot trip a rule).

Usage::

    python scripts/zvs/scan_content.py --wiki                # wiki scan (default on)
    python scripts/zvs/scan_content.py --corpus              # corpus scan (local/on-demand)
    python scripts/zvs/scan_content.py --wiki --corpus       # both

Outputs (written to the repo ``report/`` dir):

- ``zvs-scan-<iso-date>.json``  — full machine-readable result.
- ``zvs-scan-summary.md``       — human summary (counts + triage buckets).

If the wiki checkout is not present (e.g. a fresh zolai-core checkout), a
clear warning is printed and the script still exits ``0``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterator

from zolai.zvs import Report, validate
from zolai.zvs.rules_data import DEFAULT_EXCEPTIONS

# Repo root is two levels up from this file (scripts/zvs/... -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WIKI = REPO_ROOT.parent / "zolai-wiki"
DEFAULT_CORPUS = REPO_ROOT.parent / "data" / "corpus"
REPORT_DIR = REPO_ROOT / "report"

# Field names to read from JSONL records (mirrors the zolai-zvs CLI).
_JSONL_FIELDS = ("text", "zolai", "sentence", "corrected", "original")

# Triage buckets used in the summary.
_BUCKET_EXCEPTION = "exception"
_BUCKET_FIX = "fix"
_BUCKET_IGNORE = "ignore"
_BUCKET_FALSE_POSITIVE = "false-positive"
_ALL_BUCKETS = (
    _BUCKET_FIX,
    _BUCKET_EXCEPTION,
    _BUCKET_IGNORE,
    _BUCKET_FALSE_POSITIVE,
)


def _triage_bucket(report: Report, v) -> str:
    """Classify a single violation into a triage bucket."""
    if v.category == "phonotactic":
        return _BUCKET_FALSE_POSITIVE
    if v.forbidden.strip().lower() in DEFAULT_EXCEPTIONS.tokens:
        return _BUCKET_EXCEPTION
    if v.preferred is None:
        return _BUCKET_IGNORE
    return _BUCKET_FIX


def _iter_wiki_texts(wiki_dir: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(source, content)`` pairs for every Markdown file in the wiki.

    Only the file **contents** are yielded; names/dirs are never scanned.
    """
    for path in sorted(wiki_dir.rglob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        yield str(path), content


def _iter_corpus_texts(corpus_dir: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(source, text)`` pairs from JSONL records."""
    for path in sorted(corpus_dir.rglob("*.jsonl")):
        try:
            with path.open(encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        for field in _JSONL_FIELDS:
                            value = record.get(field)
                            if isinstance(value, str) and value:
                                source = f"{path}:{lineno}[{field}]"
                                yield source, value
        except OSError:
            continue


def _write_json(results: list[dict], dest: Path) -> None:
    payload = {
        "generated": date.today().isoformat(),
        "report_only": True,
        "total_sources": len(results),
        "total_invalid": sum(1 for r in results if not r["valid"]),
        "total_violations": sum(len(r["violations"]) for r in results),
        "results": results,
    }
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_summary(
    results: list[dict],
    rules_by_id: dict[str, Counter],
    buckets: Counter,
    top_files: list[tuple[str, int]],
    dest: Path,
) -> None:
    total_invalid = sum(1 for r in results if not r["valid"])
    total_violations = sum(len(r["violations"]) for r in results)
    lines: list[str] = []
    lines.append("# ZVS 2018 Content Scan Summary")
    lines.append("")
    lines.append(f"- Generated: `{date.today().isoformat()}`")
    lines.append(f"- Sources scanned: **{len(results)}**")
    lines.append(f"- Sources with violations: **{total_invalid}**")
    lines.append(f"- Total violations: **{total_violations}**")
    lines.append("")
    lines.append("> Report-only scan — non-blocking. Exit code is always 0.")
    lines.append("")

    lines.append("## Violations by rule")
    lines.append("")
    lines.append("| rule_id | forbidden | count |")
    lines.append("|---|---|---|")
    history: list[tuple[str, str, int]] = []
    for rule_id, counter in rules_by_id.items():
        for forbidden, count in counter.items():
            history.append((rule_id, forbidden, count))
    for rule_id, forbidden, count in sorted(history, key=lambda row: -row[2]):
        lines.append(f"| `{rule_id}` | `{forbidden}` | {count} |")
    lines.append("")

    lines.append("## Violations by forbidden token")
    lines.append("")
    forbidden_counter: Counter = Counter()
    for counter in rules_by_id.values():
        forbidden_counter.update(counter)
    lines.append("| forbidden token | count |")
    lines.append("|---|---|")
    for token, count in sorted(forbidden_counter.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{token}` | {count} |")
    lines.append("")

    lines.append("## Triage buckets")
    lines.append("")
    lines.append("| bucket | count |")
    lines.append("|---|---|")
    for bucket in _ALL_BUCKETS:
        lines.append(f"| {bucket} | {buckets[bucket]} |")
    lines.append("")

    lines.append("## Top files by violation count")
    lines.append("")
    lines.append("| source | violations |")
    lines.append("|---|---|")
    for source, count in top_files:
        lines.append(f"| `{source}` | {count} |")
    lines.append("")

    dest.write_text("\n".join(lines), encoding="utf-8")


def scan(wiki: bool, corpus: bool) -> int:
    """Run the report-only scan and write outputs. Always returns 0."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    rules_by_id: dict[str, Counter] = defaultdict(Counter)
    buckets: Counter = Counter()
    file_violations: Counter = Counter()

    if wiki:
        wiki_dir = DEFAULT_WIKI
        if not wiki_dir.is_dir():
            print(
                f"[zvs-scan] WARNING: wiki source not found at {wiki_dir}. "
                "Skipping wiki scan (fresh checkout?).",
                file=sys.stderr,
            )
        else:
            for source, text in _iter_wiki_texts(wiki_dir):
                report = validate(text, source=source)
                _accumulate(results, report, rules_by_id, buckets, file_violations)

    if corpus:
        corpus_dir = DEFAULT_CORPUS
        if not corpus_dir.is_dir():
            print(
                f"[zvs-scan] WARNING: corpus not found at {corpus_dir}. "
                "Skipping corpus scan.",
                file=sys.stderr,
            )
        else:
            for source, text in _iter_corpus_texts(corpus_dir):
                report = validate(text, source=source)
                _accumulate(results, report, rules_by_id, buckets, file_violations)

    json_dest = REPORT_DIR / f"zvs-scan-{date.today().isoformat()}.json"
    md_dest = REPORT_DIR / "zvs-scan-summary.md"
    top_files = file_violations.most_common(20)

    _write_json(results, json_dest)
    _write_summary(results, rules_by_id, buckets, top_files, md_dest)

    scanned = len(results)
    invalid = sum(1 for r in results if not r["valid"])
    violations = sum(len(r["violations"]) for r in results)
    print(
        f"[zvs-scan] scanned={scanned} invalid={invalid} violations={violations}"
    )
    print(f"[zvs-scan] wrote {json_dest}")
    print(f"[zvs-scan] wrote {md_dest}")
    return 0


def _accumulate(
    results: list[dict],
    report: Report,
    rules_by_id: dict[str, Counter],
    buckets: Counter,
    file_violations: Counter,
) -> None:
    results.append(report.to_dict())
    for v in report.violations:
        rules_by_id[v.rule_id][v.forbidden] += 1
        buckets[_triage_bucket(report, v)] += 1
        file_violations[report.source] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_content",
        description="Report-only ZVS 2018 content scanner (non-blocking).",
    )
    parser.add_argument(
        "--wiki",
        dest="wiki",
        action="store_true",
        default=True,
        help="Scan wiki Markdown contents (default on).",
    )
    parser.add_argument(
        "--corpus",
        action="store_true",
        help="Scan local corpus JSONL records (on-demand).",
    )
    args = parser.parse_args(argv)
    return scan(wiki=args.wiki, corpus=args.corpus)


if __name__ == "__main__":
    raise SystemExit(main())