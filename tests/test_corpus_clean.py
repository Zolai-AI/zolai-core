"""C1 corpus clean — audit (read-only), dry-run, apply, idempotency, report.

Covers the invariants of ``zolai.data.corpus_clean`` (plan C1_CORPUS_CLEAN):

- ``run_audit`` and ``corpus audit`` never write (no DB rows, no state file).
- dry-run writes nothing; ``--apply`` updates exactly the pending cells and
  appends one ``data_audit_log`` row per changed cell (``reason=REASON``).
- a second apply is a no-op (0 cells / 0 audit rows — idempotency).
- EN/MY columns, ``*_import`` staging and audit-only Bible columns are
  byte-identical after apply; row counts never change.
- HTML unescapes to a fixed point; whitespace collapses; modern ZVS forms are
  rewritten while scripture columns keep their historical forms; ``suah`` is
  never rewritten (review-need); word-sanity failures are never written.
- JSON dicts clean the ``zo`` key only; unparseable JSON is a review-need.
- direction-aware ``translations`` cleaning (``en_to_zo`` target / ``zo_to_en``
  source only) and duplicate-group counting.
- a cleaned value already owned by another row under a UNIQUE index is refused
  (review-need ``unique``) — row merges are a destructive dedupe C1 never does.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zolai.cli.main import app as cli_app
from zolai.data.corpus_clean import (
    COLUMN_REGISTRY,
    DUP_KEYS,
    REASON,
    REVERT_CATEGORIES,
    REVERT_REASON,
    _registry_tables,
    _specs_for,
    append_apply_results,
    clean_value,
    run_audit,
    run_clean,
    run_revert_c1_1,
    write_report,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

STAGING_SQL = (
    "CREATE TABLE dictionary_import (id INTEGER PRIMARY KEY, zolai TEXT, "
    "english TEXT, myanmar TEXT)"
)
AUDIT_SQL = """CREATE TABLE data_audit_log (
    id INTEGER PRIMARY KEY, table_name TEXT, row_id INT, field TEXT,
    old_value TEXT, new_value TEXT, changed_at TEXT, reason TEXT,
    version INT DEFAULT 1, created_at TEXT, updated_at TEXT, content_hash TEXT)"""


def _insert(conn: sqlite3.Connection, table: str, **values) -> int:
    cols = list(values)
    cur = conn.execute(
        f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
        [values[c] for c in cols],
    )
    return int(cur.lastrowid)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Registry-shaped store with known dirty cells in every defect class."""
    path = tmp_path / "corpus.db"
    conn = sqlite3.connect(path)
    conn.execute(AUDIT_SQL)
    conn.execute(STAGING_SQL)
    # Tables/columns come from the registry so a schema drift fails the test
    # loudly (LookupError) instead of silently scanning nothing.
    extra = {"bible_verses": ["ref", "en_kJV"]}
    for table in _registry_tables():
        cols = {"id": "INTEGER PRIMARY KEY"}
        for spec in _specs_for(table):
            cols.setdefault(spec.column, "TEXT")
            if spec.only_when:
                cols.setdefault(spec.only_when[0], "TEXT")
        for key in DUP_KEYS.get(table, ()):
            cols.setdefault(key, "TEXT")
        for key in extra.get(table, ()):
            cols.setdefault(key, "TEXT")
        decl = ", ".join(f'"{name}" {ctype}' for name, ctype in cols.items())
        conn.execute(f'CREATE TABLE "{table}" ({decl})')

    # UNIQUE indexes like the live schema — a cleaned value that already
    # exists in another row must be refused (review-need `unique`), not merged.
    conn.execute("CREATE UNIQUE INDEX ux_dict_zolai ON dictionary(zolai)")
    conn.execute("CREATE UNIQUE INDEX ux_prov_zolai ON proverbs(zolai)")

    # --- dictionary (modern ZO word column) --------------------------------
    _insert(conn, "dictionary", zolai="  khem&nbsp;  ", english="lie &amp; deceive")
    _insert(conn, "dictionary", zolai="pathian", english="God")
    _insert(conn, "dictionary", zolai="suah", english="holiness")
    # staging copy must stay byte-identical
    conn.execute(
        "INSERT INTO dictionary_import (zolai, english, myanmar) VALUES (?, ?, ?)",
        ("  khem&nbsp;  ", "lie &amp; deceive", "\u1000"),
    )
    # --- bible_verses (scripture sentence columns) -------------------------
    _insert(
        conn,
        "bible_verses",
        ref="GEN 1:1",
        en_kJV="God created",
        zo_tdb77="Pasian in leitung a piangsak hi.",
        zo_tedim2010="pathian in leitung a piangsak hi.",
        zo_hcl06="ciang in pathian a piangsak hi",
        zo_fcl="ciang in bawipa a piangsak hi",
    )
    # --- translations (direction-aware) ------------------------------------
    _insert(conn, "translations", source="hello", target="kasang&nbsp;hi", direction="en_to_zo")
    _insert(conn, "translations", source="hello", target="mangai", direction="en_to_my")
    _insert(conn, "translations", source="kasang&nbsp;hi", target="hello", direction="zo_to_en")
    _insert(conn, "translations", source="mangai", target="hello", direction="en_to_my")
    # --- phrases / dictionary_en_zo / vocabulary ---------------------------
    _insert(conn, "phrases", zolai="a&nbsp;b", english="x &amp; y")
    # JSON cell whose ONLY defect is `suah` (never rewritten → no write will
    # ever be pending) — it must still be counted as a suah review-need.
    _insert(
        conn,
        "phrases",
        zolai="ka pai",
        english="I go",
        examples='[{"ref": "1JN 1:2", "zo": "hong suah cia", "en": "x"}]',
    )
    _insert(
        conn,
        "dictionary_en_zo",
        headword="tree",
        translations=json.dumps({"zo": "ka&nbsp;hi", "en": "he goes", "ref": "GEN 1:1"}),
        translations_clean="ka hi",
    )
    _insert(conn, "dictionary_en_zo", headword="bad", translations="{not json", translations_clean="")
    _insert(conn, "vocabulary", headword="Sing&nbsp;", english="tree")
    _insert(conn, "vocabulary", headword="KHEM", english="lie")
    # --- duplicate groups (counted only) -----------------------------------
    for _ in range(2):
        _insert(conn, "training_exercises", zolai="ka pai", english="I go")
    _insert(conn, "training_exercises", zolai="ka&nbsp;pai", english="I go")
    _insert(conn, "word_collocations", word1="ni", word2="bang")
    _insert(conn, "word_collocations", word1="ni", word2="bang")
    # --- remaining registry tables (smoke cells) ---------------------------
    _insert(conn, "proverbs", zolai="ni&amp;amp; bang", english="day and")
    # `bawipa` would clean to `topa`, which already exists in another row —
    # the UNIQUE index makes that a row merge, so C1 must refuse the write.
    _insert(conn, "proverbs", zolai="bawipa", english="first")
    _insert(conn, "proverbs", zolai="topa", english="second")
    _insert(conn, "zolai_vocabulary", zolai="nunnak", english="life")
    _insert(conn, "zolai_bible_analysis", zolai="Pasian&nbsp;hi", english="God is")
    _insert(conn, "zolai_grammar_patterns", zolai_example="ka&nbsp;pai kei", english_translation="no")
    _insert(conn, "zolai_proverbs_idioms", zolai="ni&nbsp;bang", english_translation="day")
    _insert(conn, "zolai_word_usage", word="ni&nbsp;", contexts=None)
    _insert(conn, "word_usage", word="Khem&nbsp;", co_occurring_words=json.dumps([{"zo": "dam", "en": "well"}]))
    conn.commit()
    conn.close()
    return path


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY id')]


def _audit_rows(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM data_audit_log ORDER BY id")]


def _state_file(db: Path) -> Path:
    return db.parent / ".corpus_clean_state.json"


# ---------------------------------------------------------------------------
# audit — read-only
# ---------------------------------------------------------------------------


def test_audit_writes_nothing(db: Path) -> None:
    before = _rows(_connect(db), "dictionary")
    audit = run_audit(db_path=db)

    conn = _connect(db)
    assert _rows(conn, "dictionary") == before
    assert _audit_rows(conn) == []
    conn.close()
    assert not _state_file(db).exists()
    assert audit["totals"]["cells"] > 0
    assert audit["duplicate_groups_total"] == 2
    # internal bookkeeping key must not leak into the report payload
    assert all("_sample_limit" not in col for col in audit["columns"])


def test_audit_registry_excludes_import_tables() -> None:
    assert not any(t.endswith("_import") for t in _registry_tables())
    cols = {spec.column for spec in COLUMN_REGISTRY}
    # live dictionary column is `zolai` (not the JSONL `zo`), EN/MY never selected
    assert "zolai" in cols
    assert "zo" not in cols
    assert not (cols & {"english", "english_clean", "english_translation", "myanmar", "en_kJV"})


def test_audit_skips_absent_tables(tmp_path: Path) -> None:
    path = tmp_path / "partial.db"
    conn = sqlite3.connect(path)
    conn.execute(AUDIT_SQL)
    conn.execute("CREATE TABLE dictionary (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT)")
    conn.execute("INSERT INTO dictionary (zolai, english) VALUES ('pathian', 'God')")
    conn.commit()
    conn.close()

    audit = run_audit(db_path=path)  # explicit store — never the canonical one
    assert audit["scanned_tables"] == ["dictionary"]
    assert "bible_verses" in audit["skipped_tables"]


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(db: Path) -> None:
    conn = _connect(db)
    before = {t: _rows(conn, t) for t in ("dictionary", "bible_verses", "translations")}
    conn.close()

    stats = run_clean(db_path=db, apply=False)

    assert stats["applied"] is False
    assert stats["totals"]["cells_changed"] > 0
    assert stats["totals"]["audit_rows"] == 0
    conn = _connect(db)
    assert {t: _rows(conn, t) for t in before} == before
    assert _audit_rows(conn) == []
    conn.close()
    assert not _state_file(db).exists()


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_updates_exact_cells_and_appends_audit_rows(db: Path) -> None:
    stats = run_clean(db_path=db, apply=True)
    changed = stats["totals"]["cells_changed"]

    conn = _connect(db)
    audit = _audit_rows(conn)
    assert changed > 0
    assert len(audit) == changed
    assert all(row["reason"] == REASON for row in audit)
    assert all(row["old_value"] != row["new_value"] for row in audit)
    assert {row["table_name"] for row in audit} <= set(stats["scanned_tables"])

    # the audited old value is what the cell used to hold (reversible writes)
    row = next(
        r for r in audit
        if r["table_name"] == "dictionary" and r["field"] == "zolai"
        and r["old_value"] == "pathian"
    )
    live = conn.execute(
        "SELECT zolai FROM dictionary WHERE id = ?", (row["row_id"],)
    ).fetchone()[0]
    assert live == "pasian"
    assert row["new_value"] == live
    conn.close()

    assert stats["audit_log_total_after"] == changed


def test_apply_is_idempotent(db: Path) -> None:
    first = run_clean(db_path=db, apply=True)
    assert first["totals"]["cells_changed"] > 0

    second = run_clean(db_path=db, apply=True)
    assert second["totals"]["cells_changed"] == 0
    assert second["totals"]["audit_rows"] == 0
    assert second["audit_log_total_after"] == first["totals"]["audit_rows"]

    # report stays stable: a fresh audit finds no writable cell left
    audit = run_audit(db_path=db)
    assert audit["totals"]["would_write"] == 0


def test_apply_preserves_en_my_staging_and_row_counts(db: Path) -> None:
    conn = _connect(db)
    tables = _registry_tables()
    before = {t: _rows(conn, t) for t in tables}
    staging = _rows(conn, "dictionary_import")
    conn.close()

    run_clean(db_path=db, apply=True)

    conn = _connect(db)
    after = {t: _rows(conn, t) for t in tables}
    conn.close()
    for table in tables:
        assert len(after[table]) == len(before[table]), f"row count changed: {table}"

    # every column NOT in the cleaning registry (EN/MY/labels) is byte-identical
    writable = {spec.column for spec in COLUMN_REGISTRY}
    for table in tables:
        for old_row, new_row in zip(before[table], after[table]):
            for column, old_value in old_row.items():
                if column in writable:
                    continue
                assert new_row[column] == old_value, f"{table}.{column} was rewritten"
    assert _rows(_connect(db), "dictionary_import") == staging


def test_apply_never_touches_audit_only_bible_columns(db: Path) -> None:
    run_clean(db_path=db, apply=True)
    conn = _connect(db)
    row = conn.execute("SELECT zo_hcl06, zo_fcl FROM bible_verses").fetchone()
    assert row["zo_hcl06"] == "ciang in pathian a piangsak hi"
    assert row["zo_fcl"] == "ciang in bawipa a piangsak hi"
    conn.close()


def test_state_file_marks_completed_run(db: Path) -> None:
    run_clean(db_path=db, apply=True)
    state = json.loads(_state_file(db).read_text(encoding="utf-8"))
    assert state["in_progress"] is False
    assert state["db"] == str(db)


# ---------------------------------------------------------------------------
# cleaning rules
# ---------------------------------------------------------------------------


def test_html_unescapes_to_fixed_point(db: Path) -> None:
    run_clean(db_path=db, apply=True)
    conn = _connect(db)
    # NBSP + single-encoded entity collapse in ZO columns
    assert conn.execute("SELECT zolai FROM dictionary WHERE id = 1").fetchone()[0] == "khem"
    assert conn.execute("SELECT zolai FROM zolai_bible_analysis").fetchone()[0] == "Pasian hi"
    # double-encoded entity needs two unescape passes (`&amp;amp;` → `&amp;` → `&`)
    assert conn.execute("SELECT zolai FROM proverbs WHERE id = 1").fetchone()[0] == "ni& bang"
    # EN columns are never selected — byte-identical after apply
    assert conn.execute("SELECT english FROM dictionary WHERE id = 1").fetchone()[0] == "lie &amp; deceive"
    conn.close()


def test_modern_zvs_rewritten_but_suah_kept(db: Path) -> None:
    stats = run_clean(db_path=db, apply=True)
    conn = _connect(db)

    assert conn.execute("SELECT zolai FROM dictionary WHERE id = 2").fetchone()[0] == "pasian"
    # `suah` is never rewritten — it stays and becomes a review-need
    assert conn.execute("SELECT zolai FROM dictionary WHERE id = 3").fetchone()[0] == "suah"
    assert conn.execute("SELECT zolai FROM zolai_vocabulary").fetchone()[0] == "nuntakna"
    conn.close()

    assert stats["totals"]["review_needs"]["suah"] >= 1
    assert stats["totals"]["zvs"] >= 1


def test_word_sanity_failure_is_never_written(db: Path) -> None:
    stats = run_clean(db_path=db, apply=True)
    conn = _connect(db)
    # `Sing&nbsp;` would clean to `Sing` — fails ^[a-z][a-z-]*$ → refused
    assert conn.execute("SELECT headword FROM vocabulary WHERE id = 1").fetchone()[0] == "Sing&nbsp;"
    # `KHEM` needs no change (uppercase is a defect class, not a pending write)
    assert conn.execute("SELECT headword FROM vocabulary WHERE id = 2").fetchone()[0] == "KHEM"
    conn.close()

    assert stats["totals"]["review_needs"]["word_sanity"] >= 1
    # audit reports the raw defect class too (`KHEM` fails but needs no write)
    audit = run_audit(db_path=db)
    assert audit["totals"]["word_sanity"] >= 2
    assert audit["totals"]["blocked"] >= 2


def test_scripture_columns_keep_historical_forms(db: Path) -> None:
    run_clean(db_path=db, apply=True)
    conn = _connect(db)
    # scripture ctx allows historical forms (HISTORICAL_EXCEPTIONS)
    assert conn.execute("SELECT zo_tedim2010 FROM bible_verses").fetchone()[0].startswith("pathian ")
    # ... while the same forbidden form in a modern column is rewritten
    assert conn.execute("SELECT zolai FROM dictionary WHERE id = 2").fetchone()[0] == "pasian"
    conn.close()


def test_translations_is_direction_aware(db: Path) -> None:
    run_clean(db_path=db, apply=True)
    conn = _connect(db)
    rows = {r["direction"]: (r["source"], r["target"]) for r in _rows(conn, "translations")}

    assert rows["en_to_zo"][1] == "kasang hi"          # target cleaned
    assert rows["zo_to_en"][0] == "kasang hi"          # source cleaned
    # en_to_my rows are out of scope for both specs — byte-identical
    my_rows = [r for r in _rows(conn, "translations") if r["direction"] == "en_to_my"]
    assert [(r["source"], r["target"]) for r in my_rows] == [("hello", "mangai"), ("mangai", "hello")]
    conn.close()


def test_json_dicts_clean_zo_key_only(db: Path) -> None:
    stats = run_clean(db_path=db, apply=True)
    conn = _connect(db)
    cell = conn.execute("SELECT translations FROM dictionary_en_zo WHERE id = 1").fetchone()[0]
    parsed = json.loads(cell)  # still parseable after cleaning
    assert parsed["zo"] == "ka hi"          # NBSP collapsed in the ZO value
    assert parsed["en"] == "he goes"        # EN key byte-identical
    assert parsed["ref"] == "GEN 1:1"       # label key byte-identical
    # unparseable JSON is a review-need and is left alone
    assert conn.execute("SELECT translations FROM dictionary_en_zo WHERE id = 2").fetchone()[0] == "{not json"
    conn.close()
    assert stats["totals"]["json"] == 1     # one JSON cell written


def test_json_review_need_counted(db: Path) -> None:
    stats = run_clean(db_path=db, apply=True)
    # unparseable JSON is refused (review-need); the valid JSON cell is written
    assert stats["totals"]["review_needs"]["json"] == 1
    assert stats["totals"]["json"] == 1
    audit = run_audit(db_path=db)
    assert audit["totals"]["json_bad"] == 1


def test_unique_collision_is_skipped_and_counted(db: Path) -> None:
    """A cleaned value already owned by another row must never be written.

    `proverbs.bawipa` cleans to `topa`, but another row already holds `topa`
    under a UNIQUE index — writing it would merge two rows. C1 refuses the
    write, counts it as the `unique` review-need, and a second apply stays at
    0 cells (the collision is permanent until a founder decides).
    """
    audit = run_audit(db_path=db)
    assert audit["review_needs"]["unique"] == 1
    # blocked cells leave the would-write pool
    assert audit["totals"]["would_write"] < audit["totals"]["changed_cells"]

    stats = run_clean(db_path=db, apply=True)
    assert stats["totals"]["review_needs"]["unique"] == 1

    conn = _connect(db)
    zolai_values = [r["zolai"] for r in _rows(conn, "proverbs")]
    assert "bawipa" in zolai_values          # refused — still the original
    assert "topa" in zolai_values            # the pre-existing row untouched
    # no audit row was appended for the refused cell
    assert not [
        a for a in _audit_rows(conn)
        if a["table_name"] == "proverbs" and a["old_value"] == "bawipa"
    ]
    conn.close()

    # permanent collision: a second apply writes 0 cells (idempotent refusal)
    second = run_clean(db_path=db, apply=True)
    assert second["totals"]["cells_changed"] == 0
    audit2 = run_audit(db_path=db)
    assert audit2["totals"]["would_write"] == 0
    assert audit2["review_needs"]["unique"] == 1


def test_review_needs_survive_apply(db: Path) -> None:
    """Review-need counts are stable across apply — nothing silently resolves.

    Regression: the JSON dict branch dropped suah/zvs counters when no write
    was pending, so a JSON cell whose only defect was `suah` lost its
    review-need flag as soon as apply ran (its other defects were cleaned →
    no write pending → counters zeroed). `suah` is never rewritten, so the
    cell still needs the founder decision after apply.
    """
    pre = run_audit(db_path=db)
    stats = run_clean(db_path=db, apply=True)
    post = run_audit(db_path=db)

    assert post["review_needs"] == pre["review_needs"]
    assert stats["totals"]["review_needs"] == post["review_needs"]
    # the suah-only JSON fixture cell is counted (string + JSON parity)
    assert post["review_needs"]["suah"] >= 2


def test_duplicate_groups_counted_without_dropping_rows(db: Path) -> None:
    audit = run_audit(db_path=db)
    assert audit["duplicates"]["training_exercises"]["groups"] == 1
    assert audit["duplicates"]["word_collocations"]["groups"] == 1
    assert audit["duplicates"]["dictionary"]["groups"] == 0
    assert audit["duplicate_groups_total"] == 2

    stats = run_clean(db_path=db, apply=True)
    assert stats["totals"]["rows_before"] == stats["totals"]["rows_after"]
    conn = _connect(db)
    assert conn.execute("SELECT COUNT(*) FROM training_exercises").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM word_collocations").fetchone()[0] == 2
    conn.close()


def test_clean_rejects_missing_column(db: Path) -> None:
    conn = _connect(db)
    conn.execute("DROP INDEX ux_dict_zolai")  # SQLite blocks DROP COLUMN under an index
    conn.execute("ALTER TABLE dictionary DROP COLUMN zolai")
    conn.commit()
    conn.close()
    with pytest.raises(LookupError, match="dictionary.zolai"):
        run_clean(db_path=db, apply=False)


# ---------------------------------------------------------------------------
# report writer
# ---------------------------------------------------------------------------


def test_report_and_append_render(db: Path, tmp_path: Path) -> None:
    audit = run_audit(db_path=db)
    report = write_report(audit, tmp_path / "CORPUS_CLEAN_AUDIT_test.md")
    text = report.read_text(encoding="utf-8")
    assert "PRE-APPLY" in text
    assert "dictionary" in text and "`zolai`" in text
    assert "NEEDS-FOUNDER" in text.upper() or "needs-founder" in text
    assert str(db) in text

    stats = run_clean(db_path=db, apply=True)
    after = run_audit(db_path=db)
    append_apply_results(
        report, apply_stats=stats, audit_after=after,
        backup_note="backup-test.db.gz", idem_stats=None,
        notes=["pre-apply section regenerated from backup (counter fix)."],
    )
    text = report.read_text(encoding="utf-8")
    assert "## Apply results" in text
    assert "NO-drops proof" in text
    assert "backup-test.db.gz" in text
    assert "count-only — no deletes" in text
    assert "### Report notes" in text
    assert "counter fix" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _invoke(runner: CliRunner, *args: str):
    result = runner.invoke(cli_app, list(args))
    result.clean = _ANSI.sub("", result.stdout or "")  # type: ignore[attr-defined]
    return result


def test_cli_audit_is_read_only_and_json_is_valid(runner: CliRunner, db: Path, tmp_path: Path) -> None:
    report = tmp_path / "audit.md"
    result = _invoke(runner, "corpus", "audit", "--db", str(db), "--report", str(report), "--json")
    assert result.exit_code == 0, result.stdout

    payload = json.loads(result.clean)
    assert payload["totals"]["cells"] > 0
    assert payload["duplicate_groups_total"] == 2
    assert report.exists()

    conn = _connect(db)
    assert _audit_rows(conn) == []
    conn.close()
    assert not _state_file(db).exists()


def test_cli_clean_dry_run_then_apply(runner: CliRunner, db: Path) -> None:
    dry = _invoke(runner, "corpus", "clean", "--db", str(db))
    assert dry.exit_code == 0, dry.stdout
    assert "DRY-RUN" in dry.clean
    conn = _connect(db)
    assert _audit_rows(conn) == []
    assert conn.execute("SELECT zolai FROM dictionary WHERE id = 2").fetchone()[0] == "pathian"
    conn.close()

    applied = _invoke(runner, "corpus", "clean", "--db", str(db), "--apply")
    assert applied.exit_code == 0, applied.stdout
    assert "APPLY" in applied.clean
    conn = _connect(db)
    assert conn.execute("SELECT zolai FROM dictionary WHERE id = 2").fetchone()[0] == "pasian"
    assert len(_audit_rows(conn)) > 0
    conn.close()


def test_cli_clean_json_and_table_filter(runner: CliRunner, db: Path) -> None:
    result = _invoke(runner, "corpus", "clean", "--db", str(db), "--table", "dictionary", "--json")
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.clean)
    assert payload["scanned_tables"] == ["dictionary"]
    assert payload["applied"] is False


# ---------------------------------------------------------------------------
# C1.1 prevention guards (founder catch: person-name Ram / EN glosses /
# grammar meta-docs) + the revert writer
# ---------------------------------------------------------------------------


def test_c1_1_titlecase_ram_preserved_lowercase_rewritten() -> None:
    """Guard 1: `Ram` (Bible person-name) is never rewritten; exact lowercase
    `ram` still receives the ZVS rule."""
    sentence = "Jerahmeel, Ram, leh Khelubai ahi uh hi."
    kept = clean_value(sentence, "sentence")
    assert not kept.changed
    assert kept.cleaned == sentence
    assert kept.zvs_applied == 0

    rewritten = clean_value("a ram hi.", "sentence")
    assert rewritten.changed
    assert rewritten.cleaned == "a gam hi."


def test_c1_1_en_headword_element_untouched_others_cleaned() -> None:
    """Guard 2: a JSON element equal to an EN headword is an English gloss —
    no ZVS; a non-headword ZO element in the same cell is still cleaned."""
    cell = json.dumps(["ram", "pathian"])
    guarded = clean_value(
        cell, "json", table="dictionary_en_zo", en_headwords={"ram"}
    )
    assert json.loads(guarded.cleaned) == ["ram", "pasian"]

    # Without the guard (no table context) lowercase ram still rewrites.
    unguarded = clean_value(cell, "json")
    assert json.loads(unguarded.cleaned) == ["gam", "pasian"]

    # Plain `translations_clean` values take the same whole-value guard.
    word = clean_value("ram", "word", table="dictionary_en_zo", en_headwords={"ram"})
    assert not word.changed
    assert clean_value("ram", "word").cleaned == "gam"


def test_c1_1_meta_doc_guard_preserves_teaching_contrast() -> None:
    """Guard 3: `(not ` / `❌` teaching cells keep the forbidden form — the
    contrast is the content; normal cells in the same table still clean."""
    meta = "Uses `gam` (not `ram`)"
    guarded = clean_value(meta, "sentence", table="zolai_grammar_patterns")
    assert not guarded.changed
    assert guarded.cleaned == meta

    marked = clean_value("\u274c `ram`", "sentence", table="zolai_grammar_patterns")
    assert not marked.changed

    # Same text outside the grammar table is still normalised (guard is
    # table-scoped), proving the veto is what preserves the contrast.
    unguarded = clean_value(meta, "sentence")
    assert unguarded.changed
    assert unguarded.cleaned == "Uses `gam` (not `gam`)"

    # A normal (non-meta) cell in the same table keeps compound clean-up.
    normal = clean_value("ka hi leh om.", "sentence", table="zolai_grammar_patterns")
    assert normal.cleaned == "ka hihleh om."


def _seed_c1_1(db: Path) -> dict[str, int]:
    """Install one corrupted cell per founder category (A-D) plus one
    out-of-scope legit correction, each with its `corpus_clean_v1` audit row."""
    conn = _connect(db)

    def seed(table: str, row_id: int, field: str, old: str, new: str) -> None:
        conn.execute(
            f'UPDATE "{table}" SET "{field}" = ? WHERE id = ?', (new, row_id)
        )
        conn.execute(
            "INSERT INTO data_audit_log "
            "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (table, row_id, field, old, new, "2026-10-01T12:00:00", REASON),
        )

    a_id = _insert(
        conn,
        "translations",
        source="Hezron",
        target="Jerahmeel, Gam, leh Khelubai ahi uh hi.",
        direction="en_to_zo",
    )
    seed(
        "translations", a_id, "target",
        "Jerahmeel, Ram, leh Khelubai ahi uh hi.",
        "Jerahmeel, Gam, leh Khelubai ahi uh hi.",
    )

    b_id = _insert(
        conn,
        "dictionary_en_zo",
        headword="ram",
        translations='["pieces", "gam"]',
        translations_clean="",
    )
    seed("dictionary_en_zo", b_id, "translations",
         '["pieces", "ram"]', '["pieces", "gam"]')

    # B control: changed ZO leaf that is NOT an EN headword → stays.
    keep_id = _insert(
        conn,
        "dictionary_en_zo",
        headword="the",
        translations='["nasep"]',
        translations_clean="",
    )
    seed("dictionary_en_zo", keep_id, "translations",
         '["na sep"]', '["nasep"]')

    c_id = _insert(conn, "word_usage", word="gam", co_occurring_words=None)
    seed("word_usage", c_id, "word", "ram", "gam")

    d_id = _insert(
        conn,
        "zolai_grammar_patterns",
        zolai_example="Uses `gam` (not `gam`)",
        english_translation="contrast",
    )
    seed(
        "zolai_grammar_patterns", d_id, "zolai_example",
        "Uses `gam` (not `ram`)", "Uses `gam` (not `gam`)",
    )

    # Out of scope: legit compound correction in a real ZO sentence.
    keep_ex_id = _insert(
        conn, "training_exercises", zolai="a hihleh om", english="he did"
    )
    seed("training_exercises", keep_ex_id, "zolai",
         "a hi leh om", "a hihleh om")

    conn.commit()
    conn.close()
    return {
        "name_ram": a_id,
        "en_headword": b_id,
        "en_headword_keep": keep_id,
        "usage_ram": c_id,
        "grammar_meta": d_id,
        "keep_example": keep_ex_id,
    }


def test_c1_1_revert_dry_run_writes_nothing(db: Path) -> None:
    ids = _seed_c1_1(db)
    conn = _connect(db)
    audit_before = len(_audit_rows(conn))
    target_before = conn.execute(
        "SELECT target FROM translations WHERE id = ?", (ids["name_ram"],)
    ).fetchone()[0]
    conn.close()

    stats = run_revert_c1_1(db_path=db)
    assert stats["applied"] is False
    assert stats["status"]["pending"] == 4
    for key in REVERT_CATEGORIES:
        assert stats["detected"][key] == 1, (key, stats["detected"])
    assert stats["reverted"] == 0
    assert stats["audit_rows"] == 0

    conn = _connect(db)
    assert len(_audit_rows(conn)) == audit_before
    assert conn.execute(
        "SELECT target FROM translations WHERE id = ?", (ids["name_ram"],)
    ).fetchone()[0] == target_before
    conn.close()


def test_c1_1_revert_apply_restores_cells_and_appends_audit_rows(db: Path) -> None:
    ids = _seed_c1_1(db)
    count_tables = (
        "translations", "dictionary_en_zo", "word_usage",
        "zolai_grammar_patterns", "training_exercises",
    )
    conn = _connect(db)
    rows_before = {
        t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        for t in count_tables
    }
    conn.close()

    stats = run_revert_c1_1(db_path=db, apply=True)
    assert stats["applied"] is True
    assert stats["reverted"] == 4
    assert stats["reverted_by_category"] == {key: 1 for key in REVERT_CATEGORIES}
    assert stats["audit_rows"] == 4
    assert stats["pending_after"] == 0

    conn = _connect(db)
    # A: person-name restored
    assert conn.execute(
        "SELECT target FROM translations WHERE id = ?", (ids["name_ram"],)
    ).fetchone()[0] == "Jerahmeel, Ram, leh Khelubai ahi uh hi."
    # B: EN-headword cell restored; non-headword ZO cell untouched
    assert json.loads(conn.execute(
        "SELECT translations FROM dictionary_en_zo WHERE id = ?",
        (ids["en_headword"],),
    ).fetchone()[0]) == ["pieces", "ram"]
    assert json.loads(conn.execute(
        "SELECT translations FROM dictionary_en_zo WHERE id = ?",
        (ids["en_headword_keep"],),
    ).fetchone()[0]) == ["nasep"]
    # C: usage name cell restored
    assert conn.execute(
        "SELECT word FROM word_usage WHERE id = ?", (ids["usage_ram"],)
    ).fetchone()[0] == "ram"
    # D: teaching contrast restored
    assert conn.execute(
        "SELECT zolai_example FROM zolai_grammar_patterns WHERE id = ?",
        (ids["grammar_meta"],),
    ).fetchone()[0] == "Uses `gam` (not `ram`)"
    # Out-of-scope legit correction keeps its cleaned value
    assert conn.execute(
        "SELECT zolai FROM training_exercises WHERE id = ?", (ids["keep_example"],)
    ).fetchone()[0] == "a hihleh om"

    # New audit rows carry the revert reason; originals are never deleted.
    audit = _audit_rows(conn)
    assert len([r for r in audit if r["reason"] == REVERT_REASON]) == 4
    assert len([r for r in audit if r["reason"] == REASON]) == 6
    revert_rows = [r for r in audit if r["reason"] == REVERT_REASON]
    ram_row = next(r for r in revert_rows if r["field"] == "target")
    assert ram_row["old_value"] == "Jerahmeel, Gam, leh Khelubai ahi uh hi."
    assert ram_row["new_value"] == "Jerahmeel, Ram, leh Khelubai ahi uh hi."

    # Row counts unchanged (revert only ever UPDATEs).
    for table, before in rows_before.items():
        after = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        assert after == before, table
    conn.close()

    # Idempotent: a second apply finds nothing pending and writes nothing.
    again = run_revert_c1_1(db_path=db, apply=True)
    assert again["status"]["pending"] == 0
    assert again["reverted"] == 0
    assert again["audit_rows"] == 0
    assert again["audit_log_total_after"] == stats["audit_log_total_after"]


def test_cli_revert_c1_dry_run_json(runner: CliRunner, db: Path) -> None:
    _seed_c1_1(db)
    result = _invoke(runner, "corpus", "revert-c1", "--db", str(db), "--json")
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.clean)
    assert payload["applied"] is False
    assert payload["status"]["pending"] == 4
    assert payload["reverted"] == 0
    conn = _connect(db)
    assert len([r for r in _audit_rows(conn) if r["reason"] == REVERT_REASON]) == 0
    conn.close()
