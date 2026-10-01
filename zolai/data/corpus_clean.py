"""C1 corpus clean — read-only audit + in-place ZVS-2018 normalisation.

Authoritative spec: ``docs/planning/C1_CORPUS_CLEAN_PLAN.md`` (workspace root
repo).  Two commands are exposed through ``zolai corpus audit|clean``:

``zolai corpus audit [--report PATH]``
    Read-only defect scan: ZVS forbidden forms, ``suah`` conflicts, HTML
    entities/tags, whitespace, word-field sanity, JSON parse failures and
    exact-duplicate groups.  Writes a markdown report when ``--report`` is
    given.  Never writes to the database.

``zolai corpus clean [--table T] [--apply]``
    Dry-run by default (reports cells that *would* change).  ``--apply``
    writes in batches: one ``UPDATE`` of a single field plus one
    ``data_audit_log`` row per changed cell, one transaction per batch, with
    the cursor persisted to ``<db dir>/.corpus_clean_state.json`` so an
    interrupted run resumes.  A completed run leaves ``in_progress: false``,
    so a second ``--apply`` re-scans from the top and proves idempotency
    (0 new audit rows).

Invariants
----------
- Canonical store only (``zolai.config`` → workspace-root ``data/zolai.db``);
  ``*_import`` staging tables never appear in :data:`COLUMN_REGISTRY`.
- Additive writes only: **no DELETEs**, no ``version``/``content_hash`` bumps.
- Single ZVS source: ``zolai.zvs.validate()`` + ``zolai.zvs.rules_data`` — the
  cheap pre-check regex is compiled from ``zolai.zvs.rules.default_rules()``
  (same source; the pre-check is an exact union of the rule patterns, so a
  miss can never hide a violation).
- ``suah`` is **never** rewritten (rules_data → ``chuak`` vs AGENTS →
  ``suahtakna``: two canonical docs disagree → review-needs / needs-founder).
- UNIQUE-index collisions are never written: a cleaned value already owned by
  another row would merge two rows (a destructive dedupe) → refused and
  counted as the ``unique`` review-need instead (row counts never change).
  Keys are checked against the live table *and* a reserved-key set so dry-run
  and apply behave identically; the write is still wrapped in an
  ``IntegrityError`` guard as a last line of defence.
- Bible Hakha/Falam columns ``zo_hcl06``/``zo_fcl`` are audit-only — never
  written (converting them would falsify the parallel versions).
- EN/MY columns and label columns (``pattern``/``structure``/``pattern_text``)
  are never selected.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from ..config import config
from ..zvs import validate
from ..zvs.exceptions import ExceptionRegistry
from ..zvs.rules import default_rules
from ..zvs.rules_data import HISTORICAL_EXCEPTIONS

Kind = Literal["word", "sentence", "json"]
Ctx = Literal["modern", "scripture"]

#: ``data_audit_log.reason`` for every cell written by this module.
REASON = "corpus_clean_v1 by cli"

#: Rows per batched transaction (plan §4).
DEFAULT_BATCH_SIZE = 5000

#: Resume-state filename, written next to the database (plan §4).
STATE_FILENAME = ".corpus_clean_state.json"

#: Word fields must round-trip as ``^[a-z][a-z-]*$`` after cleaning (plan §3).
WORD_SANITY_RE = re.compile(r"^[a-z][a-z-]*$")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_SENTENCE_RE = re.compile(r"[^\S\n]+")  # collapse non-newline whitespace (incl. NBSP)
_WS_WORD_RE = re.compile(r"\s+")

#: Exact union of the default ZVS rule patterns (single source, no fork).
_ZVS_PRECHECK = re.compile(
    "|".join(f"(?:{rule.pattern.pattern})" for rule in default_rules()),
    re.IGNORECASE,
)

#: Non-empty placeholder that keeps ``zolai.zvs.validate()`` from eagerly
#: loading Bible words through the SQLAlchemy manager (tests stay hermetic;
#: the real word set is computed from the store being scanned instead).
_BIBLE_WORDS_SENTINEL = "\x00bible-words-unavailable"

Registries = dict[str, ExceptionRegistry]

_DEFAULT_REGISTRIES: Registries = {}


# ---------------------------------------------------------------------------
# Column inventory — verified against the live PRAGMA (plan §2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnSpec:
    """One ZO-bearing column in the cleaning registry."""

    table: str
    column: str
    kind: Kind
    ctx: Ctx = "modern"
    #: Row-level filter as ``(column, value)`` — direction-aware translations.
    only_when: tuple[str, str] | None = None
    #: ``False`` for audit-only columns that must never be written.
    writable: bool = True
    note: str = ""


COLUMN_REGISTRY: tuple[ColumnSpec, ...] = (
    ColumnSpec("dictionary", "zolai", "word"),
    # LIVE DELTA vs plan: `translations` is a JSON list, `translations_clean`
    # is a plain string (the plan called both "JSON list") → word-kind gate.
    ColumnSpec("dictionary_en_zo", "translations", "json"),
    ColumnSpec("dictionary_en_zo", "translations_clean", "word", note="plain string on live schema"),
    ColumnSpec("bible_verses", "zo_tdb77", "sentence", ctx="scripture"),
    ColumnSpec("bible_verses", "zo_tedim2010", "sentence", ctx="scripture"),
    ColumnSpec("bible_verses", "zo_tedim1932", "sentence", ctx="scripture"),
    ColumnSpec("bible_verses", "zo_hcl06", "sentence", ctx="scripture", writable=False, note="Hakha — audit only"),
    ColumnSpec("bible_verses", "zo_fcl", "sentence", ctx="scripture", writable=False, note="Falam — audit only"),
    ColumnSpec("phrases", "zolai", "word"),
    ColumnSpec("phrases", "examples", "json"),
    ColumnSpec("translations", "target", "sentence", only_when=("direction", "en_to_zo")),
    ColumnSpec("translations", "source", "sentence", only_when=("direction", "zo_to_en")),
    ColumnSpec("vocabulary", "headword", "word"),
    ColumnSpec("vocabulary", "examples", "json"),
    ColumnSpec("word_usage", "word", "word"),
    ColumnSpec("word_usage", "co_occurring_words", "json"),
    ColumnSpec("training_exercises", "zolai", "sentence"),
    ColumnSpec("proverbs", "zolai", "sentence"),
    ColumnSpec("word_collocations", "word1", "word"),
    ColumnSpec("word_collocations", "word2", "word"),
    ColumnSpec("zolai_vocabulary", "zolai", "word"),
    ColumnSpec("zolai_vocabulary", "example_zo", "sentence"),
    ColumnSpec("zolai_bible_analysis", "zolai", "sentence"),
    ColumnSpec("zolai_word_usage", "word", "word"),
    # LIVE DELTA: all 85,045 `contexts` values are NULL on the live store —
    # the column stays in the registry so a future backfill is covered.
    ColumnSpec("zolai_word_usage", "contexts", "json", note="all NULL on live store"),
    ColumnSpec("zolai_grammar_patterns", "zolai_example", "sentence"),
    ColumnSpec("zolai_proverbs_idioms", "zolai", "sentence"),
)

#: Exact-duplicate detection keys (detect + count only — DELETE is destructive
#: → needs-founder).  Plain ``GROUP BY … HAVING COUNT(*) > 1`` matches the
#: plan's cited figures (training_exercises 26,893 · bible_verses/zo_tdb77 624
#: · dictionary/vocabulary 0).  Tables without a ZO+EN prose pair have no key.
DUP_KEYS: dict[str, tuple[str, ...]] = {
    "dictionary": ("zolai", "english"),
    "dictionary_en_zo": ("translations_clean", "headword"),
    "bible_verses": ("zo_tdb77",),
    "phrases": ("zolai", "english"),
    "translations": ("source", "target"),
    "vocabulary": ("headword", "english"),
    "training_exercises": ("zolai", "english"),
    "proverbs": ("zolai", "english"),
    "zolai_vocabulary": ("zolai", "english"),
    "zolai_bible_analysis": ("zolai", "english"),
    "zolai_grammar_patterns": ("zolai_example", "english_translation"),
    "zolai_proverbs_idioms": ("zolai", "english_translation"),
    "word_collocations": ("word1", "word2"),
}

#: Documented exclusions (rendered into the report).
EXCLUSIONS: tuple[str, ...] = (
    "`*_import` staging tables — never scanned, never written.",
    "EN columns (`english`, `english_clean`, `english_translation`, `en_kJV`, "
    "`ref`/`book`/`chapter` labels) — byte-identical after apply.",
    "MY columns (`myanmar`, `myanmar_judson`) — byte-identical after apply.",
    "`grammar_patterns` — no ZO prose column (pattern metadata only).",
    "`articles` / `wiki_lessons` — mixed-language prose, 'in doubt' → left alone.",
    "Label columns in `zolai_grammar_patterns` (`pattern`, `structure`, "
    "`pattern_text`) — structural labels, not prose.",
    "`bible_verses.zo_hcl06` / `bible_verses.zo_fcl` — audit-only (Hakha/Falam "
    "parallel versions; converting them would falsify the corpus).",
)

#: Open questions the cleaner deliberately does not decide (plan §6/§7).
NEEDS_FOUNDER: tuple[str, ...] = (
    "**`suah` target** — rules_data says `chuak`, AGENTS/ZVS says `suahtakna` "
    "(context-dependent). C1 never rewrites `suah`; every hit is counted as a "
    "review-need for a founder decision.",
    "**Dedupe DELETEs** — duplicate groups are counted only (destructive "
    "removal needs an explicit founder decision; row counts are unchanged).",
    "**Unique-index collisions** — a cleaned value that already exists in "
    "another row of the same table (e.g. `dictionary.zolai` has a UNIQUE "
    "index) would merge two rows. C1 refuses the write and counts the cell as "
    "a review-need (`unique`); row counts never change.",
    "**Junk / word-sanity headwords** — word fields whose cleaned value fails "
    "`^[a-z][a-z-]*$` are left untouched (changing them could alter headword "
    "identity); they are counted as review-needs.",
    "**Fate of `zo_hcl06` / `zo_fcl`** — audit quantifies their historical-form "
    "counts; C1 never writes them.",
    "**Mixed EN/ZO JSON content** — `dictionary_en_zo.translations` and "
    "`word_usage.co_occurring_words` contain English glosses alongside ZO; "
    "plan §3 treats JSON string values as ZO, so a small number of ZVS "
    "rewrites touch EN-looking cells (reversible via `data_audit_log`).",
)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@dataclass
class CellOutcome:
    """Result of cleaning one stored cell (string or JSON column)."""

    original: str
    cleaned: str
    changed: bool = False
    #: Word-kind sanity gate refused the change (no write, review-need).
    blocked: bool = False
    html_fixed: bool = False
    ws_fixed: bool = False
    zvs_hits: int = 0
    zvs_applied: int = 0
    suah_hits: int = 0
    #: ``None`` for non-word kinds; ``True`` when the cleaned value fails the
    #: word sanity regex (defect class; blocked only when also ``changed``).
    sanity_fail: bool | None = None
    #: JSON column whose content could not be parsed (review-need, no write).
    json_bad: bool = False
    #: The cleaned value already exists in another row of the same table
    #: (UNIQUE index) — writing it would merge rows (review-need, no write).
    unique_blocked: bool = False

    @property
    def writable(self) -> bool:
        """True when this cell should be written (changed and not gated)."""
        return (
            self.changed
            and not self.blocked
            and not self.json_bad
            and not self.unique_blocked
        )


# ---------------------------------------------------------------------------
# Registries (single ZVS source — rules_data only)
# ---------------------------------------------------------------------------


def _bible_words(conn: sqlite3.Connection) -> set[str]:
    """All Bible words (``zo_tdb77`` + ``zo_tedim2010``) — mirrors
    ``DatabaseManager.get_bible_words`` but bound to *this* store."""
    try:
        rows = conn.execute(
            "SELECT zo_tdb77, zo_tedim2010 FROM bible_verses "
            "WHERE zo_tdb77 IS NOT NULL OR zo_tedim2010 IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return set()
    word_re = re.compile(r"[a-zA-Z]+")
    words: set[str] = set()
    for row in rows:
        for value in row:
            if value:
                words.update(w.lower() for w in word_re.findall(value))
    return words


def _modern_registry() -> ExceptionRegistry:
    """Empty registry — every modern-table violation is fair game to fix."""
    return ExceptionRegistry(
        rule_ids=set(),
        tokens=set(),
        phrases=set(),
        bible_words={_BIBLE_WORDS_SENTINEL},
    )


def _scripture_registry(conn: sqlite3.Connection) -> ExceptionRegistry:
    """Default historical exceptions (rules_data) + this store's Bible words."""
    return ExceptionRegistry(
        rule_ids=set(HISTORICAL_EXCEPTIONS["rule_ids"]),
        tokens={t.lower() for t in HISTORICAL_EXCEPTIONS["tokens"]},
        phrases={p.strip().lower() for p in HISTORICAL_EXCEPTIONS["phrases"]},
        bible_words=_bible_words(conn) or {_BIBLE_WORDS_SENTINEL},
    )


def build_registries(conn: sqlite3.Connection) -> Registries:
    """Build both ZVS exception registries for the store behind ``conn``."""
    return {"modern": _modern_registry(), "scripture": _scripture_registry(conn)}


def _registry_for(bible_ctx: Ctx, registries: Registries | None) -> ExceptionRegistry:
    if registries is not None:
        return registries[bible_ctx]
    cached = _DEFAULT_REGISTRIES.get(bible_ctx)
    if cached is None:
        if bible_ctx == "modern":
            cached = _modern_registry()
        else:
            conn = _connect(config.paths.db)
            try:
                cached = _scripture_registry(conn)
            finally:
                conn.close()
        _DEFAULT_REGISTRIES[bible_ctx] = cached
    return cached


# ---------------------------------------------------------------------------
# Cleaning pipeline (plan §3 — ordered, idempotent)
# ---------------------------------------------------------------------------


def _html_step(text: str) -> str:
    """Strip HTML tags to a space, then ``html.unescape`` to a fixed point
    (max 3 iterations — ``&amp;amp;`` → ``&amp;`` → ``&``)."""
    out = text
    for _ in range(3):
        new = html.unescape(_TAG_RE.sub(" ", out))
        if new == out:
            return out
        out = new
    return out


def _ws_step(text: str, kind: Kind) -> str:
    """word → collapse all whitespace + strip; sentence → collapse
    non-newline whitespace (incl. NBSP), keep newlines, strip edges."""
    if kind == "word":
        return _WS_WORD_RE.sub(" ", text).strip()
    return _WS_SENTENCE_RE.sub(" ", text).strip()


def _match_case(src: str, repl: str) -> str:
    """Case-preserving replacement: ``Pathian`` → ``Pasian``, ``RAM`` → ``GAM``."""
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _replace_right_to_left(
    text: str, violations: Sequence[Any]
) -> tuple[str, int]:
    """Apply ``preferred`` forms right-to-left, non-overlapping (plan §3)."""
    out = text
    applied = 0
    taken: list[tuple[int, int]] = []
    for v in sorted(violations, key=lambda v: v.start or 0, reverse=True):
        start, end = v.start, v.end
        if start is None or end is None:
            continue
        if any(not (end <= a or start >= b) for a, b in taken):
            continue
        out = out[:start] + _match_case(text[start:end], v.preferred) + out[end:]
        taken.append((start, end))
        applied += 1
    return out, applied


def _zvs_step(
    text: str, registry: ExceptionRegistry, bible_ctx: Ctx
) -> tuple[str, int, int, int]:
    """Run ``zolai.zvs.validate()``; rewrite violations except ``suah``.

    Returns ``(text, hits, suah_hits, applied)``.
    """
    if not text or _ZVS_PRECHECK.search(text) is None:
        return text, 0, 0, 0
    report = validate(text, exceptions=registry, context=bible_ctx)
    suah_hits = 0
    applicable: list[Any] = []
    for v in report.violations:
        if v.forbidden == "suah":
            suah_hits += 1
            continue
        if v.preferred and v.start is not None and v.end is not None:
            applicable.append(v)
    out, applied = _replace_right_to_left(text, applicable)
    return out, len(applicable), suah_hits, applied


def _clean_string(
    text: str, kind: Kind, bible_ctx: Ctx, registry: ExceptionRegistry
) -> tuple[str, bool, bool, int, int, int]:
    """Pipeline: HTML → whitespace → ZVS. Returns
    ``(cleaned, html_fixed, ws_fixed, zvs_hits, suah_hits, zvs_applied)``."""
    html_fixed = False
    after_html = text
    if "<" in text or "&" in text:
        after_html = _html_step(text)
        html_fixed = after_html != text
    after_ws = _ws_step(after_html, kind)
    ws_fixed = after_ws != after_html
    cleaned, hits, suah, applied = _zvs_step(after_ws, registry, bible_ctx)
    return cleaned, html_fixed, ws_fixed, hits, suah, applied


def _clean_json_node(
    node: Any, bible_ctx: Ctx, registry: ExceptionRegistry
) -> tuple[Any, bool, bool, bool, int, int, int]:
    """Recursively clean a parsed JSON node.

    Rule (plan §3): strings are ZO values → cleaned as sentences; dicts clean
    the ``zo`` key **only** (other keys — ``en``, ``ref`` — stay byte-identical);
    lists recurse; scalars are untouched.

    Returns ``(new_node, changed, html_fixed, ws_fixed, hits, suah, applied)``.
    """
    if isinstance(node, str):
        cleaned, h, w, hits, suah, applied = _clean_string(node, "sentence", bible_ctx, registry)
        return cleaned, cleaned != node, h, w, hits, suah, applied
    if isinstance(node, list):
        changed = False
        html_f = ws_f = False
        hits = suah = applied = 0
        out: list[Any] = []
        for item in node:
            if isinstance(item, (str, list, dict)):
                new_item, c, h, w, hi, su, ap = _clean_json_node(item, bible_ctx, registry)
            else:
                new_item, c, h, w, hi, su, ap = item, False, False, False, 0, 0, 0
            changed = changed or c
            html_f = html_f or h
            ws_f = ws_f or w
            hits += hi
            suah += su
            applied += ap
            out.append(new_item)
        return out, changed, html_f, ws_f, hits, suah, applied
    if isinstance(node, dict):
        if "zo" not in node or not isinstance(node["zo"], str):
            return node, False, False, False, 0, 0, 0
        new_zo, c, h, w, hi, su, ap = _clean_json_node(node["zo"], bible_ctx, registry)
        if not c:
            # No write pending — but the zo value may still hold a `suah`
            # review-need. Propagate the counters (they are the same ones the
            # string path returns unconditionally) so JSON cells report suah
            # exactly like sentence cells do.
            return node, False, False, False, hi, su, ap
        new_node = dict(node)
        new_node["zo"] = new_zo
        return new_node, True, h, w, hi, su, ap
    return node, False, False, False, 0, 0, 0


def clean_value(
    text: str,
    kind: Kind,
    bible_ctx: Ctx = "modern",
    *,
    registries: Registries | None = None,
) -> CellOutcome:
    """Clean one stored cell (plan §3 entry point: ``clean_value(text, kind,
    bible_ctx)``).

    ``kind``:
        ``"word"`` — collapse whitespace + ZVS, then the ``^[a-z][a-z-]*$``
        sanity gate (a failing cleaned value is **not** written — counted as a
        review-need instead);
        ``"sentence"`` — collapse non-newline whitespace + ZVS (no lowercasing);
        ``"json"`` — parse, clean ZO string values (``zo`` keys in dicts only),
        re-dump only when something actually changed (unparseable → review-need).
    """
    original = text if isinstance(text, str) else str(text)
    registry = _registry_for(bible_ctx, registries)

    if kind == "json":
        if original == "":
            return CellOutcome(original, original)
        try:
            parsed: Any = json.loads(original)
        except (ValueError, TypeError):
            return CellOutcome(original, original, json_bad=True)
        new_node, changed, h, w, hits, suah, applied = _clean_json_node(parsed, bible_ctx, registry)
        if not changed:
            return CellOutcome(original, original, zvs_hits=hits, suah_hits=suah)
        return CellOutcome(
            original,
            json.dumps(new_node, ensure_ascii=False),
            changed=True,
            html_fixed=h,
            ws_fixed=w,
            zvs_hits=hits,
            zvs_applied=applied,
            suah_hits=suah,
        )

    if original == "":
        return CellOutcome(original, original, sanity_fail=False if kind == "word" else None)

    cleaned, h, w, hits, suah, applied = _clean_string(original, kind, bible_ctx, registry)
    changed = cleaned != original
    sanity_fail: bool | None = None
    blocked = False
    if kind == "word":
        sanity_fail = WORD_SANITY_RE.match(cleaned) is None
        if changed and sanity_fail:
            blocked = True
    return CellOutcome(
        original,
        original if blocked else cleaned,
        changed=changed,
        blocked=blocked,
        html_fixed=h,
        ws_fixed=w,
        zvs_hits=hits,
        zvs_applied=applied,
        suah_hits=suah,
        sanity_fail=sanity_fail,
    )


# ---------------------------------------------------------------------------
# Store plumbing
# ---------------------------------------------------------------------------


def _resolve_db(db_path: Path | str | None) -> Path:
    """Canonical store — ``zolai.config`` (workspace-root ``data/zolai.db``).

    Refuses an empty file so the 0-byte ``zolai-core/data/zolai.db`` stub (or
    any truncated copy) can never be treated as the corpus.
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


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _unique_indexes(conn: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    """Column tuples of every UNIQUE index/constraint on *table*."""
    out: list[tuple[str, ...]] = []
    for idx in conn.execute(f'PRAGMA index_list("{table}")'):
        if not idx["unique"]:
            continue
        cols = tuple(
            r["name"] for r in conn.execute(f'PRAGMA index_info("{idx["name"]}")')
        )
        if cols and all(cols):
            out.append(cols)
    return out


def _unique_keys(
    spec: ColumnSpec,
    row: sqlite3.Row,
    cleaned: str,
    uniq: Sequence[tuple[str, ...]],
) -> list[tuple[tuple[str, ...], tuple[Any, ...]]]:
    """``(index_cols, values)`` for every UNIQUE index containing the cleaned column.

    NULL columns are dropped: SQLite treats NULLs as distinct in unique
    indexes, so such a key can never collide.
    """
    keys: list[tuple[tuple[str, ...], tuple[Any, ...]]] = []
    for cols in uniq:
        if spec.column not in cols:
            continue
        values = tuple(cleaned if col == spec.column else row[col] for col in cols)
        if any(v is None for v in values):
            continue
        keys.append((cols, values))
    return keys


def _unique_conflict(
    conn: sqlite3.Connection,
    table: str,
    keys: Sequence[tuple[tuple[str, ...], tuple[Any, ...]]],
    row_id: Any,
    reserved: set[tuple[tuple[str, ...], tuple[Any, ...]]],
) -> bool:
    """True when writing these unique keys would collide with another row.

    Checks both ``reserved`` (same-scan candidates the cleaner already
    committed to writing — keeps dry-run and apply identical) and the live
    table (rows written by earlier batches or pre-existing data).  A collision
    means two rows would share a UNIQUE key — merging them is a destructive
    dedupe C1 refuses to decide, so the cell becomes a review-need instead.
    """
    for cols, values in keys:
        if (cols, values) in reserved:
            return True
        where = " AND ".join(f'"{col}" = ?' for col in cols)
        hit = conn.execute(
            f'SELECT 1 FROM "{table}" WHERE {where} AND id != ? LIMIT 1',
            (*values, row_id),
        ).fetchone()
        if hit:
            return True
    return False


def _registry_tables() -> list[str]:
    return list(dict.fromkeys(spec.table for spec in COLUMN_REGISTRY))


def _specs_for(table: str) -> list[ColumnSpec]:
    return [spec for spec in COLUMN_REGISTRY if spec.table == table]


def _needed_columns(specs: Sequence[ColumnSpec], uniq_cols: Sequence[str] = ()) -> list[str]:
    cols: list[str] = ["id"]
    for spec in specs:
        for col in (spec.column, spec.only_when[0] if spec.only_when else None):
            if col and col not in cols:
                cols.append(col)
    # Unique-index guards need the *other* index columns of the row too
    # (e.g. `word_usage(word, book)` when only `word` is cleaned).
    for col in uniq_cols:
        if col not in cols:
            cols.append(col)
    return cols


def _quoted(cols: Sequence[str]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def _short(value: str, limit: int = 120) -> str:
    flat = value.replace("\n", "⏎").replace("\r", "")
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Column statistics (shared by audit and apply)
# ---------------------------------------------------------------------------


def _new_col_stats(spec: ColumnSpec, sample_limit: int) -> dict[str, Any]:
    return {
        "table": spec.table,
        "column": spec.column,
        "kind": spec.kind,
        "ctx": spec.ctx,
        "writable": spec.writable,
        "note": spec.note,
        "cells": 0,
        "html": 0,
        "whitespace": 0,
        "zvs_cells": 0,
        "zvs_hits": 0,
        "suah_cells": 0,
        "uppercase": 0,
        "word_sanity": 0,
        "blocked": 0,
        "unique_blocked": 0,
        "json_bad": 0,
        "changed_cells": 0,
        "would_write": 0,
        "samples": [],
        "_sample_limit": sample_limit,
    }


def _accumulate(st: dict[str, Any], spec: ColumnSpec, outcome: CellOutcome, row_id: Any) -> None:
    st["cells"] += 1
    st["html"] += int(outcome.html_fixed)
    st["whitespace"] += int(outcome.ws_fixed)
    st["zvs_cells"] += int(outcome.zvs_hits > 0)
    st["zvs_hits"] += outcome.zvs_hits
    st["suah_cells"] += int(outcome.suah_hits > 0)
    st["json_bad"] += int(outcome.json_bad)
    st["blocked"] += int(outcome.blocked)
    st["unique_blocked"] += int(outcome.unique_blocked)
    if spec.kind == "word":
        st["uppercase"] += int(any(c.isupper() for c in outcome.original))
        st["word_sanity"] += int(bool(outcome.sanity_fail))
    st["changed_cells"] += int(outcome.changed)
    if spec.writable and outcome.writable:
        st["would_write"] += 1
        limit = st["_sample_limit"]
        if len(st["samples"]) < limit:
            st["samples"].append(
                {"id": row_id, "before": _short(outcome.original), "after": _short(outcome.cleaned)}
            )


def _review_needs_from(sts: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Review-needs count only columns the cleaner would write (plan §7:
    cells that "stay untouched" because C1 refuses to guess)."""
    suah = sum(st["suah_cells"] for st in sts if st["writable"])
    word = sum(st["blocked"] for st in sts if st["writable"])
    js = sum(st["json_bad"] for st in sts if st["writable"])
    uniq = sum(st["unique_blocked"] for st in sts if st["writable"])
    return {"suah": suah, "word_sanity": word, "json": js, "unique": uniq,
            "total": suah + word + js + uniq}


def _totals_from(sts: Sequence[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "cells", "html", "whitespace", "zvs_cells", "zvs_hits", "suah_cells",
        "uppercase", "word_sanity", "blocked", "unique_blocked", "json_bad",
        "changed_cells", "would_write",
    )
    return {k: sum(st[k] for st in sts) for k in keys}


def _dup_groups(conn: sqlite3.Connection, table: str) -> int:
    key = DUP_KEYS[table]
    where = " AND ".join(f'"{c}" IS NOT NULL' for c in key)
    sql = (
        f'SELECT COUNT(*) FROM (SELECT 1 FROM "{table}" WHERE {where} '
        f'GROUP BY {_quoted(key)} HAVING COUNT(*) > 1)'
    )
    return int(conn.execute(sql).fetchone()[0])


# ---------------------------------------------------------------------------
# Audit (read-only)
# ---------------------------------------------------------------------------


def run_audit(
    db_path: Path | str | None = None,
    *,
    tables: Sequence[str] | None = None,
    sample_limit: int = 3,
) -> dict[str, Any]:
    """Scan every registered column; **never** writes (plan §1).

    Returns a JSON-serialisable report: per-column defect matrix, duplicate
    groups, review-need totals, row counts and samples.
    """
    started = time.time()
    db = _resolve_db(db_path)
    conn = _connect(db)
    try:
        present = _table_names(conn)
        wanted = [t for t in _registry_tables() if tables is None or t in tables]
        scanned = [t for t in wanted if t in present]
        skipped = [t for t in wanted if t not in present]
        registries = build_registries(conn)

        row_counts = {t: _count(conn, t) for t in scanned}
        column_stats: list[dict[str, Any]] = []
        dup: dict[str, Any] = {}

        for table in scanned:
            specs = _specs_for(table)
            for spec in specs:
                if spec.column not in _columns(conn, table):
                    raise LookupError(f"column missing from live schema: {table}.{spec.column}")
            uniq = _unique_indexes(conn, table)
            uniq_cols = tuple(dict.fromkeys(c for cols_ in uniq for c in cols_))
            cols = _needed_columns(specs, uniq_cols)
            per_spec = {spec.column: _new_col_stats(spec, sample_limit) for spec in specs}
            # Keys this scan already committed to writing — keeps dry-run and
            # apply identical for pairs that collide only with each other.
            reserved: set[tuple[tuple[str, ...], tuple[Any, ...]]] = set()
            # Only one spec per column per table (translations targets distinct
            # columns), so a keyed dict is unambiguous.
            cur = conn.execute(f"SELECT {_quoted(cols)} FROM \"{table}\"")
            while True:
                rows = cur.fetchmany(20000)
                if not rows:
                    break
                for row in rows:
                    for spec in specs:
                        if spec.only_when and row[spec.only_when[0]] != spec.only_when[1]:
                            continue
                        cell = row[spec.column]
                        if cell is None:
                            continue
                        text = cell if isinstance(cell, str) else str(cell)
                        if spec.kind != "json" and text == "":
                            continue
                        outcome = clean_value(text, spec.kind, spec.ctx, registries=registries)
                        if spec.writable and outcome.writable:
                            keys = _unique_keys(spec, row, outcome.cleaned, uniq)
                            if _unique_conflict(conn, table, keys, row["id"], reserved):
                                outcome.unique_blocked = True
                            else:
                                reserved.update(keys)
                        _accumulate(per_spec[spec.column], spec, outcome, row["id"])
            column_stats.extend(per_spec[spec.column] for spec in specs)
            if table in DUP_KEYS:
                dup[table] = {"key": list(DUP_KEYS[table]), "groups": _dup_groups(conn, table)}

        for st in column_stats:
            st.pop("_sample_limit", None)
        dup_total = sum(v["groups"] for v in dup.values())
        audit = {
            "db": str(db),
            "generated_at": _utc_now(),
            "scanned_tables": scanned,
            "skipped_tables": skipped,
            "row_counts": row_counts,
            "columns": column_stats,
            "duplicates": dup,
            "duplicate_groups_total": dup_total,
            "review_needs": _review_needs_from(column_stats),
            "totals": _totals_from(column_stats),
            "duration_s": round(time.time() - started, 2),
        }
        return audit
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Apply (dry-run default)
# ---------------------------------------------------------------------------


def _load_state(path: Path, db: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict) or state.get("db") != str(db):
        return {}
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_clean(
    db_path: Path | str | None = None,
    *,
    tables: Sequence[str] | None = None,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    state_path: Path | str | None = None,
    sample_limit: int = 2,
) -> dict[str, Any]:
    """Scan registered columns; write only when ``apply=True`` (plan §4).

    Dry-run (default) touches neither the database nor the state file.  With
    ``apply=True`` each batch of ≤ ``batch_size`` rows is one transaction
    (field-only ``UPDATE`` + one ``data_audit_log`` row per changed cell), the
    cursor is persisted for resume, and an interrupted run is picked up where
    it stopped.  Audit-only columns (``zo_hcl06``/``zo_fcl``) and
    ``only_when``-filtered rows (``en→my``) are never written.
    """
    started = time.time()
    db = _resolve_db(db_path)
    state_file = Path(state_path) if state_path is not None else db.parent / STATE_FILENAME
    conn = _connect(db)
    try:
        present = _table_names(conn)
        wanted = [t for t in _registry_tables() if tables is None or t in tables]
        scanned = [t for t in wanted if t in present]
        skipped = [t for t in wanted if t not in present]
        registries = build_registries(conn)

        resuming = False
        cursors: dict[str, int] = {}
        if apply:
            state = _load_state(state_file, db)
            resuming = state.get("in_progress") is True
            cursors = dict(state.get("cursors", {})) if resuming else {}
            _save_state(
                state_file,
                {
                    "db": str(db),
                    "in_progress": True,
                    "resumed": resuming,
                    "cursors": cursors,
                    "batch_size": batch_size,
                    "started_at": _utc_now(),
                },
            )

        row_counts_before = {t: _count(conn, t) for t in scanned}
        table_stats: dict[str, dict[str, Any]] = {}
        col_stats: list[dict[str, Any]] = []

        for table in scanned:
            specs = _specs_for(table)
            for spec in specs:
                if spec.column not in _columns(conn, table):
                    raise LookupError(f"column missing from live schema: {table}.{spec.column}")
            uniq = _unique_indexes(conn, table)
            uniq_cols = tuple(dict.fromkeys(c for cols_ in uniq for c in cols_))
            per_spec = {spec.column: _new_col_stats(spec, sample_limit) for spec in specs}
            classes = {"html": 0, "whitespace": 0, "zvs": 0, "json": 0}
            cells_changed = 0
            audit_rows = 0
            review = {"suah": 0, "word_sanity": 0, "json": 0, "unique": 0}
            # Persists across batches: dry-run never writes, so without it a
            # pair colliding only with each other would slip through in
            # dry-run but block in apply (keeps the two modes identical).
            reserved: set[tuple[tuple[str, ...], tuple[Any, ...]]] = set()

            cursor = cursors.get(table, 0) if apply else 0
            cols = _needed_columns(specs, uniq_cols)
            while True:
                rows = conn.execute(
                    f'SELECT {_quoted(cols)} FROM "{table}" WHERE id > ? ORDER BY id LIMIT ?',
                    (cursor, batch_size),
                ).fetchall()
                if not rows:
                    break
                pending: list[tuple[ColumnSpec, int, CellOutcome]] = []
                for row in rows:
                    for spec in specs:
                        if spec.only_when and row[spec.only_when[0]] != spec.only_when[1]:
                            continue
                        cell = row[spec.column]
                        if cell is None:
                            continue
                        text = cell if isinstance(cell, str) else str(cell)
                        if spec.kind != "json" and text == "":
                            continue
                        outcome = clean_value(text, spec.kind, spec.ctx, registries=registries)
                        if spec.writable and outcome.writable:
                            keys = _unique_keys(spec, row, outcome.cleaned, uniq)
                            if _unique_conflict(conn, table, keys, row["id"], reserved):
                                outcome.unique_blocked = True
                            else:
                                reserved.update(keys)
                        _accumulate(per_spec[spec.column], spec, outcome, row["id"])
                        if spec.writable and outcome.json_bad:
                            review["json"] += 1
                        if spec.writable and outcome.suah_hits:
                            review["suah"] += 1
                        if spec.writable and outcome.blocked:
                            review["word_sanity"] += 1
                        if spec.writable and outcome.unique_blocked:
                            review["unique"] += 1
                        if spec.writable and outcome.writable:
                            pending.append((spec, row["id"], outcome))

                written = pending
                if apply and pending:
                    stamp = _utc_now()
                    written = []
                    for spec, row_id, outcome in pending:
                        try:
                            conn.execute(
                                f'UPDATE "{table}" SET "{spec.column}" = ? WHERE id = ?',
                                (outcome.cleaned, row_id),
                            )
                        except sqlite3.IntegrityError:
                            # Safety net for conflicts the scan check cannot
                            # see (expression indexes, concurrent writers):
                            # skip the cell, never merge rows.
                            review["unique"] += 1
                            continue
                        conn.execute(
                            "INSERT INTO data_audit_log "
                            "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                table,
                                row_id,
                                spec.column,
                                outcome.original,
                                outcome.cleaned,
                                stamp,
                                REASON,
                            ),
                        )
                        written.append((spec, row_id, outcome))
                    conn.commit()

                for spec, _row_id, outcome in written:
                    cells_changed += 1
                    audit_rows += int(apply)
                    if spec.kind == "json":
                        classes["json"] += 1
                    else:
                        classes["html"] += int(outcome.html_fixed)
                        classes["whitespace"] += int(outcome.ws_fixed)
                        classes["zvs"] += int(outcome.zvs_applied > 0)

                cursor = rows[-1]["id"]
                if apply:
                    cursors[table] = cursor
                    _save_state(
                        state_file,
                        {
                            "db": str(db),
                            "in_progress": True,
                            "resumed": resuming,
                            "cursors": cursors,
                            "batch_size": batch_size,
                            "started_at": _utc_now(),
                        },
                    )
                if len(rows) < batch_size:
                    break

            table_stats[table] = {
                "rows_before": row_counts_before[table],
                "cells_changed": cells_changed,
                "audit_rows": audit_rows,
                "classes": classes,
                "review_needs": dict(review),
            }
            col_stats.extend(per_spec[spec.column] for spec in specs)

        row_counts_after = {t: _count(conn, t) for t in scanned} if apply else dict(row_counts_before)
        for table, st in table_stats.items():
            st["rows_after"] = row_counts_after[table]
        for st in col_stats:
            st.pop("_sample_limit", None)

        if apply:
            _save_state(
                state_file,
                {
                    "db": str(db),
                    "in_progress": False,
                    "resumed": resuming,
                    "cursors": cursors,
                    "batch_size": batch_size,
                    "started_at": _utc_now(),
                    "finished_at": _utc_now(),
                },
            )

        audit_log_total = int(conn.execute("SELECT COUNT(*) FROM data_audit_log").fetchone()[0])
        totals = {
            "cells_changed": sum(st["cells_changed"] for st in table_stats.values()),
            "audit_rows": sum(st["audit_rows"] for st in table_stats.values()),
            "html": sum(st["classes"]["html"] for st in table_stats.values()),
            "whitespace": sum(st["classes"]["whitespace"] for st in table_stats.values()),
            "zvs": sum(st["classes"]["zvs"] for st in table_stats.values()),
            "json": sum(st["classes"]["json"] for st in table_stats.values()),
            "review_needs": {
                key: sum(st["review_needs"][key] for st in table_stats.values())
                for key in ("suah", "word_sanity", "json", "unique")
            },
            "rows_before": sum(row_counts_before.values()),
            "rows_after": sum(row_counts_after.values()),
        }
        totals["review_needs"]["total"] = sum(totals["review_needs"].values())
        return {
            "applied": apply,
            "db": str(db),
            "state_path": str(state_file) if apply else None,
            "resumed": resuming if apply else False,
            "batch_size": batch_size,
            "scanned_tables": scanned,
            "skipped_tables": skipped,
            "tables": table_stats,
            "columns": col_stats,
            "row_counts_before": row_counts_before,
            "row_counts_after": row_counts_after,
            "audit_log_total_after": audit_log_total,
            "totals": totals,
            "duration_s": round(time.time() - started, 2),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Report rendering (plan §5)
# ---------------------------------------------------------------------------


def _render_matrix(columns: Sequence[dict[str, Any]]) -> list[str]:
    lines = [
        "| table | column | kind | ctx | cells | html | ws | zvs cells (hits) | suah | "
        "upper | sanity | blocked | changed | would-write |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for st in columns:
        write = f'{st["would_write"]}' if st["writable"] else "— (audit-only)"
        lines.append(
            f'| {st["table"]} | `{st["column"]}` | {st["kind"]} | {st["ctx"]} | '
            f'{st["cells"]} | {st["html"]} | {st["whitespace"]} | '
            f'{st["zvs_cells"]} ({st["zvs_hits"]}) | {st["suah_cells"]} | '
            f'{st["uppercase"]} | {st["word_sanity"]} | {st["blocked"]} | '
            f'{st["changed_cells"]} | {write} |'
        )
    return lines


def _render_needs_founder() -> list[str]:
    return [f"{i}. {item}" for i, item in enumerate(NEEDS_FOUNDER, start=1)]


def _render_report(audit: dict[str, Any]) -> str:
    totals = audit["totals"]
    rn = audit["review_needs"]
    lines = [
        "# Corpus Clean Audit — 2026-10-01",
        "",
        '- **Status:** PRE-APPLY (read-only defect matrix — committed as evidence before `--apply`)',
        f'- **DB:** `{audit["db"]}`',
        f'- **Generated:** {audit["generated_at"]} UTC · scan {audit["duration_s"]}s',
        "- **Spec:** `docs/planning/C1_CORPUS_CLEAN_PLAN.md` · module `zolai/data/corpus_clean.py`",
        f'- **Tables scanned:** {len(audit["scanned_tables"])}'
        + (f' · skipped (absent): {", ".join(audit["skipped_tables"])}' if audit["skipped_tables"] else ""),
        "",
        "## Summary",
        "",
        f'- cells scanned: **{totals["cells"]}** · changed-candidate cells: {totals["changed_cells"]} '
        f'· would-write: {totals["would_write"]}',
        f'- defects: html {totals["html"]} · whitespace {totals["whitespace"]} · '
        f'ZVS {totals["zvs_cells"]} cells ({totals["zvs_hits"]} hits) · '
        f'suah {totals["suah_cells"]} · uppercase {totals["uppercase"]} · '
        f'word-sanity {totals["word_sanity"]} · json-unparseable {totals["json_bad"]}',
        f'- review-needs: **suah {rn["suah"]}** · **word-sanity {rn["word_sanity"]}** · '
        f'**json {rn["json"]}** · **unique {rn["unique"]}** (total {rn["total"]})',
        f'- exact-duplicate groups: **{audit["duplicate_groups_total"]}** (detect + count only — no deletes)',
        "",
        "> `suah` is never rewritten; `zo_hcl06`/`zo_fcl` are audit-only; "
        "EN/MY/label/staging columns are never selected. Row counts below are the "
        "pre-apply baseline — after apply they must be identical (NO-drops proof).",
        "",
        "## Defect matrix",
        "",
        *_render_matrix(audit["columns"]),
        "",
        "### Column row counts (pre-apply baseline)",
        "",
        "| table | rows |",
        "|---|---:|",
    ]
    for table, n in audit["row_counts"].items():
        lines.append(f"| {table} | {n} |")
    lines += [
        "",
        "## Duplicate groups (detect + count only)",
        "",
        "| table | key | groups |",
        "|---|---|---:|",
    ]
    for table, info in audit["duplicates"].items():
        lines.append(f'| {table} | {", ".join(f"`{c}`" for c in info["key"])} | {info["groups"]} |')
    lines += [
        "",
        "## Review-need definitions",
        "",
        "- **suah** — cells containing standalone `suah` in a column C1 would write; "
        "two canonical docs disagree on the target → left untouched, counted only.",
        "- **word-sanity** — word-kind cells whose *cleaned* value fails `^[a-z][a-z-]*$` "
        "while a change was pending → write refused (headword identity), counted only. "
        "(The full count of word cells failing the regex — including those needing no "
        "change — is the `sanity` column of the defect matrix.)",
        "- **json** — JSON cells that fail to parse → left untouched, counted only.",
        "- **unique** — a cleaned value that would collide with another row in a "
        "UNIQUE index (shared headword/zolai key) → merging rows is a destructive "
        "dedupe C1 refuses to decide; cell left untouched, counted only.",
        "",
        "## Exclusions",
        "",
        *[f"- {item}" for item in EXCLUSIONS],
        "",
        "## Samples (first cells that `--apply` would change)",
        "",
    ]
    any_samples = False
    for st in audit["columns"]:
        if not st["samples"]:
            continue
        any_samples = True
        lines.append(f'### {st["table"]}.{st["column"]}')
        lines.append("")
        for s in st["samples"]:
            lines.append(f'- id `{s["id"]}`: `{s["before"]}` → `{s["after"]}`')
        lines.append("")
    if not any_samples:
        lines += ["_No would-change cells._", ""]
    lines += [
        "## needs-founder (none of these block audit/apply)",
        "",
        *_render_needs_founder(),
        "",
        "## Note",
        "",
        "**Cleaned ≠ zero defects.** Only deterministic ZVS/whitespace/HTML fixes are "
        "applied; everything else stays as review-needs triage (`suah`, word-sanity, "
        "json, unique-collision, duplicates). After `--apply` an `## Apply results` section is appended "
        "with before/after row counts, audit-row totals and the idempotency proof.",
        "",
    ]
    return "\n".join(lines)


def write_report(audit: dict[str, Any], path: Path | str) -> Path:
    """Write the pre-apply markdown audit report (plan §5)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_report(audit), encoding="utf-8")
    return out


def append_apply_results(
    path: Path | str,
    *,
    apply_stats: dict[str, Any],
    audit_after: dict[str, Any],
    idem_stats: dict[str, Any] | None = None,
    backup_note: str = "",
) -> Path:
    """Append the post-apply section to the audit report (plan §5)."""
    out = Path(path)
    totals = apply_stats["totals"]
    rn_after = audit_after["review_needs"]
    lines = [
        "",
        "## Apply results",
        "",
        f'- **Applied:** {apply_stats["generated_at"] if "generated_at" in apply_stats else _utc_now()} UTC '
        f'· db `{apply_stats["db"]}` · batch {apply_stats["batch_size"]}'
        + (" · resumed interrupted run" if apply_stats.get("resumed") else ""),
        f'- **Fresh backup before apply:** {backup_note or "see `data/backups/backup.log`"}',
        f'- **Cells changed:** {totals["cells_changed"]} · **`data_audit_log` rows added:** '
        f'{totals["audit_rows"]} (one per changed cell, `reason="{REASON}"`) · '
        f'log total now {apply_stats["audit_log_total_after"]}',
        f'- **Defect classes among written cells:** html {totals["html"]} · '
        f'whitespace {totals["whitespace"]} · ZVS {totals["zvs"]} · JSON {totals["json"]}',
        "",
        "### Row counts before == after (NO-drops proof)",
        "",
        "| table | rows before | rows after | cells changed | audit rows |",
        "|---|---:|---:|---:|---:|",
    ]
    for table, st in apply_stats["tables"].items():
        lines.append(
            f'| {table} | {st["rows_before"]} | {st["rows_after"]} | '
            f'{st["cells_changed"]} | {st["audit_rows"]} |'
        )
    ok = apply_stats["row_counts_before"] == apply_stats["row_counts_after"]
    lines += [
        "",
        f'Totals: {sum(apply_stats["row_counts_before"].values())} == '
        f'{sum(apply_stats["row_counts_after"].values())} — '
        + ("**identical** ✅" if ok else "**MISMATCH** ❌"),
        "",
        "### Cells changed per table",
        "",
        "| table | html | whitespace | zvs | json |",
        "|---|---:|---:|---:|---:|",
    ]
    for table, st in apply_stats["tables"].items():
        c = st["classes"]
        lines.append(f'| {table} | {c["html"]} | {c["whitespace"]} | {c["zvs"]} | {c["json"]} |')
    lines += [
        "",
        "### Re-audit after apply (remaining defects = triage)",
        "",
        f'- review-needs: suah **{rn_after["suah"]}** · word-sanity **{rn_after["word_sanity"]}** · '
        f'json **{rn_after["json"]}** · unique **{rn_after["unique"]}** (total {rn_after["total"]})',
        f'- would-write cells remaining: **{audit_after["totals"]["would_write"]}**',
        f'- duplicate groups: **{audit_after["duplicate_groups_total"]}** (unchanged — no deletes)',
        f'- html remaining: {audit_after["totals"]["html"]} · whitespace remaining: '
        f'{audit_after["totals"]["whitespace"]} · ZVS cells remaining: {audit_after["totals"]["zvs_cells"]}',
        "",
    ]
    if idem_stats is not None:
        idem_totals = idem_stats["totals"]
        lines += [
            "### Idempotency (second `--apply`)",
            "",
            f'- cells changed: **{idem_totals["cells_changed"]}** · '
            f'audit rows added: **{idem_totals["audit_rows"]}** '
            + ("✅ (0 new audit rows — clean is idempotent)" if idem_totals["audit_rows"] == 0 else "❌"),
            "",
        ]
    lines += [
        "### needs-founder",
        "",
        *_render_needs_founder(),
        "",
        "> `suah` / word-sanity / json / unique / duplicate counts above are the deliberate "
        "remainder — **cleaned ≠ zero defects**.",
        "",
    ]
    with out.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return out


__all__ = [
    "COLUMN_REGISTRY",
    "ColumnSpec",
    "DUP_KEYS",
    "DEFAULT_BATCH_SIZE",
    "NEEDS_FOUNDER",
    "REASON",
    "CellOutcome",
    "append_apply_results",
    "build_registries",
    "clean_value",
    "run_audit",
    "run_clean",
    "write_report",
]
