"""Change detector for incremental learning (Phase 5 §36).

Detects NEW/CHANGED/UNCHANGED/REMOVED records by content hash comparison.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Mapping from source file pattern to canonical table + PK column
SOURCE_TABLE_MAP = {
    "dictionary": ("dictionary", "id"),
    "bible": ("bible_verses", "id"),
    "vocabulary": ("vocabulary", "id"),
    "phrases": ("phrases", "id"),
    "translations": ("translations", "id"),
    "word_usage": ("word_usage", "id"),
    "grammar_patterns": ("grammar_patterns", "id"),
    "word_collocations": ("word_collocations", "id"),
    "training_exercises": ("training_exercises", "id"),
    "proverbs": ("proverbs", "id"),
    "syllable": ("syllable_data", "id"),
}


@dataclass
class ChangeSet:
    """Result of change detection."""
    new: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_incoming(self) -> int:
        return len(self.new) + len(self.changed) + len(self.unchanged)

    @property
    def total_changes(self) -> int:
        return len(self.new) + len(self.changed) + len(self.removed)

    def summary(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
            "removed": len(self.removed),
            "total_incoming": self.total_incoming,
            "total_changes": self.total_changes,
        }


def _compute_content_hash(record: dict[str, Any]) -> str:
    """Compute SHA256 of normalized record (excluding metadata fields)."""
    # Exclude auto-generated/metadata fields from hash
    excluded = {"id", "import_batch_id", "source_file", "version", "imported_at", "created_at", "updated_at", "content_hash", "row_version"}
    normalized = {k: v for k, v in record.items() if k not in excluded}
    # Sort keys for deterministic hash
    serialized = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _get_table_for_source(source_file: str) -> tuple[str, str] | None:
    """Map source filename to (canonical_table, pk_column)."""
    source_lower = source_file.lower()
    for pattern, (table, pk) in SOURCE_TABLE_MAP.items():
        if pattern in source_lower:
            return table, pk
    return None


def _fetch_canonical_hashes(engine: Engine, table: str, pk_col: str) -> dict[str, str]:
    """Fetch existing PK → content_hash mapping from canonical table."""
    with engine.connect() as conn:
        # Check if content_hash column exists
        cols = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        col_names = {c[1] for c in cols}
        
        if "content_hash" not in col_names:
            log.warning("Table %s has no content_hash column; treating all as NEW", table)
            return {}
        
        rows = conn.execute(
            text(f"SELECT {pk_col}, content_hash FROM {table} WHERE content_hash IS NOT NULL")
        ).fetchall()
    return {str(row[0]): row[1] for row in rows}


def _load_jsonl_records(source_file: str) -> list[dict[str, Any]]:
    """Load records from JSONL file."""
    records = []
    with open(source_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("Failed to parse JSONL line: %s", e)
    return records


def detect_changes(
    engine: Engine,
    source_file: str,
    batch_id: str | None = None,
) -> ChangeSet:
    """Detect changes between incoming JSONL and canonical table.

    Args:
        engine: SQLAlchemy engine.
        source_file: Path to JSONL file.
        batch_id: Import batch ID (for tracking).

    Returns:
        ChangeSet with NEW/CHANGED/UNCHANGED/REMOVED records.
    """
    table_info = _get_table_for_source(source_file)
    if not table_info:
        raise ValueError(f"Unknown source file pattern: {source_file}")

    canonical_table, pk_col = table_info
    log.info("Detecting changes for %s -> %s", source_file, canonical_table)

    # Load incoming records
    incoming_records = _load_jsonl_records(source_file)
    if not incoming_records:
        log.warning("No records in %s", source_file)
        return ChangeSet()

    # Compute hashes for incoming
    incoming_by_pk: dict[str, dict[str, Any]] = {}
    incoming_hashes: dict[str, str] = {}
    
    for rec in incoming_records:
        pk_val = str(rec.get(pk_col, ""))
        if not pk_val:
            # Try common PK fields
            for pk_try in ("id", "ref", "word", "headword"):
                if pk_try in rec:
                    pk_val = str(rec[pk_try])
                    break
        
        if not pk_val:
            # No PK - assign temporary
            pk_val = f"_nopk_{len(incoming_by_pk)}"
        
        content_hash = _compute_content_hash(rec)
        rec["_content_hash"] = content_hash
        rec["_pk"] = pk_val
        incoming_by_pk[pk_val] = rec
        incoming_hashes[pk_val] = content_hash

    # Fetch existing hashes
    existing_hashes = _fetch_canonical_hashes(engine, canonical_table, pk_col)

    # Classify
    changeset = ChangeSet()
    seen_pks = set()

    for pk_val, rec in incoming_by_pk.items():
        seen_pks.add(pk_val)
        incoming_hash = rec["_content_hash"]
        existing_hash = existing_hashes.get(pk_val)

        if existing_hash is None:
            changeset.new.append(rec)
        elif existing_hash != incoming_hash:
            rec["_old_hash"] = existing_hash
            changeset.changed.append(rec)
        else:
            changeset.unchanged.append(rec)

    # Detect removed (in canonical but not in incoming)
    for pk_val, old_hash in existing_hashes.items():
        if pk_val not in seen_pks:
            changeset.removed.append({"_pk": pk_val, "_old_hash": old_hash})

    log.info("Change detection: %s", changeset.summary())
    return changeset
