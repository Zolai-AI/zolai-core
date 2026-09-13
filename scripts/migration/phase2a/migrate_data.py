#!/usr/bin/env python3
"""Phase 2A: Migrate data from source tables to _v2 canonical tables.

NON-DESTRUCIVE: creates new _v2 tables alongside originals.
Includes content hashing for integrity verification.
"""
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

# Resolve DB path relative to this script (4 levels up to workspace root)
SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR.parent.parent.parent.parent / "data" / "zolai.db"


def sha256_hex(*values) -> str:
    """Compute SHA256 hex digest from a sequence of values."""
    content = "|".join(str(v) if v is not None else "" for v in values)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def migrate_dictionary(conn: sqlite3.Connection) -> int:
    """Migrate dictionary → dictionary_v2 with content hashing."""
    print("Migrating dictionary → dictionary_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dictionary_v2 (
            zolai, english, english_clean, myanmar, pos, source,
            entry_version, update_remarks, update_description,
            zvs_compliance_status, is_deleted, deleted_at,
            content_hash
        )
        SELECT
            zolai, english, english_clean, myanmar, pos, source,
            entry_version, update_remarks, update_description,
            zvs_compliance_status, is_deleted, deleted_at,
            hex(sha256(
                COALESCE(zolai,'') || '|' ||
                COALESCE(english,'') || '|' ||
                COALESCE(myanmar,'') || '|' ||
                COALESCE(pos,'') || '|' ||
                COALESCE(source,'')
            ))
        FROM dictionary
    """)
    count = cur.execute("SELECT COUNT(*) FROM dictionary_v2").fetchone()[0]
    print(f"  ✅ dictionary_v2: {count:,} rows")
    return count


def migrate_dictionary_en_zo(conn: sqlite3.Connection) -> int:
    """Migrate dictionary_en_zo → dictionary_en_zo_v2."""
    print("Migrating dictionary_en_zo → dictionary_en_zo_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dictionary_en_zo_v2 (
            headword, translations, translations_clean, pos, source, myanmar,
            entry_version, update_remarks, update_description,
            zvs_compliance_status, is_deleted, deleted_at,
            content_hash
        )
        SELECT
            headword, translations, translations_clean, pos, source, myanmar,
            entry_version, update_remarks, update_description,
            zvs_compliance_status, is_deleted, deleted_at,
            hex(sha256(
                COALESCE(headword,'') || '|' ||
                COALESCE(translations,'') || '|' ||
                COALESCE(pos,'') || '|' ||
                COALESCE(source,'')
            ))
        FROM dictionary_en_zo
    """)
    count = cur.execute("SELECT COUNT(*) FROM dictionary_en_zo_v2").fetchone()[0]
    print(f"  ✅ dictionary_en_zo_v2: {count:,} rows")
    return count


def migrate_bible_verses(conn: sqlite3.Connection) -> int:
    """Migrate bible_verses → bible_verses_v2."""
    print("Migrating bible_verses → bible_verses_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bible_verses_v2 (
            ref, book, chapter, verse,
            zo_tdb77, zo_tedim2010, en_kJV, myanmar,
            zo_tedim1932, zo_hcl06, zo_fcl, myanmar_judson, book_name,
            content_hash
        )
        SELECT
            ref, book, chapter, verse,
            zo_tdb77, zo_tedim2010, en_kJV, myanmar,
            zo_tedim1932, zo_hcl06, zo_fcl, myanmar_judson, book_name,
            hex(sha256(
                COALESCE(ref,'') || '|' ||
                COALESCE(zo_tdb77,'') || '|' ||
                COALESCE(en_kJV,'')
            ))
        FROM bible_verses
    """)
    count = cur.execute("SELECT COUNT(*) FROM bible_verses_v2").fetchone()[0]
    print(f"  ✅ bible_verses_v2: {count:,} rows")
    return count


def migrate_grammar_patterns(conn: sqlite3.Connection) -> int:
    """Migrate grammar_patterns → grammar_patterns_v2."""
    print("Migrating grammar_patterns → grammar_patterns_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO grammar_patterns_v2 (
            pattern_id, pattern, description, function, examples, frequency,
            myanmar,
            content_hash
        )
        SELECT
            pattern_id, pattern, description, function, examples, frequency,
            myanmar,
            hex(sha256(
                COALESCE(pattern_id,'') || '|' ||
                COALESCE(pattern,'') || '|' ||
                COALESCE(function,'') || '|' ||
                COALESCE(examples,'')
            ))
        FROM grammar_patterns
    """)
    count = cur.execute("SELECT COUNT(*) FROM grammar_patterns_v2").fetchone()[0]
    print(f"  ✅ grammar_patterns_v2: {count:,} rows")
    return count


def migrate_translations(conn: sqlite3.Connection) -> int:
    """Migrate translations → translations_v2."""
    print("Migrating translations → translations_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO translations_v2 (
            source, target, direction, reference, confidence,
            myanmar,
            content_hash
        )
        SELECT
            source, target, direction, reference, confidence,
            myanmar,
            hex(sha256(
                COALESCE(source,'') || '|' ||
                COALESCE(target,'') || '|' ||
                COALESCE(direction,'') || '|' ||
                COALESCE(reference,'')
            ))
        FROM translations
    """)
    count = cur.execute("SELECT COUNT(*) FROM translations_v2").fetchone()[0]
    print(f"  ✅ translations_v2: {count:,} rows")
    return count


def migrate_word_alignments(conn: sqlite3.Connection) -> int:
    """Migrate word_alignments → word_alignments_v2."""
    print("Migrating word_alignments → word_alignments_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO word_alignments_v2 (
            ref, zolai_word, english_word, position,
            myanmar,
            content_hash
        )
        SELECT
            ref, zolai_word, english_word, position,
            myanmar,
            hex(sha256(
                COALESCE(ref,'') || '|' ||
                COALESCE(zolai_word,'') || '|' ||
                COALESCE(english_word,'')
            ))
        FROM word_alignments
    """)
    count = cur.execute("SELECT COUNT(*) FROM word_alignments_v2").fetchone()[0]
    print(f"  ✅ word_alignments_v2: {count:,} rows")
    return count


def migrate_vocab(conn: sqlite3.Connection) -> int:
    """Migrate vocab → vocabulary_v2."""
    print("Migrating vocab → vocabulary_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO vocabulary_v2 (
            headword, english, frequency, books, examples,
            myanmar,
            content_hash
        )
        SELECT
            headword, english, frequency, books, examples,
            myanmar,
            hex(sha256(
                COALESCE(headword,'') || '|' ||
                COALESCE(english,'') || '|' ||
                COALESCE(CAST(frequency AS TEXT),'')
            ))
        FROM vocab
    """)
    count = cur.execute("SELECT COUNT(*) FROM vocabulary_v2").fetchone()[0]
    print(f"  ✅ vocabulary_v2: {count:,} rows")
    return count


def migrate_proverbs(conn: sqlite3.Connection) -> int:
    """Migrate proverbs → proverbs_v2."""
    print("Migrating proverbs → proverbs_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO proverbs_v2 (
            zolai, english, source, category,
            content_hash
        )
        SELECT
            zolai, english, source, category,
            hex(sha256(
                COALESCE(zolai,'') || '|' ||
                COALESCE(english,'')
            ))
        FROM proverbs
    """)
    count = cur.execute("SELECT COUNT(*) FROM proverbs_v2").fetchone()[0]
    print(f"  ✅ proverbs_v2: {count:,} rows")
    return count


def migrate_phrases(conn: sqlite3.Connection) -> int:
    """Migrate phrases → phrases_v2 (renames zo→zolai for consistency)."""
    print("Migrating phrases → phrases_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO phrases_v2 (
            zolai, english, frequency, examples,
            content_hash
        )
        SELECT
            zo, english, frequency, examples,
            hex(sha256(
                COALESCE(zo,'') || '|' ||
                COALESCE(english,'')
            ))
        FROM phrases
    """)
    count = cur.execute("SELECT COUNT(*) FROM phrases_v2").fetchone()[0]
    print(f"  ✅ phrases_v2: {count:,} rows")
    return count


def migrate_word_usage(conn: sqlite3.Connection) -> int:
    """Migrate word_usage → word_usage_v2."""
    print("Migrating word_usage → word_usage_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO word_usage_v2 (
            word, book, total_freq, meaning_shifts, co_occurring_words,
            myanmar,
            content_hash
        )
        SELECT
            word, book, total_freq, meaning_shifts, co_occurring_words,
            myanmar,
            hex(sha256(
                COALESCE(word,'') || '|' ||
                COALESCE(book,'') || '|' ||
                COALESCE(CAST(total_freq AS TEXT),'')
            ))
        FROM word_usage
    """)
    count = cur.execute("SELECT COUNT(*) FROM word_usage_v2").fetchone()[0]
    print(f"  ✅ word_usage_v2: {count:,} rows")
    return count


def migrate_syllable_data(conn: sqlite3.Connection) -> int:
    """Migrate syllable_data → syllable_data_v2."""
    print("Migrating syllable_data → syllable_data_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO syllable_data_v2 (
            word, syllables, syllable_count, engine, confidence,
            source_table, source_id, status,
            content_hash
        )
        SELECT
            word, syllables, syllable_count, engine, confidence,
            source_table, source_id, status,
            hex(sha256(
                COALESCE(word,'') || '|' ||
                COALESCE(syllables,'') || '|' ||
                COALESCE(engine,'')
            ))
        FROM syllable_data
    """)
    count = cur.execute("SELECT COUNT(*) FROM syllable_data_v2").fetchone()[0]
    print(f"  ✅ syllable_data_v2: {count:,} rows")
    return count


def migrate_word_collocations(conn: sqlite3.Connection) -> int:
    """Migrate word_collocations → word_collocations_v2."""
    print("Migrating word_collocations → word_collocations_v2 ...")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO word_collocations_v2 (
            word1, word2, frequency, pmiproxy,
            myanmar,
            content_hash
        )
        SELECT
            word1, word2, frequency, pmiproxy,
            myanmar,
            hex(sha256(
                COALESCE(word1,'') || '|' ||
                COALESCE(word2,'') || '|' ||
                COALESCE(CAST(frequency AS TEXT),'')
            ))
        FROM word_collocations
    """)
    count = cur.execute("SELECT COUNT(*) FROM word_collocations_v2").fetchone()[0]
    print(f"  ✅ word_collocations_v2: {count:,} rows")
    return count


def main():
    if not DB.exists():
        print(f"❌ Database not found: {DB}")
        sys.exit(1)

    print(f"Phase 2A Migration — {DB}")
    print("=" * 60)

    conn = sqlite3.connect(str(DB))

    # Enable SHA256 extension (available in SQLite 3.45+)
    try:
        conn.enable_load_extension(True)
        # Try sqlite3 sha256 built-in (SQLite ≥ 3.45.0)
        conn.execute("SELECT sha256('test')")
        print("✅ SHA256 function available\n")
    except Exception:
        print("⚠️  sha256() not available — will use Python hashing")
        # Fallback: monkey-patch sha256 SQL function via Python
        _sha256 = lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()
        conn.create_function("sha256", 1, _sha256)
        print("✅ Python SHA256 fallback registered\n")

    try:
        counts: dict[str, int] = {}
        counts["dictionary_v2"] = migrate_dictionary(conn)
        counts["dictionary_en_zo_v2"] = migrate_dictionary_en_zo(conn)
        counts["bible_verses_v2"] = migrate_bible_verses(conn)
        counts["grammar_patterns_v2"] = migrate_grammar_patterns(conn)
        counts["translations_v2"] = migrate_translations(conn)
        counts["word_alignments_v2"] = migrate_word_alignments(conn)
        counts["vocabulary_v2"] = migrate_vocab(conn)
        counts["proverbs_v2"] = migrate_proverbs(conn)
        counts["phrases_v2"] = migrate_phrases(conn)
        counts["word_usage_v2"] = migrate_word_usage(conn)
        counts["syllable_data_v2"] = migrate_syllable_data(conn)
        counts["word_collocations_v2"] = migrate_word_collocations(conn)

        conn.commit()

        print("\n" + "=" * 60)
        print("✅ Migration complete!")
        print(f"\n{'Table':<30} {'Rows':>12}")
        print("-" * 44)
        total = 0
        for table, count in sorted(counts.items()):
            print(f"  {table:<28} {count:>12,}")
            total += count
        print("-" * 44)
        print(f"  {'TOTAL':<28} {total:>12,}")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
