#!/usr/bin/env python3
"""Streaming data audit for /data — validates JSONL schemas, detects issues.

Usage:
    python scripts/data_audit.py                         # audit all JSONL files
    python scripts/data_audit.py --dir dictionary        # audit one subdirectory
    python scripts/data_audit.py --file dict_unified_v1.jsonl  # audit one file
    python scripts/data_audit.py --report-only           # skip per-line error details
    python scripts/data_audit.py --no-duplicate-check    # skip duplicate detection (saves memory)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ── Configuration ────────────────────────────────────────────────────────────

DATA_ROOT = Path(os.environ.get("ZOLAI_DATA_ROOT", os.path.join(Path(__file__).resolve().parent.parent.parent, "data")))

REPLACEMENT_CHAR = "\ufffd"
HTML_TAGS = re.compile(r"<(?:html|div|script|table|span|body|head)\b", re.IGNORECASE)

# Required fields per schema (field must exist AND be non-empty)
SCHEMAS: dict[str, list[str]] = {
    "dictionary_unified": ["headword"],
    "dictionary_enriched": ["zolai", "english"],
    "dictionary_semantic": ["zolai", "english"],
    "parallel_zo_en": ["zolai", "english"],
    "bible_parallel": ["instruction", "input", "output"],
    "corpus": ["text"],
}

# Duplicate detection key fields per schema
DUPLICATE_KEYS: dict[str, list[str]] = {
    "dictionary_unified": ["headword"],
    "dictionary_enriched": ["zolai", "english"],
    "dictionary_semantic": ["zolai", "english"],
    "parallel_zo_en": ["zolai", "english"],
    "bible_parallel": ["instruction", "input", "output"],
    "corpus": ["text"],
}

# Filename pattern → schema key mapping (checked in order)
FILE_PATTERNS: list[tuple[str, str]] = [
    (r"dict_unified_", "dictionary_unified"),
    (r"dict_enriched_", "dictionary_enriched"),
    (r"dict_semantic_", "dictionary_semantic"),
    (r"dict_.*_v\d", "dictionary_unified"),  # generic dict files
    (r"dict_.*\.jsonl", "dictionary_unified"),
    (r"zvs_final_master_dictionary", "dictionary_enriched"),
    (r"bible_parallel_", "bible_parallel"),
    (r"zo_en_pairs_", "parallel_zo_en"),
    (r"corpus_unified_", "corpus"),
    (r"corpus_", "corpus"),
]


# ── Core streaming ───────────────────────────────────────────────────────────

def stream_jsonl(filepath: Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    """Yield (line_number, parsed_dict_or_None, error_or_None) for each line."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if not isinstance(obj, dict):
                        yield i, None, f"Line {i}: not a JSON object (got {type(obj).__name__})"
                        continue
                    yield i, obj, None
                except json.JSONDecodeError as e:
                    yield i, None, f"Line {i}: JSON parse error — {e}"
    except OSError as e:
        yield 0, None, f"File error: {e}"


def detect_schema(filepath: Path) -> str | None:
    """Detect which schema applies to a file based on filename patterns."""
    name = filepath.name
    for pattern, schema_key in FILE_PATTERNS:
        if re.search(pattern, name):
            return schema_key
    return None


def check_encoding(text: str) -> list[str]:
    """Check for encoding issues in a string."""
    issues = []
    if REPLACEMENT_CHAR in text:
        issues.append(f"replacement_char count: {text.count(REPLACEMENT_CHAR)}")
    if HTML_TAGS.search(text):
        issues.append("html_contamination")
    return issues


def check_empty_fields(obj: dict, required: list[str]) -> list[str]:
    """Check for empty/null required fields."""
    issues = []
    for field in required:
        val = obj.get(field)
        if val is None:
            issues.append(f"missing:{field}")
        elif isinstance(val, str) and not val.strip():
            issues.append(f"empty:{field}")
        elif isinstance(val, list) and len(val) == 0:
            issues.append(f"empty_list:{field}")
    return issues


def make_duplicate_key(obj: dict, key_fields: list[str]) -> str | None:
    """Create a hashable key for duplicate detection."""
    parts = []
    for field in key_fields:
        val = obj.get(field)
        if val is None:
            return None
        if isinstance(val, list):
            parts.append(json.dumps(val, sort_keys=True))
        else:
            parts.append(str(val))
    return hashlib.md5("|".join(parts).encode()).hexdigest()


# ── Per-file audit ───────────────────────────────────────────────────────────

def audit_file(filepath: Path, *, report_only: bool = False, check_duplicates: bool = True) -> dict[str, Any]:
    """Audit a single JSONL file. Returns a report dict."""
    schema_key = detect_schema(filepath)
    required = SCHEMAS.get(schema_key, []) if schema_key else []
    dup_keys = DUPLICATE_KEYS.get(schema_key, []) if schema_key else []

    file_size = filepath.stat().st_size
    line_count = 0
    valid_lines = 0
    error_lines = 0
    empty_field_lines = 0
    encoding_issues = 0
    duplicate_count = 0
    errors: list[str] = []
    seen_hashes: set[str] = set()

    for line_num, obj, err in stream_jsonl(filepath):
        if err:
            error_lines += 1
            if not report_only:
                errors.append(err)
            continue

        line_count += 1
        valid_lines += 1

        # Check required fields
        field_issues = check_empty_fields(obj, required)
        if field_issues:
            empty_field_lines += 1
            if not report_only:
                errors.append(f"Line {line_num}: {'; '.join(field_issues)}")

        # Check encoding on text fields
        for val in obj.values():
            if isinstance(val, str):
                enc = check_encoding(val)
                if enc:
                    encoding_issues += 1
                    if not report_only:
                        errors.append(f"Line {line_num}: encoding — {'; '.join(enc)}")
                    break

        # Check duplicates
        if check_duplicates and dup_keys:
            key = make_duplicate_key(obj, dup_keys)
            if key:
                if key in seen_hashes:
                    duplicate_count += 1
                    if not report_only:
                        errors.append(f"Line {line_num}: duplicate")
                else:
                    seen_hashes.add(key)

    return {
        "file": str(filepath.relative_to(DATA_ROOT)),
        "schema": schema_key or "unknown",
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "line_count": line_count,
        "valid_lines": valid_lines,
        "error_lines": error_lines,
        "empty_field_lines": empty_field_lines,
        "encoding_issues": encoding_issues,
        "duplicate_count": duplicate_count,
        "status": "PASS" if error_lines == 0 else "FAIL",
        "errors": errors[:100],  # cap at 100 per file
        "errors_total": len(errors),
    }


# ── Directory audit ──────────────────────────────────────────────────────────

def find_jsonl_files(root: Path) -> list[Path]:
    """Find all .jsonl files under root, sorted by size."""
    files = list(root.rglob("*.jsonl"))
    files.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return files


def audit_directory(dirpath: Path, *, report_only: bool = False, check_duplicates: bool = True) -> dict[str, Any]:
    """Audit all JSONL files in a directory."""
    files = find_jsonl_files(dirpath)
    results = []
    for f in files:
        print(f"  Auditing: {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)", file=sys.stderr)
        result = audit_file(f, report_only=report_only, check_duplicates=check_duplicates)
        results.append(result)
    return {
        "directory": str(dirpath.relative_to(DATA_ROOT.parent)),
        "files_audited": len(results),
        "files_passed": sum(1 for r in results if r["status"] == "PASS"),
        "files_failed": sum(1 for r in results if r["status"] == "FAIL"),
        "results": results,
    }


def audit_all(data_root: Path, *, report_only: bool = False, check_duplicates: bool = True) -> dict[str, Any]:
    """Audit all data directories."""
    subdirs = [d for d in data_root.iterdir() if d.is_dir() and d.name != "audit"]
    all_results = []
    for d in sorted(subdirs):
        print(f"\nScanning: {d.name}/", file=sys.stderr)
        result = audit_directory(d, report_only=report_only, check_duplicates=check_duplicates)
        all_results.append(result)

    # Also audit root-level JSONL files
    root_files = [f for f in data_root.glob("*.jsonl")]
    if root_files:
        print("\nScanning: root-level files", file=sys.stderr)
        for f in root_files:
            print(f"  Auditing: {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)", file=sys.stderr)
            result = audit_file(f, report_only=report_only, check_duplicates=check_duplicates)
            all_results.append({"directory": "root", "files_audited": 1, "files_passed": 1 if result["status"] == "PASS" else 0, "files_failed": 1 if result["status"] == "FAIL" else 0, "results": [result]})

    total_files = sum(r["files_audited"] for r in all_results)
    total_passed = sum(r["files_passed"] for r in all_results)
    total_failed = sum(r["files_failed"] for r in all_results)

    return {
        "audit_time": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_root),
        "total_files": total_files,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "directories": all_results,
    }


# ── Reporting ────────────────────────────────────────────────────────────────

def format_table(report: dict) -> str:
    """Format a human-readable summary table."""
    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"  ZOLAI DATA AUDIT — {report.get('audit_time', 'N/A')}")
    lines.append(f"  Data root: {report.get('data_root', 'N/A')}")
    lines.append(f"{'='*80}\n")

    for dir_result in report.get("directories", []):
        dir_name = dir_result.get("directory", "?")
        files = dir_result.get("results", [])
        if not files:
            continue
        lines.append(f"  [{dir_name}] ({dir_result['files_passed']}/{dir_result['files_audited']} passed)")
        lines.append(f"  {'File':<45} {'Schema':<20} {'Lines':>8} {'Errors':>8} {'Dups':>6} {'Status':>6}")
        lines.append(f"  {'-'*45} {'-'*20} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")
        for r in files:
            name = Path(r["file"]).name[:44]
            lines.append(
                f"  {name:<45} {(r['schema'] or '?'):<20} {r['line_count']:>8,} "
                f"{r['error_lines']:>8,} {r['duplicate_count']:>6,} {r['status']:>6}"
            )
        lines.append("")

    total = report.get("total_files", 0)
    passed = report.get("total_passed", 0)
    failed = report.get("total_failed", 0)
    lines.append(f"  TOTAL: {total} files — {passed} passed, {failed} failed")
    lines.append(f"{'='*80}\n")

    # Show failed file details
    if failed > 0:
        lines.append("  FAILED FILES:")
        for dir_result in report.get("directories", []):
            for r in dir_result.get("results", []):
                if r["status"] == "FAIL":
                    lines.append(f"\n  ❌ {r['file']} ({r['errors_total']} errors)")
                    for err in r.get("errors", [])[:5]:
                        lines.append(f"     {err}")
                    if r["errors_total"] > 5:
                        lines.append(f"     ... and {r['errors_total'] - 5} more")
        lines.append("")

    return "\n".join(lines)


def write_report(report: dict, output_dir: Path) -> Path:
    """Write structured JSON report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = output_dir / f"report_{date_str}.json"

    # Strip per-file errors from the JSON report (too verbose)
    clean_report = json.loads(json.dumps(report, default=str))
    for dir_result in clean_report.get("directories", []):
        for r in dir_result.get("results", []):
            if "errors" in r:
                r["errors_preview"] = r["errors"][:3]
                del r["errors"]

    report_path.write_text(json.dumps(clean_report, indent=2, default=str))
    return report_path


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Streaming data audit for /data")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT, help="Data root directory")
    parser.add_argument("--dir", type=str, default=None, help="Audit specific subdirectory (e.g., 'dictionary')")
    parser.add_argument("--file", type=str, default=None, help="Audit specific file (e.g., 'dict_unified_v1.jsonl')")
    parser.add_argument("--report-only", action="store_true", help="Skip per-line error details (faster)")
    parser.add_argument("--no-duplicate-check", action="store_true", help="Skip duplicate detection (saves memory)")
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    if not data_root.exists():
        print(f"Error: data root not found: {data_root}", file=sys.stderr)
        sys.exit(2)

    check_dupes = not args.no_duplicate_check
    report_dir = data_root / "audit"

    if args.file:
        # Single file mode
        filepath = data_root / args.file
        if not filepath.exists():
            # Try searching subdirs
            matches = list(data_root.rglob(args.file))
            if matches:
                filepath = matches[0]
            else:
                print(f"Error: file not found: {args.file}", file=sys.stderr)
                sys.exit(2)
        print(f"Auditing: {filepath.name}", file=sys.stderr)
        result = audit_file(filepath, report_only=args.report_only, check_duplicates=check_dupes)
        report = {
            "audit_time": datetime.now(timezone.utc).isoformat(),
            "data_root": str(data_root),
            "total_files": 1,
            "total_passed": 1 if result["status"] == "PASS" else 0,
            "total_failed": 1 if result["status"] == "FAIL" else 0,
            "directories": [{"directory": "single", "files_audited": 1, "files_passed": 1 if result["status"] == "PASS" else 0, "files_failed": 1 if result["status"] == "FAIL" else 0, "results": [result]}],
        }
    elif args.dir:
        # Directory mode
        dirpath = data_root / args.dir
        if not dirpath.exists():
            print(f"Error: directory not found: {dirpath}", file=sys.stderr)
            sys.exit(2)
        report = {
            "audit_time": datetime.now(timezone.utc).isoformat(),
            "data_root": str(data_root),
        }
        dir_result = audit_directory(dirpath, report_only=args.report_only, check_duplicates=check_dupes)
        report.update({
            "total_files": dir_result["files_audited"],
            "total_passed": dir_result["files_passed"],
            "total_failed": dir_result["files_failed"],
            "directories": [dir_result],
        })
    else:
        # Full audit
        report = audit_all(data_root, report_only=args.report_only, check_duplicates=check_dupes)

    # Print summary
    print(format_table(report))

    # Write report
    report_path = write_report(report, report_dir)
    print(f"Report written to: {report_path}", file=sys.stderr)

    # Exit code
    sys.exit(1 if report.get("total_failed", 0) > 0 else 0)


if __name__ == "__main__":
    main()
