#!/usr/bin/env python3
"""G3 — wire vocabulary confidence / review status / glosses to the evidence store.

Root cause (see plan): ``vocabulary`` came from a one-shot import — 104,906 rows
with 100% NULL ``confidence``, 100% ``review_status='unknown'``, 100%
``status='OBSERVED'`` — and it inherited corrupted dictionary glosses (``sanga``
= ``'31'`` because ``dictionary.english`` holds the same bad value).  The good
evidence store never fed back into it.

Evidence per headword
    sources  = ``attestation_index`` grouped by word (dict / corpus / bible / extra)
    source_count = max(distinct attestation sources,
                       ``word_observation_stats.attestation`` JSON source_count)
    freq     = max(word_observation_stats.frequency,
                   Σ word_usage.total_freq)          — reported, never rewritten
    obs      = ``word_observation_stats.attestation`` JSON (confidence/source_count)

Mappings (deterministic, idempotent)
    confidence  0 src → 0.10 · 1 → 0.50 · 2 → 0.70 · 3 → 0.85 · ≥4 → 0.95
    review      0 src → unknown · 1 → attested · ≥2 → verified
    rows already ``review_status='reviewed'`` (the human path —
    ``record_review_router.REVIEWED_STATUS``) are NEVER downgraded or rewritten;
    ``status`` and ``frequency`` columns are left untouched (no invented enum).

Gloss rules (deliberately conservative — no mass semantic overwrite)
    candidates  = word_alignments (≥3 hits, alpha-only)
                + ``dictionary.english_clean``
                + ``zolai_vocabulary.english`` (curated, corrupt-class only)
    fix when     current is corrupt-class (digit-only / contains a digit /
                 empty / <2 chars) and a candidate exists
                 — auto-fixes ``sanga``='31';
                 OR current has ZERO alignment support while a candidate has
                 ≥10 hits — the ``za``='a hundered' path.
    everything else that disagrees → report-only jsonl for the owner
    (``data/reports/vocab_gloss_disagreements_<ts>.jsonl``).

Local only: stdlib sqlite3, no network, no external AI provider calls.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # allows `python scripts/learning/…py`
    sys.path.insert(0, str(_REPO_ROOT))

MIN_TARGET_BYTES = 1_000_000_000
BUSY_TIMEOUT_MS = 60_000

REVIEWED_STATUS = "reviewed"  # human path — never downgraded
PROTECTED_STATUSES = frozenset({REVIEWED_STATUS})

CONFIDENCE_BY_SOURCES = {0: 0.10, 1: 0.50, 2: 0.70, 3: 0.85}
DEFAULT_CONFIDENCE = 0.95  # ≥4 sources
REVIEW_BY_SOURCES = {0: "unknown", 1: "attested"}
DEFAULT_REVIEW = "verified"  # ≥2 sources

MIN_ALIGNMENT_HITS = 3
STRONG_ALIGNMENT_HITS = 10

_DIGIT_RE = re.compile(r"\d")
_ALPHA_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")  # alignment candidates: alpha-only
_VALID_GLOSS_RE = re.compile(r"^[A-Za-z][A-Za-z' \-]*$")  # dict/curated glosses

REPORTS_DIR = _REPO_ROOT / "data" / "reports"


def confidence_for(source_count: int) -> float:
    """Plan mapping: {0: 0.10, 1: 0.50, 2: 0.70, 3: 0.85, ≥4: 0.95}."""
    return CONFIDENCE_BY_SOURCES.get(source_count, DEFAULT_CONFIDENCE)


def review_for(source_count: int) -> str:
    """Plan mapping: {0: unknown, 1: attested, ≥2: verified}."""
    return REVIEW_BY_SOURCES.get(source_count, DEFAULT_REVIEW)


# ── preflight / connection ──────────────────────────────────────────────────


def preflight(db_path: str | Path, *, min_bytes: int = MIN_TARGET_BYTES) -> Path:
    """Refuse missing / sub-1 GB targets (the 0-byte decoy can never be written)."""
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"[G3] target database not found: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise SystemExit(
            f"[G3] target database {path} is {size:,} bytes — refusing: "
            f"a real target must be ≥1 GB ({min_bytes:,} bytes)."
        )
    return path


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # isolation_level=None → we own BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


# ── evidence ────────────────────────────────────────────────────────────────


def _empty_evidence() -> dict[str, Any]:
    return {
        "sources": [],
        "source_count": 0,
        "obs_source_count": 0,
        "obs_confidence": None,
        "freq": 0,
    }


def collect_evidence(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Evidence per vocabulary headword (keys are lower-cased headwords).

    Every ``vocabulary`` headword gets an entry (0 sources when nothing backs
    it), so callers never need a KeyError guard.
    """
    evidence: dict[str, dict[str, Any]] = {}

    def _entry(key: str) -> dict[str, Any]:
        return evidence.setdefault(key, _empty_evidence())

    for (headword,) in conn.execute("SELECT headword FROM vocabulary"):
        _entry((headword or "").lower())

    if _has_table(conn, "attestation_index"):
        for word, source in conn.execute(
            "SELECT word, source FROM attestation_index ORDER BY word, source"
        ):
            entry = _entry((word or "").lower())
            if source not in entry["sources"]:
                entry["sources"].append(source)

    if _has_table(conn, "word_observation_stats"):
        for normalized_form, frequency, source_count, attestation in conn.execute(
            "SELECT normalized_form, frequency, source_count, attestation"
            " FROM word_observation_stats"
        ):
            entry = _entry((normalized_form or "").lower())
            entry["obs_source_count"] = max(
                entry["obs_source_count"], int(source_count or 0)
            )
            entry["freq"] = max(entry["freq"], int(frequency or 0))
            try:
                payload = json.loads(attestation or "{}")
            except (ValueError, TypeError):
                payload = {}
            if isinstance(payload, dict):
                if payload.get("confidence") is not None:
                    try:
                        entry["obs_confidence"] = float(payload["confidence"])
                    except (TypeError, ValueError):
                        pass
                if payload.get("source_count"):
                    entry["obs_source_count"] = max(
                        entry["obs_source_count"], int(payload["source_count"])
                    )

    for word, total in conn.execute(
        "SELECT word, SUM(total_freq) FROM word_usage GROUP BY word"
    ):
        entry = _entry((word or "").lower())
        entry["freq"] = max(entry["freq"], int(total or 0))

    for entry in evidence.values():
        entry["source_count"] = max(
            len(entry["sources"]), entry["obs_source_count"]
        )
    return evidence


def alignment_candidates(conn: sqlite3.Connection) -> dict[str, list[tuple[str, int]]]:
    """``zolai_word`` → [(english_word, hits)] with ≥3 hits, alpha-only, best first."""
    out: dict[str, list[tuple[str, int]]] = {}
    if not _has_table(conn, "word_alignments"):
        return out
    rows = conn.execute(
        "SELECT zolai_word, english_word, COUNT(*) AS hits"
        " FROM word_alignments"
        " GROUP BY zolai_word, english_word"
        " HAVING hits >= ?",
        (MIN_ALIGNMENT_HITS,),
    )
    for zolai_word, english_word, hits in rows:
        if not _ALPHA_RE.match(english_word or ""):
            continue
        out.setdefault((zolai_word or "").lower(), []).append((english_word, int(hits)))
    for key in out:
        out[key].sort(key=lambda item: (-item[1], item[0]))
    return out


def dictionary_candidates(conn: sqlite3.Connection) -> dict[str, str]:
    """``zolai`` → first usable ``english_clean``/``english`` gloss."""
    out: dict[str, str] = {}
    if not _has_table(conn, "dictionary"):
        return out
    has_deleted = any(
        row[1] == "is_deleted" for row in conn.execute("PRAGMA table_info(dictionary)")
    )
    where = " WHERE is_deleted = 0" if has_deleted else ""
    for zolai, english_clean, english in conn.execute(
        f"SELECT zolai, english_clean, english FROM dictionary{where}"
    ):
        key = (zolai or "").lower()
        if key in out:
            continue
        for candidate in (english_clean, english):
            if candidate and _is_valid_gloss(candidate):
                out[key] = candidate
                break
    return out


def curated_candidates(conn: sqlite3.Connection) -> dict[str, str]:
    """``zolai_vocabulary.english`` — curated glosses used for corrupt-class fixes."""
    if not _has_table(conn, "zolai_vocabulary"):
        return {}
    out: dict[str, str] = {}
    for zolai, english in conn.execute(
        "SELECT zolai, english FROM zolai_vocabulary"
    ):
        key = (zolai or "").lower()
        if key in out or not english:
            continue
        if _is_valid_gloss(english):
            out[key] = english
    return out


def _is_valid_gloss(value: str) -> bool:
    """Non-corrupt: not digit-only, no digits, ≥2 chars, sane characters."""
    text = (value or "").strip()
    if len(text) < 2 or _DIGIT_RE.search(text):
        return False
    return bool(_VALID_GLOSS_RE.match(text))


def _is_corrupt_gloss(value: str) -> bool:
    """Plan corrupt-class: ``^\\d+$``, contains a digit, empty, or <2 chars."""
    text = (value or "").strip()
    if not text or len(text) < 2:
        return True
    return bool(_DIGIT_RE.search(text))


def choose_gloss(
    current: str,
    align: list[tuple[str, int]],
    dictionary_gloss: str | None,
    curated_gloss: str | None,
) -> tuple[str, str | None, str]:
    """Decide what to do with one gloss.

    Returns:
        ``(action, replacement, reason)`` with ``action`` in
        ``{"keep", "fix", "report"}``.
    """
    current_text = (current or "").strip()
    if _is_corrupt_gloss(current_text):
        replacement: str | None = None
        if align:
            replacement = align[0][0]
        elif dictionary_gloss:
            replacement = dictionary_gloss
        elif curated_gloss:
            replacement = curated_gloss
        if replacement and replacement.strip() != current_text:
            return "fix", replacement.strip(), "corrupt_class"
        return "report", None, "corrupt_no_candidate"

    supported = any(word.lower() == current_text.lower() for word, _hits in align)
    if not supported and align and align[0][1] >= STRONG_ALIGNMENT_HITS:
        return "fix", align[0][0], "zero_support_strong_candidate"
    if align and align[0][0].lower() != current_text.lower():
        return "report", None, "semantic_disagreement"
    return "keep", None, "aligned"


# ── main step ───────────────────────────────────────────────────────────────


def wire(
    db_path: str | Path,
    *,
    dry_run: bool = False,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update ``vocabulary.confidence`` / ``review_status`` / ``english``.

    Args:
        db_path: Target database.
        dry_run: Compute and report counts without writing a single row (and
            without writing the disagreement report file).
        report_path: Where to put the gloss disagreement jsonl (owner review).

    Returns:
        Summary dict (row counts only).
    """
    conn = _connect(db_path)
    try:
        evidence = collect_evidence(conn)
        align = alignment_candidates(conn)
        dictionary_gloss = dictionary_candidates(conn)
        curated_gloss = curated_candidates(conn)

        rows = conn.execute(
            "SELECT id, headword, english, confidence, review_status FROM vocabulary"
        ).fetchall()

        updates: list[tuple[Any, str, str, int]] = []
        report_lines: list[str] = []
        counters = {
            "total_rows": len(rows),
            "skipped_reviewed": 0,
            "confidence_updates": 0,
            "review_updates": 0,
            "gloss_fixes": 0,
            "gloss_reports": 0,
        }

        for row in rows:
            if row["review_status"] in PROTECTED_STATUSES:
                counters["skipped_reviewed"] += 1
                continue

            headword = row["headword"] or ""
            entry = evidence.get(headword.lower()) or _empty_evidence()
            source_count = int(entry["source_count"])
            confidence = confidence_for(source_count)
            review = review_for(source_count)

            action, replacement, reason = choose_gloss(
                row["english"] or "",
                align.get(headword.lower(), []),
                dictionary_gloss.get(headword.lower()),
                curated_gloss.get(headword.lower()),
            )

            need_confidence = (
                row["confidence"] is None
                or abs(float(row["confidence"]) - confidence) > 1e-9
            )
            need_review = row["review_status"] != review
            need_gloss = replacement is not None and replacement != row["english"]

            if need_confidence:
                counters["confidence_updates"] += 1
            if need_review:
                counters["review_updates"] += 1
            if need_gloss:
                counters["gloss_fixes"] += 1
            if action == "report":
                counters["gloss_reports"] += 1
                best = align.get(headword.lower(), [])
                report_lines.append(
                    json.dumps(
                        {
                            "headword": headword,
                            "current": row["english"],
                            "reason": reason,
                            "alignment_best": best[0][0] if best else None,
                            "alignment_hits": best[0][1] if best else 0,
                            "dictionary": dictionary_gloss.get(headword.lower()),
                            "curated": curated_gloss.get(headword.lower()),
                            "action": "report",
                        },
                        ensure_ascii=False,
                    )
                )

            if need_confidence or need_review or need_gloss:
                updates.append(
                    (
                        confidence if need_confidence else row["confidence"],
                        review if need_review else row["review_status"],
                        replacement if need_gloss else row["english"],
                        int(row["id"]),
                    )
                )

        counters["would_update"] = len(updates)
        if dry_run:
            counters["dry_run"] = True
            counters["updated"] = 0
            counters["report_path"] = None
            return counters

        if updates:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(
                    "UPDATE vocabulary SET confidence = ?, review_status = ?,"
                    " english = ? WHERE id = ?",
                    updates,
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        path: Path | None = None
        if report_lines:
            path = Path(report_path) if report_path else (
                REPORTS_DIR / f"vocab_gloss_disagreements_{datetime.now():%Y%m%dT%H%M%S}.jsonl"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

        counters["dry_run"] = False
        counters["updated"] = len(updates)
        counters["report_path"] = str(path) if path else None
        return counters
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wire vocabulary confidence / review status / glosses to evidence"
    )
    parser.add_argument("--db", default=None, help="target sqlite database (default: canonical)")
    parser.add_argument("--dry-run", action="store_true", help="write nothing, report counts")
    parser.add_argument("--report", default=None, help="gloss disagreement jsonl path")
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db)
    else:
        from zolai.config import config

        db_path = Path(config.paths.zolai_db)

    try:
        preflight(db_path)
        summary = wire(db_path, dry_run=args.dry_run, report_path=args.report)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.code) if isinstance(exc.code, int) else 2
    except (RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if summary["dry_run"] else "WROTE"
    print(f"[G3] {mode} vocabulary evidence wiring {db_path}")
    print(f"  rows                 : {summary['total_rows']:,}")
    print(f"  confidence updates   : {summary['confidence_updates']:,}"
          f" (would update {summary['would_update']:,})")
    print(f"  review-status updates: {summary['review_updates']:,}")
    print(f"  gloss fixes          : {summary['gloss_fixes']:,}")
    print(f"  gloss reports        : {summary['gloss_reports']:,}")
    print(f"  skipped (reviewed)   : {summary['skipped_reviewed']:,}")
    if summary.get("report_path"):
        print(f"  report               : {summary['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
