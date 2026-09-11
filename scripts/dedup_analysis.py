#!/usr/bin/env python3
"""Dictionary deduplication analysis for Zolai dictionary files.

Compares dict_zo_en_master_v1.jsonl (ZO→EN, 93K) with
dict_canonical_clean.jsonl (EN→ZO, 112K) to find overlapping
headwords, report statistics, and optionally merge.

Usage:
    python scripts/dedup_analysis.py --report   # dry-run analysis
    python scripts/dedup_analysis.py --fix      # merge duplicates
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
MASTER_PATH = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_master_v1.jsonl"
CANONICAL_PATH = DATA_DIR / "dictionary" / "processed" / "dict_canonical_clean.jsonl"
REPORT_PATH = DATA_DIR / "dictionary" / "processed" / "dedup_report.json"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DedupReport:
    """Statistics and details from dedup analysis."""

    master_total: int = 0
    canonical_total: int = 0
    overlapping: int = 0
    unique_master: int = 0
    unique_canonical: int = 0
    sample_overlaps: list[dict[str, Any]] = field(default_factory=list)
    unique_master_samples: list[dict[str, Any]] = field(default_factory=list)
    unique_canonical_samples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "master_total": self.master_total,
            "canonical_total": self.canonical_total,
            "overlapping": self.overlapping,
            "unique_master": self.unique_master,
            "unique_canonical": self.unique_canonical,
            "overlap_pct_of_master": round(
                self.overlapping / self.master_total * 100, 2
            )
            if self.master_total
            else 0,
            "overlap_pct_of_canonical": round(
                self.overlapping / self.canonical_total * 100, 2
            )
            if self.canonical_total
            else 0,
            "sample_overlaps": self.sample_overlaps[:20],
            "unique_master_samples": self.unique_master_samples[:20],
            "unique_canonical_samples": self.unique_canonical_samples[:20],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_headword(entry: dict[str, Any]) -> str | None:
    """Extract headword from an entry, handling both schemas."""
    for key in ("zolai", "headword"):
        val = entry.get(key)
        if val and isinstance(val, str):
            return val.strip().lower()
    return None


def _get_translations(entry: dict[str, Any]) -> list[str]:
    """Extract translation list from an entry."""
    for key in ("english", "translations"):
        val = entry.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [val]
    return []


def load_dict(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load a JSONL dictionary file, grouped by lowercase headword.

    Returns {headword: [entry, ...]}.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                print(f"WARN: skipping bad JSON at {path.name}:{lineno}",
                      file=sys.stderr)
                continue
            hw = _get_headword(entry)
            if hw:
                grouped.setdefault(hw, []).append(entry)
    return grouped


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def find_duplicates(
    master: dict[str, list[dict]],
    canonical: dict[str, list[dict]],
) -> DedupReport:
    """Find overlapping headwords between two dictionaries."""
    m_keys = set(master)
    c_keys = set(canonical)
    overlap_keys = m_keys & c_keys

    unique_m_keys = m_keys - c_keys
    unique_c_keys = c_keys - m_keys

    report = DedupReport(
        master_total=sum(len(v) for v in master.values()),
        canonical_total=sum(len(v) for v in canonical.values()),
        overlapping=len(overlap_keys),
        unique_master=len(unique_m_keys),
        unique_canonical=len(unique_c_keys),
    )

    # Sample overlaps (first 20)
    for hw in sorted(overlap_keys)[:20]:
        report.sample_overlaps.append({
            "headword": hw,
            "master_entries": len(master[hw]),
            "canonical_entries": len(canonical[hw]),
            "master_translations": _get_translations(master[hw][0]),
            "canonical_translations": _get_translations(canonical[hw][0]),
        })

    # Sample unique-to-master (first 20)
    for hw in sorted(unique_m_keys)[:20]:
        report.unique_master_samples.append({
            "headword": hw,
            "entries": len(master[hw]),
            "translations": _get_translations(master[hw][0]),
        })

    # Sample unique-to-canonical (first 20)
    for hw in sorted(unique_c_keys)[:20]:
        report.unique_canonical_samples.append({
            "headword": hw,
            "entries": len(canonical[hw]),
            "translations": _get_translations(canonical[hw][0]),
        })

    return report


def merge_duplicates(
    master: dict[str, list[dict]],
    canonical: dict[str, list[dict]],
    report: DedupReport,
) -> dict[str, list[dict]]:
    """Merge canonical entries into master.  Master is authoritative.

    For overlapping headwords, canonical entries are appended only when
    they carry translations not already present in master.
    """
    merged: dict[str, list[dict]] = {}
    added = 0
    skipped = 0

    # Start with all master entries
    for hw, entries in master.items():
        merged[hw] = list(entries)

    # Merge canonical entries for overlapping headwords
    m_keys = set(master)
    c_keys = set(canonical)
    overlap_keys = m_keys & c_keys

    for hw in sorted(overlap_keys):
        m_translations = set()
        for entry in master[hw]:
            m_translations.update(
                t.lower() for t in _get_translations(entry) if t
            )

        for entry in canonical[hw]:
            c_translations = [t.lower() for t in _get_translations(entry) if t]
            new_translations = [t for t in c_translations if t not in m_translations]
            if new_translations:
                merged[hw].append(entry)
                m_translations.update(new_translations)
                added += 1
            else:
                skipped += 1

    # Append unique-to-canonical entries
    unique_c_keys = c_keys - m_keys
    for hw in sorted(unique_c_keys):
        merged[hw] = list(canonical[hw])
        added += len(canonical[hw])

    print(f"Merge: {added} canonical entries added, "
          f"{skipped} duplicates skipped")
    return merged


def write_dict(grouped: dict[str, list[dict]], path: Path) -> int:
    """Write merged dictionary back to JSONL.  Returns line count."""
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        for hw in sorted(grouped):
            for entry in grouped[hw]:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                count += 1
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zolai dictionary deduplication analysis"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--report", action="store_true",
        help="Generate dedup report (no file changes)",
    )
    group.add_argument(
        "--fix", action="store_true",
        help="Merge duplicates and write updated canonical file",
    )
    args = parser.parse_args()

    print(f"Loading master:     {MASTER_PATH}")
    master = load_dict(MASTER_PATH)
    print(f"  → {sum(len(v) for v in master.values())} entries, "
          f"{len(master)} unique headwords")

    print(f"Loading canonical:  {CANONICAL_PATH}")
    canonical = load_dict(CANONICAL_PATH)
    print(f"  → {sum(len(v) for v in canonical.values())} entries, "
          f"{len(canonical)} unique headwords")

    report = find_duplicates(master, canonical)

    print(f"\n{'='*50}")
    print("Deduplication Report")
    print(f"{'='*50}")
    print(f"Master entries:     {report.master_total:>8,}")
    print(f"Canonical entries:  {report.canonical_total:>8,}")
    print(f"Overlapping heads:  {report.overlapping:>8,} "
          f"({report.to_dict()['overlap_pct_of_master']:.1f}% of master)")
    print(f"Unique to master:   {report.unique_master:>8,}")
    print(f"Unique to canonical:{report.unique_canonical:>8,}")

    if report.sample_overlaps:
        print("\nSample overlaps (first 5):")
        for s in report.sample_overlaps[:5]:
            print(f"  {s['headword']}: master={s['master_translations'][:2]} "
                  f"canonical={s['canonical_translations'][:2]}")

    # Write report JSON
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)
    print(f"\nReport written to {REPORT_PATH}")

    if args.fix:
        print("\nMerging duplicates (master is authoritative)...")
        merged = merge_duplicates(master, canonical, report)
        total = sum(len(v) for v in merged.values())
        print(f"  → {total} total entries after merge")
        out = CANONICAL_PATH
        written = write_dict(merged, out)
        print(f"  → {written} entries written to {out}")
    else:
        print("\nDry-run only. Use --fix to merge.")


if __name__ == "__main__":
    main()
