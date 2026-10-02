"""Bible verse ref audit / fix / revert — C2-style data correction.

Authoritative spec: ``docs/planning/BIBLE_REF_FIX_PLAN.md`` (workspace-root
repo).  Three commands are exposed through ``zolai bible-ref audit|fix|revert``
(plus ``remap`` for the Phase-2 downstream copies):

``zolai bible-ref audit [--report PATH] [--json]``
    Read-only classification of every ``bible_verses`` row into
    ``ok | impossible_ref | en_needs_review | null_en`` plus the needs-founder
    buckets.  Never writes to the database (not even DDL).

``zolai bible-ref fix [--apply] [--fill-null-en]``
    Dry-run by default.  For each **impossible ref** (a reference that names a
    verse absent from the KJV structural table — ``zolai.data.verse_counts``):
    (i) resolve the target ``(book, chapter-1, verse)`` — when no such row
    exists (chapter-1 overflow, e.g. ``3JN 1:15``) the row is archived with no
    EN restore and flagged for the founder; (ii) when the doomed row's EN
    matches the KJV text of the *target* and the target's own EN does
    not, copy that KJV-verified EN into the target cell; (iii) copy the full
    row into ``bible_verses_archive`` (lossless), append a ``data_audit_log``
    row and DELETE it from the canonical table.  Rows are restored, never
    invented: no ZO/label column is ever written, and no write may leave two
    rows sharing one ref.

``zolai bible-ref revert [--apply]``
    Reinsert archived rows by ``source_row_id`` (ids + text byte-identical)
    and reverse the KJV-verified EN restores, appending
    ``bible_ref_fix_revert by cli`` audit rows (the originals stay).

``zolai bible-ref remap [--apply]``
    Phase 2: rewrite the archived→target references held downstream
    (``word_alignments.ref`` · ``translations.reference``), audit-logged.

Founder decision (plan header): **Archive + remove** — canonical
``bible_verses`` converges on the 31,102 real KJV verses.

Invariants
----------
- **No ZO/label writes.** The only canonical-column this module ever writes is
  ``en_kJV`` (KJV-verified copies into demonstrably-wrong cells, plus
  opt-in NULL fills); the only canonical column it removes is the whole row
  (archive + DELETE).  ZO dialect columns, Myanmar columns, ``book_name``,
  ``version`` and hash columns are read-only here.
- **Additive DDL only.** ``bible_verses_archive`` is created with a single
  ``CREATE TABLE IF NOT EXISTS`` — never ALTERed, never dropped (a source scan
  test enforces this).
- **Every write is audited** — one ``data_audit_log`` row per archived row,
  per EN restore and per downstream remap (reasons are namespaced under
  ``bible_ref_fix_v1``), so ``audit rows == writes`` holds by construction.
- **Idempotent.** A second ``fix --apply`` archives nothing (source ids
  already in the archive), a second ``revert``/``remap`` finds no pending
  work; both append 0 audit rows.
- **Duplicate-ref refusal.** ``fix`` refuses to archive a row whose id or ref
  is already archived; ``revert`` refuses to reinsert a row whose id/ref is
  already live — never two rows under one ref.
- **needs-founder rows are never content-corrected** (neither-EN pairs, the
  unresolvable impossible rows, EN variants, NULL ``en_kJV``,
  parallel-passage false positives): no guessed target, no EN rewrite, no ZO
  touch — they are reported.  A doomed row that has no target is still
  archived + removed like every other impossible ref (the founder-approved
  convergence to 31,102); ``--fill-null-en`` stays off unless a founder
  passes it.
- Canonical store only (``zolai.config`` → workspace-root ``data/zolai.db``).
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from ..config import config
from .verse_counts import (
    BOOK_ORDER,
    TOTAL_VERSES,
    is_valid,
    parse_ref,
)

Class = Literal["ok", "impossible_ref", "en_needs_review", "null_en"]

#: ``data_audit_log.reason`` for the archive + DELETE of every doomed row.
REASON = "bible_ref_fix_v1 by cli"

#: ``data_audit_log.reason`` for a KJV-verified EN copy into a target cell.
EN_RESTORE_REASON = "bible_ref_fix_v1: kjv_verified_en_restore"

#: ``data_audit_log.reason`` for the off-by-default ``--fill-null-en`` writes.
FILL_NULL_REASON = "bible_ref_fix_v1: fill_null_en by cli"

#: ``data_audit_log.reason`` for Phase-2 downstream reference rewrites.
REMAP_REASON = "bible_ref_fix_v1: downstream_remap by cli"

#: ``data_audit_log.reason`` for the revert cycle (originals are kept).
REVERT_REASON = "bible_ref_fix_revert by cli"

TABLE = "bible_verses"
ARCHIVE_TABLE = "bible_verses_archive"

#: The 18 live columns of ``bible_verses`` — every one is archived verbatim.
BIBLE_COLUMNS: tuple[str, ...] = (
    "id", "ref", "book", "chapter", "verse", "zo_tdb77", "zo_tedim2010",
    "en_kJV", "myanmar", "zo_tedim1932", "zo_hcl06", "zo_fcl",
    "myanmar_judson", "book_name", "version", "created_at", "updated_at",
    "content_hash",
)

#: Canonical columns this module may write — nothing else, ever (no ZO/label).
WRITABLE_COLUMNS: frozenset[str] = frozenset({"en_kJV"})

#: Downstream (table, column) pairs carrying verse refs copied from the
#: canonical table — Phase 2 of the plan.
DOWNSTREAM: tuple[tuple[str, str], ...] = (
    ("word_alignments", "ref"),
    ("translations", "reference"),
)

#: Rows per downstream scan batch.
DEFAULT_BATCH_SIZE = 500

#: ``difflib`` threshold for "same KJV wording, different edition/punctuation".
FUZZY_RATIO = 0.80

#: Share of the KJV verse that must appear **in order** inside the stored EN
#: for the row to count as the same verse (KJV text + the translators'
#: marginal notes some editions append — measured, not guessed: all 6,245
#: footnote-carrying rows score >= 0.90 while genuinely different text does
#: not).  ``autojunk`` is disabled throughout: difflib's frequency heuristic
#: distorts ratios on verse-length strings.
PRESENT_MIN = 0.90

#: Needs-founder sample caps in the audit payload (full lists stay small).
_SAMPLE_LIMIT = 500

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s']+")


# ---------------------------------------------------------------------------
# KJV ground truth (text) — optional; structural audit works without it
# ---------------------------------------------------------------------------


def default_kjv_path() -> Path:
    """Workspace ``data/reference/kjv_en.json`` (gitignored, fetched once)."""
    return Path(config.paths.data) / "reference" / "kjv_en.json"


def load_kjv(path: Path | str | None = None) -> dict[tuple[str, int, int], str] | None:
    """Map ``(book, chapter, verse)`` → KJV text; ``None`` when unavailable.

    The JSON holds the standard 66 books in canonical order, so books are
    zipped positionally against :data:`BOOK_ORDER` (no abbreviation fork).
    Structural-only mode (``None``) is a documented degradation: ref validity
    keeps working from :mod:`zolai.data.verse_counts`, EN text repair does not.
    """
    src = Path(path) if path is not None else default_kjv_path()
    if not src.exists():
        return None
    payload = json.loads(src.read_text(encoding="utf-8"))
    if len(payload) != len(BOOK_ORDER):
        raise ValueError(f"{src}: expected {len(BOOK_ORDER)} books, got {len(payload)}")
    out: dict[tuple[str, int, int], str] = {}
    for code, book in zip(BOOK_ORDER, payload, strict=True):
        for chapter, verses in enumerate(book["chapters"], start=1):
            for verse, text in enumerate(verses, start=1):
                out[(code, chapter, verse)] = text
    if len(out) != TOTAL_VERSES:
        raise ValueError(f"{src}: expected {TOTAL_VERSES} verses, got {len(out)}")
    return out


def _norm(text: str) -> str:
    """Lowercase, straighten quotes, drop punctuation — comparison key only."""
    folded = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    folded = folded.replace("\u201c", '"').replace("\u201d", '"')
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", folded)).strip()


def _match(
    en: str | None,
    kjv_text: str | None,
) -> Literal["exact", "normalized", "extended", "fuzzy"] | None:
    """Grade how well *en* matches the KJV text of its own reference.

    ``extended`` = the stored EN *is* the KJV verse plus appended material
    (the translators' marginal notes carried by some editions); the verse
    text itself is intact, so this counts as a match rather than a variant.
    """
    if not en or not kjv_text:
        return None
    if en == kjv_text:
        return "exact"
    left, right = _norm(en), _norm(kjv_text)
    if not left or not right:
        return None
    if left == right:
        return "normalized"
    matcher = difflib.SequenceMatcher(None, right, left, autojunk=False)
    present = sum(block.size for block in matcher.get_matching_blocks()) / len(right)
    if present >= PRESENT_MIN and len(left) >= len(right):
        return "extended"
    if matcher.ratio() >= FUZZY_RATIO:
        return "fuzzy"
    return None


def _cross_refs(
    en: str,
    own_ref: str,
    kjv: dict[tuple[str, int, int], str],
    norm_index: dict[str, list[str]],
) -> list[str]:
    """Refs whose KJV text matches *en* but is not *own_ref* (parallel passage)."""
    norm = _norm(en)
    hits = [ref for ref in norm_index.get(norm, []) if ref != own_ref]
    if hits:
        return hits
    # Parallel passages often share an opening; verify any prefix candidate
    # with the same fuzzy threshold used for own-ref matching.
    words = norm.split()
    prefix = " ".join(words[:6])
    if len(words) > 6:
        for ref in norm_index.get(prefix, []):
            if ref == own_ref:
                continue
            parsed = parse_ref(ref)
            target = kjv.get(parsed) if parsed else None
            if target and difflib.SequenceMatcher(
                None, norm, _norm(target), autojunk=False
            ).ratio() >= FUZZY_RATIO:
                hits.append(ref)
    return hits


def _kjv_norm_index(
    kjv: dict[tuple[str, int, int], str],
) -> dict[str, list[str]]:
    """Normalized KJV text → refs, plus 6-word prefixes → refs."""
    index: dict[str, list[str]] = {}
    for (book, chapter, verse), text in kjv.items():
        ref = f"{book} {chapter}:{verse}"
        norm = _norm(text)
        index.setdefault(norm, []).append(ref)
        index.setdefault(" ".join(norm.split()[:6]), []).append(ref)
    return index


# ---------------------------------------------------------------------------
# Store plumbing
# ---------------------------------------------------------------------------


def _resolve_db(db_path: Path | str | None) -> Path:
    """Canonical store — ``zolai.config`` (workspace-root ``data/zolai.db``).

    Refuses an empty file so the 0-byte ``zolai-core/data/zolai.db`` stub can
    never be treated as the corpus.
    """
    path = Path(db_path) if db_path is not None else Path(config.paths.db)
    if not path.exists():
        raise FileNotFoundError(f"SQLite store not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"refusing to operate on an empty SQLite store: {path}")
    return path


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _quoted(cols: Sequence[str]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def _audit(
    conn: sqlite3.Connection,
    *,
    table: str,
    row_id: Any,
    field: str,
    old: Any,
    new: Any,
    stamp: str,
    reason: str,
) -> None:
    conn.execute(
        "INSERT INTO data_audit_log "
        "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (table, row_id, field, old, new, stamp, reason),
    )


def _assert_writable(column: str) -> None:
    if column not in WRITABLE_COLUMNS:
        raise AssertionError(f"refusing to write non-EN canonical column: {column}")


def _update_en(conn: sqlite3.Connection, row_id: Any, value: Any) -> None:
    """The single canonical write path (``en_kJV`` only — never ZO/label)."""
    _assert_writable("en_kJV")
    conn.execute(f'UPDATE {TABLE} SET "en_kJV" = ? WHERE id = ?', (value, row_id))


# ---------------------------------------------------------------------------
# Scan (shared by audit + fix)
# ---------------------------------------------------------------------------


def _ensure_archive_table(conn: sqlite3.Connection) -> bool:
    """Create ``bible_verses_archive`` if absent (the only DDL this module runs)."""
    present = ARCHIVE_TABLE in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if present:
        return False
    cols = ",\n    ".join(f'"{c}"' for c in BIBLE_COLUMNS)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLE} (\n"
        f"    {cols},\n"
        "    source_row_id INTEGER NOT NULL UNIQUE,\n"
        "    archive_reason TEXT,\n"
        "    archived_at TEXT\n"
        ")"
    )
    conn.execute(f'CREATE INDEX IF NOT EXISTS ix_archive_ref ON {ARCHIVE_TABLE} (ref)')
    conn.commit()
    return True


def _archived_keys(conn: sqlite3.Connection) -> tuple[set[int], set[str]]:
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if ARCHIVE_TABLE not in tables:
        return set(), set()
    ids = {int(r[0]) for r in conn.execute(f"SELECT source_row_id FROM {ARCHIVE_TABLE}")}
    refs = {str(r[0]) for r in conn.execute(f"SELECT ref FROM {ARCHIVE_TABLE}")}
    return ids, refs


def _scan(
    conn: sqlite3.Connection,
    kjv: dict[tuple[str, int, int], str] | None,
) -> dict[str, Any]:
    """Classify every canonical row and build the fix action list (no writes)."""
    started = time.time()
    cols = _columns(conn, TABLE)
    missing = [c for c in BIBLE_COLUMNS if c not in cols]
    if missing:
        raise LookupError(f"bible_verses schema missing columns: {missing}")

    rows = conn.execute(
        f"SELECT {_quoted(BIBLE_COLUMNS)} FROM {TABLE} ORDER BY id"
    ).fetchall()
    by_cv = {(r["book"], r["chapter"], r["verse"]): r for r in rows}
    triple_counts = Counter((r["book"], r["chapter"], r["verse"]) for r in rows)
    duplicate_triples = sum(1 for n in triple_counts.values() if n > 1)
    norm_index = _kjv_norm_index(kjv) if kjv else {}

    classes: Counter[str] = Counter()
    formula_mismatch = 0
    pair_quality: Counter[str] = Counter()
    needs = {
        "unresolvable": [],
        "neither_ok_pairs": [],
        "en_variants": [],
        "null_en": [],
        "parallel_candidates": [],
        "restore_blocked_null_target": [],
    }
    actions: list[dict[str, Any]] = []
    targets_en_restorable = 0

    for row in rows:
        book, chapter, verse = row["book"], row["chapter"], row["verse"]
        ref = row["ref"]
        en = row["en_kJV"]
        if ref != f"{book} {chapter}:{verse}":
            formula_mismatch += 1

        if not isinstance(chapter, int) or not isinstance(verse, int) or not is_valid(book, chapter, verse):
            classes["impossible_ref"] += 1
            target = None
            if isinstance(chapter, int) and isinstance(verse, int) and chapter > 1:
                target = by_cv.get((book, chapter - 1, verse))
            if target is None:
                # No target ⇒ no EN restore and no downstream remap target.
                # The row is still an impossible ref, so it is archived + removed
                # like every other doomed row (lossless, revertible) and flagged
                # for the founder — never "corrected" by guessing a home.
                needs["unresolvable"].append(
                    {
                        "ref": ref,
                        "reason": "no (book, chapter-1, verse) row present",
                        "row_id": row["id"],
                        "en_null": en is None or en == "",
                        "zo_present": bool(row["zo_tdb77"]),
                    }
                )
                actions.append(
                    {
                        "row": {c: row[c] for c in BIBLE_COLUMNS},
                        "source_ref": ref,
                        "target_ref": None,
                        "en_restore": None,
                    }
                )
                continue

            target_ref = target["ref"]
            dup_ok = kjv is not None and _match(en, kjv.get((book, chapter - 1, verse))) is not None
            target_en = target["en_kJV"]
            target_ok = kjv is not None and _match(target_en, kjv.get((book, chapter - 1, verse))) is not None
            if kjv is None:
                pair_quality["unverified_no_kjv"] += 1
            elif dup_ok and target_ok:
                pair_quality["both_ok"] += 1
            elif dup_ok:
                pair_quality["dup_only_ok"] += 1
            elif target_ok:
                pair_quality["target_only_ok"] += 1
            else:
                pair_quality["neither_ok"] += 1
                needs["neither_ok_pairs"].append(
                    {"ref": ref, "target_ref": target_ref}
                )

            restore: tuple[int, Any, Any] | None = None
            if dup_ok and not target_ok:
                if target_en is None or target_en == "":
                    needs["restore_blocked_null_target"].append(
                        {"ref": target_ref, "dup_ref": ref}
                    )
                else:
                    restore = (target["id"], target_en, en)
                    targets_en_restorable += 1
            actions.append(
                {
                    "row": {c: row[c] for c in BIBLE_COLUMNS},
                    "source_ref": ref,
                    "target_ref": target_ref,
                    "en_restore": restore,
                }
            )
            continue

        if en is None or en == "":
            classes["null_en"] += 1
            needs["null_en"].append({"ref": ref})
            continue

        if kjv is None:
            classes["ok"] += 1
            continue

        if _match(en, kjv.get((book, chapter, verse))) is not None:
            classes["ok"] += 1
            continue

        classes["en_needs_review"] += 1
        cross = _cross_refs(en, ref, kjv, norm_index)
        entry = {
            "ref": ref,
            "en": en[:200],
            "kjv": (kjv.get((book, chapter, verse)) or "")[:200],
        }
        if cross:
            entry["cross_ref"] = cross[:5]
            needs["parallel_candidates"].append(entry)
        else:
            needs["en_variants"].append(entry)

    # --- downstream reference copies ------------------------------------
    # Three distinct signals, deliberately kept apart:
    #   remap_rows     rows whose value is an archived (doomed) ref → remap rewrites them
    #   bad_refs       distinct values that are not a real 66-book verse ref
    #                  (the plan's 528 / 529 "downstream bad refs" figure)
    #   non_verse_rows rows whose value is not even verse-shaped
    #                  (e.g. translations' `news:`/`parallel:` source keys —
    #                  pseudo-references, never bible refs, never touched)
    doomed_refs = {a["source_ref"] for a in actions}
    downstream: dict[str, dict[str, int]] = {}
    live_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, column in DOWNSTREAM:
        if table not in live_tables or column not in _columns(conn, table):
            downstream[table] = {
                "present": 0, "rows": 0, "remap_rows": 0,
                "bad_refs": 0, "bad_ref_rows": 0, "non_verse_rows": 0,
            }
            continue
        total = remap_rows = bad_ref_rows = non_verse = 0
        bad_values: set[Any] = set()
        for r in conn.execute(f'SELECT "{column}" AS ref FROM "{table}"'):
            total += 1
            value = r["ref"]
            if value in doomed_refs:
                remap_rows += 1
            if value is None or not _valid_ref(value):
                bad_ref_rows += 1
                bad_values.add(value)
                if value is None or parse_ref(value) is None:
                    non_verse += 1
        downstream[table] = {
            "present": 1,
            "rows": total,
            "remap_rows": remap_rows,
            "bad_refs": len(bad_values),
            "bad_ref_rows": bad_ref_rows,
            "non_verse_rows": non_verse,
        }

    archive_present = ARCHIVE_TABLE in live_tables
    archive_count = _count(conn, ARCHIVE_TABLE) if archive_present else 0
    audit_reasons = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT reason, COUNT(*) FROM data_audit_log WHERE reason LIKE 'bible_ref_fix%' "
            "GROUP BY reason"
        )
    } if _has_audit_log(conn) else {}

    needs_counts = {k: len(v) for k, v in needs.items()}
    return {
        "db_rows": len(rows),
        "classes": {
            "ok": classes["ok"],
            "impossible_ref": classes["impossible_ref"],
            "en_needs_review": classes["en_needs_review"],
            "null_en": classes["null_en"],
        },
        "ref_formula_mismatches": formula_mismatch,
        "duplicate_ref_triples": duplicate_triples,
        "kjv_loaded": kjv is not None,
        "pair_quality": dict(pair_quality),
        "en_restore_candidates": targets_en_restorable,
        "needs_founder_counts": needs_counts,
        "needs_founder": {k: v[:_SAMPLE_LIMIT] for k, v in needs.items()},
        "downstream": downstream,
        "archive": {"present": archive_present, "count": archive_count},
        "audit_reasons": audit_reasons,
        "actions": actions,
        "duration_s": round(time.time() - started, 2),
    }


def _valid_ref(ref: str) -> bool:
    parsed = parse_ref(ref)
    return parsed is not None and is_valid(*parsed)


def _has_audit_log(conn: sqlite3.Connection) -> bool:
    return any(
        r[0] == "data_audit_log"
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )


def _public(scan: dict[str, Any]) -> dict[str, Any]:
    """Strip the action list (internal) from a scan payload."""
    return {k: v for k, v in scan.items() if k != "actions"}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def run_audit(
    db_path: Path | str | None = None,
    *,
    kjv_path: Path | str | None = None,
) -> dict[str, Any]:
    """Read-only classification of every ``bible_verses`` row (never writes)."""
    db = _resolve_db(db_path)
    kjv = load_kjv(kjv_path)
    conn = _connect(db)
    try:
        scan = _scan(conn, kjv)
        return {
            "db": str(db),
            "generated_at": _utc_now(),
            "kjv_path": str(Path(kjv_path) if kjv_path else default_kjv_path()),
            "target_row_count": TOTAL_VERSES,
            **_public(scan),
        }
    finally:
        conn.close()


def run_fix(
    db_path: Path | str | None = None,
    *,
    kjv_path: Path | str | None = None,
    apply: bool = False,
    fill_null_en: bool = False,
) -> dict[str, Any]:
    """Archive + remove impossible refs (dry-run default).

    With ``apply=True`` each action is one transaction: archive insert,
    ``data_audit_log`` row, canonical DELETE, optional KJV-verified EN restore
    (+ its own audit row).  Nothing else is touched — no ZO/label writes, no
    version/content-hash bumps, no ref that could be left duplicated.
    """
    started = time.time()
    db = _resolve_db(db_path)
    kjv = load_kjv(kjv_path)
    conn = _connect(db)
    try:
        scan = _scan(conn, kjv)
        rows_before = _count(conn, TABLE)
        archived_ids, archived_refs = _archived_keys(conn)

        # Idempotency + duplicate refusal: a row already archived (by id or
        # ref) is never touched again, so a second run archives nothing.
        actions: list[dict[str, Any]] = []
        already = refused = 0
        for action in scan["actions"]:
            if action["row"]["id"] in archived_ids:
                already += 1
            elif action["source_ref"] in archived_refs:
                refused += 1
            else:
                actions.append(action)

        created_archive = False
        archived = en_restored = fill_null = 0
        audit_rows = 0

        # Off-by-default NULL fills at *valid* refs (founder-gated).
        fill_targets: list[tuple[str, str]] = []
        if apply and fill_null_en and kjv is not None:
            for row in conn.execute(
                f"SELECT ref, book, chapter, verse FROM {TABLE} "
                "WHERE en_kJV IS NULL OR en_kJV = ''"
            ):
                text = kjv.get((row["book"], row["chapter"], row["verse"]))
                if text:
                    fill_targets.append((row["ref"], text))

        if apply:
            created_archive = _ensure_archive_table(conn)
            stamp = _utc_now()
            for action in actions:
                row = action["row"]
                restore = action["en_restore"]
                try:
                    _archive_row(conn, row, stamp)
                    _audit(
                        conn, table=TABLE, row_id=row["id"], field="ref",
                        old=row["ref"], new=f"ARCHIVED:{row['ref']}",
                        stamp=stamp, reason=REASON,
                    )
                    conn.execute(f"DELETE FROM {TABLE} WHERE id = ?", (row["id"],))
                    if restore is not None:
                        target_id, old_en, new_en = restore
                        _update_en(conn, target_id, new_en)
                        _audit(
                            conn, table=TABLE, row_id=target_id, field="en_kJV",
                            old=old_en, new=new_en, stamp=stamp,
                            reason=EN_RESTORE_REASON,
                        )
                    conn.commit()
                except sqlite3.IntegrityError:
                    # UNIQUE(source_row_id)/UNIQUE(ref) tripped: refuse the
                    # write, roll the whole action back, never merge rows.
                    conn.rollback()
                    refused += 1
                    continue
                archived += 1
                audit_rows += 1
                if restore is not None:
                    en_restored += 1
                    audit_rows += 1

            for ref, text in fill_targets:
                live = conn.execute(
                    f"SELECT id FROM {TABLE} WHERE ref = ?", (ref,)
                ).fetchone()
                if live is None:
                    continue
                _update_en(conn, live["id"], text)
                _audit(
                    conn, table=TABLE, row_id=live["id"], field="en_kJV",
                    old=None, new=text, stamp=_utc_now(), reason=FILL_NULL_REASON,
                )
                fill_null += 1
                audit_rows += 1
            conn.commit()

        rows_after = _count(conn, TABLE)
        audit_log_total = (
            _count(conn, "data_audit_log") if _has_audit_log(conn) else 0
        )
        return {
            "db": str(db),
            "apply": apply,
            "kjv_loaded": kjv is not None,
            "rows_before": rows_before,
            "rows_after": rows_after,
            "row_target": TOTAL_VERSES,
            "classes": scan["classes"],
            "actions_pending": len(scan["actions"]),
            "actions": len(actions),
            "already_archived": already,
            "refused_duplicate": refused,
            "archived": archived,
            "en_restored": en_restored,
            "fill_null_en": fill_null,
            "audit_rows_written": audit_rows,
            "audit_log_total_after": audit_log_total,
            "archive_created": created_archive,
            "archive_count": (
                _count(conn, ARCHIVE_TABLE) if ARCHIVE_TABLE in {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                } else 0
            ),
            "unresolvable": scan["needs_founder_counts"]["unresolvable"],
            "needs_founder_counts": scan["needs_founder_counts"],
            "pair_quality": scan["pair_quality"],
            "en_restore_candidates": scan["en_restore_candidates"],
            "duration_s": round(time.time() - started, 2),
        }
    finally:
        conn.close()


def _archive_row(conn: sqlite3.Connection, row: dict[str, Any], stamp: str) -> None:
    """Full-row copy into ``bible_verses_archive`` (lossless, revertible)."""
    values = [row[c] for c in BIBLE_COLUMNS]
    conn.execute(
        f"INSERT INTO {ARCHIVE_TABLE} ({_quoted(BIBLE_COLUMNS)}, "
        'source_row_id, archive_reason, archived_at) '
        f"VALUES ({', '.join('?' * (len(BIBLE_COLUMNS) + 3))})",
        [*values, row["id"], REASON, stamp],
    )


def run_revert(
    db_path: Path | str | None = None,
    *,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Restore archived rows byte-identically and undo EN restores (dry-run)."""
    started = time.time()
    db = _resolve_db(db_path)
    conn = _connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if ARCHIVE_TABLE not in tables:
            return {
                "db": str(db), "apply": apply, "archive_present": False,
                "archive_rows": 0, "restored": 0, "refused_duplicate": 0,
                "en_restores_seen": 0, "en_restores_reversed": 0,
                "en_restores_conflict": 0, "audit_rows_written": 0,
                "archive_rows_after": 0, "duration_s": round(time.time() - started, 2),
            }

        archived = conn.execute(
            f"SELECT {_quoted(BIBLE_COLUMNS)}, source_row_id, ref FROM {ARCHIVE_TABLE} "
            "ORDER BY source_row_id"
        ).fetchall()
        restores = conn.execute(
            "SELECT id, row_id, old_value, new_value FROM data_audit_log "
            "WHERE reason IN (?, ?) ORDER BY id",
            (EN_RESTORE_REASON, FILL_NULL_REASON),
        ).fetchall() if _has_audit_log(conn) else []

        rows_before = _count(conn, TABLE)
        restored = refused = reversed_ = conflict = audit_rows = 0

        if apply:
            stamp = _utc_now()
            for a in archived:
                source_id = a["source_row_id"]
                ref = a["ref"]
                clash = conn.execute(
                    f"SELECT 1 FROM {TABLE} WHERE id = ? OR ref = ?",
                    (source_id, ref),
                ).fetchone()
                if clash:
                    refused += 1
                    continue
                conn.execute(
                    f"INSERT INTO {TABLE} ({_quoted(BIBLE_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(BIBLE_COLUMNS))})",
                    [a[c] for c in BIBLE_COLUMNS],
                )
                _audit(
                    conn, table=TABLE, row_id=source_id, field="ref",
                    old=f"ARCHIVED:{ref}", new=ref, stamp=stamp, reason=REVERT_REASON,
                )
                conn.execute(
                    f"DELETE FROM {ARCHIVE_TABLE} WHERE source_row_id = ?",
                    (source_id,),
                )
                restored += 1
                audit_rows += 1
                if restored % batch_size == 0:
                    conn.commit()

            for log in restores:
                live = conn.execute(
                    f'SELECT "en_kJV" FROM {TABLE} WHERE id = ?', (log["row_id"],)
                ).fetchone()
                if live is None or live["en_kJV"] != log["new_value"]:
                    conflict += 1
                    continue
                _update_en(conn, log["row_id"], log["old_value"])
                _audit(
                    conn, table=TABLE, row_id=log["row_id"], field="en_kJV",
                    old=log["new_value"], new=log["old_value"],
                    stamp=stamp, reason=REVERT_REASON,
                )
                reversed_ += 1
                audit_rows += 1
            conn.commit()

        return {
            "db": str(db),
            "apply": apply,
            "archive_present": True,
            "archive_rows": len(archived),
            "rows_before": rows_before,
            "rows_after": _count(conn, TABLE),
            "restored": restored,
            "refused_duplicate": refused,
            "en_restores_seen": len(restores),
            "en_restores_reversed": reversed_,
            "en_restores_conflict": conflict,
            "audit_rows_written": audit_rows,
            "archive_rows_after": _count(conn, ARCHIVE_TABLE),
            "duration_s": round(time.time() - started, 2),
        }
    finally:
        conn.close()


def run_remap(
    db_path: Path | str | None = None,
    *,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Phase 2 — rewrite downstream copies of archived refs (dry-run default)."""
    started = time.time()
    db = _resolve_db(db_path)
    conn = _connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        mapping: dict[str, str] = {}
        unmapped_refs: set[str] = set()
        if ARCHIVE_TABLE in tables:
            live_refs = {r[0] for r in conn.execute(f"SELECT ref FROM {TABLE}")}
            for a in conn.execute(
                f"SELECT ref, book, chapter, verse FROM {ARCHIVE_TABLE}"
            ):
                if a["chapter"] <= 1:
                    # Chapter 1 has no (chapter-1) predecessor — the archived
                    # ref has no home; downstream copies stay as they are.
                    unmapped_refs.add(a["ref"])
                    continue
                target = f"{a['book']} {a['chapter'] - 1}:{a['verse']}"
                if a["ref"] == target:
                    continue
                if not _valid_ref(target) or target not in live_refs:
                    # e.g. 1CH 19:20 → 1CH 18:20, but 1 Chronicles 18 has 17
                    # verses: no real verse to point at.  Report, never rewrite
                    # downstream rows onto a fabricated reference.
                    unmapped_refs.add(a["ref"])
                    continue
                mapping[a["ref"]] = target

        results: dict[str, dict[str, int]] = {}
        audit_rows = 0
        stamp = _utc_now()
        for table, column in DOWNSTREAM:
            if table not in tables:
                results[table] = {"present": 0, "scanned": 0, "remapped": 0, "unmapped": 0}
                continue
            scanned = remapped = unmapped = 0
            for row in conn.execute(f'SELECT id, "{column}" AS ref FROM "{table}"'):
                scanned += 1
                new_ref = mapping.get(row["ref"])
                if new_ref is None:
                    if row["ref"] in unmapped_refs:
                        unmapped += 1
                    continue
                if not _valid_ref(new_ref):
                    raise AssertionError(f"remap target is not a real verse: {new_ref}")
                if apply:
                    _assert_writable_ref(table, column)
                    conn.execute(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE id = ?',
                        (new_ref, row["id"]),
                    )
                    _audit(
                        conn, table=table, row_id=row["id"], field=column,
                        old=row["ref"], new=new_ref, stamp=stamp, reason=REMAP_REASON,
                    )
                    audit_rows += 1
                remapped += 1
                if apply and remapped % batch_size == 0:
                    conn.commit()
            if apply:
                conn.commit()
            results[table] = {
                "present": 1,
                "scanned": scanned,
                "remapped": remapped,
                "unmapped": unmapped,
            }

        return {
            "db": str(db),
            "apply": apply,
            "mapping_size": len(mapping),
            "unmapped_refs": sorted(unmapped_refs),
            "downstream": results,
            "audit_rows_written": audit_rows,
            "audit_log_total_after": _count(conn, "data_audit_log")
            if _has_audit_log(conn) else 0,
            "duration_s": round(time.time() - started, 2),
        }
    finally:
        conn.close()


#: Downstream ref columns are ref-correction targets (allowed writes).
_DOWNSTREAM_COLUMNS = frozenset({(t, c) for t, c in DOWNSTREAM})


def _assert_writable_ref(table: str, column: str) -> None:
    if (table, column) not in _DOWNSTREAM_COLUMNS:
        raise AssertionError(f"refusing to write {table}.{column}")


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def write_report(audit: dict[str, Any], path: Path | str) -> Path:
    """Write the audit payload as a markdown report (read-only)."""
    lines = [
        "# Bible ref audit — read-only report",
        "",
        f"- db: `{audit['db']}`",
        f"- generated: {audit['generated_at']}",
        f"- kjv ground truth: `{audit['kjv_path']}` (loaded: {audit['kjv_loaded']})",
        f"- target row count: {audit['target_row_count']}",
        "",
        "## Classification",
        "",
        "| Class | Rows |",
        "|---|---|",
    ]
    for key, value in audit["classes"].items():
        lines.append(f"| `{key}` | {value} |")
    lines += [
        "",
        f"- ref-formula mismatches: {audit['ref_formula_mismatches']}",
        f"- duplicate (book,chapter,verse): {audit['duplicate_ref_triples']}",
        f"- archive: present={audit['archive']['present']} count={audit['archive']['count']}",
        "",
        "## Pair quality (impossible row → target)",
        "",
        "| Pair | Count |",
        "|---|---|",
    ]
    for key, value in audit["pair_quality"].items():
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Needs-founder (report only — never auto-corrected)", ""]
    for key, value in audit["needs_founder_counts"].items():
        lines.append(f"- **{key}**: {value}")
        for item in audit["needs_founder"].get(key, [])[:20]:
            lines.append(f"  - `{json.dumps(item, ensure_ascii=False)}`")
    lines += ["", "## Downstream copies", ""]
    for table, stats in audit["downstream"].items():
        lines.append(f"- `{table}`: {stats}")
    lines += ["", "## Prior bible_ref_fix audit rows", ""]
    for reason, count in audit["audit_reasons"].items():
        lines.append(f"- `{reason}`: {count}")
    lines.append("")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
