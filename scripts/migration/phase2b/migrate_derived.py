#!/usr/bin/env python3
"""Phase 2B: Migrate data from source tables to 11 derived _v2 tables.

NON-DESTRUCTIVE: creates new _v2 tables alongside originals.
Includes SHA256 content hashing for integrity verification.
Must run create_derived_tables.sql first to create the target tables.
"""
import hashlib
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"


def sha256_hex(*values: str) -> str:
    """Compute SHA256 hex digest from a sequence of string values."""
    content = "|".join(v if v else "" for v in values)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------- Migration functions ----------
# Each returns the row count inserted.


def migrate_bible_analysis(conn: sqlite3.Connection) -> int:
    """bible_context → bible_analysis_v2"""
    print("Migrating bible_context → bible_analysis_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bible_analysis_v2 (
            book, chapter, analysis_type, data,
            import_batch_id, source_file, content_hash
        )
        SELECT
            book, chapter, analysis_type, data,
            import_batch_id, source_file,
            hex(sha256(
                COALESCE(book,'') || '|' ||
                COALESCE(CAST(chapter AS TEXT),'') || '|' ||
                COALESCE(analysis_type,'') || '|' ||
                COALESCE(data,'')
            ))
        FROM bible_context
    """)
    n = cur.execute("SELECT COUNT(*) FROM bible_analysis_v2").fetchone()[0]
    print(f"  ✅ bible_analysis_v2: {n:,} rows")
    return n


def migrate_articles(conn: sqlite3.Connection) -> int:
    """articles → articles_v2"""
    print("Migrating articles → articles_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO articles_v2 (
            title, content, excerpt, categories, date, link, language,
            import_batch_id, source_file, content_hash
        )
        SELECT
            title, content, excerpt, categories, date, link, language,
            import_batch_id, source_file,
            hex(sha256(
                COALESCE(title,'') || '|' ||
                COALESCE(content,'') || '|' ||
                COALESCE(link,'')
            ))
        FROM articles
    """)
    n = cur.execute("SELECT COUNT(*) FROM articles_v2").fetchone()[0]
    print(f"  ✅ articles_v2: {n:,} rows")
    return n


def migrate_songs(conn: sqlite3.Connection) -> int:
    """zolai_songs → songs_v2"""
    print("Migrating zolai_songs → songs_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO songs_v2 (
            collection, song_number, title, text, source,
            import_batch_id, source_file, content_hash
        )
        SELECT
            collection, song_number, title, text, source,
            import_batch_id, source_file,
            hex(sha256(
                COALESCE(collection,'') || '|' ||
                COALESCE(CAST(song_number AS TEXT),'') || '|' ||
                COALESCE(title,'')
            ))
        FROM zolai_songs
    """)
    n = cur.execute("SELECT COUNT(*) FROM songs_v2").fetchone()[0]
    print(f"  ✅ songs_v2: {n:,} rows")
    return n


def migrate_wiki_content(conn: sqlite3.Connection) -> int:
    """wiki_content → wiki_content_v2"""
    print("Migrating wiki_content → wiki_content_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wiki_content_v2 (
            wiki_category, source_path, title, content,
            word_count, section_count, content_hash,
            entry_version,
            import_batch_id, source_file
        )
        SELECT
            wiki_category, source_path, title, content,
            word_count, section_count, content_hash,
            entry_version,
            import_batch_id, source_file
        FROM wiki_content
    """)
    n = cur.execute("SELECT COUNT(*) FROM wiki_content_v2").fetchone()[0]
    print(f"  ✅ wiki_content_v2: {n:,} rows")
    return n


def migrate_data_audit_log(conn: sqlite3.Connection) -> int:
    """data_audit_log → data_audit_log_v2"""
    print("Migrating data_audit_log → data_audit_log_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO data_audit_log_v2 (
            table_name, row_id, field, old_value, new_value,
            changed_at, reason, content_hash
        )
        SELECT
            table_name, row_id, field, old_value, new_value,
            changed_at, reason,
            hex(sha256(
                COALESCE(table_name,'') || '|' ||
                COALESCE(CAST(row_id AS TEXT),'') || '|' ||
                COALESCE(field,'') || '|' ||
                COALESCE(reason,'')
            ))
        FROM data_audit_log
    """)
    n = cur.execute("SELECT COUNT(*) FROM data_audit_log_v2").fetchone()[0]
    print(f"  ✅ data_audit_log_v2: {n:,} rows")
    return n


def migrate_audit_findings(conn: sqlite3.Connection) -> int:
    """audit_findings → audit_findings_v2"""
    print("Migrating audit_findings → audit_findings_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_findings_v2 (
            finding_type, word, old_value, new_value, source,
            confidence, verified_by, entry_id,
            import_batch_id, source_file, content_hash
        )
        SELECT
            finding_type, word, old_value, new_value, source,
            confidence, verified_by, entry_id,
            import_batch_id, source_file,
            hex(sha256(
                COALESCE(finding_type,'') || '|' ||
                COALESCE(word,'') || '|' ||
                COALESCE(source,'')
            ))
        FROM audit_findings
    """)
    n = cur.execute("SELECT COUNT(*) FROM audit_findings_v2").fetchone()[0]
    print(f"  ✅ audit_findings_v2: {n:,} rows")
    return n


def migrate_provenance(conn: sqlite3.Connection) -> int:
    """provenance → provenance_v2"""
    print("Migrating provenance → provenance_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO provenance_v2 (
            filename, size_bytes, sha256, row_count, source,
            generator_script, version, status, updated_at, change_log,
            content_hash
        )
        SELECT
            filename, size_bytes, sha256, row_count, source,
            generator_script, version, status, updated_at, change_log,
            hex(sha256(
                COALESCE(filename,'') || '|' ||
                COALESCE(sha256,'') || '|' ||
                COALESCE(source,'')
            ))
        FROM provenance
    """)
    n = cur.execute("SELECT COUNT(*) FROM provenance_v2").fetchone()[0]
    print(f"  ✅ provenance_v2: {n:,} rows")
    return n


def migrate_import_log(conn: sqlite3.Connection) -> int:
    """jsonl_import_log → import_log_v2"""
    print("Migrating jsonl_import_log → import_log_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO import_log_v2 (
            batch_id, source_file, table_name, rows_imported, sha256,
            imported_at, version, status, error_message,
            content_hash
        )
        SELECT
            batch_id, source_file, table_name, rows_imported, sha256,
            imported_at, version, status, error_message,
            hex(sha256(
                COALESCE(batch_id,'') || '|' ||
                COALESCE(source_file,'') || '|' ||
                COALESCE(table_name,'')
            ))
        FROM jsonl_import_log
    """)
    n = cur.execute("SELECT COUNT(*) FROM import_log_v2").fetchone()[0]
    print(f"  ✅ import_log_v2: {n:,} rows")
    return n


def migrate_tone_sandhi(conn: sqlite3.Connection) -> int:
    """zolai_tone_sandhi → tone_sandhi_v2"""
    print("Migrating zolai_tone_sandhi → tone_sandhi_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tone_sandhi_v2 (
            rule_number, rule_name, underlying_pattern, surface_pattern,
            condition, examples, domain, source_category, source_file,
            import_batch_id, content_hash
        )
        SELECT
            rule_number, rule_name, underlying_pattern, surface_pattern,
            condition, examples, domain, source_category, source_file,
            import_batch_id,
            hex(sha256(
                COALESCE(rule_number,'') || '|' ||
                COALESCE(rule_name,'') || '|' ||
                COALESCE(underlying_pattern,'')
            ))
        FROM zolai_tone_sandhi
    """)
    n = cur.execute("SELECT COUNT(*) FROM tone_sandhi_v2").fetchone()[0]
    print(f"  ✅ tone_sandhi_v2: {n:,} rows")
    return n


def migrate_tone_patterns(conn: sqlite3.Connection) -> int:
    """tone_patterns → tone_patterns_v2"""
    print("Migrating tone_patterns → tone_patterns_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tone_patterns_v2 (
            word, tone_category, meaning_t1, meaning_t3, meaning_t4,
            sandhi_rules, source_category, source_file,
            import_batch_id, content_hash
        )
        SELECT
            word, tone_category, meaning_t1, meaning_t3, meaning_t4,
            sandhi_rules, source_category, source_file,
            import_batch_id,
            hex(sha256(
                COALESCE(word,'') || '|' ||
                COALESCE(tone_category,'') || '|' ||
                COALESCE(sandhi_rules,'')
            ))
        FROM tone_patterns
    """)
    n = cur.execute("SELECT COUNT(*) FROM tone_patterns_v2").fetchone()[0]
    print(f"  ✅ tone_patterns_v2: {n:,} rows")
    return n


def migrate_training_runs(conn: sqlite3.Connection) -> int:
    """training_runs → training_runs_v2"""
    print("Migrating training_runs → training_runs_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO training_runs_v2 (
            model_name, dataset_name, entry_count, entry_version,
            update_remarks, metrics_json, status, completed_at,
            test_type, test_date, total_tests, passed_tests, score,
            details, import_batch_id, source_file, content_hash
        )
        SELECT
            model_name, dataset_name, entry_count, entry_version,
            update_remarks, metrics_json, status, completed_at,
            test_type, test_date, total_tests, passed_tests, score,
            details, import_batch_id, source_file,
            hex(sha256(
                COALESCE(model_name,'') || '|' ||
                COALESCE(dataset_name,'') || '|' ||
                COALESCE(status,'')
            ))
        FROM training_runs
    """)
    n = cur.execute("SELECT COUNT(*) FROM training_runs_v2").fetchone()[0]
    print(f"  ✅ training_runs_v2: {n:,} rows")
    return n


# ---------- Main ----------

MIGRATIONS = [
    ("bible_analysis_v2", migrate_bible_analysis),
    ("articles_v2", migrate_articles),
    ("songs_v2", migrate_songs),
    ("wiki_content_v2", migrate_wiki_content),
    ("data_audit_log_v2", migrate_data_audit_log),
    ("audit_findings_v2", migrate_audit_findings),
    ("provenance_v2", migrate_provenance),
    ("import_log_v2", migrate_import_log),
    ("tone_sandhi_v2", migrate_tone_sandhi),
    ("tone_patterns_v2", migrate_tone_patterns),
    ("training_runs_v2", migrate_training_runs),
]


def main() -> int:
    if not DB.exists():
        print(f"❌ Database not found: {DB}")
        return 1

    print(f"Phase 2B Derived Table Migration — {DB}")
    print("=" * 60)

    conn = sqlite3.connect(str(DB))

    # Register Python SHA256 fallback if SQLite lacks it
    try:
        conn.execute("SELECT sha256('test')")
        print("✅ SHA256 function available\n")
    except Exception:
        conn.create_function(
            "sha256", 1,
            lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest(),
        )
        print("✅ Python SHA256 fallback registered\n")

    try:
        counts: dict[str, int] = {}
        for table_name, fn in MIGRATIONS:
            counts[table_name] = fn(conn)
        conn.commit()

        print("\n" + "=" * 60)
        print("✅ Derived table migration complete!")
        print(f"\n{'Table':<30} {'Rows':>12}")
        print("-" * 44)
        total = 0
        for table, count in sorted(counts.items()):
            print(f"  {table:<28} {count:>12,}")
            total += count
        print("-" * 44)
        print(f"  {'TOTAL':<28} {total:>12,}")
        return 0

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
