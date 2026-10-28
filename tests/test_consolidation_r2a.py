"""Fixture suite for the R2-A consolidation runner (plan §5).

Synthetic tmp_path sqlite databases ONLY — no live DB, no network, no LLM.
Covers: D13 ref normalization (incl. NAH + empty), en→my split, articles
3-population namespace + protected triple + content-dup guard, exact-2x
collapse with a differing-column negative case, import_log rebuild
(archived/alias/lost/census), archive-validated drops, phase-2 empty drops,
index reclaim with keep-guard, and idempotent second-run no-ops.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "maintenance"))

import consolidation_r2a as runner  # noqa: E402
from r2a_lib import (  # noqa: E402
    ARCHIVED_STATUS,
    CENSUS_STATUS,
    R2A_BATCH_STATUS,
    R2AStop,
    articles_content_hash,
    backfill_content_hash,
    data_columns,
    prefix_covered,
)

# ── fixture builders ─────────────────────────────────────────────────────────


def _mk_import_log(con: sqlite3.Connection, rows: list[tuple]) -> None:
    con.execute(
        "CREATE TABLE import_log (id INTEGER PRIMARY KEY, batch_id TEXT, source_file TEXT,"
        " table_name TEXT, rows_imported INTEGER, sha256 TEXT, imported_at TEXT, version INTEGER,"
        " status TEXT, error_message TEXT)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO import_log (batch_id, source_file, table_name, rows_imported, imported_at,"
            " version, status, error_message) VALUES (?,?,?,?,?,1,?,?)",
            r,
        )


def _mk_provenance(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE provenance (id INTEGER PRIMARY KEY, filename TEXT, size_bytes INTEGER,"
        " sha256 TEXT, row_count INTEGER, source TEXT, generator_script TEXT, version TEXT,"
        " status TEXT, updated_at TEXT, change_log TEXT)"
    )
    con.execute(
        "INSERT INTO provenance (filename, size_bytes, sha256, row_count, source, generator_script,"
        " version, status, updated_at, change_log) VALUES ('seed',0,'x',1,'s','g','1','applied','t','c')"
    )


def mk_d13_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE bible_verses (ref TEXT PRIMARY KEY)")
    for ref in ("GEN 1:1", "NAM 1:1", "MAT 5:3", "1JO 1:1"):
        con.execute("INSERT INTO bible_verses VALUES (?)", (ref,))
    con.execute(
        "CREATE TABLE translations_import (zolai TEXT, english TEXT, dialect TEXT, source TEXT,"
        " reference TEXT, category TEXT, import_batch_id TEXT, source_file TEXT, version INTEGER,"
        " imported_at TEXT)"
    )
    refs = ["GEN.1:1", "NAH.1:1", "MAT.5:3", "1JO.1:1", ""]  # +1 empty kept; live format = dot after book only
    for i, ref in enumerate(refs):
        con.execute(
            "INSERT INTO translations_import (zolai, english, reference, version) VALUES (?,?,?,1)",
            (f"z{i}", f"e{i}", ref),
        )
    _mk_import_log(con, [("b1", "f", "translations_import", 5, "t", "completed", None)])
    _mk_provenance(con)
    con.commit()
    con.close()


def mk_split_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE translations (id INTEGER PRIMARY KEY, source TEXT, target TEXT,"
        " direction TEXT, reference TEXT, confidence REAL, myanmar TEXT, version INTEGER,"
        " created_at TEXT, updated_at TEXT, content_hash TEXT)"
    )
    insert = "INSERT INTO translations (source, target, direction, reference) VALUES (?,?,?,?)"
    for i in range(3):
        con.execute(insert, (f"s{i}", f"t{i}", "en_to_zo", f"GEN {i + 1}:1"))
    for i in range(2):
        con.execute(insert, (f"s{i}", f"t{i}", "zo_to_en", f"GEN {i + 1}:1"))
    for i in range(4):
        con.execute(insert, (f"n{i}", f"m{i}", "en→my", f"news:chuuhte{i}"))
    _mk_import_log(con, [])
    _mk_provenance(con)
    con.commit()
    con.close()


def mk_articles_db(path: Path, *, with_dup: bool = True, protected_dup: bool = False) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, content TEXT, excerpt TEXT,"
        " categories TEXT, date TEXT, link TEXT, language TEXT, import_batch_id TEXT,"
        " source_file TEXT, version INTEGER, created_at TEXT, updated_at TEXT, content_hash TEXT)"
    )
    rows = [
        # news rows (NULL hash)
        (1, "news-a", "body-a", "zomidaily", "zolai", None),
        (2, "news-b", "body-b", "zomidaily", "zolai", None),
        # lessons (simbu, link NOT NULL)
        (3, "zolai-simbu-tan-1", "lesson-1", None, "zolai_simbu", "http://x/1"),
        (4, "zolai-simbu-tan-2", "lesson-2", None, "zolai_simbu", "http://x/2"),
        # protected legacy
        (5, "legacy-a", "old-a", None, "zolai", None),
        (6, "legacy-b", "old-b", None, "zolai", None),
    ]
    for rid, title, content, sf, lang, link in rows:
        con.execute(
            "INSERT INTO articles (id, title, content, language, link, source_file) VALUES (?,?,?,?,?,?)",
            (rid, title, content, lang, link, sf),
        )
    if with_dup:
        if protected_dup:
            # dup pair among protected legacy rows
            con.execute("UPDATE articles SET content='same' WHERE id IN (5,6)")
        else:
            con.execute(
                "INSERT INTO articles (id, title, content, language, source_file) VALUES"
                " (7,'dup-a','dupbody','zolai','zomidaily'),(8,'dup-b','dupbody','zolai','zomidaily')"
            )
    _mk_import_log(con, [])
    _mk_provenance(con)
    con.commit()
    con.close()


def mk_collapse_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE word_alignments (id INTEGER PRIMARY KEY, ref TEXT, zolai_word TEXT,"
        " english_word TEXT, position INTEGER, myanmar TEXT, version INTEGER, created_at TEXT,"
        " updated_at TEXT, content_hash TEXT)"
    )
    # exact pair (identical data cols, differing content_hash only) -> collapsible
    con.execute("INSERT INTO word_alignments VALUES (1,'GEN 1:1','adam','Adam',0,NULL,1,'t','t','aa')")
    con.execute("INSERT INTO word_alignments VALUES (2,'GEN 1:1','adam','Adam',0,NULL,1,'t','t','bb')")
    # negative case: SAME key but differing DATA column (myanmar) -> identity gate must skip
    con.execute("INSERT INTO word_alignments VALUES (3,'GEN 1:2','seth','Seth',0,'က',1,'t','t','cc')")
    con.execute("INSERT INTO word_alignments VALUES (4,'GEN 1:2','seth','Seth',0,'ဗ',1,'t','t','dd')")
    # zba dialect collision: same (book_code,chapter,verse), differing book_name -> skip
    con.execute(
        "CREATE TABLE zolai_bible_analysis (id INTEGER PRIMARY KEY, book_code TEXT, book_name TEXT,"
        " chapter INTEGER, verse INTEGER, zolai TEXT, english TEXT, myanmar_text TEXT,"
        " verse_hash TEXT, morpheme_analysis TEXT, grammar_tags TEXT, tone_analysis TEXT,"
        " compounds_found TEXT, rare_words TEXT, source_version TEXT, created_at TEXT,"
        " import_batch_id TEXT, source_file TEXT, version INTEGER, imported_at TEXT)"
    )
    zba_insert = (
        "INSERT INTO zolai_bible_analysis VALUES "
        "(?,'1 C',?,1,1,?,?,'m','h',NULL,NULL,NULL,'[]',NULL,'T','t','b',NULL,1,'t')"
    )
    con.execute(zba_insert, (1, "1 Chronicles", "z1", "e1"))
    con.execute(zba_insert, (2, "1 Corinthians", "z2", "e2"))
    _mk_import_log(con, [])
    _mk_provenance(con)
    con.commit()
    con.close()


def mk_import_tables_db(path: Path) -> None:
    con = sqlite3.connect(path)
    for t in ("bible_verses_import", "translations_import", "vocab_import"):
        con.execute(f"CREATE TABLE {t} (a TEXT, b INTEGER)")
        con.execute(f"INSERT INTO {t} VALUES ('x',1),('y',2)")
    con.execute("CREATE TABLE bible_verses (ref TEXT)")
    con.execute("INSERT INTO bible_verses VALUES ('GEN 1:1')")
    # populated table with no import_log entry -> census scope
    con.execute("CREATE TABLE agents (name TEXT)")
    con.execute("INSERT INTO agents VALUES ('a'),('b')")
    _mk_import_log(
        con,
        [
            ("b1", "f1", "bible_verses_import", 2, "t", "completed", None),
            ("b2", "f2", "vocab", 0, "t", "failed", "Broken pipe"),
            ("b3", "f3", "dictionary_my", 0, "t", "failed", "Broken pipe"),
            ("b4", "f4", "bible_verses", 1, "t", "completed", None),
        ],
    )
    _mk_provenance(con)
    con.commit()
    con.close()


def mk_phase2_db(path: Path) -> None:
    con = sqlite3.connect(path)
    for t in runner.PHASE2_EMPTY_DROPS:
        con.execute(f"CREATE TABLE {t} (a TEXT)")
    con.execute("CREATE TABLE ngram (a TEXT)")
    con.execute("INSERT INTO ngram VALUES ('keep-me')")
    _mk_import_log(con, [])
    _mk_provenance(con)
    con.commit()
    con.close()


def mk_index_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE translations (source TEXT, target TEXT)")
    con.execute("CREATE UNIQUE INDEX uniq_trans_v2_source_target ON translations(source,target)")
    con.execute("CREATE INDEX idx_translations_source ON translations(source)")
    con.execute("CREATE INDEX ix_trans_source ON translations(source)")
    _mk_import_log(con, [])
    _mk_provenance(con)
    con.commit()
    con.close()


# ── paths helper ─────────────────────────────────────────────────────────────


def paths_for(db: Path, expected: dict | None = None) -> runner.Paths:
    d = db.parent
    return runner.Paths(
        db=db,
        archive_db=d / "archive.db",
        manifest=d / "archive.manifest.json",
        skiplist_zba=d / "skiplist-zba.json",
        final_counts=d / "final-counts.txt",
        backup_dir=d / "backups",
        use_gates=False,
        expected=expected or _scale_expected(db),
    )


def _scale_expected(db: Path) -> dict:
    """Fixture-scale plan expectations derived from the synthetic DB itself.

    The live plan §0 numbers only apply to the production DB; fixtures exercise
    the same assert plumbing at their own scale.
    """
    import copy

    from r2a_lib import PLAN_EXPECTED

    exp = copy.deepcopy(PLAN_EXPECTED)
    con = sqlite3.connect(db)
    try:
        if runner.table_exists(con, "translations_import"):
            try:
                total = con.execute("SELECT COUNT(*) FROM translations_import").fetchone()[0]
                dot = con.execute("SELECT COUNT(*) FROM translations_import WHERE reference LIKE '%.%'").fetchone()[0]
                nah = con.execute("SELECT COUNT(*) FROM translations_import WHERE reference LIKE 'NAH.%'").fetchone()[0]
                empty = con.execute("SELECT COUNT(*) FROM translations_import WHERE reference=''").fetchone()[0]
                exp["d13"] = {"translations_import": total, "dot_refs": dot, "nah_rows": nah, "empty_refs": empty}
            except sqlite3.OperationalError:
                pass  # fixture schema lacks reference column — d13 not exercised
        if runner.table_exists(con, "translations"):
            try:
                total = con.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
                enmy = con.execute("SELECT COUNT(*) FROM translations WHERE direction='en→my'").fetchone()[0]
                post = total - enmy
                exp["split"] = {
                    "translations": total,
                    "en_to_my": enmy,
                    "news": enmy,
                    "parallel": 0,
                    "post": post,
                }
            except sqlite3.OperationalError:
                pass  # fixture schema lacks direction column — split not exercised
        if runner.table_exists(con, "articles"):
            total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            nullh = con.execute("SELECT COUNT(*) FROM articles WHERE content_hash IS NULL").fetchone()[0]
            news = con.execute("SELECT COUNT(*) FROM articles WHERE source_file='zomidaily'").fetchone()[0]
            lessons = con.execute("SELECT COUNT(*) FROM articles WHERE language='zolai_simbu'").fetchone()[0]
            legacy = con.execute(
                "SELECT COUNT(*) FROM articles WHERE source_file IS NULL AND language='zolai'"
            ).fetchone()[0]
            dup_groups = con.execute(
                "SELECT COUNT(*) FROM (SELECT content FROM articles WHERE content IS NOT NULL AND content<>''"
                " GROUP BY content HAVING COUNT(*)>1)"
            ).fetchone()[0]
            exp["articles"] = {
                "total": total,
                "null_hash": nullh,
                "news": news,
                "lessons": lessons,
                "legacy": legacy,
                "dup_groups": dup_groups,
                "dup_extra": dup_groups,
                "post": total - dup_groups,
            }
        if runner.table_exists(con, "word_alignments"):
            wa = con.execute("SELECT COUNT(*) FROM word_alignments").fetchone()[0]
            zba = (
                con.execute("SELECT COUNT(*) FROM zolai_bible_analysis").fetchone()[0]
                if runner.table_exists(con, "zolai_bible_analysis")
                else 0
            )
            # identity-aware removable count: only groups whose data cols are byte-identical
            dcols = data_columns(con, "word_alignments")
            ident = ", ".join(dcols)
            removable = 0
            for key in con.execute(
                "SELECT ref,zolai_word,english_word,position FROM word_alignments"
                " GROUP BY ref,zolai_word,english_word,position HAVING COUNT(*)>1"
            ):
                cond = " AND ".join(f"{c}=?" for c in ("ref", "zolai_word", "english_word", "position"))
                distinct = con.execute(
                    f"SELECT COUNT(*) FROM (SELECT {ident} FROM word_alignments WHERE {cond} GROUP BY {ident})",
                    key,
                ).fetchone()[0]
                if distinct == 1:
                    n = con.execute(f"SELECT COUNT(*) FROM word_alignments WHERE {cond}", key).fetchone()[0]
                    removable += n - 1
            exp["collapse"] = {
                "wa": wa,
                "wa_groups": removable,
                "wa_post": wa - removable,
                "zba": zba,
                "zba_groups": 0,
                "zba_post_if_merged": zba,
            }
        if runner.table_exists(con, "import_log"):
            total = con.execute("SELECT COUNT(*) FROM import_log").fetchone()[0]
            completed = con.execute("SELECT COUNT(*) FROM import_log WHERE status='completed'").fetchone()[0]
            failed = con.execute("SELECT COUNT(*) FROM import_log WHERE status='failed'").fetchone()[0]
            exp["import_log"] = {"total": total, "completed": completed, "failed": failed, "aliases": 0, "unlogged": 0}
    finally:
        con.close()
    return exp


def run_item(db: Path, item: str, mode: str) -> runner.ItemReport:
    p = paths_for(db)
    con = runner.connect_ro(db) if mode != "apply" else runner.connect_rw(db)
    try:
        if mode == "apply":
            from r2a_lib import write_batch

            if item == "archive":
                return runner.ITEMS[item](con, "apply", p)  # self-managed txn
            with write_batch(con):
                return runner.ITEMS[item](con, "apply", p)
        return runner.ITEMS[item](con, mode, p)
    finally:
        con.close()


def apply_item(con: sqlite3.Connection, item: str, p: runner.Paths) -> runner.ItemReport:
    """Apply one item on an existing rw connection (archive self-managed)."""
    from r2a_lib import write_batch

    if item == "archive":
        return runner.ITEMS[item](con, "apply", p)
    with write_batch(con):
        return runner.ITEMS[item](con, "apply", p)


# ── D13 ──────────────────────────────────────────────────────────────────────


def test_d13_dryrun_projects_and_flags_empty(tmp_path):
    db = tmp_path / "z.db"
    mk_d13_db(db)
    rep = run_item(db, "d13", "dry-run")
    assert not rep.errors
    assert rep.before["dot_refs"] == 4
    assert rep.before["nah_refs"] == 1
    assert rep.before["empty_refs"] == 1


def test_d13_apply_normalizes_and_keeps_empty(tmp_path):
    db = tmp_path / "z.db"
    mk_d13_db(db)
    rep = run_item(db, "d13", "apply")
    assert rep.applied["nah_fixed"] == 1
    con = sqlite3.connect(db)
    refs = [r[0] for r in con.execute("SELECT reference FROM translations_import ORDER BY rowid")]
    assert refs == ["GEN 1:1", "NAM 1:1", "MAT 5:3", "1JO 1:1", ""]
    # bible_verses untouched
    assert con.execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0] == 4
    # import_log batch entry with r2a status (invisible to M8a)
    assert con.execute("SELECT COUNT(*) FROM import_log WHERE status=?", (R2A_BATCH_STATUS,)).fetchone()[0] >= 1
    assert con.execute("SELECT COUNT(*) FROM provenance WHERE row_count=0").fetchone()[0] == 0
    con.close()


def test_d13_idempotent_second_run_noops(tmp_path):
    db = tmp_path / "z.db"
    mk_d13_db(db)
    run_item(db, "d13", "apply")
    rep2 = run_item(db, "d13", "apply")
    assert rep2.skipped  # already normalized -> skip
    assert not rep2.errors


def test_d13_unresolved_ref_blocks(tmp_path):
    db = tmp_path / "z.db"
    mk_d13_db(db)
    con = sqlite3.connect(db)
    con.execute("UPDATE translations_import SET reference='XXX.9.9' WHERE rowid=1")
    con.commit()
    con.close()
    with pytest.raises(R2AStop):
        run_item(db, "d13", "apply")


# ── split ────────────────────────────────────────────────────────────────────


def test_split_apply_moves_en_my(tmp_path):
    db = tmp_path / "z.db"
    mk_split_db(db)
    rep = run_item(db, "split", "apply")
    assert not rep.errors
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM translations_news").fetchone()[0] == 4
    dirs = {r[0] for r in con.execute("SELECT DISTINCT direction FROM translations")}
    assert dirs == {"en_to_zo", "zo_to_en"}
    con.close()


def test_split_idempotent(tmp_path):
    db = tmp_path / "z.db"
    mk_split_db(db)
    run_item(db, "split", "apply")
    rep2 = run_item(db, "split", "apply")
    assert rep2.skipped
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM translations_news").fetchone()[0] == 4
    con.close()


# ── articles ─────────────────────────────────────────────────────────────────


def test_articles_apply_backfills_and_namespaces(tmp_path):
    db = tmp_path / "z.db"
    mk_articles_db(db)
    rep = run_item(db, "articles", "apply")
    assert not rep.errors
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM articles WHERE content_hash IS NULL").fetchone()[0] == 0
    ns = dict(con.execute("SELECT namespace, COUNT(*) FROM articles GROUP BY namespace"))
    # dup pair (7,8) both zomidaily: keep MIN(id)=7 -> news = 1,2,7 = 3
    assert ns["news"] == 3
    assert ns["lessons"] == 2
    assert ns["legacy"] == 2
    assert sum(ns.values()) == 7
    # hash format is double-hex of sha256hex
    h = con.execute("SELECT content_hash FROM articles WHERE id=1").fetchone()[0]
    assert h == articles_content_hash("body-a")
    con.close()


def test_articles_protected_triple_never_drops(tmp_path):
    db = tmp_path / "z.db"
    mk_articles_db(db, protected_dup=True)
    rep = run_item(db, "articles", "apply")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 6  # nothing dropped
    assert any("protected" in s for s in rep.skipped)
    legacy = con.execute("SELECT COUNT(*) FROM articles WHERE source_file IS NULL AND language='zolai'").fetchone()[0]
    assert legacy == 2
    con.close()


def test_articles_dryrun_flags_dup_without_mutation(tmp_path):
    db = tmp_path / "z.db"
    mk_articles_db(db)
    rep = run_item(db, "articles", "dry-run")
    assert rep.after["dup_ids"] == [7, 8]
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 8
    assert con.execute("SELECT COUNT(*) FROM articles WHERE content_hash IS NOT NULL").fetchone()[0] == 0
    con.close()


def test_backfill_helper_idempotent(tmp_path):
    db = tmp_path / "z.db"
    mk_articles_db(db, with_dup=False)
    con = sqlite3.connect(db)
    n1 = backfill_content_hash(con, "articles")
    n2 = backfill_content_hash(con, "articles")
    assert n1 == 6 and n2 == 0
    con.close()


# ── collapse ─────────────────────────────────────────────────────────────────


def test_collapse_exact_pair_and_skip_negative(tmp_path):
    db = tmp_path / "z.db"
    mk_collapse_db(db)
    rep = run_item(db, "collapse", "apply")
    con = sqlite3.connect(db)
    # exact pair collapsed (kept MIN(id)=1), negative case untouched
    ids = [r[0] for r in con.execute("SELECT id FROM word_alignments ORDER BY id")]
    assert ids == [1, 3, 4]
    # zba dialect collision skipped entirely
    assert con.execute("SELECT COUNT(*) FROM zolai_bible_analysis").fetchone()[0] == 2
    con.close()
    assert rep.applied["word_alignments_removed"] == 1
    # skip-list filed for zba
    p = paths_for(db)
    assert p.skiplist_zba.exists()


def test_collapse_data_columns_exclude_non_data(tmp_path):
    db = tmp_path / "z.db"
    mk_collapse_db(db)
    con = sqlite3.connect(db)
    cols = data_columns(con, "word_alignments")
    assert "content_hash" not in cols and "version" not in cols and "id" not in cols
    assert "english_word" in cols and "myanmar" in cols
    con.close()


# ── archive + drops ──────────────────────────────────────────────────────────


def test_archive_apply_builds_verified_manifest(tmp_path):
    db = tmp_path / "z.db"
    mk_import_tables_db(db)
    p = paths_for(db)
    con = runner.connect_rw(db)
    rep = apply_item(con, "archive", p)
    con.close()
    assert not rep.errors
    arc = sqlite3.connect(p.archive_db)
    assert arc.execute("SELECT COUNT(*) FROM bible_verses_import").fetchone()[0] == 2
    arc.close()
    import json

    man = json.loads(p.manifest.read_text())
    for t in ("bible_verses_import", "translations_import", "vocab_import"):
        assert man[t]["source_count"] == man[t]["archive_count"] == 2


def test_drop_imports_blocked_without_archive(tmp_path):
    db = tmp_path / "z.db"
    mk_import_tables_db(db)
    with pytest.raises(R2AStop):
        run_item(db, "drop_imports", "apply")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='bible_verses_import'").fetchone()[0] == 1
    con.close()


def test_drop_imports_after_archive(tmp_path):
    db = tmp_path / "z.db"
    mk_import_tables_db(db)
    p = paths_for(db)
    con = runner.connect_rw(db)
    apply_item(con, "archive", p)
    rep = apply_item(con, "drop_imports", p)
    con.close()
    assert not rep.errors
    c = sqlite3.connect(db)
    residual = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%_import'").fetchone()[0]
    assert residual == 0
    # canonical untouched
    assert c.execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0] == 1
    c.close()


def test_drop_blocked_when_archive_count_mismatches(tmp_path):
    db = tmp_path / "z.db"
    mk_import_tables_db(db)
    p = paths_for(db)
    con = runner.connect_rw(db)
    apply_item(con, "archive", p)
    con.close()
    # tamper: add a row after archive -> count mismatch -> drop must refuse
    con = sqlite3.connect(db)
    con.execute("INSERT INTO vocab_import VALUES ('z', 9)")
    con.commit()
    con.close()
    with pytest.raises(R2AStop):
        run_item(db, "drop_imports", "apply")


# ── import_log rebuild ───────────────────────────────────────────────────────


def test_import_log_rebuild_archives_aliases_census(tmp_path):
    db = tmp_path / "z.db"
    mk_import_tables_db(db)
    p = paths_for(db)
    con = runner.connect_rw(db)
    apply_item(con, "archive", p)
    apply_item(con, "drop_imports", p)
    rep = apply_item(con, "import_log", p)
    con.close()
    assert not rep.errors
    c = sqlite3.connect(db)
    # (a) completed for dropped table -> archived with note
    archived = c.execute(
        "SELECT COUNT(*) FROM import_log WHERE table_name='bible_verses_import'"
        " AND status=? AND error_message LIKE 'R2-A%'",
        (ARCHIVED_STATUS,),
    ).fetchone()[0]
    assert archived == 1
    # (b) alias canonicalized
    assert c.execute("SELECT COUNT(*) FROM import_log WHERE table_name='vocab'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM import_log WHERE table_name='vocab_import'").fetchone()[0] >= 1
    # (c) surviving completed recounted to live count
    recounted = c.execute(
        "SELECT rows_imported FROM import_log WHERE table_name='bible_verses' AND status='completed'"
    ).fetchone()[0]
    assert recounted == 1
    # (d) census entries exist with status census + uuid batch ids
    census = c.execute("SELECT COUNT(*) FROM import_log WHERE status=?", (CENSUS_STATUS,)).fetchone()[0]
    assert census >= 1
    # (e) provenance row_count > 0 only
    assert c.execute("SELECT COUNT(*) FROM provenance WHERE row_count=0").fetchone()[0] == 0
    # failed entry untouched (M8d keeps error_message)
    failed = c.execute(
        "SELECT COUNT(*) FROM import_log WHERE status='failed' AND error_message='Broken pipe'"
    ).fetchone()[0]
    assert failed == 2
    c.close()


def test_import_log_lost_status_when_claimed_but_empty(tmp_path):
    db = tmp_path / "z.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE empty_import (a TEXT)")  # table exists but 0 rows
    _mk_import_log(con, [("b1", "f", "empty_import", 500, "t", "completed", None)])
    _mk_provenance(con)
    con.commit()
    con.close()
    p = paths_for(db)
    c = runner.connect_rw(db)
    rep = apply_item(c, "import_log", p)
    c.close()
    assert rep.applied["lost"] == 1
    cc = sqlite3.connect(db)
    lost = cc.execute(
        "SELECT COUNT(*) FROM import_log WHERE status='lost' AND error_message LIKE '%claimed%'"
    ).fetchone()[0]
    assert lost == 1
    cc.close()


# ── phase2 + indexes ─────────────────────────────────────────────────────────


def test_phase2_drops_empty_and_keeps_set(tmp_path):
    db = tmp_path / "z.db"
    mk_phase2_db(db)
    rep = run_item(db, "phase2_drops", "apply")
    assert not rep.errors
    con = sqlite3.connect(db)
    for t in runner.PHASE2_EMPTY_DROPS:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (t,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM ngram").fetchone()[0] == 1
    con.close()


def test_phase2_refuses_nonempty(tmp_path):
    db = tmp_path / "z.db"
    mk_phase2_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO dictionary_enhanced VALUES ('x')")
    con.commit()
    con.close()
    with pytest.raises(R2AStop):
        run_item(db, "phase2_drops", "apply")


def test_indexes_drop_and_keep_survives(tmp_path):
    db = tmp_path / "z.db"
    mk_index_db(db)
    rep = run_item(db, "indexes", "apply")
    assert not rep.errors
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_translations_source" not in names
    assert "ix_trans_source" not in names
    assert "uniq_trans_v2_source_target" in names
    con.close()
    assert rep.applied


def test_prefix_covered_helper(tmp_path):
    db = tmp_path / "z.db"
    mk_index_db(db)
    con = sqlite3.connect(db)
    assert prefix_covered(con, "idx_translations_source", "uniq_trans_v2_source_target")
    assert prefix_covered(con, "ix_trans_source", "uniq_trans_v2_source_target")
    con.close()


def test_index_drop_refused_when_keep_missing(tmp_path):
    db = tmp_path / "z.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (a TEXT, b TEXT)")
    con.execute("CREATE INDEX idx_translations_source ON t(a)")  # keep-index absent
    _mk_import_log(con, [])
    _mk_provenance(con)
    con.commit()
    con.close()
    with pytest.raises(R2AStop):
        run_item(db, "indexes", "apply")


# ── end-to-end fixture pipeline (apply then verify then re-run) ─────────────


def test_full_pipeline_idempotent(tmp_path):
    db = tmp_path / "z.db"
    # combined fixture: d13 + split + articles + collapse + import tables
    mk_d13_db(db)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE translations (id INTEGER PRIMARY KEY, source TEXT, target TEXT,"
        " direction TEXT, reference TEXT, confidence REAL, myanmar TEXT, version INTEGER,"
        " created_at TEXT, updated_at TEXT, content_hash TEXT)"
    )
    con.execute("INSERT INTO translations (source,target,direction,reference) VALUES ('s','t','en→my','news:x')")
    con.execute("INSERT INTO translations (source,target,direction,reference) VALUES ('s2','t2','en_to_zo','GEN 1:1')")
    for t in ("vocab_import",):
        con.execute(f"CREATE TABLE {t} (a TEXT)")
        con.execute(f"INSERT INTO {t} VALUES ('v')")
    con.commit()
    con.close()
    p = paths_for(db)
    con = runner.connect_rw(db)
    for item in ("d13", "split", "archive", "drop_imports", "import_log"):
        apply_item(con, item, p)
    con.close()
    # verify pass must be clean
    con = runner.connect_ro(db)
    for item in ("d13", "split", "import_log"):
        rep = runner.ITEMS[item](con, "verify", p)
        assert not rep.errors, rep.errors
    con.close()
    # second apply is a no-op (skips, no errors)
    con = runner.connect_rw(db)
    rep = apply_item(con, "import_log", p)
    con.close()
    assert rep.skipped
    assert not rep.errors


def test_gates_block_apply_without_backup(tmp_path):
    db = tmp_path / "z.db"
    mk_d13_db(db)
    p = paths_for(db)
    p.use_gates = True  # fixture has no backups dir -> gate must abort with rc=2
    rc = runner.run(db, "apply", ["d13"], p)
    assert rc == 2
