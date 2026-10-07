#!/usr/bin/env python3
"""
Nightly backup script for zolai.db — KR2.2
Uses sqlite3 .backup() for consistent hot backup, compresses with gzip,
stores to data/backups/zolai-YYYY-MM-DD_HH-MM.db.gz, records sha256 in
data/backups/backup.log (JSONL), retains last 30 days.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_db_path() -> Path:
    """Resolve the database path from environment or default."""
    # ZOLAI_DB_PATH takes precedence (full path to db file)
    db_path_env = os.environ.get("ZOLAI_DB_PATH", "").strip()
    if db_path_env:
        return Path(db_path_env)

    # ZOLAI_DATA_ROOT + zolai.db
    data_root = os.environ.get("ZOLAI_DATA_ROOT", "").strip()
    if data_root:
        return Path(data_root) / "zolai.db"

    # Default to workspace root data/ (for cron context)
    return Path("/home/peter/Documents/Projects/zolai-ai/data/zolai.db")


def get_backup_dir() -> Path:
    """Resolve the backup directory."""
    data_root = os.environ.get("ZOLAI_DATA_ROOT", "").strip()
    if data_root:
        return Path(data_root) / "backups"
    return Path("/home/peter/Documents/Projects/zolai-ai/data/backups")


def sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def log_jsonl(log_path: Path, entry: dict) -> None:
    """Append a JSONL entry to the log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    db_path = get_db_path()
    backup_dir = get_backup_dir()
    log_path = backup_dir / "backup.log"

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_file = backup_dir / f"zolai-{timestamp}.db"
    backup_gz = backup_dir / f"zolai-{timestamp}.db.gz"

    print(f"Starting backup of {db_path} → {backup_gz}")

    # WAL-safe online backup using sqlite3 .backup()
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(backup_file)
        src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        print(f"ERROR: Backup failed: {e}", file=sys.stderr)
        if backup_file.exists():
            backup_file.unlink()
        return 1

    if not backup_file.exists() or backup_file.stat().st_size == 0:
        print("ERROR: Backup file empty or missing", file=sys.stderr)
        if backup_file.exists():
            backup_file.unlink()
        return 1

    # Row-count sanity check
    dict_count = "FAIL"
    try:
        conn = sqlite3.connect(backup_file)
        cursor = conn.execute("SELECT count(*) FROM dictionary;")
        dict_count = str(cursor.fetchone()[0])
        conn.close()
    except Exception:
        pass

    # Compress with gzip
    try:
        with backup_file.open("rb") as f_in:
            with gzip.open(backup_gz, "wb") as f_out:
                f_out.write(f_in.read())
    except Exception as e:
        print(f"ERROR: Compression failed: {e}", file=sys.stderr)
        if backup_file.exists():
            backup_file.unlink()
        if backup_gz.exists():
            backup_gz.unlink()
        return 1

    # Remove uncompressed backup
    backup_file.unlink()

    # Compute hash and size
    file_hash = sha256_file(backup_gz)
    file_size = backup_gz.stat().st_size
    size_human = f"{file_size / (1024*1024):.1f}M"

    # Log to JSONL
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "file": str(backup_gz),
        "sha256": file_hash,
        "size_bytes": file_size,
        "dictionary_rows": dict_count,
    }
    log_jsonl(log_path, log_entry)

    print(f"Backup done → {backup_gz} ({size_human}), dictionary rows: {dict_count}")
    print(f"SHA256: {file_hash}")

    # Retention: delete files older than 30 days
    cutoff = datetime.now() - timedelta(days=30)
    for old_file in backup_dir.glob("zolai-*.db.gz"):
        try:
            # Parse timestamp from filename: zolai-YYYY-MM-DD_HH-MM.db.gz
            name = old_file.stem.replace(".db", "")  # zolai-YYYY-MM-DD_HH-MM
            parts = name.split("-")
            if len(parts) >= 4:
                file_date_str = "-".join(parts[1:4])  # YYYY-MM-DD_HH-MM
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d_%H-%M")
                if file_date < cutoff:
                    old_file.unlink()
                    log_jsonl(log_path, {
                        "timestamp": datetime.now().isoformat(),
                        "action": "retention_delete",
                        "file": str(old_file),
                    })
                    print(f"Retention: deleted old backup {old_file}")
        except Exception:
            # If we can't parse, skip
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())