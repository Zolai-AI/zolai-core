#!/usr/bin/env python3
"""R2-A consolidation runner (plan data/conductor/plan-r2a-db-consolidation.md).

Operates on the import/staging side of the zolai single-DB system. Binding rules
enforced in code (see r2a_lib docstring):
  1. write gate = fuser single-writer + fresh backup + EXCLUSIVE/BEGIN IMMEDIATE;
  2. one item = one atomic journal write with before/after count asserts;
  3. nothing unrecoverable is deleted: all 26 ``*_import`` tables are first dumped
     into one archive DB (CREATE TABLE … AS SELECT) with per-table count equality;
  4. never touched: bible_verses canonical rows, ZOMIDAILY/phrase:* word_usage
     sentinels, the 2,208 protected legacy articles, the 545 import-only verse
     refs (they stay in the archive only), KQ ``--persist``.
Canonical changes are limited to plan §2: translations_import ref normalization,
translations en→my split, articles content_hash backfill + namespace column +
one protected-checked content-dup drop, exact-pair collapses behind a full-row-
identity gate, import_log rebuild, empty-table + redundant-index drops.

Subcommands (dry-run/apply/verify all accept --item to run a subset, in the
canonical order regardless of argument order):
  python scripts/maintenance/consolidation_r2a.py dry-run [--item I ...]
  python scripts/maintenance/consolidation_r2a.py apply   [--item I ...]
  python scripts/maintenance/consolidation_r2a.py verify  [--item I ...]
  python scripts/maintenance/consolidation_r2a.py vacuum

Exit codes: 0 = consistent; 1 = drift/assert/item error; 2 = write gate failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2a_lib import (  # noqa: E402
    ALIAS_MAP,
    ARCHIVE_DB,
    ARCHIVE_MANIFEST,
    BACKUP_DIR,
    CANONICAL_POST,
    CENSUS_STATUS,
    DEFAULT_DB,
    DROP_KEEP,
    FINAL_COUNTS,
    IMPORT_TABLES,
    INDEX_DROP_SET,
    NON_DATA_COLS,
    PHASE2_EMPTY_DROPS,
    PLAN_EXPECTED,
    R2AStop,
    SKIPLIST_ZBA,
    ItemReport,
    assert_drift,
    assert_eq,
    backfill_content_hash,
    connect_ro,
    connect_rw,
    count,
    data_columns,
    fuser_clean,
    fuser_pids,
    index_exists,
    log_batch,
    now_iso,
    require_fresh_backup,
    scalar,
    table_exists,
    write_batch,
)

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

CENSUS_EXCLUDE = frozenset({"import_log", "provenance"})
ARCHIVED_NOTE = "R2-A: archived to zolai-import-archive-2026-10-27.db"


# ── paths overridable for tests ─────────────────────────────────────────────


@dataclass
class Paths:
    db: Path = DEFAULT_DB
    archive_db: Path = ARCHIVE_DB
    manifest: Path = ARCHIVE_MANIFEST
    skiplist_zba: Path = SKIPLIST_ZBA
    final_counts: Path = FINAL_COUNTS
    backup_dir: Path = BACKUP_DIR
    use_gates: bool = True  # fuser + backup gates (disable only for fixtures)

    def ensure_dirs(self) -> None:
        self.archive_db.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.final_counts.parent.mkdir(parents=True, exist_ok=True)


# ── D13 — translations_import.reference normalization (plan §2 step 1) ───────


def item_d13(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("d13", mode)
    exp = PLAN_EXPECTED["d13"]
    total = count(con, "translations_import")
    dot = scalar(con, "SELECT COUNT(*) FROM translations_import WHERE reference LIKE '%.%'")
    nah = scalar(con, "SELECT COUNT(*) FROM translations_import WHERE reference LIKE 'NAH.%'")
    empty = scalar(con, "SELECT COUNT(*) FROM translations_import WHERE reference=''")
    rep.before = {"rows": total, "dot_refs": dot, "nah_refs": nah, "empty_refs": empty}
    if not table_exists(con, "translations_import"):
        rep.skipped.append("translations_import already dropped (idempotent re-run)")
        return rep
    if dot == 0 and nah == 0:
        rep.skipped.append("refs already normalized (idempotent re-run)")
        rep.after = {"dot_refs": 0, "unresolved": 0}
        return rep
    assert_drift("translations_import", total, exp["translations_import"])
    assert_eq("D13 dot refs before", dot, exp["dot_refs"])
    assert_eq("D13 NAH refs before", nah, exp["nah_rows"])
    assert_eq("D13 empty refs", empty, exp["empty_refs"])
    rep.planned_sql = [
        "UPDATE translations_import SET reference='NAM'||substr(reference,4) WHERE reference LIKE 'NAH.%';",
        "UPDATE translations_import SET reference=replace(reference,'.',' ') WHERE reference LIKE '%.%';",
    ]
    unresolved_sql = (
        "SELECT COUNT(*) FROM translations_import WHERE reference <> ''"
        " AND replace(reference,'.',' ') NOT IN (SELECT ref FROM bible_verses)"
    )
    if mode == "apply":
        n1 = con.execute(
            "UPDATE translations_import SET reference='NAM'||substr(reference,4) WHERE reference LIKE 'NAH.%'"
        ).rowcount
        n2 = con.execute(
            "UPDATE translations_import SET reference=replace(reference,'.',' ') WHERE reference LIKE '%.%'"
        ).rowcount
        rep.applied = {"nah_fixed": n1, "dots_fixed": n2}
        assert_eq(
            "D13 NAH residual", scalar(con, "SELECT COUNT(*) FROM translations_import WHERE reference LIKE 'NAH.%'"), 0
        )
        assert_eq("D13 unresolved after normalize", scalar(con, unresolved_sql), 0)
        log_batch(
            con,
            item="d13",
            table="translations_import",
            rows=n1 + n2,
            note="D13 ref normalization NAH->NAM + dot->space",
        )
    elif mode == "verify":
        assert_eq("D13 unresolved after normalize", scalar(con, unresolved_sql), 0)
    rep.after = {
        "dot_refs": scalar(con, "SELECT COUNT(*) FROM translations_import WHERE reference LIKE '%.%'") or 0,
        "unresolved": scalar(con, unresolved_sql) or 0,
    }
    rep.projections["note"] = "14 empty refs intentionally kept (documented, excluded from assert)"
    return rep


# ── Archive — one archive DB, per-table count equality, sha256 manifest ─────


def _archive_tables(con: sqlite3.Connection, p: Paths) -> dict[str, dict]:
    return json.loads(p.manifest.read_text()) if p.manifest.exists() else {}


def item_archive(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("archive", mode)
    present = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    targets = [t for t in IMPORT_TABLES if t in present]
    counts = {t: count(con, t) for t in targets}
    rep.before = {"import_tables_present": len(targets), "total_rows": sum(counts.values())}
    rep.projections["archive_db"] = str(p.archive_db)
    if mode == "dry-run":
        rep.after = {"per_table": counts}
        return rep
    if mode == "verify":
        man = _archive_tables(con, p)
        assert_eq("archive manifest tables", len([t for t in IMPORT_TABLES if t in man]), len(IMPORT_TABLES))
        for t in IMPORT_TABLES:
            entry = man[t]
            assert_eq(f"archive {t} source==archive", entry["source_count"], entry["archive_count"])
        rep.after = {"manifest_tables": len(man)}
        return rep
    # apply: build/refresh archive DB via ATTACH (idempotent: verified tables skipped)
    man = _archive_tables(con, p)
    p.ensure_dirs()
    arc = sqlite3.connect(str(p.archive_db))
    try:
        arc.execute("PRAGMA journal_mode=DELETE")
        fresh: list[str] = []
        for t in targets:
            n = counts[t]
            entry = man.get(t)
            if entry and entry.get("source_count") == n and table_exists(arc, t) and count(arc, t) == n:
                rep.skipped.append(f"{t} (archive already verified: {n} rows)")
            else:
                fresh.append(t)
        if fresh:
            arc.execute("ATTACH DATABASE ? AS src", (str(p.db),))
            try:
                for t in fresh:
                    arc.execute(f"DROP TABLE IF EXISTS main.{t}")
                    arc.execute(f"CREATE TABLE main.{t} AS SELECT * FROM src.{t}")
                    assert_eq(f"archive {t} count", count(arc, t), counts[t])
                    man[t] = {"source_count": counts[t], "archive_count": count(arc, t), "archived_at": now_iso()}
            finally:
                arc.execute("DETACH DATABASE src")
        digest = hashlib.sha256()
        with p.archive_db.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        man["_manifest"] = {
            "archive_db": str(p.archive_db),
            "sha256": digest.hexdigest(),
            "generated_at": now_iso(),
        }
        p.manifest.write_text(json.dumps(man, indent=2, sort_keys=True))
        for t in targets:
            assert_eq(f"archive {t} count", count(arc, t), counts[t])
        log_batch(
            con,
            item="archive",
            table="r2a_archive",
            rows=sum(counts.values()),
            note=f"archive-validated dump of {len(targets)} import tables",
        )
    finally:
        arc.close()
    rep.applied = {"archived_tables": len(targets), "fresh": len(fresh)}
    rep.after = {"manifest_tables": len([t for t in IMPORT_TABLES if t in man])}
    return rep


# ── Split — translations en→my → translations_news (plan §2 step 3) ─────────


def item_split(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("split", mode)
    exp = PLAN_EXPECTED["split"]
    canon = count(con, "translations")
    enmy = scalar(con, "SELECT COUNT(*) FROM translations WHERE direction='en→my'")
    rep.before = {"translations": canon, "en_to_my": enmy}
    if enmy == 0 and table_exists(con, "translations_news"):
        rep.skipped.append("split already applied (idempotent re-run)")
        rep.after = {"translations": canon, "translations_news": count(con, "translations_news")}
        return rep
    assert_drift("translations", canon, exp["translations"])
    assert_drift("translations en→my", enmy, exp["en_to_my"])
    rep.planned_sql = [
        "CREATE TABLE translations_news AS SELECT * FROM translations WHERE direction='en→my';",
        "DELETE FROM translations WHERE direction='en→my';",
    ]
    rep.projections["news_split"] = (
        f"news:chuuhte {exp['news']:,} + parallel:arc {exp['parallel']:,} = {exp['en_to_my']:,}"
    )
    if mode == "apply":
        if not table_exists(con, "translations_news"):
            n = con.execute(
                "CREATE TABLE translations_news AS SELECT * FROM translations WHERE direction='en→my'"
            ).rowcount
            rep.applied["news_created"] = n or exp["en_to_my"]
        else:
            rep.skipped.append("translations_news exists")
        deleted = con.execute("DELETE FROM translations WHERE direction='en→my'").rowcount
        rep.applied["deleted"] = deleted
        assert_eq("translations after split", count(con, "translations"), exp["post"])
        assert_eq("translations_news after split", count(con, "translations_news"), exp["en_to_my"])
        assert_eq(
            "non-Bible directions residual",
            scalar(con, "SELECT COUNT(*) FROM translations WHERE direction NOT IN ('en_to_zo','zo_to_en')"),
            0,
        )
        log_batch(
            con,
            item="split",
            table="translations_news",
            rows=deleted or exp["en_to_my"],
            note="M9c en→my news/parallel split",
        )
    if mode == "verify":
        assert_eq("translations post", count(con, "translations"), exp["post"])
        assert_eq("translations_news post", count(con, "translations_news"), exp["en_to_my"])
    rep.after = {
        "translations": count(con, "translations"),
        "translations_news": count(con, "translations_news") if table_exists(con, "translations_news") else 0,
    }
    return rep


# ── Articles — hash backfill + namespace + protected-checked dup drop ───────


PROTECTED_SQL = "source_file IS NULL AND language='zolai'"


def item_articles(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("articles", mode)
    exp = PLAN_EXPECTED["articles"]
    total = count(con, "articles")
    null_hash = scalar(con, "SELECT COUNT(*) FROM articles WHERE content_hash IS NULL")
    prot = scalar(con, f"SELECT COUNT(*) FROM articles WHERE {PROTECTED_SQL}")
    rep.before = {"rows": total, "null_hash": null_hash, "protected": prot}
    ns_col = "namespace" in {r[1] for r in con.execute("PRAGMA table_info(articles)")}
    if null_hash == 0 and ns_col:
        rep.skipped.append("articles backfill+namespace already applied (idempotent re-run)")
        rep.after = {"rows": total, "null_hash": 0, "protected": prot}
        return rep
    assert_drift("articles", total, exp["total"])
    assert_drift("articles NULL hash", null_hash, exp["null_hash"])
    assert_eq("protected triple before", prot, exp["legacy"])
    dup_sql = (
        "SELECT id FROM articles WHERE content IN (SELECT content FROM articles"
        " WHERE content IS NOT NULL AND content<>'' GROUP BY content HAVING COUNT(*)>1) ORDER BY id"
    )
    dup_ids = [r[0] for r in con.execute(dup_sql)]
    rep.planned_sql = [
        "-- backfill: UPDATE articles SET content_hash=doublehex(sha256(content)) WHERE content_hash IS NULL",
        "ALTER TABLE articles ADD COLUMN namespace TEXT;  -- news|lessons|legacy",
        "-- namespace: source_file='zomidaily'->news; language='zolai_simbu'->lessons; protected->legacy",
        "-- dup: DELETE FROM articles WHERE id=MAX(id) of content-identical unprotected pair",
    ]
    if mode == "dry-run":
        rep.after = {"null_hash_target": 0, "dup_ids": dup_ids, "post_rows": total - (1 if dup_ids else 0)}
        return rep
    if mode == "apply":
        n = backfill_content_hash(con, "articles")
        rep.applied["hash_backfilled"] = n
        if "namespace" not in {r[1] for r in con.execute("PRAGMA table_info(articles)")}:
            con.execute("ALTER TABLE articles ADD COLUMN namespace TEXT")
        con.execute("UPDATE articles SET namespace='news' WHERE source_file='zomidaily'")
        con.execute("UPDATE articles SET namespace='lessons' WHERE language='zolai_simbu'")
        con.execute(f"UPDATE articles SET namespace='legacy' WHERE {PROTECTED_SQL}")
        # content-dup drop: keep MIN(id), only if neither row is protected
        groups = con.execute(
            "SELECT content, MIN(id), MAX(id), COUNT(*) FROM articles WHERE content IS NOT NULL AND content<>''"
            " GROUP BY content HAVING COUNT(*)>1"
        ).fetchall()
        dropped = 0
        for _content, keep_id, drop_id, _n in groups:
            protected_hit = scalar(
                con,
                f"SELECT COUNT(*) FROM articles WHERE id IN ({keep_id},{drop_id}) AND {PROTECTED_SQL}",
            )
            if protected_hit:
                rep.skipped.append(f"content-dup pair {keep_id}/{drop_id} touches protected row — kept")
                continue
            con.execute("DELETE FROM articles WHERE id=?", (drop_id,))
            dropped += 1
        rep.applied["dup_dropped"] = dropped
        assert_eq(
            "articles NULL hash after", scalar(con, "SELECT COUNT(*) FROM articles WHERE content_hash IS NULL"), 0
        )
        assert_eq(
            "protected triple after", scalar(con, f"SELECT COUNT(*) FROM articles WHERE {PROTECTED_SQL}"), exp["legacy"]
        )
        assert_eq(
            "namespace news", scalar(con, "SELECT COUNT(*) FROM articles WHERE namespace='news'"), exp["news"] - dropped
        )
        assert_eq(
            "namespace lessons", scalar(con, "SELECT COUNT(*) FROM articles WHERE namespace='lessons'"), exp["lessons"]
        )
        assert_eq(
            "namespace legacy", scalar(con, "SELECT COUNT(*) FROM articles WHERE namespace='legacy'"), exp["legacy"]
        )
        log_batch(
            con,
            item="articles",
            table="articles",
            rows=n + dropped,
            note="D14 hash backfill + namespace + protected dup drop",
        )
    if mode == "verify":
        assert_eq(
            "articles NULL hash verify", scalar(con, "SELECT COUNT(*) FROM articles WHERE content_hash IS NULL"), 0
        )
        assert_eq(
            "protected triple verify",
            scalar(con, f"SELECT COUNT(*) FROM articles WHERE {PROTECTED_SQL}"),
            exp["legacy"],
        )
        assert_eq("articles rows verify", count(con, "articles"), exp["post"])
    rep.after = {
        "rows": count(con, "articles"),
        "null_hash": scalar(con, "SELECT COUNT(*) FROM articles WHERE content_hash IS NULL") or 0,
        "protected": scalar(con, f"SELECT COUNT(*) FROM articles WHERE {PROTECTED_SQL}") or 0,
    }
    return rep


# ── Collapse — exact-pair, full-row-identity gate (plan §2 step 5) ──────────


def _collapse_table(con: sqlite3.Connection, table: str, key_cols: list[str], rep: ItemReport) -> tuple[int, int]:
    """Collapse exact dup groups (keep MIN(id)); returns (removed, skipped_groups)."""
    cols = data_columns(con, table)
    ident = ", ".join(cols)
    key = ", ".join(key_cols)
    # GROUP BY unifies NULLs; do NOT filter on data cols (wa.myanmar is NULL on the dup pairs)
    groups = con.execute(f"SELECT {key}, COUNT(*) FROM {table} GROUP BY {key} HAVING COUNT(*)>1").fetchall()
    removed = skipped = 0
    kcond = " AND ".join(f"{c}=?" for c in key_cols)
    for group in groups:
        keys = group[:-1]
        ids = [r[0] for r in con.execute(f"SELECT id FROM {table} WHERE {kcond} ORDER BY id", keys)]
        if len(ids) < 2:
            continue
        # full-row-identity gate over data columns
        distinct = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {ident} FROM {table} WHERE {kcond} GROUP BY {ident})", keys
        ).fetchone()[0]
        if distinct > 1:
            skipped += 1
            continue  # owner gate: differing data columns — never merge
        ph = ",".join("?" * (len(ids) - 1))
        con.execute(f"DELETE FROM {table} WHERE id IN ({ph})", ids[1:])
        removed += len(ids) - 1
    return removed, skipped


def item_collapse(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("collapse", mode)
    exp = PLAN_EXPECTED["collapse"]
    wa = count(con, "word_alignments")
    zba = count(con, "zolai_bible_analysis")
    rep.before = {"word_alignments": wa, "zolai_bible_analysis": zba}
    assert_drift("word_alignments", wa, exp["wa"])
    assert_drift("zolai_bible_analysis", zba, exp["zba"])
    if mode == "dry-run":
        rep.after = {"word_alignments_target": exp["wa_post"], "zolai_bible_analysis": "skip-list (dialect collisions)"}
        return rep
    if mode == "apply":
        removed, skipped = _collapse_table(
            con, "word_alignments", ["ref", "zolai_word", "english_word", "position"], rep
        )
        assert_eq("word_alignments after collapse", count(con, "word_alignments"), exp["wa_post"])
        rep.applied["word_alignments_removed"] = removed
        zremoved, zskipped = _collapse_table(con, "zolai_bible_analysis", ["book_code", "chapter", "verse"], rep)
        if zremoved:
            rep.applied["zba_removed"] = zremoved
        if zskipped:
            p.skiplist_zba.parent.mkdir(parents=True, exist_ok=True)
            p.skiplist_zba.write_text(
                json.dumps(
                    {
                        "reason": "full-row-identity gate: dialect book-code collisions differ in data columns",
                        "skipped_groups": zskipped,
                        "table": "zolai_bible_analysis",
                        "generated_at": now_iso(),
                    },
                    indent=2,
                )
            )
            rep.skipped.append(
                f"zba {zskipped} groups skipped (identity gate) -> {p.skiplist_zba.name} (M4.13 owner gate)"
            )
        assert_eq("zolai_bible_analysis rows", count(con, "zolai_bible_analysis"), zba - zremoved)
        log_batch(
            con,
            item="collapse",
            table="word_alignments",
            rows=removed,
            note=f"R2-A exact-pair collapse (wa {removed}, zba skipped {zskipped})",
        )
    if mode == "verify":
        assert_eq("word_alignments verify", count(con, "word_alignments"), exp["wa_post"])
    rep.after = {
        "word_alignments": count(con, "word_alignments"),
        "zolai_bible_analysis": count(con, "zolai_bible_analysis"),
    }
    return rep


# ── Drop imports — archive-validated (plan §2 step 6) ───────────────────────


def item_drop_imports(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("drop_imports", mode)
    present = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    targets = [t for t in IMPORT_TABLES if t in present]
    man = _archive_tables(con, p)
    rep.before = {"import_tables_present": len(targets)}
    # BLOCK unless archive manifest proves count equality for every target
    unverified = [t for t in targets if t not in man or man[t].get("source_count") != man[t].get("archive_count")]
    if unverified:
        if mode == "apply":
            raise R2AStop(f"drop blocked: no count-verified archive for {unverified[:5]}")
        rep.projections["blocked_until_archive"] = (
            f"{len(unverified)} tables await count-verified archive (archive item runs first in apply)"
        )
        rep.after = {"dropped": 0}
        return rep
    if mode == "dry-run":
        rep.after = {"dropped": len(targets)}
        return rep
    if mode == "verify":
        assert_eq("import tables after drop", len(targets), 0)
        return rep
    for t in targets:
        con.execute(f"DROP TABLE {t}")
        rep.applied[t] = "dropped"
    assert_eq(
        "import tables residual",
        scalar(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%_import'"),
        0,
    )
    log_batch(
        con,
        item="drop_imports",
        table="r2a_import_tables",
        rows=len(targets),
        note=f"dropped {len(targets)} archived import tables",
    )
    rep.after = {"import_tables": 0}
    return rep


# ── import_log rebuild (plan §2 step 7) ────────────────────────────────────


def item_import_log(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("import_log", mode)
    exp = PLAN_EXPECTED["import_log"]
    total = count(con, "import_log")
    completed = scalar(con, "SELECT COUNT(*) FROM import_log WHERE status='completed'")
    failed = scalar(con, "SELECT COUNT(*) FROM import_log WHERE status='failed'")
    rep.before = {"entries": total, "completed": completed, "failed": failed}
    census_done = scalar(con, "SELECT COUNT(*) FROM import_log WHERE status='census'")
    if completed == 0 and (census_done or 0) > 0:
        rep.skipped.append("import_log rebuild already applied (idempotent re-run)")
        rep.after = {"entries": total, "completed": 0}
        return rep
    assert_drift("import_log entries", total, exp["total"])
    alias_hits = scalar(
        con,
        f"SELECT COUNT(*) FROM import_log WHERE table_name IN ({','.join('?' * len(ALIAS_MAP))})",
        tuple(ALIAS_MAP),
    )
    rep.planned_sql = [
        "-- (a) completed w/ dropped table -> status='archived' + note",
        "-- (b) alias table_names -> canonical *_import",
        "-- (c) remaining completed -> rows_imported=live; claimed>0&live=0 -> 'lost'",
        "-- (d) census: populated unlogged tables, status='census', uuid batch_id",
        "-- (e) one provenance row for census batch (row_count>0)",
    ]
    census_scope = _census_scope(con)
    rep.projections["census_tables"] = len(census_scope)
    if mode == "dry-run":
        rep.after = {"alias_entries": alias_hits, "completed_target": 0}
        return rep
    if mode == "apply":
        # (a) completed entries whose table no longer exists -> archived
        cur = con.execute("SELECT id, table_name FROM import_log WHERE status='completed'").fetchall()
        archived = lost = recounted = 0
        for eid, tname in cur:
            if not table_exists(con, tname):
                con.execute(
                    "UPDATE import_log SET status='archived', error_message=? WHERE id=?",
                    (ARCHIVED_NOTE, eid),
                )
                archived += 1
            else:
                live = count(con, tname)
                claimed = scalar(con, "SELECT rows_imported FROM import_log WHERE id=?", (eid)) or 0
                if claimed > 0 and live == 0:
                    con.execute(
                        "UPDATE import_log SET status='lost', error_message=? WHERE id=?",
                        (f"R2-A: claimed {claimed} rows, live table empty", eid),
                    )
                    lost += 1
                else:
                    con.execute("UPDATE import_log SET rows_imported=? WHERE id=?", (live, eid))
                    recounted += 1
        # (b) alias canonicalization
        renamed = 0
        for alias, canonical in ALIAS_MAP.items():
            cur2 = con.execute(
                "UPDATE import_log SET table_name=? WHERE table_name=? AND NOT EXISTS "
                "(SELECT 1 FROM import_log l2 WHERE l2.table_name=?)",
                (canonical, alias, canonical),
            )
            renamed += cur2.rowcount
        # (d)+(e) census
        census_rows = 0
        for tname, live in census_scope:
            con.execute(
                "INSERT INTO import_log (batch_id, source_file, table_name, rows_imported, imported_at, version, status)"
                " VALUES (?,?,?,?,?,1,?)",
                (str(uuid.uuid4()), "r2a-census", tname, live, now_iso(), CENSUS_STATUS),
            )
            census_rows += 1
        if census_rows:
            con.execute(
                "INSERT INTO provenance (filename, size_bytes, sha256, row_count, source, generator_script, version, status, updated_at, change_log)"
                " VALUES (?,?,?,?,'R2-A consolidation','scripts/maintenance/consolidation_r2a.py','1','applied',?,?)",
                (
                    f"r2a-census-{now_iso()}",
                    0,
                    hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
                    census_rows,
                    now_iso(),
                    f"R2-A census of {census_rows} populated unlogged tables",
                ),
            )
        rep.applied = {
            "archived": archived,
            "lost": lost,
            "recounted": recounted,
            "renamed": renamed,
            "census": census_rows,
        }
        assert_eq("M8a completed residual", scalar(con, "SELECT COUNT(*) FROM import_log WHERE status='completed'"), 0)
        assert_eq(
            "M8d failed missing error_message",
            scalar(
                con,
                "SELECT COUNT(*) FROM import_log WHERE status='failed' AND (error_message IS NULL OR TRIM(error_message)='')",
            ),
            0,
        )
        log_batch(
            con,
            item="import_log",
            table="import_log",
            rows=archived + lost + recounted + renamed + census_rows,
            note="D16 import_log rebuild: archived/lost/recount/aliases/census",
        )
    if mode == "verify":
        assert_eq(
            "M8a completed residual verify", scalar(con, "SELECT COUNT(*) FROM import_log WHERE status='completed'"), 0
        )
    rep.after = {
        "entries": count(con, "import_log"),
        "completed": scalar(con, "SELECT COUNT(*) FROM import_log WHERE status='completed'") or 0,
    }
    return rep


def _census_scope(con: sqlite3.Connection) -> list[tuple[str, int]]:
    logged = {r[0] for r in con.execute("SELECT DISTINCT table_name FROM import_log WHERE status='completed'")}
    out: list[tuple[str, int]] = []
    for (t,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        " AND name NOT LIKE '%_fts' ORDER BY name"
    ):
        if t in CENSUS_EXCLUDE or t in logged:
            continue
        n = count(con, t)
        if n > 0:
            out.append((t, n))
    return out


# ── Phase-2 empty drops + index reclaim + final counts ─────────────────────


def item_phase2_drops(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("phase2_drops", mode)
    present = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    targets = [t for t in PHASE2_EMPTY_DROPS if t in present]
    nonempty = {t: count(con, t) for t in targets if count(con, t) > 0}
    rep.before = {"targets_present": len(targets)}
    if nonempty:
        if mode == "apply":
            raise R2AStop(f"phase2 drop blocked: non-empty {nonempty}")
        rep.errors.append(f"drop would be BLOCKED: non-empty {nonempty}")
        return rep
    if mode == "dry-run":
        rep.after = {"dropped": len(targets)}
        return rep
    if mode == "verify":
        assert_eq("phase2 tables residual", len(targets), 0)
        return rep
    for t in targets:
        con.execute(f"DROP TABLE {t}")
        rep.applied[t] = "dropped"
    for keep in DROP_KEEP:
        assert_eq(f"keep table {keep}", table_exists(con, keep), 1)
    log_batch(
        con,
        item="phase2_drops",
        table="r2a_phase2",
        rows=len(targets),
        note="dropped 4 empty phase-2c tables; KEEP set intact",
    )
    return rep


def item_indexes(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("indexes", mode)
    present = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    drops = [n for n in INDEX_DROP_SET if n in present]
    missing_keep = [INDEX_DROP_SET[n] for n in drops if not index_exists(con, INDEX_DROP_SET[n])]
    rep.before = {"drop_candidates": len(drops), "of_planned": len(INDEX_DROP_SET)}
    rep.projections["reclaimed"] = "~147 MiB after VACUUM"
    if missing_keep:
        if mode == "apply":
            raise R2AStop(f"index drop blocked: keep-index missing {missing_keep}")
        rep.errors.append(f"drop would be BLOCKED: keep-index missing {missing_keep}")
        return rep
    if mode == "dry-run":
        rep.after = {"dropped": len(drops)}
        return rep
    if mode == "verify":
        assert_eq("planned index drops residual", len([n for n in INDEX_DROP_SET if n in present]), 0)
        return rep
    for n in drops:
        sql = scalar(con, "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (n,))
        if sql is None or "UNIQUE" in sql.upper() or "PRIMARY KEY" in sql.upper():
            rep.skipped.append(f"{n} implicit/unique/pk — refused")
            continue
        con.execute(f"DROP INDEX {n}")
        rep.applied[n] = "dropped"
    for keep in {INDEX_DROP_SET[n] for n in drops}:
        assert_eq(f"keep index {keep}", index_exists(con, keep), 1)
    log_batch(
        con,
        item="indexes",
        table="r2a_indexes",
        rows=len(rep.applied),
        note=f"reclaimed {len(rep.applied)} redundant indexes",
    )
    rep.after = {"dropped": len(rep.applied)}
    return rep


def item_final_counts(con: sqlite3.Connection, mode: str, p: Paths) -> ItemReport:
    rep = ItemReport("final_counts", mode)
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts = {t: count(con, t) for t in tables}
    freelist = scalar(con, "PRAGMA freelist_count")
    rep.before = {"tables": len(tables), "freelist_pp": freelist or 0}
    for name, want in CANONICAL_POST.items():
        if name in counts:
            if mode != "dry-run":
                assert_eq(f"final count {name}", counts[name], want)
        elif mode != "dry-run":
            rep.errors.append(f"final count {name}: table missing")
    if mode != "dry-run":
        p.final_counts.parent.mkdir(parents=True, exist_ok=True)
        p.final_counts.write_text(
            f"# R2-A final counts ({now_iso()})\n"
            + "\n".join(f"{t}={counts[t]}" for t in tables)
            + f"\nfreelist_pages={freelist}\n"
        )
    rep.after = {k: counts.get(k, 0) for k in CANONICAL_POST if k in counts}
    return rep


ITEMS = {
    "d13": item_d13,
    "archive": item_archive,
    "split": item_split,
    "articles": item_articles,
    "collapse": item_collapse,
    "drop_imports": item_drop_imports,
    "import_log": item_import_log,
    "phase2_drops": item_phase2_drops,
    "indexes": item_indexes,
    "final_counts": item_final_counts,
}


# ── driver ──────────────────────────────────────────────────────────────────


def run(db: Path, mode: str, items: list[str], p: Paths) -> int:
    reports: list[ItemReport] = []
    ordered = [i for i in ITEM_ORDER if i in items]
    if mode != "apply":
        con = connect_ro(db)
        try:
            for name in ordered:
                try:
                    rep = ITEMS[name](con, mode, p)
                except R2AStop as e:
                    rep = ItemReport(name, mode)
                    rep.errors.append(str(e))
                rep.report()
                reports.append(rep)
        finally:
            con.close()
    else:
        if p.use_gates:
            try:
                fuser_clean(db)
                require_fresh_backup(db, p.backup_dir)
            except R2AStop as e:
                print(f"[gate] {e}", file=sys.stderr)
                return 2
        con = connect_rw(db)
        try:
            for name in ordered:
                try:
                    with write_batch(con):
                        rep = ITEMS[name](con, "apply", p)
                except R2AStop as e:
                    print(f"[{name}] STOP: {e}", file=sys.stderr)
                    rep = ItemReport(name, "apply")
                    rep.errors.append(str(e))
                    rep.report()
                    reports.append(rep)
                    continue
                rep.report()
                reports.append(rep)
        finally:
            con.close()
    bad = [r for r in reports if r.errors]
    if bad:
        print(f"{len(bad)} item(s) reported errors", file=sys.stderr)
        return 1
    return 0


def cmd_vacuum(p: Paths) -> int:
    """VACUUM LAST (plan §2 step 11): VACUUM INTO + integrity_check + atomic swap."""
    db = p.db
    if p.use_gates:
        try:
            fuser_clean(db)
        except R2AStop as e:
            print(f"[gate] {e}", file=sys.stderr)
            return 2
    tmp = db.with_suffix(".vacuum-tmp")
    if tmp.exists():
        tmp.unlink()
    # flush WAL into the main file so the pre-vacuum backup is self-contained
    con = connect_rw(db)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    con = sqlite3.connect(str(db))
    try:
        con.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        con.close()
    chk = sqlite3.connect(str(tmp)).execute("PRAGMA integrity_check").fetchone()[0]
    if chk != "ok":
        tmp.unlink(missing_ok=True)
        print(f"integrity_check failed: {chk} — abort, original untouched", file=sys.stderr)
        return 1
    stamp = now_iso().replace(":", "").replace("-", "").replace("+0000", "Z")
    backup = p.backup_dir / f"zolai.db.pre-vacuum-{stamp}"
    p.backup_dir.mkdir(parents=True, exist_ok=True)
    os.replace(db, backup)
    for side in (f"{db}-wal", f"{db}-shm"):
        sidep = Path(side)
        if sidep.exists():
            os.replace(sidep, backup.parent / (backup.name + sidep.suffix[-4:]))
    os.replace(tmp, db)
    rw = sqlite3.connect(str(db))
    try:
        rw.execute("PRAGMA journal_mode=WAL")
        chk2 = rw.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        rw.close()
    if chk2 != "ok":
        print(f"post-swap integrity_check failed: {chk2} — restore from {backup}", file=sys.stderr)
        return 1
    freelist = sqlite3.connect(str(db)).execute("PRAGMA freelist_count").fetchone()[0]
    print(f"VACUUM OK: swapped; pre-vacuum file at {backup}; freelist={freelist} pp")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["dry-run", "apply", "verify", "vacuum"])
    ap.add_argument("--item", action="append", choices=ITEM_ORDER, help="restrict to item(s), repeatable")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--archive-db", default=str(ARCHIVE_DB))
    ap.add_argument("--manifest", default=str(ARCHIVE_MANIFEST))
    ap.add_argument("--skiplist-zba", default=str(SKIPLIST_ZBA))
    ap.add_argument("--final-counts", default=str(FINAL_COUNTS))
    ap.add_argument("--backup-dir", default=str(BACKUP_DIR))
    ap.add_argument("--no-gates", action="store_true", help="skip fuser+backup gates (fixtures only)")
    ap.add_argument("--report", default=None, help="write JSON report here")
    args = ap.parse_args(argv)

    p = Paths(
        db=Path(args.db),
        archive_db=Path(args.archive_db),
        manifest=Path(args.manifest),
        skiplist_zba=Path(args.skiplist_zba),
        final_counts=Path(args.final_counts),
        backup_dir=Path(args.backup_dir),
        use_gates=not args.no_gates,
    )
    if not p.db.exists():
        print(f"DB not found: {p.db}", file=sys.stderr)
        return 2
    if args.mode == "vacuum":
        return cmd_vacuum(p)
    rc = run(p.db, args.mode, args.item or ITEM_ORDER, p)
    if args.report:
        Path(args.report).write_text(json.dumps({"mode": args.mode, "rc": rc, "at": now_iso()}, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
