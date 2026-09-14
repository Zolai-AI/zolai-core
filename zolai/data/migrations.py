"""Database schema migrations for constraints and indexes.

Run this after init_db to add proper constraints and indexes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .database import DatabaseManager

CONSTRAINT_MIGRATIONS = [
    # Dictionary table constraints
    (
        "dictionary",
        "UNIQUE(zolai, source)",
        "ix_dict_zolai_source_unique",
        "Unique constraint on (zolai, source)",
    ),
    (
        "dictionary",
        "CHECK(zolai != '')",
        "ck_dict_zolai_not_empty",
        "Zolai headword cannot be empty",
    ),
    (
        "dictionary",
        "CHECK(english != '')",
        "ck_dict_english_not_empty",
        "English translation cannot be empty",
    ),
    # Dictionary EN-ZO table constraints
    (
        "dictionary_en_zo",
        "UNIQUE(headword)",
        "ix_en_zo_headword_unique",
        "Unique constraint on headword",
    ),
    (
        "dictionary_en_zo",
        "CHECK(headword != '')",
        "ck_en_zo_headword_not_empty",
        "Headword cannot be empty",
    ),
    # Bible verses constraints
    (
        "bible_verses",
        "UNIQUE(ref)",
        "ix_bible_ref_unique",
        "Unique constraint on verse reference",
    ),
    (
        "bible_verses",
        "CHECK(chapter > 0 AND verse > 0)",
        "ck_bible_chapter_verse_positive",
        "Chapter and verse must be positive",
    ),
    # Grammar patterns constraints
    (
        "grammar_patterns",
        "UNIQUE(pattern_id)",
        "ix_grammar_pattern_id_unique",
        "Unique constraint on pattern_id",
    ),
    (
        "grammar_patterns",
        "CHECK(frequency >= 0)",
        "ck_grammar_frequency_nonneg",
        "Frequency cannot be negative",
    ),
    # Phrases constraints
    (
        "phrases",
        "UNIQUE(zo)",
        "ix_phrases_zo_unique",
        "Unique constraint on Zolai phrase",
    ),
    (
        "phrases",
        "CHECK(frequency >= 0)",
        "ck_phrases_frequency_nonneg",
        "Frequency cannot be negative",
    ),
    # Vocabulary constraints
    (
        "vocabulary",
        "UNIQUE(headword)",
        "ix_vocabulary_headword_unique",
        "Unique constraint on headword",
    ),
    (
        "vocabulary",
        "CHECK(frequency >= 0)",
        "ck_vocabulary_frequency_nonneg",
        "Frequency cannot be negative",
    ),
    # Translations constraints
    (
        "translations",
        "CHECK(confidence >= 0.0 AND confidence <= 1.0)",
        "ck_trans_confidence_range",
        "Confidence must be 0.0-1.0",
    ),
    # Word usage constraints
    (
        "word_usage",
        "UNIQUE(word, book)",
        "ix_usage_word_book_unique",
        "Unique constraint on (word, book)",
    ),
    (
        "word_usage",
        "CHECK(total_freq >= 0)",
        "ck_usage_total_freq_nonneg",
        "Total frequency cannot be negative",
    ),
    # Provenance constraints
    (
        "provenance",
        "UNIQUE(filename)",
        "ix_provenance_filename_unique",
        "Unique constraint on filename",
    ),
    (
        "provenance",
        "CHECK(size_bytes >= 0)",
        "ck_provenance_size_nonneg",
        "File size cannot be negative",
    ),
    (
        "provenance",
        "CHECK(row_count >= 0)",
        "ck_provenance_rowcount_nonneg",
        "Row count cannot be negative",
    ),
    # Training exercises constraints
    (
        "training_exercises",
        "CHECK(difficulty IN ('easy', 'medium', 'hard'))",
        "ck_exercise_difficulty_valid",
        "Difficulty must be easy/medium/hard",
    ),
    # Word collocations constraints
    (
        "word_collocations",
        "CHECK(frequency >= 0)",
        "ck_colloc_frequency_nonneg",
        "Frequency cannot be negative",
    ),
    (
        "word_collocations",
        "CHECK(pmiproxy >= 0)",
        "ck_colloc_pmi_nonneg",
        "PMI proxy cannot be negative",
    ),
]

# Additional indexes for query performance
PERFORMANCE_INDEXES = [
    # Dictionary indexes
    ("dictionary", "CREATE INDEX IF NOT EXISTS ix_dict_english_clean ON dictionary(english_clean)"),
    ("dictionary", "CREATE INDEX IF NOT EXISTS ix_dict_myanmar ON dictionary(myanmar)"),
    ("dictionary", "CREATE INDEX IF NOT EXISTS ix_dict_pos ON dictionary(pos)"),
    ("dictionary", "CREATE INDEX IF NOT EXISTS ix_dict_source ON dictionary(source)"),
    # Dictionary EN-ZO indexes
    (
        "dictionary_en_zo",
        "CREATE INDEX IF NOT EXISTS ix_en_zo_translations_clean "
        "ON dictionary_en_zo(translations_clean)",
    ),
    ("dictionary_en_zo", "CREATE INDEX IF NOT EXISTS ix_en_zo_pos ON dictionary_en_zo(pos)"),
    (
        "dictionary_en_zo",
        "CREATE INDEX IF NOT EXISTS ix_en_zo_myanmar ON dictionary_en_zo(myanmar)",
    ),
    # Bible verses indexes
    ("bible_verses", "CREATE INDEX IF NOT EXISTS ix_bible_zo_tdb77 ON bible_verses(zo_tdb77)"),
    ("bible_verses", "CREATE INDEX IF NOT EXISTS ix_bible_zo_tedim2010 ON bible_verses(zo_tedim2010)"),
    ("bible_verses", "CREATE INDEX IF NOT EXISTS ix_bible_en_kjv ON bible_verses(en_kJV)"),
    ("bible_verses", "CREATE INDEX IF NOT EXISTS ix_bible_myanmar ON bible_verses(myanmar)"),
    # Grammar patterns indexes
    ("grammar_patterns", "CREATE INDEX IF NOT EXISTS ix_grammar_function ON grammar_patterns(function)"),
    ("grammar_patterns", "CREATE INDEX IF NOT EXISTS ix_grammar_book ON grammar_patterns(book)"),
    ("grammar_patterns", "CREATE INDEX IF NOT EXISTS ix_grammar_frequency ON grammar_patterns(frequency)"),
    # Phrases indexes
    ("phrases", "CREATE INDEX IF NOT EXISTS ix_phrases_english ON phrases(english)"),
    ("phrases", "CREATE INDEX IF NOT EXISTS ix_phrases_myanmar ON phrases(myanmar)"),
    ("phrases", "CREATE INDEX IF NOT EXISTS ix_phrases_frequency ON phrases(frequency)"),
    # Vocabulary indexes
    ("vocabulary", "CREATE INDEX IF NOT EXISTS ix_vocabulary_english ON vocabulary(english)"),
    ("vocabulary", "CREATE INDEX IF NOT EXISTS ix_vocabulary_frequency ON vocabulary(frequency)"),
    ("vocabulary", "CREATE INDEX IF NOT EXISTS ix_vocabulary_pos ON vocabulary(pos)"),
    ("vocabulary", "CREATE INDEX IF NOT EXISTS ix_vocabulary_book_count ON vocabulary(book_count)"),
    # Translations indexes
    ("translations", "CREATE INDEX IF NOT EXISTS ix_trans_source ON translations(source)"),
    ("translations", "CREATE INDEX IF NOT EXISTS ix_trans_target ON translations(target)"),
    ("translations", "CREATE INDEX IF NOT EXISTS ix_trans_confidence ON translations(confidence)"),
    ("translations", "CREATE INDEX IF NOT EXISTS ix_trans_reference ON translations(reference)"),
    # Word usage indexes
    ("word_usage", "CREATE INDEX IF NOT EXISTS ix_usage_total_freq ON word_usage(total_freq)"),
    ("word_usage", "CREATE INDEX IF NOT EXISTS ix_usage_books_found ON word_usage(books_found)"),
    # Word alignments indexes
    ("word_alignments", "CREATE INDEX IF NOT EXISTS ix_align_zolai ON word_alignments(zolai_word)"),
    ("word_alignments", "CREATE INDEX IF NOT EXISTS ix_align_english ON word_alignments(english_word)"),
    ("word_alignments", "CREATE INDEX IF NOT EXISTS ix_align_position ON word_alignments(position)"),
    # Word collocations indexes
    ("word_collocations", "CREATE INDEX IF NOT EXISTS ix_colloc_word1 ON word_collocations(word1)"),
    ("word_collocations", "CREATE INDEX IF NOT EXISTS ix_colloc_word2 ON word_collocations(word2)"),
    ("word_collocations", "CREATE INDEX IF NOT EXISTS ix_colloc_frequency ON word_collocations(frequency)"),
    ("word_collocations", "CREATE INDEX IF NOT EXISTS ix_colloc_pmi ON word_collocations(pmiproxy)"),
    # Training exercises indexes
    (
        "training_exercises",
        "CREATE INDEX IF NOT EXISTS ix_exercise_type_diff "
        "ON training_exercises(exercise_type, difficulty)",
    ),
    ("training_exercises", "CREATE INDEX IF NOT EXISTS ix_exercise_source ON training_exercises(source)"),
    # Provenance indexes
    ("provenance", "CREATE INDEX IF NOT EXISTS ix_prov_status ON provenance(status)"),
    ("provenance", "CREATE INDEX IF NOT EXISTS ix_prov_source ON provenance(source)"),
    ("provenance", "CREATE INDEX IF NOT EXISTS ix_prov_generator ON provenance(generator_script)"),
    # Audit log indexes
    ("data_audit_log", "CREATE INDEX IF NOT EXISTS idx_audit_changed_at ON data_audit_log(changed_at)"),
    ("data_audit_log", "CREATE INDEX IF NOT EXISTS idx_audit_reason ON data_audit_log(reason)"),
    # Bible analysis indexes
    ("bible_analysis", "CREATE INDEX IF NOT EXISTS ix_bible_analysis_chapter ON bible_analysis(chapter)"),
    ("bible_analysis", "CREATE INDEX IF NOT EXISTS ix_bible_analysis_type ON bible_analysis(analysis_type)"),
]


def apply_constraints(mgr: DatabaseManager) -> dict[str, Any]:
    """Apply all constraint migrations.

    Returns:
        Dict with 'applied', 'skipped', 'errors' lists.
    """
    applied = []
    skipped = []
    errors = []

    for table_name, constraint_sql, constraint_name, description in CONSTRAINT_MIGRATIONS:
        try:
            with mgr.engine.connect() as conn:
                # Check if constraint already exists
                if mgr._db_url.startswith("sqlite"):
                    check_sql = f"""
                        SELECT 1 FROM sqlite_master
                        WHERE type = 'index' AND name = '{constraint_name}'
                        UNION
                        SELECT 1 FROM sqlite_master
                        WHERE type = 'table' AND sql LIKE '%{constraint_name}%'
                    """
                else:
                    check_sql = f"""
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = '{constraint_name}'
                        AND table_name = '{table_name}'
                    """

                exists = conn.execute(text(check_sql)).first()

                if exists:
                    skipped.append(f"{table_name}.{constraint_name} (already exists)")
                    continue

                # Apply constraint
                if mgr._db_url.startswith("sqlite"):
                    # SQLite: constraints are added via ALTER TABLE or at creation
                    if constraint_sql.startswith("UNIQUE"):
                        cols = constraint_sql.replace("UNIQUE(", "").replace(")", "")
                        alter_sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {constraint_name} ON {table_name}({cols})"
                    if constraint_sql.startswith("CHECK"):
                        # SQLite doesn't support ALTER TABLE ADD CHECK easily
                        # Would need to recreate table - skip for now
                        skipped.append(
                            f"{table_name}.{constraint_name} "
                            "(CHECK not supported on existing SQLite tables)"
                        )
                        continue
                    else:
                        alter_sql = f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {constraint_sql}"
                else:
                    # PostgreSQL
                    alter_sql = f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {constraint_sql}"

                conn.execute(text(alter_sql))
                conn.commit()
                applied.append(f"{table_name}.{constraint_name}: {description}")

        except Exception as exc:
            errors.append(f"{table_name}.{constraint_name}: {exc}")

    return {"applied": applied, "skipped": skipped, "errors": errors}


def apply_indexes(mgr: DatabaseManager) -> dict[str, Any]:
    """Apply all performance indexes.

    Returns:
        Dict with 'applied', 'skipped', 'errors' lists.
    """
    applied = []
    skipped = []
    errors = []

    for table_name, index_sql in PERFORMANCE_INDEXES:
        try:
            with mgr.engine.connect() as conn:
                # Check if index exists
                if mgr._db_url.startswith("sqlite"):
                    idx_name = index_sql.split("INDEX IF NOT EXISTS ")[1].split(" ON")[0]
                    check_sql = f"SELECT 1 FROM sqlite_master WHERE type='index' AND name='{idx_name}'"
                else:
                    idx_name = index_sql.split("INDEX IF NOT EXISTS ")[1].split(" ON")[0]
                    check_sql = f"""
                        SELECT 1 FROM pg_indexes
                        WHERE indexname = '{idx_name}' AND tablename = '{table_name}'
                    """

                exists = conn.execute(text(check_sql)).first()

                if exists:
                    skipped.append(f"{table_name}.{idx_name} (already exists)")
                    continue

                conn.execute(text(index_sql))
                conn.commit()
                applied.append(f"{table_name}.{idx_name}")

        except Exception as exc:
            errors.append(f"{table_name}.{index_sql}: {exc}")

    return {"applied": applied, "skipped": skipped, "errors": errors}


def run_all_migrations(mgr: DatabaseManager) -> dict[str, Any]:
    """Run all constraint and index migrations.

    Returns:
        Combined results from constraints and indexes.
    """
    constraint_results = apply_constraints(mgr)
    index_results = apply_indexes(mgr)

    return {
        "constraints": constraint_results,
        "indexes": index_results,
    }


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Apply database constraints and indexes")
    parser.add_argument(
        "--db",
        default="sqlite:///zolai.db",
        help="Database URL",
    )
    args = parser.parse_args()

    mgr = DatabaseManager(args.db)
    mgr.init_db()

    print("Applying constraints...")
    constraint_results = apply_constraints(mgr)
    print(f"  Applied: {len(constraint_results['applied'])}")
    print(f"  Skipped: {len(constraint_results['skipped'])}")
    print(f"  Errors: {len(constraint_results['errors'])}")
    for err in constraint_results["errors"]:
        print(f"  ERROR: {err}")

    print("\nApplying indexes...")
    index_results = apply_indexes(mgr)
    print(f"  Applied: {len(index_results['applied'])}")
    print(f"  Skipped: {len(index_results['skipped'])}")
    print(f"  Errors: {len(index_results['errors'])}")
    for err in index_results["errors"]:
        print(f"  ERROR: {err}")

    mgr.dispose()


if __name__ == "__main__":
    main()
