"""Database schema migrations for constraints and indexes.

Run this after init_db to add proper constraints and indexes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .database import DatabaseManager
from .models import Base


def create_foundation_tables(mgr: DatabaseManager) -> dict[str, Any]:
    """Create all Foundation layer tables (Phase B).

    This creates the 13 Foundation tables:
    - Raw Layer: foundation_raw_corpus, foundation_raw_llm
    - Staging Layer: foundation_staging_words, foundation_staging_sentences,
      foundation_staging_paragraphs, foundation_staging_evidence
    - Canonical Layer: canonical_words, canonical_sentences, canonical_paragraphs
    - Evidence/Consensus: foundation_evidence, foundation_verifications, foundation_consensus
    - Meta: foundation_batches, foundation_review_queue, foundation_metrics

    Returns:
        Dict with 'created', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    foundation_tables = [
        "foundation_raw_corpus",
        "foundation_raw_llm",
        "foundation_staging_words",
        "foundation_staging_sentences",
        "foundation_staging_paragraphs",
        "foundation_staging_evidence",
        "canonical_words",
        "canonical_sentences",
        "canonical_paragraphs",
        "foundation_evidence",
        "foundation_verifications",
        "foundation_consensus",
        "foundation_batches",
        "foundation_review_queue",
        "foundation_metrics",
    ]

    created = []
    skipped = []
    errors = []

    # Create tables that don't exist yet
    for table_name in foundation_tables:
        if table_name in existing_tables:
            skipped.append(f"{table_name} (already exists)")
            continue

    if not skipped or len(skipped) != len(foundation_tables):
        # Some tables need to be created - use SQLAlchemy's create_all
        try:
            # Get the metadata for only foundation tables
            foundation_metadata = Base.metadata
            tables_to_create = [
                foundation_metadata.tables[t]
                for t in foundation_tables
                if t in foundation_metadata.tables and t not in existing_tables
            ]
            if tables_to_create:
                foundation_metadata.create_all(mgr.engine, tables=tables_to_create)
                for t in tables_to_create:
                    created.append(t.name)
        except Exception as exc:
            errors.append(f"create_all: {exc}")

    return {"created": created, "skipped": skipped, "errors": errors}


# Constraint migrations for Foundation tables
FOUNDATION_CONSTRAINT_MIGRATIONS = [
    # foundation_raw_corpus
    (
        "foundation_raw_corpus",
        "UNIQUE(content_hash)",
        "ix_fraw_content_hash_unique",
        "Unique constraint on content_hash",
    ),
    (
        "foundation_raw_corpus",
        "CHECK(source_type != '')",
        "ck_fraw_source_type_not_empty",
        "Source type cannot be empty",
    ),
    # foundation_raw_llm
    (
        "foundation_raw_llm",
        "CHECK(model != '')",
        "ck_frawllm_model_not_empty",
        "Model cannot be empty",
    ),
    # foundation_staging_words
    (
        "foundation_staging_words",
        "UNIQUE(form, source_hash)",
        "ix_fstg_word_form_source_unique",
        "Unique constraint on (form, source_hash)",
    ),
    (
        "foundation_staging_words",
        "CHECK(form != '')",
        "ck_fstg_word_form_not_empty",
        "Word form cannot be empty",
    ),
    (
        "foundation_staging_words",
        "CHECK(frequency >= 0)",
        "ck_fstg_word_frequency_nonneg",
        "Frequency cannot be negative",
    ),
    # foundation_staging_sentences
    (
        "foundation_staging_sentences",
        "CHECK(text != '')",
        "ck_fstg_sent_text_not_empty",
        "Sentence text cannot be empty",
    ),
    # foundation_staging_paragraphs
    (
        "foundation_staging_paragraphs",
        "CHECK(text != '')",
        "ck_fstg_para_text_not_empty",
        "Paragraph text cannot be empty",
    ),
    # foundation_staging_evidence
    (
        "foundation_staging_evidence",
        "CHECK(fact_type IN ('word', 'sentence', 'paragraph', 'grammar'))",
        "ck_fstg_ev_fact_type_valid",
        "Fact type must be word/sentence/paragraph/grammar",
    ),
    (
        "foundation_staging_evidence",
        "CHECK(tier BETWEEN 1 AND 5)",
        "ck_fstg_ev_tier_range",
        "Evidence tier must be 1-5",
    ),
    (
        "foundation_staging_evidence",
        "CHECK(confidence >= 0.0 AND confidence <= 1.0)",
        "ck_fstg_ev_confidence_range",
        "Confidence must be 0.0-1.0",
    ),
    # canonical_words
    (
        "canonical_words",
        "UNIQUE(form, version)",
        "ix_cword_form_version_unique",
        "Unique constraint on (form, version)",
    ),
    (
        "canonical_words",
        "CHECK(form != '')",
        "ck_cword_form_not_empty",
        "Word form cannot be empty",
    ),
    (
        "canonical_words",
        "CHECK(syllable_count >= 0)",
        "ck_cword_syllable_count_nonneg",
        "Syllable count cannot be negative",
    ),
    (
        "canonical_words",
        "CHECK(frequency >= 0)",
        "ck_cword_frequency_nonneg",
        "Frequency cannot be negative",
    ),
    (
        "canonical_words",
        "CHECK(version >= 1)",
        "ck_cword_version_positive",
        "Version must be >= 1",
    ),
    # canonical_sentences
    (
        "canonical_sentences",
        "CHECK(text != '')",
        "ck_csent_text_not_empty",
        "Sentence text cannot be empty",
    ),
    (
        "canonical_sentences",
        "CHECK(version >= 1)",
        "ck_csent_version_positive",
        "Version must be >= 1",
    ),
    # canonical_paragraphs
    (
        "canonical_paragraphs",
        "CHECK(text != '')",
        "ck_cpara_text_not_empty",
        "Paragraph text cannot be empty",
    ),
    (
        "canonical_paragraphs",
        "CHECK(version >= 1)",
        "ck_cpara_version_positive",
        "Version must be >= 1",
    ),
    # foundation_evidence
    (
        "foundation_evidence",
        "CHECK(fact_type IN ('word', 'sentence', 'paragraph', 'grammar'))",
        "ck_fev_fact_type_valid",
        "Fact type must be word/sentence/paragraph/grammar",
    ),
    (
        "foundation_evidence",
        "CHECK(tier BETWEEN 1 AND 5)",
        "ck_fev_tier_range",
        "Evidence tier must be 1-5",
    ),
    (
        "foundation_evidence",
        "CHECK(confidence >= 0.0 AND confidence <= 1.0)",
        "ck_fev_confidence_range",
        "Confidence must be 0.0-1.0",
    ),
    # foundation_verifications
    (
        "foundation_verifications",
        "CHECK(score >= 0.0 AND score <= 1.0)",
        "ck_fver_score_range",
        "Score must be 0.0-1.0",
    ),
    # foundation_consensus
    (
        "foundation_consensus",
        "UNIQUE(fact_type, fact_key, method)",
        "ix_fcon_fact_method_unique",
        "Unique constraint on (fact_type, fact_key, method)",
    ),
    (
        "foundation_consensus",
        "CHECK(fact_type IN ('word', 'sentence', 'paragraph', 'grammar'))",
        "ck_fcon_fact_type_valid",
        "Fact type must be word/sentence/paragraph/grammar",
    ),
    (
        "foundation_consensus",
        "CHECK(confidence >= 0.0 AND confidence <= 1.0)",
        "ck_fcon_confidence_range",
        "Confidence must be 0.0-1.0",
    ),
    (
        "foundation_consensus",
        "CHECK(method IN ('majority_vote', 'weighted_evidence', 'threshold'))",
        "ck_fcon_method_valid",
        "Method must be majority_vote/weighted_evidence/threshold",
    ),
    # foundation_batches
    (
        "foundation_batches",
        "CHECK(batch_type IN ('ingest', 'build_staging', 'promote', 'verify'))",
        "ck_fbatch_type_valid",
        "Batch type must be ingest/build_staging/promote/verify",
    ),
    (
        "foundation_batches",
        "CHECK(status IN ('pending', 'running', 'completed', 'failed'))",
        "ck_fbatch_status_valid",
        "Status must be pending/running/completed/failed",
    ),
    # foundation_review_queue
    (
        "foundation_review_queue",
        "CHECK(fact_type IN ('word', 'sentence', 'paragraph', 'grammar'))",
        "ck_frev_fact_type_valid",
        "Fact type must be word/sentence/paragraph/grammar",
    ),
    (
        "foundation_review_queue",
        "CHECK(status IN ('pending', 'in_review', 'approved', 'rejected'))",
        "ck_frev_status_valid",
        "Status must be pending/in_review/approved/rejected",
    ),
    # foundation_metrics
    (
        "foundation_metrics",
        "CHECK(run_id != '')",
        "ck_fmetric_run_id_not_empty",
        "Run ID cannot be empty",
    ),
    (
        "foundation_metrics",
        "CHECK(metric != '')",
        "ck_fmetric_metric_not_empty",
        "Metric name cannot be empty",
    ),
]


# Additional indexes for Foundation tables
FOUNDATION_PERFORMANCE_INDEXES = [
    # Raw layer
    ("foundation_raw_corpus", "CREATE INDEX IF NOT EXISTS ix_fraw_source_type ON foundation_raw_corpus(source_type)"),
    ("foundation_raw_corpus", "CREATE INDEX IF NOT EXISTS ix_fraw_imported_at ON foundation_raw_corpus(imported_at)"),
    ("foundation_raw_llm", "CREATE INDEX IF NOT EXISTS ix_frawllm_model ON foundation_raw_llm(model)"),
    ("foundation_raw_llm", "CREATE INDEX IF NOT EXISTS ix_frawllm_created ON foundation_raw_llm(created_at)"),
    # Staging layer
    ("foundation_staging_words", "CREATE INDEX IF NOT EXISTS ix_fstg_word_pos ON foundation_staging_words(pos)"),
    ("foundation_staging_words", "CREATE INDEX IF NOT EXISTS ix_fstg_word_zvs ON foundation_staging_words(zvs_compliant)"),
    ("foundation_staging_words", "CREATE INDEX IF NOT EXISTS ix_fstg_word_created ON foundation_staging_words(created_at)"),
    ("foundation_staging_sentences", "CREATE INDEX IF NOT EXISTS ix_fstg_sent_created ON foundation_staging_sentences(created_at)"),
    ("foundation_staging_paragraphs", "CREATE INDEX IF NOT EXISTS ix_fstg_para_created ON foundation_staging_paragraphs(created_at)"),
    ("foundation_staging_evidence", "CREATE INDEX IF NOT EXISTS ix_fstg_ev_confidence ON foundation_staging_evidence(confidence)"),
    # Canonical layer
    ("canonical_words", "CREATE INDEX IF NOT EXISTS ix_cword_pos ON canonical_words(pos)"),
    ("canonical_words", "CREATE INDEX IF NOT EXISTS ix_cword_zvs ON canonical_words(zvs_compliant)"),
    ("canonical_words", "CREATE INDEX IF NOT EXISTS ix_cword_verified_by ON canonical_words(verified_by)"),
    ("canonical_sentences", "CREATE INDEX IF NOT EXISTS ix_csent_verified_by ON canonical_sentences(verified_by)"),
    ("canonical_paragraphs", "CREATE INDEX IF NOT EXISTS ix_cpara_verified_by ON canonical_paragraphs(verified_by)"),
    # Evidence/Consensus
    ("foundation_evidence", "CREATE INDEX IF NOT EXISTS ix_fev_source ON foundation_evidence(source)"),
    ("foundation_evidence", "CREATE INDEX IF NOT EXISTS ix_fev_created ON foundation_evidence(created_at)"),
    ("foundation_verifications", "CREATE INDEX IF NOT EXISTS ix_fver_candidate ON foundation_verifications(candidate_id)"),
    ("foundation_verifications", "CREATE INDEX IF NOT EXISTS ix_fver_created ON foundation_verifications(created_at)"),
    ("foundation_consensus", "CREATE INDEX IF NOT EXISTS ix_fcon_confidence ON foundation_consensus(confidence)"),
    ("foundation_consensus", "CREATE INDEX IF NOT EXISTS ix_fcon_created ON foundation_consensus(created_at)"),
    # Meta
    ("foundation_batches", "CREATE INDEX IF NOT EXISTS ix_fbatch_status ON foundation_batches(status)"),
    ("foundation_batches", "CREATE INDEX IF NOT EXISTS ix_fbatch_completed ON foundation_batches(completed_at)"),
    ("foundation_review_queue", "CREATE INDEX IF NOT EXISTS ix_frev_assignee ON foundation_review_queue(assignee)"),
    ("foundation_review_queue", "CREATE INDEX IF NOT EXISTS ix_frev_resolved ON foundation_review_queue(resolved_at)"),
    ("foundation_metrics", "CREATE INDEX IF NOT EXISTS ix_fmetric_value ON foundation_metrics(value)"),
]


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


def apply_foundation_constraints(mgr: DatabaseManager) -> dict[str, Any]:
    """Apply Foundation table constraint migrations.

    Returns:
        Dict with 'applied', 'skipped', 'errors' lists.
    """
    applied = []
    skipped = []
    errors = []

    for table_name, constraint_sql, constraint_name, description in FOUNDATION_CONSTRAINT_MIGRATIONS:
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
                    elif constraint_sql.startswith("CHECK"):
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


def apply_foundation_indexes(mgr: DatabaseManager) -> dict[str, Any]:
    """Apply Foundation table performance indexes.

    Returns:
        Dict with 'applied', 'skipped', 'errors' lists.
    """
    applied = []
    skipped = []
    errors = []

    for table_name, index_sql in FOUNDATION_PERFORMANCE_INDEXES:
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


def create_foundation_cost_tracking_table(mgr: DatabaseManager) -> dict[str, Any]:
    """Create the foundation_cost_tracking table.

    Returns:
        Dict with 'created', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    if "foundation_cost_tracking" in existing_tables:
        return {"created": [], "skipped": ["foundation_cost_tracking (already exists)"], "errors": []}

    try:
        foundation_metadata = Base.metadata
        tables_to_create = [
            foundation_metadata.tables[t]
            for t in ["foundation_cost_tracking"]
            if t in foundation_metadata.tables
        ]
        if tables_to_create:
            foundation_metadata.create_all(mgr.engine, tables=tables_to_create)
            return {"created": ["foundation_cost_tracking"], "skipped": [], "errors": []}
    except Exception as exc:
        return {"created": [], "skipped": [], "errors": [f"create_all: {exc}"]}

    return {"created": [], "skipped": [], "errors": []}


# Cost tracking indexes
FOUNDATION_COST_TRACKING_INDEXES = [
    (
        "foundation_cost_tracking",
        "CREATE INDEX IF NOT EXISTS ix_fct_request_id ON foundation_cost_tracking(request_id)",
    ),
    (
        "foundation_cost_tracking",
        "CREATE INDEX IF NOT EXISTS ix_fct_task_type ON foundation_cost_tracking(task_type)",
    ),
    (
        "foundation_cost_tracking",
        "CREATE INDEX IF NOT EXISTS ix_fct_created ON foundation_cost_tracking(created_at)",
    ),
    (
        "foundation_cost_tracking",
        "CREATE INDEX IF NOT EXISTS ix_fct_model ON foundation_cost_tracking(model)",
    ),
]


def apply_foundation_cost_tracking_indexes(mgr: DatabaseManager) -> dict[str, Any]:
    """Apply Foundation cost tracking performance indexes.

    Returns:
        Dict with 'applied', 'skipped', 'errors' lists.
    """
    applied = []
    skipped = []
    errors = []

    for table_name, index_sql in FOUNDATION_COST_TRACKING_INDEXES:
        try:
            with mgr.engine.connect() as conn:
                idx_name = index_sql.split("INDEX IF NOT EXISTS ")[1].split(" ON")[0]
                if mgr._db_url.startswith("sqlite"):
                    check_sql = f"SELECT 1 FROM sqlite_master WHERE type='index' AND name='{idx_name}'"
                else:
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


# ---------------------------------------------------------------------------
# SM-2 Spaced Repetition columns for vocabulary table
# ---------------------------------------------------------------------------

SM2_COLUMNS = [
    ("ease_factor", "REAL DEFAULT 2.5"),
    ("interval", "INTEGER DEFAULT 0"),
    ("repetitions", "INTEGER DEFAULT 0"),
    ("next_review", "TEXT"),
]


def add_sm2_columns_to_vocabulary(mgr: DatabaseManager) -> dict[str, Any]:
    """Add SM-2 spaced repetition columns to the vocabulary table.

    Columns added:
      - ease_factor REAL DEFAULT 2.5
      - interval INTEGER DEFAULT 0
      - repetitions INTEGER DEFAULT 0
      - next_review TEXT

    Returns:
        Dict with 'added', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    added = []
    skipped = []
    errors = []

    try:
        inspector = sa_inspect(mgr.engine)
        existing_cols = {c["name"] for c in inspector.get_columns("vocabulary")}
    except Exception as exc:
        return {"added": [], "skipped": [], "errors": [f"inspect vocabulary: {exc}"]}

    with mgr.engine.connect() as conn:
        for col_name, col_def in SM2_COLUMNS:
            if col_name in existing_cols:
                skipped.append(f"vocabulary.{col_name} (already exists)")
                continue
            try:
                conn.execute(text(f"ALTER TABLE vocabulary ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                added.append(f"vocabulary.{col_name}")
            except Exception as exc:
                errors.append(f"vocabulary.{col_name}: {exc}")

    return {"added": added, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# user_reviews table for per-user spaced repetition history
# ---------------------------------------------------------------------------

USER_REVIEWS_DDL = """
CREATE TABLE IF NOT EXISTS user_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    word TEXT NOT NULL,
    quality INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    next_review TEXT NOT NULL,
    ease_factor REAL NOT NULL,
    interval INTEGER NOT NULL,
    repetitions INTEGER NOT NULL
)
"""

USER_REVIEWS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_user_reviews_user_word ON user_reviews(user_id, word)",
    "CREATE INDEX IF NOT EXISTS idx_user_reviews_reviewed_at ON user_reviews(reviewed_at)",
]


def create_user_reviews_table(mgr: DatabaseManager) -> dict[str, Any]:
    """Create the user_reviews table for per-user SM-2 history.

    Returns:
        Dict with 'created', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    if "user_reviews" in existing_tables:
        return {"created": [], "skipped": ["user_reviews (already exists)"], "errors": []}

    try:
        with mgr.engine.connect() as conn:
            conn.execute(text(USER_REVIEWS_DDL))
            for idx_sql in USER_REVIEWS_INDEXES:
                conn.execute(text(idx_sql))
            conn.commit()
        return {"created": ["user_reviews"], "skipped": [], "errors": []}
    except Exception as exc:
        return {"created": [], "skipped": [], "errors": [f"user_reviews: {exc}"]}


def run_all_migrations(mgr: DatabaseManager) -> dict[str, Any]:
    """Run all constraint and index migrations including Foundation tables.

    Returns:
        Combined results from constraints and indexes.
    """
    constraint_results = apply_constraints(mgr)
    index_results = apply_indexes(mgr)
    foundation_constraint_results = apply_foundation_constraints(mgr)
    foundation_index_results = apply_foundation_indexes(mgr)
    cost_tracking_table = create_foundation_cost_tracking_table(mgr)
    cost_tracking_indexes = apply_foundation_cost_tracking_indexes(mgr)

    return {
        "constraints": constraint_results,
        "indexes": index_results,
        "foundation_constraints": foundation_constraint_results,
        "foundation_indexes": foundation_index_results,
        "cost_tracking_table": cost_tracking_table,
        "cost_tracking_indexes": cost_tracking_indexes,
        "sm2_columns": add_sm2_columns_to_vocabulary(mgr),
        "user_reviews_table": create_user_reviews_table(mgr),
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
