#!/usr/bin/env python3
"""ZVS 2018 content scanner with regression-gated CI mode.

Scans repository text content (wiki Markdown files and/or corpus JSONL
records) against the ZVS 2018 validator and writes a non-blocking report, and
optionally enforces a **regression gate** against a committed baseline.

This is a **report-only** tool by default:

- It NEVER writes to any source file (wiki / corpus content is read-only).
- It exits ``0`` ALWAYS, even when violations are found — scans are
  informational and non-blocking for CI.
- Only the **contents** of files are scanned. File names and directory names
  are never tokenized (so a file named ``bawipa.md`` cannot trip a rule).

With ``--gate`` it becomes a regression check:

- A baseline file (committed, e.g. ``report/zvs-baseline.json``) is loaded.
  It is a flat dict keyed by ``source\\u0000rule_id\\u0000forbidden`` -> count.
- The gate exits ``1`` **only** when a combo is NEW or its current count
  exceeds the baseline count. Existing counts never fail, so vocabulary /
  generated / stem-mapping reference content is auto-excluded by having been
  captured in the baseline — with zero per-source classification code.
- The gate exits ``0`` when every combo is present at or below its baseline.

A ``reference`` triage bucket is assigned (for the human summary only) to
lexicographic reference content: paths under ``vocabulary/`` /
``vocabulary/generated/``, ``*_auto.md`` files, and the stem-mapping tables.
This bucket is purely cosmetic and is **never** consulted by the gate.

Usage::

    python scripts/zvs/scan_content.py --wiki                 # wiki scan (default on)
    python scripts/zvs/scan_content.py --corpus               # corpus scan (local/on-demand)
    python scripts/zvs/scan_content.py --wiki --corpus        # both
    python scripts/zvs/scan_content.py --wiki --write-baseline report/zvs-baseline.json
    python scripts/zvs/scan_content.py --wiki --gate --baseline report/zvs-baseline.json

Outputs (written to the repo ``report/`` dir):

- ``zvs-scan-<iso-date>.json``  — full machine-readable result.
- ``zvs-scan-summary.md``       — human summary (counts + triage buckets).
- ``zvs-baseline.json``         — committed baseline (only with --write-baseline).

If the wiki checkout is not present (e.g. a fresh zolai-core checkout), a
clear warning is printed and (in default report-only mode) the script still
exits ``0``. In ``--gate`` mode a missing wiki or missing baseline is a hard
``1`` because the regression gate cannot be validated.
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
DEFAULT_BASELINE = REPORT_DIR / "zvs-baseline.json"

# Field names to read from JSONL records (mirrors the zolai-zvs CLI).
_JSONL_FIELDS = ("text", "zolai", "sentence", "corrected", "original")

# Composite-key separator between source / rule_id / forbidden.
_KEY_SEP = "\u0000"

# Triage buckets used in the summary.
_BUCKET_REFERENCE = "reference"  # lexicographic reference — never gates.
_BUCKET_EXCEPTION = "exception"
_BUCKET_FIX = "fix"
_BUCKET_IGNORE = "ignore"
_BUCKET_FALSE_POSITIVE = "false-positive"
_ALL_BUCKETS = (
    _BUCKET_FIX,
    _BUCKET_REFERENCE,
    _BUCKET_EXCEPTION,
    _BUCKET_IGNORE,
    _BUCKET_FALSE_POSITIVE,
)

# Reference content markers used only for the human summary (never for gating).
_REFERENCE_MARKERS = ("vocabulary/", "_auto.md", "forbidden_stems_auto.md")


def _is_reference_source(source: str) -> bool:
    """True for lexicographic/auto reference sources (cosmetic bucket only).

    Matches vocabulary/, vocabulary/generated/, ``*_auto.md`` files, and the
    auto-joined stem-mapping table ``grammar/forbidden_stems_auto.md``.
    """
    normalized = source.replace("\\", "/")
    for marker in _REFERENCE_MARKERS:
        if marker in normalized:
            return True
    return False


def _triage_bucket(report: Report, v) -> str:
    """Classify a single violation into a triage bucket."""
    # The stem-mapping section of 03_negation_particles.md is a reference
    # table; its STEM_* rows are bucketed as reference (cosmetic only).
    if report.source.replace("\\", "/").endswith(
        "bundle/03_negation_particles.md"
    ) and v.rule_id.startswith("STEM_"):
        return _BUCKET_REFERENCE
    if _is_reference_source(report.source):
        return _BUCKET_REFERENCE
    if v.category == "phonotactic":
        return _BUCKET_FALSE_POSITIVE
    if v.forbidden.strip().lower() in DEFAULT_EXCEPTIONS.tokens:
        return _BUCKET_EXCEPTION
    if v.preferred is None:
        return _BUCKET_IGNORE
    return _BUCKET_FIX


def _rel_source(source: str) -> str:
    """Normalize an absolute scan source to a repo-stable relative path.

    Baseline keys must be portable across machines (local vs CI), so absolute
    source paths are stripped back to the wiki/corpus root.
    """
    normalized = source.replace("\\", "/")
    for root in (DEFAULT_WIKI, DEFAULT_CORPUS):
        prefix = str(root).replace("\\", "/").rstrip("/") + "/"
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
        if normalized == str(root):
            return ""
    return normalized


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
    *,
    gated: bool,
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
    if gated:
        lines.append(
            "> Regression-gated scan — exits 1 only on NEW or increased "
            "violations vs the committed baseline."
        )
    else:
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


def _write_baseline(composite_counts: Counter, dest: Path) -> None:
    """Write a committed baseline as a flat ``key -> count`` JSON dict."""
    payload: dict[str, int] = {
        str(key): int(count) for key, count in sorted(composite_counts.items())
    }
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _load_baseline(path: Path) -> dict[str, int]:
    """Load a baseline JSON dict, tolerating a missing/empty file.

    A missing baseline is reported as empty; the caller decides hard-fail.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"baseline {path} is not a JSON object")
    return {str(k): int(v) for k, v in raw.items()}


def _check_gate(composite_counts: Counter, baseline: dict[str, int]) -> list[tuple[str, int, int]]:
    """Return ``(key, baseline_count, current_count)`` for violations to flag.

    A combo is flagged when it is NEW (baseline_count == 0) or its current
    count exceeds the baseline count. Existing combos never flag.
    """
    flagged: list[tuple[str, int, int]] = []
    for key, current_count in sorted(composite_counts.items()):
        base_count = int(baseline.get(key, 0))
        if base_count == 0 or current_count > base_count:
            flagged.append((key, base_count, current_count))
    return flagged


def scan(
    wiki: bool,
    corpus: bool,
    *,
    gate: bool = False,
    baseline_file: Path | None = None,
    write_baseline_file: Path | None = None,
) -> int:
    """Run the scan and (optionally) the regression gate.

    Returns 0 on success (or no gate trip); 1 when the gate trips, the gate
    cannot be validated (missing wiki/baseline), or writing fails.
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    rules_by_id: dict[str, Counter] = defaultdict(Counter)
    buckets: Counter = Counter()
    file_violations: Counter = Counter()
    composite_counts: Counter = Counter()

    scan_contexts = []

    if wiki:
        wiki_dir = DEFAULT_WIKI
        if not wiki_dir.is_dir():
            print(
                f"[zvs-scan] WARNING: wiki source not found at {wiki_dir}. "
                "Skipping wiki scan (fresh checkout?).",
                file=sys.stderr,
            )
        else:
            scan_contexts.append(("wiki", wiki_dir, _iter_wiki_texts(wiki_dir)))

    if corpus:
        corpus_dir = DEFAULT_CORPUS
        if not corpus_dir.is_dir():
            print(
                f"[zvs-scan] WARNING: corpus not found at {corpus_dir}. "
                "Skipping corpus scan.",
                file=sys.stderr,
            )
        else:
            scan_contexts.append(("corpus", corpus_dir, _iter_corpus_texts(corpus_dir)))

    for _kind, _root, texts in scan_contexts:
        for source, text in texts:
            report = validate(text, source=source)
            _accumulate(
                results,
                report,
                rules_by_id,
                buckets,
                file_violations,
                composite_counts,
            )

    if write_baseline_file is not None:
        resolved = Path(write_baseline_file)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        _write_baseline(composite_counts, resolved)
        print(f"[zvs-scan] wrote baseline {resolved}")

    json_dest = REPORT_DIR / f"zvs-scan-{date.today().isoformat()}.json"
    md_dest = REPORT_DIR / "zvs-scan-summary.md"
    top_files = file_violations.most_common(20)

    _write_json(results, json_dest)
    _write_summary(
        results,
        rules_by_id,
        buckets,
        top_files,
        md_dest,
        gated=gate,
    )

    scanned = len(results)
    invalid = sum(1 for r in results if not r["valid"])
    violations = sum(len(r["violations"]) for r in results)
    print(
        f"[zvs-scan] scanned={scanned} invalid={invalid} violations={violations}"
    )
    print(f"[zvs-scan] wrote {json_dest}")
    print(f"[zvs-scan] wrote {md_dest}")

    if not gate:
        return 0

    # ---- Regression gate ----
    if not scan_contexts:
        print(
            "[zvs-scan] GATE: no content sources present to scan "
            "(wiki/corpus missing). Gate cannot be validated -> FAIL.",
            file=sys.stderr,
        )
        return 1

    baseline_path = Path(baseline_file) if baseline_file else DEFAULT_BASELINE
    if not baseline_path.is_file():
        print(
            f"[zvs-scan] GATE: baseline not found at {baseline_path} -> FAIL.",
            file=sys.stderr,
        )
        return 1

    baseline = _load_baseline(baseline_path)

    flagged = _check_gate(composite_counts, baseline)
    if flagged:
        print(f"[zvs-scan] GATE: FAILED — {len(flagged)} new/increased violation(s).")
        print(f"{'key':90} baseline  current")
        for key, base_count, current_count in flagged:
            print(
                f"  {key}{' ' * max(1, 88 - len(key))} {base_count:8} {current_count:8}"
            )
        return 1

    print("[zvs-scan] GATE: PASS — no new or increased violations vs baseline.")
    return 0


def _accumulate(
    results: list[dict],
    report: Report,
    rules_by_id: dict[str, Counter],
    buckets: Counter,
    file_violations: Counter,
    composite_counts: Counter,
) -> None:
    results.append(report.to_dict())
    for v in report.violations:
        rules_by_id[v.rule_id][v.forbidden] += 1
        buckets[_triage_bucket(report, v)] += 1
        file_violations[report.source] += 1
        rel_source = _rel_source(report.source)
        composite_counts[_KEY_SEP.join((rel_source, v.rule_id, v.forbidden))] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_content",
        description=(
            "ZVS 2018 content scanner. Report-only by default; "
            "--gate turns it into a regression check against a baseline."
        ),
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
    parser.add_argument(
        "--gate",
        action="store_true",
        help=(
            "Enforce regression gate: exit 1 iff a (source, rule, token) "
            "combo is NEW or its count exceeds the baseline."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Baseline JSON file (default: report/zvs-baseline.json). "
            "Used with --gate."
        ),
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="Write the current scan counts as a baseline JSON file.",
    )
    args = parser.parse_args(argv)
    return scan(
        wiki=args.wiki,
        corpus=args.corpus,
        gate=args.gate,
        baseline_file=args.baseline if args.gate else None,
        write_baseline_file=args.write_baseline,
    )


if __name__ == "__main__":
    raise SystemExit(main())