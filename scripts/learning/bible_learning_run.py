#!/usr/bin/env python3
"""Bible learning run orchestrator — backup → G1 → attestation → G3 → G2 → G5.

One command that runs the whole Bible word/grammar learning round against a
single sqlite database, in the plan's order:

    1. preflight   ≥1 GB target, ≥6 GB free disk, then a verified ``.backup``
    2. before-counts for every table the round touches
    3. G1  repair + rebuild ``word_usage`` from ``bible_verses``
    4.     ``build_attestation_index(force=True)`` (fresh evidence materialised)
    5. G3  wire vocabulary confidence / review status / glosses to that evidence
    6. G2  dedupe ``bible_verses_import`` + unique ``ref`` guard
    7. G5  learn grammar patterns into ``grammar_patterns`` (book='BIBLE')
    8. after-counts + ``PRAGMA quick_check`` + WAL checkpoint
    9. report JSON ``data/reports/bible_learning_<ts>.json`` (sizes / hashes /
       row counts only — no verse text, no secrets)

Failure handling: every step owns its transaction, so a failure rolls back the
*active* step, aborts the run (``StepFailure`` → exit code 1) and leaves the
pre-run backup as the full-restore path.  Steps are individually idempotent, so
resuming is a plain rerun.  ``--dry-run`` computes everything with zero writes
to the database (the report JSON is the only file it produces).

Local only: stdlib sqlite3 + the ``sqlite3`` CLI for the backup.  No network,
no external AI provider calls.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # allows `python scripts/learning/…py`
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.learning.learn_grammar_from_bible import (  # noqa: E402
    learn as learn_grammar_patterns,
)
from scripts.learning.repair_and_learn_bible_word_usage import (  # noqa: E402
    learn_bible,
)
from scripts.learning.wire_vocabulary_evidence import wire  # noqa: E402
from scripts.maintenance.dedupe_bible_verses_import import dedupe  # noqa: E402

MIN_TARGET_BYTES = 1_000_000_000
MIN_DISK_FREE_BYTES = 6_000_000_000
BACKUP_MIN_RATIO = 0.8
BUSY_TIMEOUT_MS = 60_000

REPORTS_DIR = _REPO_ROOT / "data" / "reports"

#: Every table this round writes (plus the SoT that must not move).
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


# ── preflight / backup ──────────────────────────────────────────────────────


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


def make_backup(
    db_path: str | Path, backup_dir: str | Path, *, tag: str | None = None
) -> Path:
    """Snapshot the database with ``sqlite3 … ".backup '<dest>'"`` before writing."""
    source = Path(db_path)
    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = tag or f"{datetime.now():%Y%m%dT%H%M%S}"
    dest = dest_dir / f"{source.name}.bak-{stamp}"
    if shutil.which("sqlite3") is None:
        raise SystemExit(f"[run] sqlite3 CLI not found on PATH — cannot back up {source}")
    cmd = [shutil.which("sqlite3") or "sqlite3", str(source), f".backup '{dest}'"]
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


# ── counts / integrity ──────────────────────────────────────────────────────


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def count_tables(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for every table the round touches (bible_verses is the SoT)."""
    counts: dict[str, int] = {}
    for table in COUNTED_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        counts[table] = (
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else 0
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


def run_attestation(db_path: str, *, force: bool = True) -> dict[str, Any]:
    """Materialise ``attestation_index`` (imported lazily — heavy loader)."""
    from zolai.learning.word_attestation import build_attestation_index

    return build_attestation_index(db_path, force=force)


# ── the run ─────────────────────────────────────────────────────────────────


def run(
    db_path: str | Path,
    *,
    dry_run: bool = False,
    backup_dir: str | Path | None = None,
    report_path: str | Path | None = None,
    min_bytes: int = MIN_TARGET_BYTES,
    disk_min_bytes: int = MIN_DISK_FREE_BYTES,
    attestation_builder=None,
) -> dict[str, Any]:
    """Run the whole Bible learning round against ``db_path``.

    Args:
        db_path: Target database (the live round passes the canonical path).
        dry_run: Compute every step with zero database writes.
        backup_dir: Where the pre-run snapshot goes (default: ``<db>/backups``).
        report_path: Report JSON path (default: ``data/reports/bible_learning_<ts>.json``).
        min_bytes / disk_min_bytes: Preflight floors (tests lower them for
            synthetic fixtures; the CLI always uses the 1 GB / 6 GB defaults).
        attestation_builder: Injectable ``build_attestation_index`` stand-in.

    Returns:
        The report dict (also written to ``report_path``).

    Raises:
        SystemExit: preflight or backup failure (nothing written).
        StepFailure: a step failed — the active step rolled back, the run
            aborted, the pre-run backup is the full-restore path.
    """
    path = preflight(db_path, min_bytes=min_bytes, disk_min_bytes=disk_min_bytes)
    ts = f"{datetime.now():%Y%m%dT%H%M%S}"

    backup_info: dict[str, Any] | None = None
    if not dry_run:
        dest_dir = Path(backup_dir) if backup_dir else path.parent / "backups"
        backup_path = make_backup(path, dest_dir, tag=ts)
        backup_info = {
            "path": str(backup_path),
            "size_bytes": verify_backup(path, backup_path),
            "ok": True,
        }

    conn = _connect(path)
    steps: dict[str, Any] = {}
    step_name = "counts"
    try:
        before = count_tables(conn)
        step_name = "g1"
        steps["g1"] = learn_bible(str(path), dry_run=dry_run)
        step_name = "attestation"
        if dry_run:
            steps["attestation"] = {"skipped": "dry-run"}
        else:
            builder = attestation_builder if attestation_builder is not None else run_attestation
            steps["attestation"] = builder(str(path), force=True)
        report_dir = Path(report_path).parent if report_path else REPORTS_DIR
        step_name = "g3"
        steps["g3"] = wire(
            str(path),
            dry_run=dry_run,
            report_path=report_dir / f"vocab_gloss_disagreements_{ts}.jsonl",
        )
        step_name = "g2"
        steps["g2"] = dedupe(str(path), dry_run=dry_run)
        step_name = "g5"
        steps["g5"] = learn_grammar_patterns(str(path), dry_run=dry_run)
        step_name = "integrity"
        after = count_tables(conn)
        quick = quick_check(conn)
        wal = {"skipped": "dry-run"} if dry_run else checkpoint(conn)
    except BaseException as exc:
        try:
            conn.rollback()
        except sqlite3.Error:  # pragma: no cover — defensive
            pass
        conn.close()
        raise StepFailure(
            f"{step_name}: {exc} — ROLLBACK (the active step wrote nothing; "
            "rerun resumes from the last committed step)"
        ) from exc
    finally:
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover — defensive
            pass

    report: dict[str, Any] = {
        "ts": ts,
        "ok": quick == "ok",
        "dry_run": dry_run,
        "db_path": str(path),
        "db_size_bytes": path.stat().st_size,
        "disk_free_bytes": shutil.disk_usage(path.parent).free,
        "backup": backup_info,
        "quick_check": quick,
        "wal_checkpoint": wal,
        "before": before,
        "after": after,
        "steps": steps,
    }
    dest = Path(report_path) if report_path else (
        REPORTS_DIR / f"bible_learning_{ts}.json"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["report_path"] = str(dest)
    return report


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full Bible word/grammar learning round (backup-first, transactional)"
    )
    parser.add_argument("--db", default=None, help="target sqlite database (default: canonical)")
    parser.add_argument("--dry-run", action="store_true", help="compute everything, write nothing")
    parser.add_argument("--backup-dir", default=None, help="pre-run snapshot directory")
    parser.add_argument("--report", default=None, help="report JSON path")
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db)
    else:
        from zolai.config import config

        db_path = Path(config.paths.zolai_db)

    try:
        report = run(
            db_path,
            dry_run=args.dry_run,
            backup_dir=args.backup_dir,
            report_path=args.report,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.code) if isinstance(exc.code, int) else 2
    except StepFailure as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if report["dry_run"] else "WROTE"
    print(f"[run] {mode} bible learning round → {report['db_path']}")
    for table in COUNTED_TABLES:
        print(f"  {table:<22} {report['before'][table]:,} → {report['after'][table]:,}")
    if report["backup"]:
        print(
            f"  backup               {report['backup']['path']}"
            f" ({report['backup']['size_bytes']:,} bytes)"
        )
    print(f"  quick_check          {report['quick_check']}")
    print(f"  wal_checkpoint       {report['wal_checkpoint']}")
    print(f"  report               {report['report_path']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
