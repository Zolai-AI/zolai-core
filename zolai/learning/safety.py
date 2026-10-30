"""Stop-gates for anything that writes to a live database.

The Bible learning round already grew these helpers; the local learning cycle
needs the same guarantees, so they live here and the script re-exports them.
Nothing in this module writes unless the caller explicitly asks it to:
``preflight`` and ``verify_backup`` are read-only, ``make_backup`` only creates
a new file, and ``restore_backup`` is the single destructive entry point —
and it verifies its source before touching the target.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

MIN_TARGET_BYTES = 1_000_000_000
MIN_DISK_FREE_BYTES = 6_000_000_000
BACKUP_MIN_RATIO = 0.8
BUSY_TIMEOUT_MS = 60_000

#: Every table the Bible round writes (plus the SoT that must not move).
COUNTED_TABLES = (
    "word_usage",
    "bible_verses_import",
    "vocabulary",
    "grammar_patterns",
    "attestation_index",
    "bible_verses",
)


class StepFailure(RuntimeError):
    """A run step failed: the active step rolled back and the run aborted."""


def preflight(
    db_path: str | Path,
    *,
    min_bytes: int = MIN_TARGET_BYTES,
    disk_min_bytes: int = MIN_DISK_FREE_BYTES,
) -> Path:
    """Validate target size + free disk before anything is written.

    Raises:
        SystemExit: missing file, target <1 GB (0-byte decoy), or <6 GB free.
    """
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"[run] target database not found: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise SystemExit(
            f"[run] target database {path} is {size:,} bytes — refusing: "
            f"a real target must be ≥1 GB ({min_bytes:,} bytes)."
        )
    usage = shutil.disk_usage(path.parent if path.parent.exists() else path)
    if usage.free < disk_min_bytes:
        raise SystemExit(
            f"[run] insufficient free disk space next to {path}: "
            f"{usage.free / 1e9:.2f} GB free — need ≥{disk_min_bytes / 1e9:.1f} GB "
            "for the rebuild plus its backup"
        )
    return path


def sqlite_cli() -> str:
    """Path to the ``sqlite3`` CLI, or raise when it is not installed."""
    found = shutil.which("sqlite3")
    if found is None:
        raise SystemExit("[run] sqlite3 CLI not found on PATH — cannot snapshot/restore")
    return found


def make_backup(
    db_path: str | Path, backup_dir: str | Path, *, tag: str | None = None
) -> Path:
    """Snapshot the database with ``sqlite3 … ".backup '<dest>'"`` before writing."""
    source = Path(db_path)
    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = tag or f"{datetime.now():%Y%m%dT%H%M%S}"
    dest = dest_dir / f"{source.name}.bak-{stamp}"
    if dest.exists():
        raise SystemExit(f"[run] refusing to overwrite an existing backup: {dest}")
    cmd = [sqlite_cli(), str(source), f".backup '{dest}'"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise SystemExit(
            f"[run] backup failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return dest


def verify_backup(
    src: str | Path, backup: str | Path, *, min_ratio: float = BACKUP_MIN_RATIO
) -> int:
    """Refuse a missing or implausibly small backup (stop-gate).

    Returns:
        Backup size in bytes.
    """
    src_path, backup_path = Path(src), Path(backup)
    if not backup_path.exists():
        raise SystemExit(f"[run] backup missing after .backup: {backup_path}")
    src_size = src_path.stat().st_size
    backup_size = backup_path.stat().st_size
    if src_size and backup_size < int(src_size * min_ratio):
        raise SystemExit(
            f"[run] backup mis-sized: {backup_path} is {backup_size:,} bytes but the "
            f"source is {src_size:,} bytes (expected ≥{int(src_size * min_ratio):,})"
        )
    return backup_size


def sha256_file(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file (backups and model artifacts)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def restore_backup(db_path: str | Path, backup: str | Path) -> Path:
    """Restore ``backup`` over ``db_path`` via the CLI ``.restore`` command.

    Verifies the backup against the current file first, so a truncated or
    missing snapshot can never wipe a working database.
    """
    target = Path(db_path)
    verify_backup(target, backup)
    cmd = [sqlite_cli(), str(target), f".restore '{Path(backup)}'"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise SystemExit(
            f"[run] restore failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return target


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


#: Backwards-compatible alias — the Bible script imports the private name.
_connect = connect


def count_tables(
    conn: sqlite3.Connection, tables: tuple[str, ...] = COUNTED_TABLES
) -> dict[str, int]:
    """Row counts for every table in ``tables`` (absent tables count as 0)."""
    counts: dict[str, int] = {}
    for table in tables:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        counts[table] = (
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if exists
            else 0
        )
    return counts


def quick_check(conn: sqlite3.Connection) -> str:
    """``PRAGMA quick_check`` — first (and only) verdict line."""
    rows = conn.execute("PRAGMA quick_check").fetchall()
    return "; ".join(str(row[0]) for row in rows)


def checkpoint(conn: sqlite3.Connection) -> dict[str, Any]:
    """Flush the WAL after a write round (stop-gate), without failing the run."""
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {"ok": True, "busy": row[0], "log": row[1], "checkpointed": row[2]}
    except sqlite3.Error as exc:  # non-WAL databases are a no-op here
        return {"ok": False, "error": str(exc)}
