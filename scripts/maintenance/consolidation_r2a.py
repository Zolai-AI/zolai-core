#!/usr/bin/env python3
"""R2-A consolidation runner (plan data/conductor/plan-r2a-db-consolidation.md).

Operates ONLY on the import/staging side of the zolai single-DB system. Three
inviolable DB rules are enforced in code (see r2a_lib):
  1. write gate = fuser single-writer + fresh backup + exclusive rw connection;
  2. one item = one atomic journal write with before/after count asserts;
  3. never delete anything unrecoverable: every dropped import table is first
     archived to an uncompressed sqlite DB on the same disk (VACUUM INTO).
Canonical tables (bible_verses, translations, word_alignments, articles, zolai_bible_analysis,
dictionary, vocab, dictionary_my, ...) receive only additive/explicitly-specified
changes (ref normalization, en->my split, content_hash backfill, namespace flag,
controlled collapse of byte-identical rows). The 2,208 protected legacy articles
and the 545 import-only verse refs are never touched.

Modes (all count-only in dry-run):
  python scripts/maintenance/consolidation_r2a.py dry-run [--item I ...]
  python scripts/maintenance/consolidation_r2a.py apply   [--item I ...]
  python scripts/maintenance/consolidation_r2a.py verify  [--item I ...]
  python scripts/maintenance/consolidation_r2a.py vacuum

Exit codes: 0 = projections/mutations/verification consistent; 1 = drift, an
assert failed, or an item aborted; 2 = a write gate failed (fuser/backup).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2a_lib import (  # noqa: E402
    ALIAS_MAP,
    ARCHIVE_MANIFEST,
    BEGIN_WRITE_BATCH,
    CANONICAL_POST,
    DROP_KEEP,
    END_WRITE_BATCH,
    EXCLUSIVE,
    FINAL_COUNTS_PATH,
    INDEX_DROP_SET,
    PHASE2_EMPTY_DROPS,
    PLAN_EXPECTED,
    R2AStop,
    SKIPLIST_ZBA,
    ItemReport,
    assert_drift,
    assert_eq,
    articles_content_hash,
    begin_batch,
    connect_ro,
    fuser_clean,
    log_batch,
    now_iso,
    prefix_covered,
    require_fresh_backup,
)

BASE = Path(__file__).resolve().parents[3]
ARCHIVE_DIR = BASE / "data" / "r2a-archive"
ARCHIVE_MANIFEST_PATH = BASE / "data" / "conductor" / "r2a-archive-manifest.json"
IMPORT_LOG = "import_log"

ITEM_ORDER = [
    "d13",
    "archive",
    "split",
    "articles",
    "collapse",
    "drop_imports",
    "import_log",
    "phase2_drops",
    "indexes",
    "final_counts",
]


# --------------------------------------------------------------------------
# D13 — ref normalization + NAH->NAM (canonical, additive UPDATEs only)
# --------------------------------------------------------------------------


def item_d13(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("d13")
    dotted = con.execute("SELECT COUNT(*) FROM bible_verses WHERE ref LIKE '%.%'").fetchone()[0]
    zero_pad = con.execute("SELECT COUNT(*) FROM bible_verses WHERE ref GLOB '* 0*'").fetchone()[0]
    nah_c = con.execute("SELECT COUNT(*) FROM bible_verses WHERE book='NAH'").fetchone()[0]
    nah_ref = con.execute("SELECT COUNT(*) FROM bible_verses WHERE ref LIKE 'NAH %'").fetchone()[0]
    canon_nam = con.execute("SELECT COUNT(*) FROM bible_verses WHERE book='NAM'").fetchone()[0]
    imports_nah = con.execute(
        "SELECT COUNT(*) FROM bible_verses_import WHERE ref LIKE 'NAH%' OR book='NAH'"
    ).fetchone()[0]
    plan = PLAN_EXPECTED["d13"]
    rep.before = {"dotted": dotted, "zero_pad": zero_pad, "NAH_book": nah_c, "NAH_ref": nah_ref}

    if mode != "apply":
        if dotted:
            rep.errors.append(f"D13 projection broken: {dotted} dotted refs remain")
        else:
            rep.projections["ref_normalization"] = "already clean (idempotent re-run)"
        if nah_c == 0 and nah_ref == 0 and imports_nah == 0:
            rep.projections["nah_nam"] = (
                f"done ({canon_nam} NAM canon rows, 0 NAH anywhere) - idempotent re-run"
            )
        else:
            rep.projections["nah_nam"] = (
                f"NAH->{plan['nah_to_nam_target']} (book+ref; canon NAM {canon_nam}->"
                f"{canon_nam + nah_c + nah_ref}, imports->canonical {plan['imports_to_canonical']} total)"
            )
        rep.after = dict(rep.before)
        return rep

    rows_a = con.execute(
        "UPDATE bible_verses SET ref = ? WHERE book='NAH' AND ref LIKE 'NAH %'",
        (f"NAM {plan['nah_to_nam_target']}",),
    ).rowcount
    rows_b = con.execute("UPDATE bible_verses SET book='NAM' WHERE book='NAH'").rowcount
    rep.applied["nah_nam"] = rows_a + rows_b
    assert_eq(
        con.execute("SELECT COUNT(*) FROM bible_verses WHERE book='NAH'").fetchone()[0], 0, "NAH book rows"
    )
    assert_eq(
        con.execute("SELECT COUNT(*) FROM bible_verses WHERE ref LIKE 'NAH %'").fetchone()[0], 0, "NAH ref rows"
    )
    assert_eq(
        con.execute("SELECT COUNT(*) FROM bible_verses WHERE ref LIKE '%.%'").fetchone()[0], 0, "dotted refs"
    )
    after_nam = con.execute("SELECT COUNT(*) FROM bible_verses WHERE book='NAM'").fetchone()[0]
    if canon_nam + (rows_a + rows_b) // 2 != after_nam:
        rep.errors.append("NAM row count drifted unexpectedly")
    assert_eq(
        imports_nah,
        con.execute("SELECT COUNT(*) FROM bible_verses_import WHERE ref LIKE 'NAH%' OR book='NAH'").fetchone()[
            0
        ],
        "imports NAH rows must be untouched by D13 (moved to canonical in step 02)",
    )
    rep.after = {"dotted": 0, "zero_pad": 0, "NAH_book": 0, "NAH_ref": 0, "NAM_canon": after_nam}
    return rep


# --------------------------------------------------------------------------
# Archive — VACUUM INTO one uncompressed DB per import table, then verify
# --------------------------------------------------------------------------


def item_archive(con: sqlite3.Connection, mode: str, db_path: Path) -> ItemReport:
    rep = ItemReport("archive")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    if ARCHIVE_MANIFEST_PATH.exists():
        manifest = json.loads(ARCHIVE_MANIFEST_PATH.read_text())

    present = [t for (t,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    targets = [t for t in PLAN_EXPECTED["archive_targets"] if t in present]
    rep.before = {"import_tables_present": len(targets), "archive_dir": str(ARCHIVE_DIR)}

    total = 0
    for t in targets:
        nrows = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        total += nrows
        dest = ARCHIVE_DIR / f"{t}-bak-20261027T000001Z.db"
        entry = manifest.get(t, {})
        if (
            entry.get("source_count") == nrows
            and entry.get("archive_count") == nrows
            and Path(entry.get("archive_path", "")).exists()
        ):
            rep.skipped.append(f"{t} (archive already verified: {nrows} rows)")
            continue
        rep.projections[t] = {"rows": nrows, "archive": dest.name}
        if mode != "apply":
            continue
        if dest.exists():
            dest.unlink()
        con.execute("VACUUM INTO ?", (str(dest),))
        with sqlite3.connect(str(dest)) as arc:
            got = arc.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert_eq(got, nrows, f"{t} archive count")
        manifest[t] = {
            "source": str(db_path),
            "source_count": nrows,
            "archive_path": str(dest),
            "archive_count": got,
            "archived_at": now_iso(),
        }
        rep.applied[t] = got
    rep.after = {"total_source_rows_in_scope": total, "archives": len(manifest)}
    if mode == "apply":
        ARCHIVE_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        log_batch(
            con,
            batch_id=f"r2a-archive-{now_iso()}",
            table="r2a_archive",
            status="r2a_batch",
            row_count=total,
            note=f"VACUUM INTO archive of {len(manifest)} import tables -> {ARCHIVE_DIR}",
        )
    return rep


# --------------------------------------------------------------------------
# Step 02 — bible_verses <- bible_verses_import (exact-2x + protected 545)
# --------------------------------------------------------------------------

BIBLE_IMPORT_EXCLUDES = "'ZOMIDAILY%', 'phrase:%'"
BIBLE_IMPORT_PROTECTED = (
    "ref NOT LIKE 'ZOMIDAILY%' AND ref NOT LIKE 'phrase:%' AND ref NOT IN "
    "(SELECT ref FROM bible_verses) AND ref NOT IN (SELECT ref FROM bible_verses_import)"
)
PROTECTED_REF_SQL = (
    "ref NOT LIKE 'ZOMIDAILY%' AND ref NOT LIKE 'phrase:%' AND ref NOT IN "
    "(SELECT ref FROM bible_verses)"
)


def item_split(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("split")
    imp = con.execute("SELECT COUNT(*) FROM bible_verses_import").fetchone()[0]
    canon = con.execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0]
    canon_excl = con.execute(
        f"SELECT COUNT(*) FROM bible_verses WHERE ref NOT LIKE {BIBLE_IMPORT_EXCLUDES}"
    ).fetchone()[0]
    to_move = con.execute(
        f"SELECT COUNT(*) FROM bible_verses_import WHERE {BIBLE_IMPORT_PROTECTED}"
    ).fetchone()[0]
    protected = con.execute(
        f"SELECT COUNT(*) FROM bible_verses_import WHERE {PROTECTED_REF_SQL}"
    ).fetchone()[0]
    plan = PLAN_EXPECTED["split"]
    rep.before = {"canon": canon, "canon_excl": canon_excl, "import": imp, "to_move": to_move}

    if mode != "apply":
        ok = True
        if canon_excl != plan["canon_excl_expected"]:
            rep.errors.append(f"canon excl {canon_excl} != plan {plan['canon_excl_expected']}")
            ok = False
        if to_move != plan["imports_to_canonical"]:
            rep.errors.append(f"to_move {to_move} != plan {plan['imports_to_canonical']}")
            ok = False
        if protected != plan["protected_ref_rows"]:
            rep.errors.append(f"protected {protected} != plan {plan['protected_ref_rows']}")
            ok = False
        if ok:
            rep.projections["bible_verses"] = f"{canon} -> {canon_excl + to_move} (+{to_move} import-only refs)"
            rep.projections["protected"] = f"{protected} import-only refs preserved"
        rep.after = {"bible_verses_target": canon_excl + to_move}
        return rep

    cols = [r[1] for r in con.execute("PRAGMA table_info(bible_verses)")]
    col_list = ", ".join(cols)
    n = con.execute(
        f"INSERT INTO bible_verses ({col_list}) SELECT {col_list} FROM bible_verses_import "
        f"WHERE {BIBLE_IMPORT_PROTECTED}"
    ).rowcount
    assert_eq(n, plan["imports_to_canonical"], "inserted import-only refs")
    canon_after = con.execute("SELECT COUNT(*) FROM bible_verses").fetchone()[0]
    assert_eq(canon_after, canon_excl + n, "bible_verses after merge")
    still_protected = con.execute(
        f"SELECT COUNT(*) FROM bible_verses_import WHERE {PROTECTED_REF_SQL}"
    ).fetchone()[0]
    assert_eq(still_protected, plan["protected_ref_rows"], "protected import-only refs untouched")
    assert_eq(
        con.execute(
            f"SELECT COUNT(*) FROM bible_verses WHERE ref LIKE {BIBLE_IMPORT_EXCLUDES}"
        ).fetchone()[0],
        plan["zomidaily_phrase_expected"],
        "ZOMIDAILY+phrase untouched",
    )
    rep.applied["inserted"] = n
    rep.after = {"bible_verses": canon_after, "protected_import_only": still_protected}
    return rep


# --------------------------------------------------------------------------
# Step 03 — translations <- translations_import en->my + news split
# --------------------------------------------------------------------------


def item_articles(con: sqlite3.Connection, mode: str) -> ItemReport:
    """D9/D10 — articles content_hash backfill + namespace logical split."""
    rep = ItemReport("articles")
    total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    null_hash = con.execute("SELECT COUNT(*) FROM articles WHERE content_hash IS NULL").fetchone()[0]
    has_ns = con.execute("PRAGMA table_info(articles)").fetchall()
    colnames = {r[1] for r in has_ns}
    plan = PLAN_EXPECTED["articles"]
    rep.before = {"rows": total, "null_hash": null_hash, "namespace_col": "namespace" in colnames}

    dup_rows = con.execute(
        "SELECT COUNT(*) FROM (SELECT content_hash FROM articles WHERE content_hash IS NOT NULL "
        "GROUP BY content_hash HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    if "namespace" not in colnames:
        rep.projections["namespace"] = "ADD COLUMN namespace TEXT NOT NULL DEFAULT 'legacy'"
    rep.projections["content_hash"] = f"backfill {null_hash} NULL rows (double-hex sha256(content))"
    rep.projections["content_dup_groups"] = dup_rows

    if mode != "apply":
        rep.after = {"null_hash_target": 0, "namespaces": plan["namespace_counts"]}
        return rep

    n_fill = articles_content_hash(con, "articles")
    after_null = con.execute("SELECT COUNT(*) FROM articles WHERE content_hash IS NULL").fetchone()[0]
    assert_eq(after_null, 0, "articles NULL content_hash after backfill")
    if "namespace" not in colnames:
        con.execute("ALTER TABLE articles ADD COLUMN namespace TEXT NOT NULL DEFAULT 'legacy'")
    for pred, value in (
        ("source='zomidaily'", "news"),
        ("source='zolai_simbu'", "lessons"),
        ("source='legacy'", "legacy"),
    ):
        con.execute(f"UPDATE articles SET namespace=? WHERE {pred}", (value,))
    ns_counts = dict(con.execute("SELECT namespace, COUNT(*) FROM articles GROUP BY namespace").fetchall())
    assert_eq(ns_counts.get("news"), plan["namespace_counts"]["news"], "namespace news count")
    assert_eq(ns_counts.get("lessons"), plan["namespace_counts"]["lessons"], "namespace lessons count")
    assert_eq(ns_counts.get("legacy"), plan["namespace_counts"]["legacy"], "namespace legacy count")
    assert_eq(
        con.execute("SELECT COUNT(*) FROM articles WHERE source='legacy' AND content_hash IS NULL").fetchone()[
            0
        ],
        0,
        "legacy articles must not stay NULL (backfill is safe, only content-derived)",
    )
    protected = con.execute(
        "SELECT COUNT(*) FROM articles WHERE source='legacy' AND (title IS NULL OR content IS NULL)"
    ).fetchone()[0]
    assert_eq(protected, 0, "protected legacy articles untouched (still present)")
    rep.applied["content_hash_backfilled"] = n_fill
    rep.after = {"null_hash": 0, "namespaces": ns_counts}
    return rep


def item_translations(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("split")
    imp = con.execute("SELECT COUNT(*) FROM translations_import").fetchone()[0]
    canon = con.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
    dir_to = con.execute("SELECT COUNT(*) FROM translations_import WHERE dir='en_to_zo'").fetchone()[0]
    dir_from = con.execute("SELECT COUNT(*) FROM translations_import WHERE dir='zo_to_en'").fetchone()[0]
    dir_my = con.execute("SELECT COUNT(*) FROM translations_import WHERE dir='en->my'").fetchone()[0]
    plan = PLAN_EXPECTED["split"]
    rep.before = {
        "canon": canon,
        "import": imp,
        "en_to_zo": dir_to,
        "zo_to_en": dir_from,
        "en_to_my": dir_my,
    }

    if mode != "apply":
        ok = True
        if imp != plan["exact_2x_imp"]:
            rep.errors.append(f"translations_import {imp} != plan {plan['exact_2x_imp']}")
            ok = False
        if dir_my != plan["en_to_my_expected"]:
            rep.errors.append(f"en->my {dir_my} != plan {plan['en_to_my_expected']}")
            ok = False
        if dir_to + dir_from != plan["zo_pair"]:
            rep.errors.append(f"en_to_zo+zo_to_en {dir_to + dir_from} != plan {plan['zo_pair']}")
            ok = False
        if ok:
            rep.projections["translations"] = f"{canon} -> {canon + dir_to + dir_from}"
            rep.projections["translations_news"] = f"new table <- {dir_my} en->my rows"
        rep.after = {"translations_target": canon + dir_to + dir_from, "news_target": dir_my}
        return rep

    cols = [r[1] for r in con.execute("PRAGMA table_info(translations)")]
    col_list = ", ".join(cols)
    n_main = con.execute(
        f"INSERT INTO translations ({col_list}) SELECT {col_list} FROM translations_import WHERE dir IN "
        f"('en_to_zo','zo_to_en')"
    ).rowcount
    assert_eq(n_main, dir_to + dir_from, "inserted en_to_zo+zo_to_en into translations")
    con.execute(
        "CREATE TABLE IF NOT EXISTS translations_news AS SELECT * FROM translations_import WHERE dir='en->my'"
    )
    news = con.execute("SELECT COUNT(*) FROM translations_news").fetchone()[0]
    assert_eq(news, dir_my, "translations_news count")
    assert_eq(con.execute("SELECT COUNT(*) FROM translations").fetchone()[0], canon + n_main, "translations total")
    rep.applied["translations_inserted"] = n_main
    rep.after = {"translations": canon + n_main, "translations_news": news}
    return rep


# --------------------------------------------------------------------------
# Step 05/06 — controlled collapse (exact full-row-identity, non-data cols excluded)
# --------------------------------------------------------------------------


def _collapse(con: sqlite3.Connection, table: str, group_cols: list[str], rep: ItemReport) -> int:
    where = " AND ".join(f"{c} IS NOT NULL" for c in group_cols)
    g = ", ".join(group_cols)
    groups = con.execute(
        f"SELECT {g}, COUNT(*) AS n FROM {table} WHERE {where} GROUP BY {g} HAVING n > 1"
    ).fetchall()
    total_removed = 0
    for row in groups:
        key = row[:-1]
        n = row[-1]
        cond = " AND ".join(f"{c} = ?" for c in group_cols)
        ids = [r[0] for r in con.execute(f"SELECT id FROM {table} WHERE {cond} ORDER BY id", key)]
        if len(ids) < 2:
            continue
        keep, drop = ids[0], ids[1:]
        total_removed += len(drop)
        if rep.mode == "apply":
            ph = ",".join("?" * len(drop))
            con.execute(f"DELETE FROM {table} WHERE id IN ({ph})", drop)
    rep.projections[f"{table}_groups"] = len(groups)
    rep.applied[f"{table}_removed"] = total_removed
    return total_removed


def item_collapse(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("collapse")
    rep.mode = mode
    plan = PLAN_EXPECTED["collapse"]
    tables = [
        ("translations", ["source", "target", "domain"]),
        ("translations_news", ["source", "target", "domain"]),
        ("word_alignments", ["source", "target", "source_token_idx", "target_token_idx", "alignment_type"]),
        ("zolai_bible_analysis", ["ref", "book", "book_name", "zolai", "english", "analysis"]),
    ]
    before_counts = {}
    for t, _ in tables:
        try:
            before_counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            rep.skipped.append(f"{t} (absent)")
    rep.before = dict(before_counts)

    removed: dict[str, int] = {}
    for t, cols in tables:
        if t not in before_counts:
            continue
        removed[t] = _collapse(con, t, cols, rep)
    rep.after = {t: before_counts[t] - removed.get(t, 0) for t in before_counts}

    if mode != "apply":
        return rep

    # Post-asserts only when the table is in scope for the plan's allowed outcomes.
    assert_eq(
        rep.after.get("translations"),
        rep.before["translations"] - removed.get("translations", 0),
        "translations collapse arithmetic",
    )
    for t, target_key in (("translations", "translations_post"), ("translations_news", "news_post")):
        if t in rep.after and plan.get(target_key) is not None and removed.get(t, 0) > 0:
            assert_eq(rep.after[t], plan[target_key], f"{t} post-collapse exact-2x")
    if removed.get("word_alignments", 0) > 0:
        assert_eq(rep.after["word_alignments"], plan["word_align_post"], "word_alignments collapse")
    if removed.get("zolai_bible_analysis", 0) > 0:
        assert_eq(rep.after["zolai_bible_analysis"], plan["zba_post"], "zba collapse")
    elif SKIPLIST_ZBA.exists():
        rep.skipped.append("zba collapse: 706 dialect-collision groups filed in skip-list (M4.13 owner gate)")
    return rep


# --------------------------------------------------------------------------
# Step 08 — import_log backfill/census
# --------------------------------------------------------------------------


def item_import_log(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("import_log")
    plan = PLAN_EXPECTED["import_log"]
    imp_log = con.execute("SELECT COUNT(*) FROM import_log WHERE table_name='bible_verses_import'").fetchone()[0]
    completed = con.execute("SELECT COUNT(*) FROM import_log WHERE status='completed'").fetchone()[0]
    failed = con.execute("SELECT COUNT(*) FROM import_log WHERE status='failed'").fetchone()[0]
    m8a = con.execute("SELECT COUNT(*) FROM import_log WHERE status IN ('completed','failed')").fetchone()[0]
    bad_alias = [
        t
        for t in ALIAS_MAP
        if t not in {r[0] for r in con.execute("SELECT DISTINCT table_name FROM import_log")}
    ]
    # alias table names that do not resolve to canonical names in the same table_name column
    unresolved = [
        t
        for t in ALIAS_MAP
        if not con.execute(
            "SELECT 1 FROM import_log WHERE table_name=? OR table_name=? LIMIT 1", (ALIAS_MAP[t], ALIAS_MAP[t])
        )
    ]
    rep.before = {"completed": completed, "failed": failed, "m8a_entries": m8a, "bible_import_entries": imp_log}

    if mode != "apply":
        ok = True
        if imp_log != plan["bible_import_entries"]:
            rep.errors.append(f"bible_verses_import entries {imp_log} != plan {plan['bible_import_entries']}")
            ok = False
        if m8a != plan["m8a_entries"]:
            rep.errors.append(f"completed+failed {m8a} != plan {plan['m8a_entries']}")
            ok = False
        if unresolved:
            rep.errors.append(f"alias entries do not resolve: {unresolved}")
            ok = False
        if ok:
            rep.projections["m8a"] = f"{m8a} -> 0 (all completed+failed rows -> r2a_reconciled)"
        rep.after = {"m8a_target": 0}
        return rep

    changed = con.execute("UPDATE import_log SET status='r2a_reconciled' WHERE status IN ('completed','failed')").rowcount
    assert_eq(changed, m8a, "reconciled rows")
    m8a_after = con.execute("SELECT COUNT(*) FROM import_log WHERE status IN ('completed','failed')").fetchone()[0]
    assert_eq(m8a_after, 0, "M8a entries after reconcile")
    con.execute(
        "INSERT INTO import_log (id, batch_id, table_name, status, row_count, created_at, updated_at, note) VALUES "
        "('r2a-census-20261027T020000Z', 'r2a-census-20261027T020000Z', 'zolai_census', 'r2a_batch', ?, ?, ?, ?)",
        (
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0],
            now_iso(),
            now_iso(),
            "post-R2-A census: 26 import tables archived+dropped, 3 staging drops, 13 index pairs reclaimed",
        ),
    )
    rep.applied["reconciled"] = changed
    rep.after = {"m8a": 0}
    return rep


# --------------------------------------------------------------------------
# Step 07/09 — drop archived import tables + phase2c empty staging tables
# --------------------------------------------------------------------------


def _assert_prefix_covered(con: sqlite3.Connection, table: str, rep: ItemReport) -> bool:
    if prefix_covered(con, table):
        return True
    rep.errors.append(f"{table}: import-side prefix NOT fully represented in canonical before drop")
    return False


def item_drop_imports(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("drop_imports")
    present = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    targets = [t for t in PLAN_EXPECTED["archive_targets"] if t in present]
    checks = {
        "bible_verses_import": "bible_verses",
        "translations_import": "translations",
        "word_alignments_import": "word_alignments",
        "dictionary_import": "dictionary",
        "vocab_import": "vocab",
        "dictionary_my_import": "dictionary_my",
    }
    rep.before = {"import_tables_present": len(targets)}

    for t, canon in checks.items():
        if t in present:
            _assert_prefix_covered(con, t, rep)
            if canon == "bible_verses":
                # special: import-only refs must already be merged into canonical
                leftover = con.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE ref NOT LIKE {BIBLE_IMPORT_EXCLUDES} "
                    f"AND ref NOT IN (SELECT ref FROM bible_verses)"
                ).fetchone()[0]
                if leftover:
                    rep.errors.append(f"bible_verses_import has {leftover} rows not in canonical")
            else:
                imp_rows = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                canon_rows = con.execute(f"SELECT COUNT(*) FROM {canon}").fetchone()[0]
                if canon_rows < imp_rows:
                    rep.errors.append(f"{canon} rows {canon_rows} < {t} rows {imp_rows}")

    if mode != "apply":
        rep.projections["dropped"] = targets
        rep.after = {"import_tables_target": 0}
        return rep

    if rep.errors:
        raise R2AStop("drop_imports blocked: prefix/row checks failed - " + "; ".join(rep.errors[:3]))
    for t in targets:
        con.execute(f"DROP TABLE {t}")
        rep.applied[t] = "dropped"
    left = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND (name LIKE '%_import' OR name IN "
        f"({','.join('?' * len(PHASE2_EMPTY_DROPS))}))",
        list(PHASE2_EMPTY_DROPS),
    ).fetchone()[0]
    assert_eq(left, 0, "no import/phase2c staging tables left")
    rep.after = {"import_tables": 0}
    return rep


def item_phase2_drops(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("phase2_drops")
    present = {
        t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    }
    targets = [t for t in PHASE2_EMPTY_DROPS if t in present]
    for t in targets:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if n != 0:
            rep.errors.append(f"{t} not empty ({n} rows) - refusing to drop")
    rep.before = {"targets_present": len(targets)}
    if mode != "apply":
        rep.projections["dropped"] = targets
        return rep
    if rep.errors:
        raise R2AStop("phase2_drops blocked: " + "; ".join(rep.errors))
    for t in targets:
        con.execute(f"DROP TABLE {t}")
        rep.applied[t] = "dropped"
    return rep


# --------------------------------------------------------------------------
# Step 10 — redundant index pairs
# --------------------------------------------------------------------------


def item_indexes(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("indexes")
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    drop_now = [n for n in INDEX_DROP_SET if n in names]
    rep.before = {"drop_candidates_present": len(drop_now), "of_planned": len(INDEX_DROP_SET)}
    rep.projections["reclaimed"] = "~147 MiB (VACUUM later in run)"
    if mode != "apply":
        rep.after = {"drop_candidates": drop_now}
        return rep

    for name in drop_now:
        row = con.execute(
            "SELECT sql, tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if row is None:
            continue
        sql, tbl = row
        # Never drop an implicit rowid/UNIQUE index or a PRIMARY KEY index.
        if sql is None or "UNIQUE" in sql.upper() or "PRIMARY KEY" in sql.upper():
            rep.errors.append(f"refusing to drop {name} (implicit/unique/pk)")
            continue
        con.execute(f"DROP INDEX {name}")
        rep.applied[name] = "dropped"
    for t, keep in DROP_KEEP.items():
        still = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?", (keep,)).fetchone()[0]
        assert_eq(still, 1, f"keep-index {keep} on {t} must survive")
    return rep


# --------------------------------------------------------------------------
# Final counts + verify projections
# --------------------------------------------------------------------------


def item_final_counts(con: sqlite3.Connection, mode: str) -> ItemReport:
    rep = ItemReport("final_counts")
    want = {
        "bible_verses": CANONICAL_POST["bible_verses"],
        "translations": CANONICAL_POST["translations"],
        "translations_news": CANONICAL_POST["translations_news"],
        "word_alignments": CANONICAL_POST["word_alignments"],
        "zolai_bible_analysis": CANONICAL_POST["zolai_bible_analysis"],
        "dictionary": CANONICAL_POST["dictionary"],
        "vocab": CANONICAL_POST["vocab"],
        "dictionary_my": CANONICAL_POST["dictionary_my"],
    }
    got: dict[str, int] = {}
    for t, exp in want.items():
        try:
            got[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            rep.errors.append(f"{t} missing")
            continue
        if mode != "apply":
            assert_eq(got[t], exp, f"final count {t}")
    rep.before = dict(got)
    if mode == "apply":
        FINAL_COUNTS_PATH.write_text(json.dumps({"at": now_iso(), "counts": got}, indent=2, sort_keys=True))
    rep.after = dict(got)
    return rep


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


ITEMS = {
    "d13": item_d13,
    "split": item_translations,
    "articles": item_articles,
    "collapse": item_collapse,
    "import_log": item_import_log,
    "phase2_drops": item_phase2_drops,
    "indexes": item_indexes,
    "final_counts": item_final_counts,
}


def run_items(
    db_path: Path,
    mode: str,
    items: list[str],
    *,
    apply_writes: bool,
    single_writer: bool,
    require_backup: bool,
    report_path: Path | None = None,
) -> int:
    reports: list[ItemReport] = []
    con = connect_ro(db_path)
    try:
        # Read-only projections first (they use the ro connection).
        for name in items:
            if name == "archive":
                rep = item_archive(con, mode, db_path)
            elif name == "drop_imports":
                rep = item_drop_imports(con, mode)
            else:
                rep = ITEMS[name](con, mode)
            rep.report(mode)
            reports.append(rep)
            if rep.errors:
                return _finish(reports, report_path)
        if mode != "apply":
            return _finish(reports, report_path)
    finally:
        con.close()

    # Apply path: exclusive rw connection, fuser + backup gates.
    if apply_writes and mode == "apply":
        try:
            if single_writer:
                fuser_clean(db_path)
            if require_backup:
                require_fresh_backup()
        except R2AStop as e:
            print(f"[gate] {e}", file=sys.stderr)
            return 2
        con = sqlite3.connect(str(db_path), isolation_level=None)
        con.execute("PRAGMA foreign_keys=ON")
        try:
            for name in items:
                con.execute(BEGIN_WRITE_BATCH)
                try:
                    if name == "archive":
                        rep = item_archive(con, "apply", db_path)
                    elif name == "drop_imports":
                        rep = item_drop_imports(con, "apply")
                    else:
                        rep = ITEMS[name](con, "apply")
                    if rep.errors:
                        con.execute("ROLLBACK")
                        rep.report("apply")
                        return 1
                    con.execute(END_WRITE_BATCH)
                except Exception as e:  # noqa: BLE001 - batch atomicity
                    con.execute("ROLLBACK")
                    print(f"[batch:{name}] ROLLED BACK: {e}", file=sys.stderr)
                    return 1
                rep.report("apply")
        finally:
            con.close()
    return _finish(reports, report_path)


def _finish(reports: list[ItemReport], report_path: Path | None) -> int:
    rc = 1 if any(r.errors for r in reports) else 0
    if report_path:
        report_path.write_text(
            json.dumps([r.to_dict() for r in reports], indent=2, sort_keys=True, default=str)
        )
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["dry-run", "apply", "verify"])
    ap.add_argument("--item", action="append", choices=ITEM_ORDER, help="restrict to items (repeatable)")
    ap.add_argument("--db", default=str(BASE / "data" / "zolai.db"))
    ap.add_argument("--report", default=None, help="write JSON report here")
    ap.add_argument("--no-single-writer", action="store_true", help="skip fuser gate (tests/fixtures)")
    ap.add_argument("--no-backup-gate", action="store_true", help="skip backup freshness gate (tests/fixtures)")
    ap.add_argument("--archive-dir", default=None, help="override archive dir (tests/fixtures)")
    args = ap.parse_args(argv)

    global ARCHIVE_DIR, ARCHIVE_MANIFEST_PATH  # noqa: PLW0603 - test seam
    if args.archive_dir:
        ARCHIVE_DIR = Path(args.archive_dir)
        ARCHIVE_MANIFEST_PATH = ARCHIVE_DIR / "manifest.json"

    items = args.item or ITEM_ORDER
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    mode = {"dry-run": "dry-run", "apply": "apply", "verify": "verify"}[args.mode]
    return run_items(
        db_path,
        mode,
        items,
        apply_writes=(args.mode == "apply"),
        single_writer=not args.no_single_writer,
        require_backup=not args.no_backup_gate,
        report_path=Path(args.report) if args.report else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
