#!/usr/bin/env python3
"""Phase 2B: Validate ALL 23 _v2 tables (12 core + 11 derived).

Checks: row counts, key uniqueness (where applicable), NULL checks, index existence.
"""
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"

# --- Core tables (12) ---
# (v2_table, source_table, key_col, description)
CORE_TABLES = [
    ("dictionary_v2", "dictionary", "zolai", "ZO→EN dictionary"),
    ("dictionary_en_zo_v2", "dictionary_en_zo", "headword", "EN→ZO dictionary"),
    ("bible_verses_v2", "bible_verses", "ref", "Bible verses"),
    ("grammar_patterns_v2", "grammar_patterns", "pattern_id", "Grammar patterns"),
    ("translations_v2", "translations", "source", "Translation pairs"),
    ("word_alignments_v2", "word_alignments", "ref", "Word alignments"),
    ("vocabulary_v2", "vocab", "headword", "Vocabulary index"),
    ("proverbs_v2", "proverbs", "zolai", "Proverbs"),
    ("phrases_v2", "phrases", "zolai", "Phrases"),
    ("word_usage_v2", "word_usage", "word", "Word usage profiles"),
    ("syllable_data_v2", "syllable_data", "word", "Syllable data"),
    ("word_collocations_v2", "word_collocations", "word1", "Word collocations"),
]

# --- Derived tables (11) ---
DERIVED_TABLES = [
    ("bible_analysis_v2", "bible_context", "book", "Bible analysis"),
    ("articles_v2", "articles", "id", "Articles"),
    ("songs_v2", "zolai_songs", "id", "Songs"),
    ("wiki_content_v2", "wiki_content", "source_path", "Wiki content"),
    ("data_audit_log_v2", "data_audit_log", "id", "Data audit log"),
    ("audit_findings_v2", "audit_findings", "id", "Audit findings"),
    ("provenance_v2", "provenance", "sha256", "Provenance"),
    ("import_log_v2", "jsonl_import_log", "batch_id", "Import log"),
    ("tone_sandhi_v2", "zolai_tone_sandhi", "id", "Tone sandhi rules"),
    ("tone_patterns_v2", "tone_patterns", "word", "Tone patterns"),
    ("training_runs_v2", "training_runs", "id", "Training runs"),
]


def validate_table(cur: sqlite3.Cursor, v2: str, source: str, key: str, desc: str) -> dict:
    """Validate one table. Returns result dict."""
    r: dict = {
        "v2": v2,
        "source": source,
        "desc": desc,
        "v2_count": 0,
        "source_count": 0,
        "key_duplicates": 0,
        "null_keys": 0,
        "has_version_col": False,
        "has_hash_col": False,
        "status": "ok",
        "errors": [],
        "warnings": [],
    }

    # Source count
    try:
        r["source_count"] = cur.execute(f"SELECT COUNT(*) FROM [{source}]").fetchone()[0]
    except Exception as e:
        r["errors"].append(f"Cannot read source {source}: {e}")

    # V2 count
    try:
        r["v2_count"] = cur.execute(f"SELECT COUNT(*) FROM [{v2}]").fetchone()[0]
    except Exception as e:
        r["errors"].append(f"Cannot read {v2}: {e}")
        r["status"] = "error"
        return r

    if r["v2_count"] == 0:
        r["errors"].append(f"{v2} is empty")
        r["status"] = "error"
        return r

    # Row count match
    if r["source_count"] != r["v2_count"]:
        r["warnings"].append(
            f"Row count mismatch: source={r['source_count']:,} vs v2={r['v2_count']:,}"
        )

    # Key duplicates
    try:
        r["key_duplicates"] = cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT [{key}], COUNT(*) AS cnt FROM [{v2}]
                GROUP BY [{key}] HAVING cnt > 1
            )
        """).fetchone()[0]
        if r["key_duplicates"] > 0:
            r["warnings"].append(f"{r['key_duplicates']:,} duplicate [{key}] values")
    except Exception as e:
        r["warnings"].append(f"Duplicate check failed: {e}")

    # NULL/empty keys
    try:
        r["null_keys"] = cur.execute(f"""
            SELECT COUNT(*) FROM [{v2}]
            WHERE [{key}] IS NULL OR [{key}] = ''
        """).fetchone()[0]
        if r["null_keys"] > 0:
            r["warnings"].append(f"{r['null_keys']:,} NULL/empty [{key}] values")
    except Exception as e:
        r["warnings"].append(f"NULL check failed: {e}")

    # version column
    try:
        r["has_version_col"] = cur.execute(
            f"SELECT COUNT(*) FROM [{v2}] WHERE version > 0"
        ).fetchone()[0] > 0
    except Exception:
        pass

    # content_hash column
    try:
        r["has_hash_col"] = cur.execute(f"""
            SELECT COUNT(*) FROM [{v2}]
            WHERE content_hash IS NOT NULL AND content_hash != ''
        """).fetchone()[0] > 0
    except Exception:
        pass

    if r["errors"]:
        r["status"] = "error"
    elif r["key_duplicates"] > 0 or r["null_keys"] > 0 or r["warnings"]:
        r["status"] = "warning"

    return r


def check_indexes(cur: sqlite3.Cursor) -> dict:
    """Check that expected indexes exist on _v2 tables."""
    result: dict = {"total": 0, "tables_with_indexes": 0, "indexes": []}
    rows = cur.execute("""
        SELECT tbl_name, name, sql FROM sqlite_master
        WHERE type='index' AND tbl_name LIKE '%_v2'
        ORDER BY tbl_name, name
    """).fetchall()
    result["total"] = len(rows)
    tables = set()
    for tbl, name, sql in rows:
        tables.add(tbl)
        result["indexes"].append({"table": tbl, "name": name})
    result["tables_with_indexes"] = len(tables)
    return result


def main() -> int:
    if not DB.exists():
        print(f"❌ Database not found: {DB}")
        return 1

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    all_tables = CORE_TABLES + DERIVED_TABLES

    print("Phase 2B Validation — ALL 23 _v2 tables")
    print("=" * 72)
    print(f"Database: {DB}")
    print(f"Tables: {len(all_tables)} (12 core + 11 derived)")
    print("=" * 72)

    all_errors: list[str] = []
    all_warnings: list[str] = []
    results: list[dict] = []

    for v2, source, key, desc in all_tables:
        r = validate_table(cur, v2, source, key, desc)
        results.append(r)
        all_errors.extend(r["errors"])
        all_warnings.extend(r["warnings"])

        icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}[r["status"]]
        print(f"\n{icon} {desc} ({r['v2']})")
        print(f"   Source: {r['source_count']:>10,}  │  V2: {r['v2_count']:>10,}")
        print(f"   Dups:   {r['key_duplicates']:>10,}  │  NULLs:  {r['null_keys']:>10,}")
        tags = []
        if r["has_version_col"]:
            tags.append("version✓")
        if r["has_hash_col"]:
            tags.append("hash✓")
        if tags:
            print(f"   Tags:   {', '.join(tags)}")
        for w in r["warnings"]:
            print(f"   ⚠️  {w}")
        for e in r["errors"]:
            print(f"   ❌ {e}")

    # Index check
    print("\n" + "=" * 72)
    print("Index Summary")
    idx = check_indexes(cur)
    print(f"  Total _v2 indexes: {idx['total']}")
    print(f"  Tables with indexes: {idx['tables_with_indexes']}/{len(all_tables)}")

    # Summary
    print("\n" + "=" * 72)
    total_v2 = sum(r["v2_count"] for r in results)
    total_src = sum(r["source_count"] for r in results)
    ok = sum(1 for r in results if r["status"] == "ok")
    warn = sum(1 for r in results if r["status"] == "warning")
    err = sum(1 for r in results if r["status"] == "error")

    print(f"Total source rows: {total_src:>10,}")
    print(f"Total V2 rows:     {total_v2:>10,}")
    print(f"Results: {ok} ok, {warn} warnings, {err} errors")

    if all_errors:
        print(f"\n❌ VALIDATION FAILED — {len(all_errors)} errors")
        for e in all_errors:
            print(f"   - {e}")
        success = False
    else:
        print(f"\n✅ VALIDATION PASSED ({len(all_warnings)} warnings)")
        success = True

    conn.close()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
