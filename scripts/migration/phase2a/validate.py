#!/usr/bin/env python3
"""Phase 2A: Validate migration results.

Checks row counts, key uniqueness, NULL keys, and content hash integrity.
"""
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"

# (source_table, target_table, key_column, description)
VALIDATIONS = [
    ("dictionary", "dictionary_v2", "zolai", "ZO→EN dictionary"),
    ("dictionary_en_zo", "dictionary_en_zo_v2", "headword", "EN→ZO dictionary"),
    ("bible_verses", "bible_verses_v2", "ref", "Bible verses"),
    ("grammar_patterns", "grammar_patterns_v2", "pattern_id", "Grammar patterns"),
    ("translations", "translations_v2", "source", "Translation pairs"),
    ("word_alignments", "word_alignments_v2", "ref", "Word alignments"),
    ("vocab", "vocabulary_v2", "headword", "Vocabulary index"),
    ("proverbs", "proverbs_v2", "zolai", "Proverbs"),
    ("phrases", "phrases_v2", "zolai", "Phrases"),
    ("word_usage", "word_usage_v2", "word", "Word usage profiles"),
    ("syllable_data", "syllable_data_v2", "word", "Syllable data"),
    ("word_collocations", "word_collocations_v2", "word1", "Word collocations"),
]


def validate_table(cur, source: str, target: str, key_col: str, desc: str) -> dict:
    """Validate one migration pair. Returns dict with counts + status."""
    result = {
        "source": source,
        "target": target,
        "desc": desc,
        "source_count": 0,
        "target_count": 0,
        "key_duplicates": 0,
        "null_keys": 0,
        "status": "ok",
        "errors": [],
        "warnings": [],
    }

    try:
        result["source_count"] = cur.execute(
            f"SELECT COUNT(*) FROM [{source}]"
        ).fetchone()[0]
    except Exception as e:
        result["errors"].append(f"Cannot read {source}: {e}")
        result["status"] = "error"
        return result

    try:
        result["target_count"] = cur.execute(
            f"SELECT COUNT(*) FROM [{target}]"
        ).fetchone()[0]
    except Exception as e:
        result["errors"].append(f"Cannot read {target}: {e}")
        result["status"] = "error"
        return result

    # Check for empty target
    if result["target_count"] == 0:
        result["errors"].append(f"{target} is empty")
        result["status"] = "error"
        return result

    # Check key duplicates
    try:
        result["key_duplicates"] = cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT [{key_col}], COUNT(*) as cnt
                FROM [{target}]
                GROUP BY [{key_col}]
                HAVING cnt > 1
            )
        """).fetchone()[0]
        if result["key_duplicates"] > 0:
            result["warnings"].append(
                f"{result['key_duplicates']:,} duplicate [{key_col}] values"
            )
    except Exception as e:
        result["warnings"].append(f"Could not check duplicates: {e}")

    # Check NULL/empty keys
    try:
        result["null_keys"] = cur.execute(f"""
            SELECT COUNT(*) FROM [{target}]
            WHERE [{key_col}] IS NULL OR [{key_col}] = ''
        """).fetchone()[0]
        if result["null_keys"] > 0:
            result["warnings"].append(
                f"{result['null_keys']:,} NULL/empty [{key_col}] values"
            )
    except Exception as e:
        result["warnings"].append(f"Could not check NULL keys: {e}")

    # Check content_hash column exists and has values
    try:
        hash_count = cur.execute(f"""
            SELECT COUNT(*) FROM [{target}]
            WHERE content_hash IS NOT NULL AND content_hash != ''
        """).fetchone()[0]
        if hash_count < result["target_count"]:
            result["warnings"].append(
                f"Only {hash_count:,}/{result['target_count']:,} rows have content_hash"
            )
    except Exception:
        pass  # content_hash might not exist in some tables

    # Check version column exists and has values
    try:
        version_count = cur.execute(f"""
            SELECT COUNT(*) FROM [{target}] WHERE version > 0
        """).fetchone()[0]
    except Exception:
        pass

    if result["errors"]:
        result["status"] = "error"
    elif result["key_duplicates"] > 0 or result["null_keys"] > 0:
        result["status"] = "warning"

    return result


def main():
    if not DB.exists():
        print(f"❌ Database not found: {DB}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    print("Phase 2A Validation")
    print("=" * 70)
    print(f"Database: {DB}")
    print(f"Tables: {len(VALIDATIONS)}")
    print("=" * 70)

    all_errors = []
    all_warnings = []
    results = []

    for source, target, key_col, desc in VALIDATIONS:
        r = validate_table(cur, source, target, key_col, desc)
        results.append(r)
        all_errors.extend(r["errors"])
        all_warnings.extend(r["warnings"])

        icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}[r["status"]]
        print(f"\n{icon} {desc}")
        print(f"   Source: {r['source_count']:>10,}  │  Target: {r['target_count']:>10,}")
        print(f"   Dups:   {r['key_duplicates']:>10,}  │  NULLs:  {r['null_keys']:>10,}")
        for w in r["warnings"]:
            print(f"   ⚠️  {w}")
        for e in r["errors"]:
            print(f"   ❌ {e}")

    # Summary
    print("\n" + "=" * 70)
    total_source = sum(r["source_count"] for r in results)
    total_target = sum(r["target_count"] for r in results)
    ok_count = sum(1 for r in results if r["status"] == "ok")
    warn_count = sum(1 for r in results if r["status"] == "warning")
    err_count = sum(1 for r in results if r["status"] == "error")

    print(f"Total source rows: {total_source:>10,}")
    print(f"Total target rows: {total_target:>10,}")
    print(f"Results: {ok_count} ok, {warn_count} warnings, {err_count} errors")

    if all_errors:
        print(f"\n❌ VALIDATION FAILED — {len(all_errors)} errors")
        for e in all_errors:
            print(f"   - {e}")
        success = False
    else:
        print(f"\n✅ VALIDATION PASSED ({len(all_warnings)} warnings)")
        success = True

    conn.close()
    return success


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
