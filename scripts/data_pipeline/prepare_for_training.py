#!/usr/bin/env python3
"""
Prepare Master Dataset for LLM Training
Generates training-ready formats and statistics
Run: python3 prepare_for_training.py

The manifest describes files that actually exist: every declared split is
counted and hashed, and a missing split fails the run instead of reporting a
fabricated ``PASSED``.
"""
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from zolai.trainer.manifest import content_version  # noqa: E402

DATA = ROOT / "data"
TRAIN_FILE = DATA / "training/master_train_complete.jsonl"
LOG = DATA / "training/prepare_training.log"


def split_facts(path: Path) -> dict:
    """Row count + sha256 of a split file, or ``exists: False``."""
    if not path.exists():
        return {"exists": False}
    digest = hashlib.sha256()
    rows = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
            rows += chunk.count(b"\n")
    return {
        "exists": True,
        "records": rows,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }

def log(msg):
    print(msg, flush=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")

LOG.parent.mkdir(parents=True, exist_ok=True)
LOG.write_text("")
log("\n" + "="*80)
log("PREPARING DATASET FOR TRAINING")
log("="*80 + "\n")

# Stage 1: Analyze dataset
log("[1/3] ANALYZING DATASET...\n")

stats = {
    "total_records": 0,
    "total_chars": 0,
    "total_tokens_est": 0,
    "format_types": Counter(),
    "sources": Counter(),
    "avg_length": 0,
    "min_length": float('inf'),
    "max_length": 0,
}

sample_records = []

try:
    with open(TRAIN_FILE, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            rec = json.loads(line)
            stats["total_records"] += 1

            # Extract text
            text = (rec.get("text") or rec.get("output") or
                   rec.get("zolai") or rec.get("headword") or "")
            text = str(text).strip()

            # Stats
            text_len = len(text)
            stats["total_chars"] += text_len
            stats["total_tokens_est"] += len(text.split())
            stats["min_length"] = min(stats["min_length"], text_len)
            stats["max_length"] = max(stats["max_length"], text_len)

            # Format
            if "instruction" in rec:
                stats["format_types"]["instruction"] += 1
            elif "text" in rec:
                stats["format_types"]["corpus"] += 1
            elif "headword" in rec:
                stats["format_types"]["dictionary"] += 1
            elif "zolai" in rec:
                stats["format_types"]["parallel"] += 1

            # Source (consolidation writes ``source_file`` + ``source_line`` so a
            # row can be traced back; legacy rows carry ``source``).
            source = rec.get("source") or rec.get("source_file") or "unknown"
            stats["sources"][source] += 1

            # Sample
            if i < 5:
                sample_records.append(rec)

            if (i + 1) % 100000 == 0:
                log(f"  Analyzed: {i+1:,} records")

except Exception as e:
    log(f"ERROR: {e}")

stats["avg_length"] = stats["total_chars"] / stats["total_records"] if stats["total_records"] > 0 else 0

if stats["total_records"] == 0:
    log(f"ERROR: {TRAIN_FILE} is missing or empty — refusing to write a manifest")
    raise SystemExit(1)

log(f"Total records: {stats['total_records']:,}")
log(f"Total characters: {stats['total_chars']:,}")
log(f"Estimated tokens: {stats['total_tokens_est']:,}")
log(f"Avg text length: {stats['avg_length']:.0f} chars")
log(f"Min/Max length: {stats['min_length']}/{stats['max_length']} chars\n")

log("Format distribution:")
for fmt, count in stats["format_types"].most_common():
    pct = 100 * count / stats["total_records"]
    log(f"  • {fmt:15} {count:>10,} ({pct:5.1f}%)")

log("\nTop 10 sources:")
for source, count in stats["sources"].most_common(10):
    pct = 100 * count / stats["total_records"]
    log(f"  • {source:30} {count:>10,} ({pct:5.1f}%)")

# Stage 2: Create training manifest
log("\n[2/3] CREATING TRAINING MANIFEST...\n")

manifest = {
    "dataset_name": "Zolai Master Dataset - Complete",
    "version": "1.0",
    "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "input": {"path": TRAIN_FILE.name, **split_facts(TRAIN_FILE)},
    "statistics": {
        "total_records": stats["total_records"],
        "total_characters": stats["total_chars"],
        "estimated_tokens": stats["total_tokens_est"],
        "average_text_length": round(stats["avg_length"], 2),
        "min_text_length": stats["min_length"],
        "max_text_length": stats["max_length"],
    },
    "format_distribution": dict(stats["format_types"]),
    "top_sources": dict(stats["sources"].most_common(10)),
    "files": {
        "train": split_facts(DATA / "training/master_train_complete.jsonl"),
        "val": split_facts(DATA / "training/master_val_complete.jsonl"),
        "test": split_facts(DATA / "training/master_test_complete.jsonl"),
    },
    "recommendations": [
        "Use master_train_complete.jsonl for training",
        "Use master_val_complete.jsonl for validation",
        "Use master_test_complete.jsonl for final evaluation",
        "Estimated training time: 2-4 hours on GPU",
        "Recommended batch size: 8-16",
        "Recommended learning rate: 2e-5 to 5e-5",
    ]
}

# Content-addressed version: stable across regenerations of unchanged data,
# different as soon as a split's bytes change or a split disappears.
manifest["dataset_version"] = content_version(manifest["files"])

# Quality is derived, never asserted: a split that is absent or empty makes the
# dataset not-ready and fails the run.
missing = [name for name, facts in manifest["files"].items() if not facts.get("records")]
manifest["quality"] = {
    "validation_status": "FAILED" if missing else "PASSED",
    "missing_splits": missing,
    "ready_for_training": not missing,
}

manifest_file = DATA / "training/training_manifest.json"
with open(manifest_file, "w") as f:
    json.dump(manifest, f, indent=2)

log(f"✓ Created: {manifest_file.name}")
if missing:
    log(f"ERROR: missing or empty splits: {', '.join(missing)}")
    raise SystemExit(1)

# Stage 3: Sample records
log("\n[3/3] SAMPLE RECORDS...\n")

sample_file = DATA / "training/sample_records.jsonl"
with open(sample_file, "w") as f:
    for rec in sample_records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

log(f"✓ Created: {sample_file.name} (5 sample records)")

# Summary
log("\n" + "="*80)
log("✅ TRAINING PREPARATION COMPLETE")
log("="*80)
log("\nGenerated files:")
log("  • training_manifest.json - Dataset metadata & recommendations")
log("  • sample_records.jsonl - 5 sample records for inspection")
log("\nReady to train! Next steps:")
log("  1. Review: cat data/training/training_manifest.json")
log("  2. Inspect samples: head -5 data/training/sample_records.jsonl")
log("  3. Start training with master_train_complete.jsonl")
log("="*80 + "\n")

print("\n✅ Preparation complete!")
print("Log: data/training/prepare_training.log")
