"""Knowledge versioning & snapshots (Phase 4 §36).

Creates versioned knowledge artifacts with manifests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.data.repositories.knowledge import KnowledgeVersionRepository

log = logging.getLogger(__name__)

# Canonical tables that participate in versioning
CANONICAL_TABLES = [
    "dictionary",
    "dictionary_en_zo",
    "bible_verses",
    "vocabulary",
    "phrases",
    "translations",
    "word_usage",
    "grammar_patterns",
    "word_collocations",
    "word_alignments",
    "training_exercises",
    "syllable_data",
    "proverbs",
    "knowledge_claims",
    "hypotheses",
    "foundation_evidence",
    "observations",
    "word_observation_stats",
    "attestation_index",
]

_SHA3_LINE = re.compile(r"^([0-9a-f]{16,})\|")


def _get_table_count(engine: Engine, table: str) -> int:
    """Row count for one canonical table (0 if the table is absent)."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _get_table_row_counts(engine: Engine) -> dict[str, int]:
    """Get row counts for all canonical tables."""
    return {table: _get_table_count(engine, table) for table in CANONICAL_TABLES}


def _count_hash(engine: Engine, table: str) -> str:
    """Count-only fingerprint — the old manifest, kept as the fallback."""
    return hashlib.sha256(f"{table}:{_get_table_count(engine, table)}".encode()).hexdigest()[:16]


def _content_hash(engine: Engine, table: str) -> str | None:
    """sha3 digest of a table's full content, via the ``sqlite3`` CLI ``.sha3sum``.

    Returns ``None`` when the CLI or a file-backed database is unavailable, so
    the caller degrades to count hashing rather than failing the snapshot.
    """
    db_path = engine.url.database
    cli = shutil.which("sqlite3")
    if not db_path or not cli:
        return None
    try:
        proc = subprocess.run(
            [cli, db_path, f".sha3sum {table}"],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("sha3sum unavailable for %s: %s", table, exc)
        return None
    if proc.returncode != 0:
        log.warning("sha3sum failed for %s: %s", table, proc.stderr.strip())
        return None
    for line in proc.stdout.splitlines():
        match = _SHA3_LINE.match(line.strip())
        if match:
            return match.group(1)
    return None


def _get_table_hash(engine: Engine, table: str, *, content: bool = False) -> str:
    """Fingerprint one table: content digest when possible, else row count."""
    if content:
        digest = _content_hash(engine, table)
        if digest:
            return digest
    return _count_hash(engine, table)


def _collect_table_hashes(
    engine: Engine, *, content: bool = True
) -> tuple[dict[str, str], str]:
    """Fingerprints for every canonical table plus the mode actually achieved.

    A counts-only manifest cannot tell two databases with the same row numbers
    apart, so content hashing is the default; ``mode`` reports ``content``,
    ``counts``, or ``mixed`` (some tables fell back) so a verifier never has to
    guess which comparison is valid.
    """
    hashes: dict[str, str] = {}
    fell_back = False
    for table in CANONICAL_TABLES:
        digest = _content_hash(engine, table) if content else None
        if digest:
            hashes[table] = digest
        else:
            hashes[table] = _count_hash(engine, table)
            if content:
                fell_back = True
    mode = "counts" if not content else ("mixed" if fell_back else "content")
    return hashes, mode


def _compute_manifest_hash(
    engine: Engine, table_hashes: dict[str, str] | None = None
) -> str:
    """Compute the manifest hash over every canonical table fingerprint."""
    if table_hashes is None:
        table_hashes, _ = _collect_table_hashes(engine)
    combined = "|".join(f"{table}:{table_hashes[table]}" for table in CANONICAL_TABLES)
    return hashlib.sha256(combined.encode()).hexdigest()


def create_knowledge_version(
    engine: Engine,
    version: str,
    source_versions: dict[str, str] | None = None,
    pipeline_version: str | None = None,
    schema_version: str | None = None,
    eval_run_id: int | None = None,
    manifest_hash: str | None = None,
    status: str = "OBSERVED",
    user: str = "system",
    content_manifest: bool = True,
) -> int:
    """Create a knowledge version snapshot.

    Args:
        engine: SQLAlchemy engine.
        version: Version tag (e.g., "2026.10.0").
        source_versions: Dict of source → version (e.g., {"bible": "tdb77", "dictionary": "v2"}).
        pipeline_version: Pipeline version string.
        schema_version: Database schema version.
        eval_run_id: FK to eval_runs table.
        manifest_hash: Pre-computed manifest hash (computed if not provided).
        status: Version status (OBSERVED, SUPPORTED, VERIFIED, etc.).
        user: User/process identifier for audit.
        content_manifest: Hash table content (default) instead of only row counts.

    Returns:
        The version ID (row ID).
    """

    repo = KnowledgeVersionRepository(engine)

    table_hashes, manifest_mode = _collect_table_hashes(engine, content=content_manifest)

    if manifest_hash is None:
        manifest_hash = _compute_manifest_hash(engine, table_hashes)

    row_counts = _get_table_row_counts(engine)

    quality = {
        "total_rows": sum(row_counts.values()),
        "tables_checked": len(row_counts),
        "manifest_hash": manifest_hash,
        "manifest_mode": manifest_mode,
        "table_hashes": table_hashes,
    }

    data = {
        "version": version,
        "git_commit": _get_git_commit(),
        "source_versions": json.dumps(source_versions or {}, ensure_ascii=False),
        "pipeline_version": pipeline_version or "phase4",
        "schema_version": schema_version or "1.0",
        "row_counts": json.dumps(row_counts, ensure_ascii=False),
        "quality": json.dumps(quality, ensure_ascii=False),
        "eval_run_id": eval_run_id,
        "manifest_hash": manifest_hash,
        "status": status,
    }

    return repo.create(data, user=user)


def list_knowledge_versions(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    """List knowledge versions, newest first."""

    repo = KnowledgeVersionRepository(engine)
    return repo.find({}, limit=limit, offset=0, order_by="-id")


def get_knowledge_version(engine: Engine, version: str) -> dict[str, Any] | None:
    """Get a specific knowledge version by tag."""

    repo = KnowledgeVersionRepository(engine)
    return repo.get_by_version(version)


def verify_knowledge_version(engine: Engine, version: str) -> dict[str, Any]:
    """Compare a recorded snapshot against the live database.

    Returns a report with ``ok``, the mode the snapshot was taken in, and a
    ``drift`` map of tables whose fingerprint changed.  ``ok`` is ``False`` for
    an unknown tag, a malformed snapshot, or any drift.
    """
    row = get_knowledge_version(engine, version)
    if not row:
        return {"ok": False, "version": version, "error": "unknown version"}

    try:
        quality = json.loads(row.get("quality") or "{}")
    except (TypeError, ValueError):
        return {"ok": False, "version": version, "error": "malformed quality payload"}

    recorded: dict[str, str] = quality.get("table_hashes") or {}
    mode: str = quality.get("manifest_mode") or "counts"
    if not recorded:
        return {
            "ok": False,
            "version": version,
            "error": "snapshot has no per-table fingerprints (counts-only legacy row)",
        }

    drift: dict[str, dict[str, str]] = {}
    for table in CANONICAL_TABLES:
        live = _get_table_hash(engine, table, content=mode != "counts")
        if recorded.get(table) != live:
            drift[table] = {"recorded": recorded.get(table, ""), "live": live}

    return {
        "ok": not drift,
        "version": version,
        "mode": mode,
        "checked": len(CANONICAL_TABLES),
        "drift": drift,
    }


def _get_git_commit() -> str:
    """Get current git commit SHA (zolai-core HEAD, not the caller's cwd)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"
