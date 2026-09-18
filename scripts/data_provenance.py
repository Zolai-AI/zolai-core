#!/usr/bin/env python3
"""Data provenance tracking for Zolai ecosystem.

Generates a manifest of all data files with metadata and verifies
current files against the manifest to detect drift.
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Data directories to scan (relative to workspace root)
SCAN_DIRS = [
    "data/bible",
    "data/dictionary/processed",
    "data/parallel",
    "data/bible/knowledge_base",
    "data/bible/context",
]

DEFAULT_MANIFEST = "data/provenance.json"


def get_workspace_root() -> Path:
    """Find workspace root by looking for AGENTS.md."""
    current = Path(__file__).parent
    for _ in range(5):
        if (current / "AGENTS.md").exists():
            return current
        current = current.parent
    return Path(__file__).parent.parent


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def count_jsonl_rows(file_path: Path) -> int:
    """Count lines in a JSONL file (non-empty lines)."""
    try:
        with open(file_path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return -1


def scan_data_files(workspace_root: Path) -> list[dict[str, Any]]:
    """Scan all data directories and return file metadata."""
    files = []
    for scan_dir in SCAN_DIRS:
        dir_path = workspace_root / scan_dir
        if not dir_path.exists():
            continue

        for jsonl_file in dir_path.rglob("*.jsonl"):
            rel_path = jsonl_file.relative_to(workspace_root)
            stat = jsonl_file.stat()

            entry = {
                "filename": str(rel_path),
                "size_bytes": stat.st_size,
                "sha256": compute_sha256(jsonl_file),
                "row_count": count_jsonl_rows(jsonl_file),
                "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "source": determine_source(jsonl_file),
                "generator_script": determine_generator(jsonl_file),
            }
            files.append(entry)

    return sorted(files, key=lambda x: x["filename"])


def determine_source(file_path: Path) -> str:
    """Determine the data source based on file location and name."""
    name = file_path.name.lower()
    parts = file_path.parts

    if "bible" in parts:
        if "context" in parts:
            return "bible_context_analysis"
        elif "knowledge_base" in parts:
            return "bible_knowledge_base"
        elif "grammar" in name:
            return "bible_grammar_extraction"
        elif "vocab" in name:
            return "bible_vocabulary_extraction"
        elif "phrase" in name:
            return "bible_phrase_extraction"
        elif "translation" in name:
            return "bible_translation_pairs"
        elif "exercise" in name:
            return "bible_exercise_generation"
        elif "alignment" in name:
            return "bible_word_alignment"
        elif "parallel" in name:
            return "bible_parallel_corpus"
        else:
            return "bible_processing"
    elif "dictionary" in parts:
        if "dalsuum" in name:
            return "dalsuum_dictionary_merge"
        elif "zomidaily" in name:
            return "zomidaily_dictionary_expansion"
        elif "canonical" in name:
            return "canonical_dictionary_clean"
        else:
            return "dictionary_processing"
    elif "parallel" in parts:
        return "parallel_corpus_merge"

    return "unknown_source"


def determine_generator(file_path: Path) -> str:
    """Determine the likely generator script based on file name."""
    name = file_path.name.lower()

    generators = {
        "grammar_patterns": "extract_grammar_patterns.py",
        "vocab_index": "build_vocabulary_db.py",
        "phrases": "extract_phrases.py",
        "translation_pairs": "generate_training_data.py",
        "parallel_corpus": "build_parallel_corpus.py",
        "word_alignments": "align_words.py",
        "negation_exercises": "generate_training_data.py",
        "question_exercises": "generate_training_data.py",
        "pronoun_exercises": "generate_training_data.py",
        "error_correction_exercises": "generate_training_data.py",
        "conditional_exercises": "generate_training_data.py",
        "dict_zo_en": "integrate_dictionary.py",
        "dict_canonical": "build_canonical_dict.py",
        "dict_dalsuum": "integrate_dalsuum.py",
        "dict_bible": "build_bible_dict.py",
        "dict_zomidaily": "extract_zomidaily_vocab.py",
        "per_book": "context_deep_learner.py",
        "per_chapter": "context_deep_learner.py",
        "word_usage": "context_deep_learner.py",
        "phrase_context": "context_deep_learner.py",
        "topic_clusters": "context_deep_learner.py",
        "sentence_patterns": "context_deep_learner.py",
        "book_vocabularies": "context_deep_learner.py",
        "zo_en_pairs": "integrate_parallel_corpus.py",
    }

    for key, script in generators.items():
        if key in name:
            return script

    return "unknown_script"


def generate_manifest(data_dir: str | None = None) -> dict[str, Any]:
    """Generate provenance manifest for all data files."""
    workspace_root = Path(data_dir) if data_dir else get_workspace_root()

    files = scan_data_files(workspace_root)

    manifest = {
        "version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "workspace_root": str(workspace_root),
        "total_files": len(files),
        "total_size_bytes": sum(f["size_bytes"] for f in files),
        "files": files,
    }

    return manifest


def save_manifest(manifest: dict[str, Any], output_path: str | None = None) -> Path:
    """Save manifest to JSON file."""
    workspace_root = get_workspace_root()
    if output_path:
        manifest_path = Path(output_path)
    else:
        manifest_path = workspace_root / DEFAULT_MANIFEST

    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest_path


def verify_manifest(data_dir: str | None = None, manifest_path: str | None = None) -> list[str]:
    """Verify current files against the manifest and return drift warnings."""
    workspace_root = Path(data_dir) if data_dir else get_workspace_root()

    if manifest_path:
        manifest_file = Path(manifest_path)
    else:
        manifest_file = workspace_root / DEFAULT_MANIFEST

    if not manifest_file.exists():
        return [f"Manifest not found: {manifest_file}"]

    with open(manifest_file, encoding="utf-8") as f:
        manifest = json.load(f)

    # Index manifest entries by filename
    manifest_files = {f["filename"]: f for f in manifest.get("files", [])}

    # Scan current files
    current_files = scan_data_files(workspace_root)
    current_filenames = {f["filename"] for f in current_files}
    manifest_filenames = set(manifest_files.keys())

    warnings = []

    # Check for missing files (in manifest but not on disk)
    missing = manifest_filenames - current_filenames
    for filename in sorted(missing):
        warnings.append(f"MISSING: {filename} (in manifest but not on disk)")

    # Check for new files (on disk but not in manifest)
    new_files = current_filenames - manifest_filenames
    for filename in sorted(new_files):
        warnings.append(f"NEW: {filename} (on disk but not in manifest)")

    # Check for modified files (different hash or size)
    for current_file in current_files:
        filename = current_file["filename"]
        if filename in manifest_files:
            manifest_entry = manifest_files[filename]

            if current_file["sha256"] != manifest_entry["sha256"]:
                warnings.append(
                    f"MODIFIED: {filename} "
                    f"(hash changed: {manifest_entry['sha256'][:8]}... → "
                    f"{current_file['sha256'][:8]}...)"
                )

            if current_file["size_bytes"] != manifest_entry["size_bytes"]:
                warnings.append(
                    f"SIZE_CHANGED: {filename} "
                    f"({manifest_entry['size_bytes']} → {current_file['size_bytes']} bytes)"
                )

            if current_file["row_count"] != manifest_entry["row_count"]:
                warnings.append(
                    f"ROWS_CHANGED: {filename} "
                    f"({manifest_entry['row_count']} → {current_file['row_count']} rows)"
                )

    return warnings


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(description="Data provenance tracking for Zolai ecosystem")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate provenance manifest",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify current files against manifest",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Data directory to scan (default: workspace root)",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Manifest file path (default: data/provenance.json)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output (exit code only)",
    )

    args = parser.parse_args()

    if not args.generate and not args.verify:
        parser.print_help()
        sys.exit(1)

    if args.generate:
        manifest = generate_manifest(args.data_dir)
        manifest_path = save_manifest(manifest, args.manifest)

        if not args.quiet:
            print(f"✅ Generated manifest: {manifest_path}")
            print(f"   Total files: {manifest['total_files']}")
            print(f"   Total size: {manifest['total_size_bytes']:,} bytes")

        sys.exit(0)

    if args.verify:
        warnings = verify_manifest(args.data_dir, args.manifest)

        if warnings:
            if not args.quiet:
                print(f"⚠️  Found {len(warnings)} drift warnings:")
                for warning in warnings:
                    print(f"   - {warning}")
            sys.exit(1)
        else:
            if not args.quiet:
                print("✅ Manifest verification passed - no drift detected")
            sys.exit(0)


if __name__ == "__main__":
    main()
