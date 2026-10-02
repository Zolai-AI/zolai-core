"""Bible ref fix — audit (read-only), dry-run, apply, idempotency, revert.

Covers the invariants of ``zolai.data.bible_ref_fix`` (plan
``BIBLE_REF_FIX_PLAN.md``):

- ``run_audit`` / ``zolai bible-ref audit`` classify every row
  (``ok | impossible_ref | en_needs_review | null_en``) and **never write** —
  not even DDL (the archive table stays absent).
- dry-run touches nothing; ``--apply`` archives + deletes exactly the
  impossible rows, restores KJV-verified EN into the target cell, and appends
  one ``data_audit_log`` row per write (``audit rows == writes``).
- a second ``fix --apply`` archives nothing (0 audit rows — idempotency) and
  refuses any duplicate id/ref.
- **no ZO/label write**: every non-EN canonical column is byte-identical after
  fix/revert, and no audit row names one.
- ``revert --apply`` restores ids + text byte-identically, empties the archive
  and is itself idempotent.
- needs-founder rows (unresolvable, neither-EN pairs, NULL ``en_kJV``, EN
  variants) are report-only — never auto-corrected (``--fill-null-en`` stays
  off unless explicitly passed).
- ``remap`` rewrites downstream copies of archived refs (audit-logged) and is
  idempotent.
- archive DDL is ``CREATE TABLE IF NOT EXISTS`` only (18 cols + 3 bookkeeping).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zolai.cli.main import app as cli_app
from zolai.data import bible_ref_fix as brf
from zolai.data.bible_ref_fix import (
    ARCHIVE_TABLE,
    BIBLE_COLUMNS,
    EN_RESTORE_REASON,
    FILL_NULL_REASON,
    REASON,
    REMAP_REASON,
    REVERT_REASON,
    _match,
    load_kjv,
    run_audit,
    run_fix,
    run_remap,
    run_revert,
    write_report,
)
from zolai.data.verse_counts import BOOK_ORDER, TOTAL_VERSES, VERSE_COUNTS

AUDIT_SQL = """CREATE TABLE data_audit_log (
    id INTEGER PRIMARY KEY, table_name TEXT, row_id INT, field TEXT,
    old_value TEXT, new_value TEXT, changed_at TEXT, reason TEXT,
    version INT DEFAULT 1, created_at TEXT, updated_at TEXT, content_hash TEXT)"""

BIBLE_SQL = (
    "CREATE TABLE bible_verses ("
    "id INTEGER PRIMARY KEY, ref TEXT NOT NULL, book TEXT NOT NULL, "
    "chapter INTEGER NOT NULL, verse INTEGER NOT NULL, zo_tdb77 TEXT, "
    "zo_tedim2010 TEXT, en_kJV TEXT, myanmar TEXT, zo_tedim1932 TEXT, "
    "zo_hcl06 TEXT, zo_fcl TEXT, myanmar_judson TEXT, book_name TEXT, "
    "version INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, "
    "content_hash TEXT)"
)

ALIGN_SQL = (
    "CREATE TABLE word_alignments (id INTEGER PRIMARY KEY, ref TEXT, "
    "zolai_word TEXT, english_word TEXT)"
)
TRANS_SQL = (
    "CREATE TABLE translations (id INTEGER PRIMARY KEY, source TEXT, "
    "target TEXT, direction TEXT, reference TEXT)"
)

_ZO_LABEL = (
    "zo_tdb77", "zo_tedim2010", "zo_tedim1932", "zo_hcl06", "zo_fcl",
    "myanmar", "myanmar_judson", "book_name", "version", "created_at",
    "updated_at", "content_hash", "book", "chapter", "verse",
)

_RUNNER = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _kjv_text(book: str, chapter: int, verse: int) -> str:
    return f"KJV {book} {chapter}:{verse}"


def _write_kjv(tmp_path: Path) -> Path:
    """Full 66-book KJV fixture with deterministic, ref-echoing verse text."""
    books = [
        {
            "abbrev": code.lower(),
            "name": code,
            "chapters": [
                [_kjv_text(code, ch, v) for v in range(1, count + 1)]
                for ch, count in enumerate(VERSE_COUNTS[code], start=1)
            ],
        }
        for code in BOOK_ORDER
    ]
    path = tmp_path / "kjv_en.json"
    path.write_text(json.dumps(books), encoding="utf-8")
    return path


def _row(conn: sqlite3.Connection, ref: str, **values) -> int:
    record = {
        "ref": ref,
        "book": ref.split(" ")[0],
        "chapter": int(ref.split(" ")[1].split(":")[0]),
        "verse": int(ref.split(":")[1]),
        "book_name": "Fixture",
        "version": 1,
        "created_at": "2026-10-03T00:00:00",
        "updated_at": "2026-10-03T00:00:00",
        "content_hash": f"hash-{ref}",
        **values,
    }
    cols = list(record)
    cur = conn.execute(
        f'INSERT INTO bible_verses ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
        [record[c] for c in cols],
    )
    return int(cur.lastrowid)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Miniature canonical store covering every classification bucket."""
    path = tmp_path / "zolai.db"
    conn = sqlite3.connect(path)
    conn.execute(AUDIT_SQL)
    conn.execute(BIBLE_SQL)
    conn.execute(ALIGN_SQL)
    conn.execute(TRANS_SQL)
    conn.execute("CREATE UNIQUE INDEX ux_bv_ref ON bible_verses(ref)")
    conn.execute("CREATE UNIQUE INDEX ux_bv_cv ON bible_verses(book, chapter, verse)")

    # --- ok / null_en / en_needs_review (valid refs) ---------------------
    _row(conn, "GEN 1:1", en_kJV=_kjv_text("GEN", 1, 1),
         zo_tdb77="Pasian in vantung leh leitung a piangsak hi.")
    _row(conn, "GEN 1:2", en_kJV=None,
         zo_tdb77="Leitung in lim leh meel nei loin a awngthawlpi ahi hi.")
    _row(conn, "GEN 1:3", en_kJV="totally different wording",
         zo_tdb77="Pasian a ko hi.")
    # --- fix pair 1: target with defective EN + impossible dup (restore) --
    _row(conn, "GEN 1:31", en_kJV="defective target text",
         zo_tdb77="A khatpik nek ding hi.")
    _row(conn, "GEN 2:31", en_kJV=_kjv_text("GEN", 1, 31),
         zo_tdb77="dup row for chapter-final verse")
    # --- fix pair 2: neither EN matches KJV (needs-founder) --------------
    _row(conn, "GEN 1:26", en_kJV="target wording unknown",
         zo_tdb77="A hong bawlna hi.")
    _row(conn, "GEN 2:26", en_kJV="dup wording unknown",
         zo_tdb77="dup row for missing verse")
    # --- unresolvable: chapter 1 verse 32 (no chapter 0) -----------------
    _row(conn, "GEN 1:32", en_kJV=_kjv_text("GEN", 1, 32),
         zo_tdb77="impossible ref, no target")

    # --- downstream copies of the doomed refs ----------------------------
    conn.execute(
        "INSERT INTO word_alignments (id, ref, zolai_word, english_word) "
        "VALUES (1, 'GEN 2:31', 'gam', 'earth')"
    )
    conn.execute(
        "INSERT INTO word_alignments (id, ref, zolai_word, english_word) "
        "VALUES (2, 'GEN 1:1', 'pasian', 'God')"
    )
    conn.execute(
        "INSERT INTO translations (id, source, target, direction, reference) "
        "VALUES (1, 'a', 'b', 'zo_to_en', 'GEN 2:31')"
    )
    conn.execute(
        "INSERT INTO translations (id, source, target, direction, reference) "
        "VALUES (2, 'c', 'd', 'zo_to_en', 'GEN 1:1')"
    )
    conn.commit()
    conn.close()
    return path


def _kjv(db: Path) -> Path:
    return _write_kjv(db.parent)


def _dump(conn: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        f"SELECT {', '.join(BIBLE_COLUMNS)} FROM bible_verses ORDER BY id"
    )]


def _snapshot(db: Path, cols: tuple[str, ...] = _ZO_LABEL) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(
            f"SELECT id, {', '.join(cols)} FROM bible_verses ORDER BY id"
        )]
    finally:
        conn.close()


def _audit_rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT table_name, row_id, field, old_value, new_value, reason "
            "FROM data_audit_log ORDER BY id"
        )]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit (read-only)
# ---------------------------------------------------------------------------


def test_audit_classifies_every_bucket(db: Path) -> None:
    audit = run_audit(db, kjv_path=_kjv(db))
    assert audit["classes"] == {
        "ok": 1,
        "impossible_ref": 3,
        "en_needs_review": 3,  # GEN 1:3, 1:26 and 1:31 all differ from KJV
        "null_en": 1,
    }
    assert audit["ref_formula_mismatches"] == 0
    assert audit["duplicate_ref_triples"] == 0
    assert audit["target_row_count"] == TOTAL_VERSES == 31102
    assert audit["kjv_loaded"] is True
    # 2 resolvable impossible rows + 1 unresolvable
    assert audit["needs_founder_counts"]["unresolvable"] == 1
    assert audit["needs_founder_counts"]["neither_ok_pairs"] == 1
    assert audit["needs_founder_counts"]["null_en"] == 1
    assert audit["needs_founder_counts"]["en_variants"] == 3
    assert audit["pair_quality"] == {"dup_only_ok": 1, "neither_ok": 1}
    assert audit["en_restore_candidates"] == 1
    for table in ("word_alignments", "translations"):
        downstream = audit["downstream"][table]
        assert downstream["bad_refs"] == 1  # distinct non-verse ref values
        assert downstream["bad_ref_rows"] == 1
        assert downstream["non_verse_rows"] == 0
        assert downstream["remap_rows"] == 1  # rows holding a doomed ref


def test_audit_never_writes(db: Path) -> None:
    conn = sqlite3.connect(db)
    before_dump = _dump(conn)
    tables_before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    audit_before = conn.execute("SELECT COUNT(*) FROM data_audit_log").fetchone()[0]
    conn.close()

    run_audit(db, kjv_path=_kjv(db))

    conn = sqlite3.connect(db)
    assert _dump(conn) == before_dump
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == tables_before
    assert ARCHIVE_TABLE not in tables_before  # no DDL in audit mode
    assert conn.execute("SELECT COUNT(*) FROM data_audit_log").fetchone()[0] == audit_before
    conn.close()


def test_audit_without_kjv_degrades_to_structural(db: Path) -> None:
    audit = run_audit(db, kjv_path=db.parent / "missing.json")
    assert audit["kjv_loaded"] is False
    assert audit["classes"]["impossible_ref"] == 3  # structural still works
    assert audit["classes"]["en_needs_review"] == 0  # text checks are skipped
    assert audit["classes"]["ok"] == 4  # all valid refs (EN checks skipped)


def test_audit_report_written(db: Path, tmp_path: Path) -> None:
    audit = run_audit(db, kjv_path=_kjv(db))
    out = write_report(audit, tmp_path / "report.md")
    text = out.read_text(encoding="utf-8")
    assert "needs-founder" in text.lower()
    assert "impossible_ref" in text
    assert "word_alignments" in text


# ---------------------------------------------------------------------------
# Fix (dry-run / apply / idempotency)
# ---------------------------------------------------------------------------


def test_fix_dry_run_writes_nothing(db: Path) -> None:
    conn = sqlite3.connect(db)
    before = _dump(conn)
    audit_before = conn.execute("SELECT COUNT(*) FROM data_audit_log").fetchone()[0]
    conn.close()

    stats = run_fix(db, kjv_path=_kjv(db), apply=False)

    assert stats["apply"] is False
    assert stats["actions"] == 3
    assert stats["archived"] == 0 and stats["audit_rows_written"] == 0
    conn = sqlite3.connect(db)
    assert _dump(conn) == before
    assert ARCHIVE_TABLE not in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }, "dry-run must not even create the archive table"
    assert conn.execute("SELECT COUNT(*) FROM data_audit_log").fetchone()[0] == audit_before
    conn.close()


def test_fix_apply_archives_restores_and_audits(db: Path) -> None:
    kjv = _kjv(db)
    stats = run_fix(db, kjv_path=kjv, apply=True)

    assert stats["rows_before"] == 8
    assert stats["rows_after"] == 5
    assert stats["archived"] == 3  # 2 resolvable + 1 unresolvable
    assert stats["en_restored"] == 1
    assert stats["fill_null_en"] == 0
    assert stats["refused_duplicate"] == 0
    assert stats["archive_count"] == 3
    assert stats["audit_rows_written"] == 4  # 3 archive + 1 EN restore
    assert stats["audit_log_total_after"] == 4
    assert stats["unresolvable"] == 1

    conn = sqlite3.connect(db)
    refs = {r[0] for r in conn.execute("SELECT ref FROM bible_verses")}
    assert refs == {"GEN 1:1", "GEN 1:2", "GEN 1:3", "GEN 1:26", "GEN 1:31"}
    # KJV-verified EN restored into the (demonstrably wrong) target cell
    target = conn.execute("SELECT en_kJV FROM bible_verses WHERE ref='GEN 1:31'").fetchone()[0]
    assert target == _kjv_text("GEN", 1, 31)
    # needs-founder rows untouched
    blocked = conn.execute(
        "SELECT en_kJV FROM bible_verses WHERE ref='GEN 1:26'"
    ).fetchone()[0]
    assert blocked == "target wording unknown"
    assert conn.execute("SELECT en_kJV FROM bible_verses WHERE ref='GEN 1:2'").fetchone()[0] is None
    # the unresolvable row is archived too — kept verbatim, never guessed a home
    assert conn.execute("SELECT 1 FROM bible_verses WHERE ref='GEN 1:32'").fetchone() is None
    archived = conn.execute(
        "SELECT ref, source_row_id, archive_reason FROM bible_verses_archive ORDER BY source_row_id"
    ).fetchall()
    assert [(r[0], r[1]) for r in archived] == [
        ("GEN 2:31", 5), ("GEN 2:26", 7), ("GEN 1:32", 8)
    ]
    assert {r[2] for r in archived} == {REASON}
    conn.close()

    rows = _audit_rows(db)
    assert len(rows) == stats["audit_rows_written"] == 4
    archive_rows = [r for r in rows if r[5] == REASON]
    restore_rows = [r for r in rows if r[5] == EN_RESTORE_REASON]
    assert len(archive_rows) == 3 and len(restore_rows) == 1
    assert all(r[2] == "ref" and r[4] == f"ARCHIVED:{r[3]}" for r in archive_rows)
    assert restore_rows[0][2] == "en_kJV"


def test_fix_apply_is_idempotent(db: Path) -> None:
    kjv = _kjv(db)
    first = run_fix(db, kjv_path=kjv, apply=True)
    second = run_fix(db, kjv_path=kjv, apply=True)

    assert second["archived"] == 0
    assert second["en_restored"] == 0
    assert second["audit_rows_written"] == 0
    assert second["actions_pending"] == 0  # nothing impossible left to act on
    assert second["already_archived"] == 0
    assert second["rows_after"] == first["rows_after"] == 5
    assert second["archive_count"] == first["archive_count"] == 3
    assert len(_audit_rows(db)) == first["audit_rows_written"] == 4


def test_fix_never_writes_zo_or_label_columns(db: Path) -> None:
    before = {row[0]: row[1:] for row in _snapshot(db)}
    run_fix(db, kjv_path=_kjv(db), apply=True)

    # every surviving row keeps its ZO/label cells byte-for-byte …
    after = {row[0]: row[1:] for row in _snapshot(db)}
    assert set(after) == {1, 2, 3, 4, 6}  # ids 5, 7 and 8 were archived
    for row_id, values in after.items():
        assert values == before[row_id], f"row {row_id}: ZO/label column changed"

    # … and the archived rows carry their original cells into the archive
    conn = sqlite3.connect(db)
    archived = {
        r[0]: tuple(r[1:])
        for r in conn.execute(
            f"SELECT source_row_id, {', '.join(_ZO_LABEL)} FROM {ARCHIVE_TABLE}"
        )
    }
    conn.close()
    assert set(archived) == {5, 7, 8}
    for row_id, values in archived.items():
        assert values == before[row_id], f"archived row {row_id}: cell altered"

    rows = _audit_rows(db)
    assert len(rows) == 4
    for table_name, _row_id, field, *_rest in rows:
        assert table_name == "bible_verses"
        assert field in {"ref", "en_kJV"}, f"audit row wrote {field}"


def test_fix_source_only_updates_en_column() -> None:
    """Static guard: the module's write paths can never touch a ZO/label cell."""
    source = Path(brf.__file__).read_text(encoding="utf-8")
    updates = re.findall(r'UPDATE\s+\{?TABLE\}?\s+SET\s+"?(\w+)"?', source)
    assert updates, "expected at least one canonical UPDATE site"
    assert set(updates) <= {"en_kJV"}
    downstream_updates = re.findall(
        r'UPDATE\s+"\{table\}"\s+SET\s+"\{column\}"', source
    )
    assert downstream_updates, "remap must UPDATE the downstream ref column"
    assert "DELETE FROM" in source


def test_fix_refuses_duplicate_archive(db: Path) -> None:
    """A row already archived (by id or ref) is never archived twice."""
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)

    # Same ref, *different* row id → duplicate refusal (id archived, id not).
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO bible_verses (id, ref, book, chapter, verse, book_name) "
        "VALUES (99, 'GEN 2:31', 'GEN', 2, 31, 'Fixture')"
    )
    conn.commit()
    conn.close()

    stats = run_fix(db, kjv_path=kjv, apply=True)
    assert stats["refused_duplicate"] == 1
    assert stats["archived"] == 0
    assert stats["archive_count"] == 3


# ---------------------------------------------------------------------------
# Revert (round-trip)
# ---------------------------------------------------------------------------


def test_revert_restores_rows_byte_identically(db: Path) -> None:
    kjv = _kjv(db)
    before = _dump(sqlite3.connect(db))
    before_audit = _audit_rows(db)

    run_fix(db, kjv_path=kjv, apply=True)
    reverted = run_revert(db, apply=True)

    assert reverted["archive_rows"] == 3
    assert reverted["restored"] == 3
    assert reverted["refused_duplicate"] == 0
    assert reverted["en_restores_seen"] == 1
    assert reverted["en_restores_reversed"] == 1
    assert reverted["archive_rows_after"] == 0
    assert reverted["audit_rows_written"] == 4  # 3 reinserts + 1 EN reversal

    after = _dump(sqlite3.connect(db))
    assert after == before, "revert must restore ids and text byte-identically"

    rows = _audit_rows(db)
    # 4 rows from the fix (3 archive + 1 EN restore) + 4 from the revert
    assert len(rows) == len(before_audit) + 8
    assert all(r[5] == REVERT_REASON for r in rows[len(before_audit) + 4:])

    # idempotent: a second revert has nothing left to do
    again = run_revert(db, apply=True)
    assert again["archive_rows"] == 0
    assert again["restored"] == 0
    assert again["en_restores_seen"] == 1 and again["en_restores_reversed"] == 0
    assert again["audit_rows_written"] == 0
    assert len(_audit_rows(db)) == len(rows)


def test_revert_dry_run_writes_nothing(db: Path) -> None:
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)
    snapshot = _dump(sqlite3.connect(db))
    audit_before = len(_audit_rows(db))

    stats = run_revert(db, apply=False)

    assert stats["archive_rows"] == 3
    assert stats["restored"] == 0 and stats["audit_rows_written"] == 0
    assert _dump(sqlite3.connect(db)) == snapshot
    assert len(_audit_rows(db)) == audit_before
    assert sqlite3.connect(db).execute(
        f"SELECT COUNT(*) FROM {ARCHIVE_TABLE}"
    ).fetchone()[0] == 3


def test_revert_refuses_duplicate_ref(db: Path) -> None:
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)

    # Somebody re-inserted GEN 2:31 by hand → revert must not duplicate it.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO bible_verses (id, ref, book, chapter, verse, book_name) "
        "VALUES (77, 'GEN 2:31', 'GEN', 2, 31, 'Fixture')"
    )
    conn.commit()
    conn.close()

    stats = run_revert(db, apply=True)
    assert stats["refused_duplicate"] == 1
    assert stats["restored"] == 2  # GEN 2:26 + GEN 1:32 still came back
    assert stats["archive_rows_after"] == 1  # the refused row stays archived

    conn = sqlite3.connect(db)
    refs = [r[0] for r in conn.execute("SELECT ref FROM bible_verses ORDER BY id")]
    assert refs.count("GEN 2:31") == 1, "duplicate ref created"
    conn.close()


def test_revert_without_archive_is_a_no_op(db: Path) -> None:
    stats = run_revert(db, apply=True)
    assert stats["archive_present"] is False
    assert stats["archive_rows"] == 0
    assert stats["audit_rows_written"] == 0


# ---------------------------------------------------------------------------
# Fill-NULL gate + downstream remap
# ---------------------------------------------------------------------------


def test_null_en_is_report_only_unless_flagged(db: Path) -> None:
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)  # default: never fills NULL
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT en_kJV FROM bible_verses WHERE ref='GEN 1:2'").fetchone()[0] is None
    conn.close()

    stats = run_fix(db, kjv_path=kjv, apply=True, fill_null_en=True)
    assert stats["fill_null_en"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT en_kJV FROM bible_verses WHERE ref='GEN 1:2'").fetchone()[0] == _kjv_text("GEN", 1, 2)
    conn.close()
    assert any(r[5] == FILL_NULL_REASON for r in _audit_rows(db))

    # reversal of the opt-in fill
    reverted = run_revert(db, apply=True)
    assert reverted["en_restores_seen"] == 2
    assert reverted["en_restores_reversed"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT en_kJV FROM bible_verses WHERE ref='GEN 1:2'").fetchone()[0] is None
    conn.close()


def test_audit_counts_archived_refs_as_pending_remap(db: Path) -> None:
    """After fix, downstream copies of archived refs stay 'pending remap'."""
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)

    audit = run_audit(db, kjv_path=kjv)

    assert audit["archive"]["count"] == 3
    for table in ("word_alignments", "translations"):
        assert audit["downstream"][table]["remap_rows"] == 1
        assert audit["downstream"][table]["bad_refs"] == 1


def test_remap_rewrites_downstream_refs(db: Path) -> None:
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)

    dry = run_remap(db, apply=False)
    assert dry["mapping_size"] == 2
    assert dry["downstream"]["word_alignments"]["remapped"] == 1
    assert dry["downstream"]["translations"]["remapped"] == 1
    assert dry["audit_rows_written"] == 0

    applied = run_remap(db, apply=True)
    assert applied["audit_rows_written"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT ref FROM word_alignments WHERE id=1").fetchone()[0] == "GEN 1:31"
    assert conn.execute("SELECT reference FROM translations WHERE id=1").fetchone()[0] == "GEN 1:31"
    assert conn.execute("SELECT ref FROM word_alignments WHERE id=2").fetchone()[0] == "GEN 1:1"
    conn.close()
    assert [r for r in _audit_rows(db) if r[5] == REMAP_REASON] and len(
        [r for r in _audit_rows(db) if r[5] == REMAP_REASON]
    ) == 2

    again = run_remap(db, apply=True)
    assert again["audit_rows_written"] == 0
    assert again["downstream"]["word_alignments"]["remapped"] == 0

    # downstream is clean now
    assert run_audit(db, kjv_path=kjv)["downstream"]["word_alignments"]["bad_refs"] == 0


def test_remap_reports_unmapped_unresolvable_refs(db: Path) -> None:
    """An archived row with no real target never rewrites downstream rows."""
    run_fix(db, kjv_path=_kjv(db), apply=True)  # archives GEN 1:32 too

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO word_alignments (id, ref, zolai_word, english_word) "
        "VALUES (3, 'GEN 1:32', 'khem', 'thin')"
    )
    conn.commit()
    conn.close()

    stats = run_remap(db, apply=True)
    # GEN 2:31 and GEN 2:26 map to chapter 1; GEN 1:32 has no home at all.
    assert stats["mapping_size"] == 2
    assert stats["unmapped_refs"] == ["GEN 1:32"]
    assert stats["downstream"]["word_alignments"]["remapped"] == 1
    assert stats["downstream"]["word_alignments"]["unmapped"] == 1
    assert stats["downstream"]["translations"]["unmapped"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT ref FROM word_alignments WHERE id=3").fetchone()[0] == "GEN 1:32"
    conn.close()


def test_remap_dry_run_never_writes(db: Path) -> None:
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)
    snapshot = _dump(sqlite3.connect(db))
    audit_before = len(_audit_rows(db))

    run_remap(db, apply=False)

    assert _dump(sqlite3.connect(db)) == snapshot
    assert len(_audit_rows(db)) == audit_before
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT ref FROM word_alignments WHERE id=1").fetchone()[0] == "GEN 2:31"
    conn.close()


# ---------------------------------------------------------------------------
# Archive DDL + structural table
# ---------------------------------------------------------------------------


def test_archive_ddl_is_create_only_and_additive(db: Path) -> None:
    run_fix(db, kjv_path=_kjv(db), apply=True)
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({ARCHIVE_TABLE})")]
    assert cols == [*BIBLE_COLUMNS, "source_row_id", "archive_reason", "archived_at"]
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (ARCHIVE_TABLE,)
    ).fetchone()[0]
    conn.close()
    assert ddl.startswith("CREATE TABLE bible_verses_archive")

    source = Path(brf.__file__).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" in source
    assert "ALTER TABLE" not in source
    assert not re.search(r"DROP TABLE", source)


def test_second_fix_run_never_creates_a_second_archive_row(db: Path) -> None:
    kjv = _kjv(db)
    run_fix(db, kjv_path=kjv, apply=True)
    conn = sqlite3.connect(db)
    source_ids = [r[0] for r in conn.execute("SELECT source_row_id FROM bible_verses_archive")]
    assert len(source_ids) == len(set(source_ids)) == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (ARCHIVE_TABLE,)
    ).fetchone()[0] == 1
    conn.close()


def test_verse_counts_table_is_consistent() -> None:
    assert len(BOOK_ORDER) == 66
    assert set(VERSE_COUNTS) == set(BOOK_ORDER)
    assert sum(sum(v) for v in VERSE_COUNTS.values()) == TOTAL_VERSES == 31102
    assert all(v >= 1 for counts in VERSE_COUNTS.values() for v in counts)


def test_load_kjv_round_trips_the_fixture(tmp_path: Path) -> None:
    kjv = _write_kjv(tmp_path)
    loaded = load_kjv(kjv)
    assert loaded is not None
    assert len(loaded) == TOTAL_VERSES
    assert loaded[("GEN", 1, 31)] == _kjv_text("GEN", 1, 31)
    assert load_kjv(tmp_path / "nope.json") is None


def test_match_grades_kjv_text_variants() -> None:
    """Verse + appended marginal note counts as the same verse, not a variant."""
    verse = "And the sons of Gomer; Ashkenaz, and Riphath, and Togarmah."
    note = "Riphath: or, Diphath as it is in some copies"
    assert _match(verse, verse) == "exact"
    assert _match("And the sons of Gomer Ashkenaz and Riphath and Togarmah", verse) == "normalized"
    assert _match(verse + note, verse) == "extended"
    assert _match("An entirely unrelated sentence about the sea.", verse) is None
    assert _match(None, verse) is None
    assert _match(verse, None) is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_audit_json_is_read_only(db: Path, tmp_path: Path) -> None:
    report = tmp_path / "audit.json"
    before = _dump(sqlite3.connect(db))
    result = _RUNNER.invoke(
        cli_app,
        ["bible-ref", "audit", "--db", str(db), "--kjv", str(_kjv(db)), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["classes"]["impossible_ref"] == 3
    assert ARCHIVE_TABLE not in {
        r[0] for r in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert _dump(sqlite3.connect(db)) == before
    assert report.exists() is False


def test_cli_fix_dry_run_then_apply_then_revert(db: Path) -> None:
    args = ["bible-ref", "fix", "--db", str(db), "--kjv", str(_kjv(db))]
    dry = _RUNNER.invoke(cli_app, args)
    assert dry.exit_code == 0, dry.output
    assert "DRY-RUN" in dry.output
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0] == 8

    applied = _RUNNER.invoke(cli_app, [*args, "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "APPLY" in applied.output
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0] == 5
    assert conn.execute(f"SELECT COUNT(*) FROM {ARCHIVE_TABLE}").fetchone()[0] == 3
    conn.close()

    reverted = _RUNNER.invoke(cli_app, ["bible-ref", "revert", "--db", str(db), "--apply"])
    assert reverted.exit_code == 0, reverted.output
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0] == 8
    assert conn.execute(f"SELECT COUNT(*) FROM {ARCHIVE_TABLE}").fetchone()[0] == 0
    conn.close()


def test_cli_remap_after_fix(db: Path) -> None:
    kjv = _kjv(db)
    assert _RUNNER.invoke(cli_app, ["bible-ref", "fix", "--db", str(db), "--kjv", str(kjv), "--apply"]).exit_code == 0

    dry = _RUNNER.invoke(cli_app, ["bible-ref", "remap", "--db", str(db)])
    assert dry.exit_code == 0, dry.output
    assert "DRY-RUN" in dry.output

    applied = _RUNNER.invoke(cli_app, ["bible-ref", "remap", "--db", str(db), "--apply"])
    assert applied.exit_code == 0, applied.output
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT ref FROM word_alignments WHERE id=1").fetchone()[0] == "GEN 1:31"
    conn.close()
