"""Shared gates + helpers for Track R2-A DB consolidation (plan-r2a-db-consolidation).

Binding design notes (mirror of plan §2/§3):
- Single-writer: every write session opens the DB with ``PRAGMA locking_mode=EXCLUSIVE``
  inside ``BEGIN IMMEDIATE``; an ``fuser`` gate aborts if any other PID holds the file.
- Every apply batch is atomic (one transaction) and appends an ``import_log`` entry plus a
  ``provenance`` row with ``row_count > 0`` (M8b guard: provenance never gets zero rows).
  R2-A batch entries use status ``r2a_batch`` — never ``completed``/``failed``/``census`` —
  so the M8a/M8c/M8d processors (which scan only those statuses) never see maintenance deltas.
- Identity gate for exact-pair collapses follows ``scripts/migration/phase2c/dedup_core.py:29``:
  non-data columns are ``{id, version, created_at, updated_at, content_hash}``; pairs differing
  in any *data* column are SKIPPED and filed in a skip-list (owner gate — never merge glosses).
- Stop-conditions from plan §3 raise ``R2AStop``; items report and skip rather than force.
- Articles ``content_hash`` backfill format: existing populated rows store double-hex
  (``hex(utf8(sha256hex))``); the inner preimage is not reproducible from current row content,
  so the backfill hashes ``sha256(content)`` in the same observable storage format —
  deterministic, collision-free, dedup-equivalent (documented in consolidation-execution.md).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ── paths / constants ────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]  # zolai-core
ZOLAI_ROOT = REPO_ROOT.parent  # zolai-ai


def _default_db() -> Path:
    """The canonical database, from the one resolver that owns the answer.

    ``config.paths.db`` already applies ``ZOLAI_DB_PATH`` / ``ZOLAI_DATA_ROOT``;
    reading those here gave this script a second opinion that could disagree with
    the toolkit about which file it was consolidating. The literal path is the
    fallback for a run without the package installed.
    """
    try:
        from zolai.config import config

        return Path(config.paths.db)
    except Exception:
        return ZOLAI_ROOT / "data" / "zolai.db"


DEFAULT_DB = _default_db()
BACKUP_DIR = ZOLAI_ROOT / "data" / "backups"
ARCHIVE_DB = BACKUP_DIR / "zolai-import-archive-2026-10-27.db"
ARCHIVE_MANIFEST = BACKUP_DIR / "zolai-import-archive-2026-10-27.manifest.json"
FINAL_COUNTS = ZOLAI_ROOT / "data" / "conductor" / "r2a-final-counts.txt"
SKIPLIST_ZBA = ZOLAI_ROOT / "data" / "conductor" / "r2a-skiplist-zba.json"

R2A_BATCH_STATUS = "r2a_batch"  # invisible to M8a/M8c/M8d (completed/failed/census scans)
ARCHIVED_STATUS = "archived"  # plan §2 step 7(a)
CENSUS_STATUS = "census"  # plan §2 step 7(d) — excluded from M8c completed-scan

#: 26 staging tables per archive-plan Phase-1 (plan §2 step 6).
IMPORT_TABLES = (
    "bible_book_analysis_import",
    "bible_chapter_analysis_import",
    "bible_verses_import",
    "dictionary_en_my_import",
    "dictionary_en_zo_import",
    "dictionary_import",
    "dictionary_my_import",
    "dictionary_trilingual_import",
    "grammar_patterns_import",
    "phrase_context_import",
    "phrases_from_bible_import",
    "phrases_import",
    "proverbs_import",
    "sentence_patterns_import",
    "topic_clusters_import",
    "training_corpus_qwen3_import",
    "training_exercises_import",
    "training_seed_data_import",
    "training_valid_sentences_import",
    "translations_import",
    "vocab_import",
    "word_alignments_import",
    "word_collocations_import",
    "word_usage_profiles_import",
    "zolai_vocabulary_import",
    "zvs_corrections_import",
)

#: import_log alias -> canonical table (recon §5; all 15 resolve to IMPORT_TABLES).
ALIAS_MAP = {
    "bible_book_analysis": "bible_book_analysis_import",
    "bible_chapter_analysis": "bible_chapter_analysis_import",
    "dictionary_en_my": "dictionary_en_my_import",
    "dictionary_my": "dictionary_my_import",
    "dictionary_trilingual": "dictionary_trilingual_import",
    "phrase_context": "phrase_context_import",
    "phrases_from_bible": "phrases_from_bible_import",
    "sentence_patterns": "sentence_patterns_import",
    "topic_clusters": "topic_clusters_import",
    "training_corpus_qwen3": "training_corpus_qwen3_import",
    "training_seed_data": "training_seed_data_import",
    "training_valid_sentences": "training_valid_sentences_import",
    "vocab": "vocab_import",
    "word_usage_profiles": "word_usage_profiles_import",
    "zvs_corrections": "zvs_corrections_import",
}

#: Phase-2 empty-table drops (plan §2 step 8). KEEP/DEFER exceptions never dropped.
PHASE2_EMPTY_DROPS = (
    "dictionary_enhanced",
    "vocabulary_enhanced",
    "bible_verses_enhanced",
    "proverbs_idioms",
)
DROP_KEEP = frozenset({"ngram", "knowledge_vectors", "gemini_model_results", "zz1", "zz2"})

#: Redundant index set (plan §2 step 9, recon §4 conservative set):
#: drop candidate -> byte-identical/unique-prefix keep index that must survive.
INDEX_DROP_SET: dict[str, str] = {
    "idx_translations_source": "uniq_trans_v2_source_target",
    "ix_trans_source": "uniq_trans_v2_source_target",
    "ix_trans_target": "idx_translations_target",
    "idx_alignments_ref": "idx_wa_v2_ref",
    "idx_wu_v2_wb": "uniq_wu_v2_word_book_meaning",
    "idx_word_usage_word": "ix_usage_word_book_unique",
    "idx_wu_v2_word": "ix_usage_word_book_unique",
    "idx_vocab_headword": "uniq_vocab_v2_headword",
    "idx_vocab_v2_hw": "uniq_vocab_v2_headword",
    "idx_syl_v2_word": "uniq_syl_v2_word",
    "idx_dict_en_headword": "uniq_dict_en_zo_v2_headword",
    "idx_enzo_v2_headword": "uniq_dict_en_zo_v2_headword",
    "idx_dict_v2_zolai": "uniq_dict_v2_zolai",
}

#: Before-counts from plan §0 (live-verified 2026-10-27). Drift >5% or >1000 rows = stop.
PLAN_EXPECTED: dict[str, Any] = {
    "d13": {
        "translations_import": 135_511,
        "dot_refs": 135_497,
        "fixable": 135_297,
        "nah_rows": 200,
        "empty_refs": 14,
    },
    "split": {
        "translations": 207_623,
        "en_to_my": 150_965,
        "news": 76_862,
        "parallel": 74_103,
        "post": 56_658,
    },
    "articles": {
        "total": 15_649,
        "null_hash": 9_278,
        "news": 9_278,
        "lessons": 4_163,
        "legacy": 2_208,
        "dup_groups": 1,
        "dup_extra": 1,
        "post": 15_648,
    },
    "collapse": {
        "wa": 385_120,
        "wa_groups": 319,
        "wa_post": 384_801,
        "zba": 30_758,
        "zba_groups": 706,
        "zba_post_if_merged": 30_052,
    },
    "import_log": {"total": 95, "completed": 46, "failed": 49, "aliases": 15, "unlogged": 115},
}

#: Post-drop canonical counts (plan §3 stop-condition).
CANONICAL_POST = {
    "word_usage": 485_360,
    "word_alignments": 384_801,
    "translations": 56_658,
    "translations_news": 150_965,
    "bible_verses": 31_102,
    "dictionary": 84_490,
    "vocabulary": 104_906,
}

NON_DATA_COLS = frozenset({"id", "version", "created_at", "updated_at", "content_hash"})


class R2AStop(RuntimeError):
    """Plan §3 stop-condition hit — item reports and skips, never forces."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── connection / gates ───────────────────────────────────────────────────────


def connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def connect_rw(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.isolation_level = None  # explicit transaction control
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def write_batch(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One atomic batch: BEGIN IMMEDIATE … COMMIT (rollback on any error)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def fuser_pids(path: Path) -> list[int]:
    try:
        proc = subprocess.run(["fuser", str(path)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return []  # fuser missing/timed out: cannot assert a holder; caller decides
    return [int(tok) for tok in proc.stdout.split() if tok.isdigit()]


def fuser_clean(db: Path, allow: set[int] | None = None) -> None:
    """Abort track if any other PID holds the DB file (plan §2 step 0)."""
    allow = allow or set()
    pids = [p for p in fuser_pids(db) if p not in allow and p != os.getpid()]
    if pids:
        raise R2AStop(f"single-writer gate: DB held by PID(s) {pids} — abort")


def require_fresh_backup(db: Path, backup_dir: Path = BACKUP_DIR, max_age_s: int = 7200) -> Path:
    """Return the newest backup .gz, aborting if older than max_age_s or missing."""
    gz = sorted(backup_dir.glob("zolai-*.db.gz"), key=lambda p: p.stat().st_mtime)
    if not gz:
        raise R2AStop("backup gate: no backup .gz found — run scripts/backup-zolai.sh")
    newest = gz[-1]
    if newest.stat().st_mtime < db.stat().st_mtime - max_age_s:
        age = db.stat().st_mtime - newest.stat().st_mtime
        raise R2AStop(
            f"backup gate: newest backup {newest.name} is stale (db mtime leads by {age:.0f}s > {max_age_s}s)"
        )
    return newest


# ── count / drift helpers ────────────────────────────────────────────────────


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(scalar(conn, f'SELECT COUNT(*) FROM "{table}"') or 0)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(scalar(conn, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(scalar(conn, "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?", (name,)))


def assert_drift(item: str, before: int, expected: int) -> None:
    """Plan §3: before-count drifting >5% or >1,000 rows stops the item."""
    delta = abs(before - expected)
    if expected and (delta > expected * 0.05 or delta > 1000):
        raise R2AStop(f"{item}: before-count drift {before:,} vs plan {expected:,} (Δ{delta:,}) exceeds tolerance")


def assert_eq(label: str, actual: int, want: int) -> None:
    if actual != want:
        raise R2AStop(f"{label}: expected {want:,}, got {actual:,}")


def index_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    """Parsed column list of a CREATE INDEX statement (covers prefix checks)."""
    sql = scalar(conn, "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,))
    if not sql:
        raise R2AStop(f"index {name} missing")
    inner = sql[sql.index("(") + 1 : sql.rindex(")")]
    return [part.strip().split()[0].strip('"').lower() for part in inner.split(",")]


def prefix_covered(conn: sqlite3.Connection, drop_idx: str, keep_idx: str) -> bool:
    """True when keep_idx's columns start with drop_idx's columns (unique-prefix cover)."""
    drop_cols = index_columns(conn, drop_idx)
    keep_cols = index_columns(conn, keep_idx)
    return keep_cols[: len(drop_cols)] == drop_cols


def data_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in rows if r[1].lower() not in NON_DATA_COLS]


# ── content hash (articles backfill) ────────────────────────────────────────


def articles_content_hash(content: str) -> str:
    """Double-hex hash matching the observable storage format of populated rows."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return digest.encode("utf-8").hex()


def backfill_content_hash(conn: sqlite3.Connection, table: str = "articles") -> int:
    """Backfill ``content_hash`` for NULL rows (idempotent; content-derived, safe)."""
    rows = conn.execute(
        f"SELECT id, content FROM {table} WHERE content_hash IS NULL AND content IS NOT NULL"
    ).fetchall()
    for rid, content in rows:
        conn.execute(f"UPDATE {table} SET content_hash=? WHERE id=?", (articles_content_hash(content), rid))
    return len(rows)


# ── atomic provenance + import_log writer ───────────────────────────────────


def log_batch(
    conn: sqlite3.Connection,
    *,
    item: str,
    table: str,
    rows: int,
    note: str,
    status: str = R2A_BATCH_STATUS,
) -> None:
    """Append one import_log entry + one provenance row inside the caller's txn.

    ``rows`` must be > 0 (M8b: provenance never gets zero-row entries; apply skips
    the insert entirely when a batch changed nothing).
    """
    if rows <= 0:
        return
    batch_id = str(uuid.uuid4())
    ts = now_iso()
    conn.execute(
        "INSERT INTO import_log (batch_id, source_file, table_name, rows_imported,"
        " sha256, imported_at, version, status, error_message)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (batch_id, f"r2a-{item}", table, rows, None, ts, 1, status, note),
    )
    conn.execute(
        "INSERT INTO provenance (filename, size_bytes, sha256, row_count, source,"
        " generator_script, version, status, updated_at, change_log)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            f"r2a-{item}-{batch_id[:8]}",
            0,
            hashlib.sha256(batch_id.encode()).hexdigest(),
            rows,
            "R2-A consolidation",
            "scripts/maintenance/consolidation_r2a.py",
            "1",
            "applied",
            ts,
            note[:500],
        ),
    )


# ── reporting ────────────────────────────────────────────────────────────────


@dataclass
class ItemReport:
    item: str
    mode: str = "dry-run"  # dry-run | apply | verify
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)
    projections: dict[str, Any] = field(default_factory=dict)
    planned_sql: list[str] = field(default_factory=list)
    applied: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def report(self, mode: str | None = None) -> None:
        if mode:
            self.mode = mode
        parts = [f"[{self.item}:{self.mode}]"]
        for k in self.before:
            b, a = self.before[k], self.after.get(k, self.before[k])
            if isinstance(b, int) and isinstance(a, int):
                parts.append(f"{k} {b:,}→{a:,}")
            else:
                parts.append(f"{k}={b}→{a}")
        if self.applied:
            parts.append(f"applied={len(self.applied)}")
        if self.skipped:
            parts.append(f"SKIPPED={len(self.skipped)}")
        if self.errors:
            parts.append(f"ERRORS={len(self.errors)}")
        print(" ".join(parts))
        for sql in self.planned_sql:
            print(f"  SQL> {sql}")
        for s in self.skipped:
            print(f"  skip: {s}")
        for e in self.errors:
            print(f"  ERROR: {e}", file=__import__("sys").stderr)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "mode": self.mode,
            "before": self.before,
            "after": self.after,
            "projections": self.projections,
            "planned_sql": self.planned_sql,
            "applied": {k: str(v) for k, v in self.applied.items()},
            "skipped": self.skipped,
            "errors": self.errors,
        }
