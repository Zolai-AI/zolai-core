"""Database schema migrations for constraints and indexes.

Run this after init_db to add proper constraints and indexes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from .database import DatabaseManager
from .models import Base
from .pos_normalize import normalize_legacy_pos


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
                    # SQLite: UNIQUE via index; CHECK not alterable on existing tables
                    if constraint_sql.startswith("UNIQUE"):
                        cols = constraint_sql.replace("UNIQUE(", "").replace(")", "")
                        # Schema drift: phrases live DB uses zolai; ORM init_db uses zo
                        if table_name == "phrases":
                            col_rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
                            colnames = {r[1] for r in col_rows}
                            parts = [c.strip() for c in cols.split(",")]
                            resolved = []
                            for c in parts:
                                if c == "zo" and "zo" not in colnames and "zolai" in colnames:
                                    resolved.append("zolai")
                                elif c == "zolai" and "zolai" not in colnames and "zo" in colnames:
                                    resolved.append("zo")
                                else:
                                    resolved.append(c)
                            cols = ", ".join(resolved)
                        alter_sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {constraint_name} ON {table_name}({cols})"
                    elif constraint_sql.startswith("CHECK"):
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

                # Skip indexes whose columns are absent (ORM vs live-DB drift)
                if mgr._db_url.startswith("sqlite") and " ON " in index_sql:
                    try:
                        cols_part = index_sql.split(" ON ", 1)[1]
                        # e.g. table(col1, col2)
                        inside = cols_part[cols_part.find("(")+1:cols_part.rfind(")")]
                        want = {c.strip() for c in inside.split(",") if c.strip()}
                        have = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
                        missing = want - have
                        if missing:
                            skipped.append(
                                f"{table_name}.{idx_name} (missing columns: {sorted(missing)})"
                            )
                            continue
                    except Exception:
                        pass

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
                        # Schema drift: phrases live DB uses zolai; ORM init_db uses zo
                        if table_name == "phrases":
                            col_rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
                            colnames = {r[1] for r in col_rows}
                            parts = [c.strip() for c in cols.split(",")]
                            resolved = []
                            for c in parts:
                                if c == "zo" and "zo" not in colnames and "zolai" in colnames:
                                    resolved.append("zolai")
                                elif c == "zolai" and "zolai" not in colnames and "zo" in colnames:
                                    resolved.append("zo")
                                else:
                                    resolved.append(c)
                            cols = ", ".join(resolved)
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


USER_STREAKS_DDL = """
CREATE TABLE IF NOT EXISTS user_streaks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    streak_type TEXT NOT NULL,
    current_streak INTEGER NOT NULL DEFAULT 0,
    longest_streak INTEGER NOT NULL DEFAULT 0,
    last_activity_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, streak_type)
)
"""

USER_STREAKS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_user_streaks_user_type ON user_streaks(user_id, streak_type)",
    "CREATE INDEX IF NOT EXISTS idx_user_streaks_last_activity ON user_streaks(last_activity_date)",
]


def create_user_streaks_table(mgr: DatabaseManager) -> dict[str, Any]:
    """Create the user_streaks table for tracking learning streaks.

    Returns:
        Dict with 'created', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    if "user_streaks" in existing_tables:
        return {"created": [], "skipped": ["user_streaks (already exists)"], "errors": []}

    try:
        with mgr.engine.connect() as conn:
            conn.execute(text(USER_STREAKS_DDL))
            for idx_sql in USER_STREAKS_INDEXES:
                conn.execute(text(idx_sql))
            conn.commit()
        return {"created": ["user_streaks"], "skipped": [], "errors": []}
    except Exception as exc:
        return {"created": [], "skipped": [], "errors": [f"user_streaks: {exc}"]}


# ---------------------------------------------------------------------------
# L1.3 — canonical POS + provenance columns for the lexicon tables
#
# Additive only: the legacy ``pos`` column is NEVER modified or dropped, and
# no table is rebuilt.  Every statement below is ``ALTER TABLE ... ADD COLUMN``
# or ``CREATE INDEX IF NOT EXISTS``.
#
# Reverse (rollback) SQL, run per table:
#     DROP INDEX IF EXISTS ix_dictionary_pos_canonical;
#     DROP INDEX IF EXISTS ix_vocabulary_pos_canonical;
#     DROP INDEX IF EXISTS ix_zolai_vocabulary_pos_canonical;
#     ALTER TABLE dictionary        DROP COLUMN pos_canonical;   -- SQLite >= 3.35
#     ALTER TABLE dictionary        DROP COLUMN pos_candidates;
#     ALTER TABLE dictionary        DROP COLUMN pos_evidence;
#     ALTER TABLE dictionary        DROP COLUMN morph_features;
#     ALTER TABLE dictionary        DROP COLUMN source_type;
#     ALTER TABLE dictionary        DROP COLUMN source_url;
#     ALTER TABLE dictionary        DROP COLUMN creator;
#     ALTER TABLE dictionary        DROP COLUMN license;
#     ALTER TABLE dictionary        DROP COLUMN collection_date;
#     ALTER TABLE dictionary        DROP COLUMN import_date;
#     ALTER TABLE dictionary        DROP COLUMN processing_version;
#     ALTER TABLE dictionary        DROP COLUMN review_status;
#     ALTER TABLE dictionary        DROP COLUMN confidence;
#     ... repeat DROP COLUMN for vocabulary and zolai_vocabulary ...
# ``DROP COLUMN`` cannot drop an indexed column — drop the index first.
# ---------------------------------------------------------------------------

#: Lexicon tables that receive the canonical POS / provenance columns.
LEXICON_TABLES: tuple[str, ...] = ("dictionary", "vocabulary", "zolai_vocabulary")

#: (column name, SQLite column DDL) — 13 columns × 3 tables = 39 ALTERs.
LEXICON_POS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pos_canonical", "TEXT NULL"),
    ("pos_candidates", "TEXT DEFAULT '[]'"),
    ("pos_evidence", "TEXT DEFAULT 'unknown'"),
    ("morph_features", "TEXT DEFAULT '{}'"),
    ("source_type", "TEXT DEFAULT 'unknown'"),
    ("source_url", "TEXT NULL"),
    ("creator", "TEXT NULL"),
    ("license", "TEXT NULL"),
    ("collection_date", "TEXT NULL"),
    ("import_date", "TEXT NULL"),
    ("processing_version", "TEXT NULL"),
    ("review_status", "TEXT DEFAULT 'unknown'"),
    ("confidence", "REAL NULL"),
)

#: Partial indexes so queries can seek rows that already carry a canonical POS.
LEXICON_POS_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "ix_dictionary_pos_canonical",
        "dictionary",
        "CREATE INDEX IF NOT EXISTS ix_dictionary_pos_canonical "
        "ON dictionary(pos_canonical) WHERE pos_canonical IS NOT NULL",
    ),
    (
        "ix_vocabulary_pos_canonical",
        "vocabulary",
        "CREATE INDEX IF NOT EXISTS ix_vocabulary_pos_canonical "
        "ON vocabulary(pos_canonical) WHERE pos_canonical IS NOT NULL",
    ),
    (
        "ix_zolai_vocabulary_pos_canonical",
        "zolai_vocabulary",
        "CREATE INDEX IF NOT EXISTS ix_zolai_vocabulary_pos_canonical "
        "ON zolai_vocabulary(pos_canonical) WHERE pos_canonical IS NOT NULL",
    ),
)

#: Stamped on every row written by :func:`backfill_pos_canonical`.
LEXICON_BACKFILL_VERSION = "l1.3-pos-backfill"


def add_lexicon_pos_columns(mgr: DatabaseManager) -> dict[str, Any]:
    """Add the 13 canonical POS / provenance columns to the 3 lexicon tables.

    Uses ``ALTER TABLE ... ADD COLUMN`` only — the legacy ``pos`` column and
    every existing row are left untouched, and no table is rebuilt.

    Columns (13): pos_canonical, pos_candidates, pos_evidence, morph_features,
    source_type, source_url, creator, license, collection_date, import_date,
    processing_version, review_status, confidence.

    Tables missing from the database are skipped.  Columns that already exist
    (e.g. ``zolai_vocabulary.confidence``, which predates L1.3) are skipped so
    the migration stays idempotent.

    Returns:
        Dict with 'added', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    added: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for table_name in LEXICON_TABLES:
        if table_name not in existing_tables:
            skipped.append(f"{table_name} (table missing)")
            continue

        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        with mgr.engine.connect() as conn:
            for col_name, col_def in LEXICON_POS_COLUMNS:
                if col_name in existing_cols:
                    skipped.append(f"{table_name}.{col_name} (already exists)")
                    continue
                try:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                except Exception as exc:  # noqa: BLE001 - collect and continue
                    errors.append(f"{table_name}.{col_name}: {exc}")
                    continue
                added.append(f"{table_name}.{col_name}")

    return {"added": added, "skipped": skipped, "errors": errors}


def backfill_pos_canonical(mgr: DatabaseManager) -> dict[str, Any]:
    """Fill ``pos_canonical`` / ``pos_candidates`` / ``pos_evidence`` from ``pos``.

    Reads every row whose legacy ``pos`` is non-empty and that has not been
    processed yet, normalizes it with
    :func:`zolai.data.pos_normalize.normalize_legacy_pos`, and writes back
    **only**:

    * ``pos_canonical`` (a 17-tag UPOS or NULL when ambiguous/unmapped)
    * ``pos_candidates`` (JSON list, ``[]`` when not ambiguous)
    * ``pos_evidence`` (one of the documented evidence values)
    * ``import_date`` (UTC date of this backfill)
    * ``processing_version`` (= ``l1.3-pos-backfill``)

    The legacy ``pos`` value is never written to, and rows with an empty /
    NULL ``pos`` are left completely untouched (all new columns stay NULL or
    at their server defaults).  Rows whose POS is ambiguous or unmapped keep
    ``pos_canonical IS NULL`` but still get ``processing_version`` stamped, so
    a re-run updates 0 rows.

    Returns:
        Dict with 'updated' (row count), 'tables' (per-table counts),
        'skipped' and 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())
    import_date = datetime.now(timezone.utc).date().isoformat()

    updated = 0
    per_table: dict[str, int] = {}
    skipped: list[str] = []
    errors: list[str] = []

    for table_name in LEXICON_TABLES:
        if table_name not in existing_tables:
            skipped.append(f"{table_name} (table missing)")
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        if "pos" not in existing_cols:
            skipped.append(f"{table_name} (no pos column)")
            continue
        if "pos_canonical" not in existing_cols:
            skipped.append(f"{table_name} (no pos_canonical column — run add_lexicon_pos_columns)")
            continue

        try:
            with mgr.engine.begin() as conn:
                rows = conn.execute(
                    text(
                        f"SELECT id, pos FROM {table_name} "
                        "WHERE pos IS NOT NULL AND TRIM(pos) != '' "
                        "AND pos_canonical IS NULL AND processing_version IS NULL"
                    )
                ).all()
                updates = []
                for row in rows:
                    decision = normalize_legacy_pos(row.pos)
                    if decision.untouched:
                        continue
                    updates.append(
                        {
                            "id": row.id,
                            "pos_canonical": decision.canonical,
                            "pos_candidates": decision.candidates_json,
                            "pos_evidence": decision.evidence,
                            "import_date": import_date,
                            "processing_version": LEXICON_BACKFILL_VERSION,
                        }
                    )
                if updates:
                    conn.execute(
                        text(
                            f"UPDATE {table_name} SET "
                            "pos_canonical = :pos_canonical, "
                            "pos_candidates = :pos_candidates, "
                            "pos_evidence = :pos_evidence, "
                            "import_date = :import_date, "
                            "processing_version = :processing_version "
                            "WHERE id = :id"
                        ),
                        updates,
                    )
        except Exception as exc:  # noqa: BLE001 - collect and continue
            errors.append(f"{table_name}: {exc}")
            continue

        per_table[table_name] = len(updates)
        updated += len(updates)

    return {"updated": updated, "tables": per_table, "skipped": skipped, "errors": errors}


def create_lexicon_pos_indexes(mgr: DatabaseManager) -> dict[str, Any]:
    """Create the 3 partial indexes on ``pos_canonical``.

    Indexes only cover rows where ``pos_canonical IS NOT NULL``, so unmapped
    legacy labels stay out of the index.

    Returns:
        Dict with 'created', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for index_name, table_name, index_sql in LEXICON_POS_INDEXES:
        if table_name not in existing_tables:
            skipped.append(f"{table_name} (table missing)")
            continue
        try:
            existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        except Exception as exc:  # noqa: BLE001 - collect and continue
            errors.append(f"{table_name}: {exc}")
            continue
        if index_name in existing_indexes:
            skipped.append(f"{index_name} (already exists)")
            continue
        try:
            with mgr.engine.connect() as conn:
                conn.execute(text(index_sql))
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - collect and continue
            errors.append(f"{index_name}: {exc}")
            continue
        created.append(index_name)

    return {"created": created, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# L-ops — monitoring / evaluation / integrity tables
#
# Additive only: CREATE TABLE IF NOT EXISTS + CREATE UNIQUE INDEX IF NOT
# EXISTS.  No legacy table is rebuilt, dropped, or altered in place, and the
# shared 2.3GB store keeps every existing column (including ``pos``) intact.
#
# Reverse (rollback) SQL — safe to run at any time:
#     DROP INDEX IF EXISTS ux_fraw_content_hash;
#     DROP TABLE IF EXISTS eval_runs;
#     DROP TABLE IF EXISTS monitoring_annotations;
#     DROP TABLE IF EXISTS db_integrity_runs;
#
# ``ux_fraw_content_hash`` is the only index added to a pre-existing table.
# ``foundation_raw_corpus`` stays indexed through the non-unique
# ``ix_fraw_source_hash``, so dropping the unique index leaves the column
# queryable.  The migration refuses to create it when duplicate
# ``content_hash`` values already exist (verified 0 duplicates on the live
# store) rather than failing a startup migration.
# ---------------------------------------------------------------------------

MONITORING_ANNOTATIONS_DDL = """
CREATE TABLE IF NOT EXISTS monitoring_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    kind TEXT NOT NULL DEFAULT 'manual'
        CHECK (kind IN ('deploy', 'eval', 'manual')),
    dashboard_id INTEGER,
    panel_id INTEGER,
    grafana_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

EVAL_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_name TEXT NOT NULL REFERENCES eval_sets(set_name),
    created_at TEXT NOT NULL,
    case_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    gate_passed INTEGER NOT NULL DEFAULT 1,
    metrics TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'db'
)
"""

DB_INTEGRITY_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS db_integrity_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_type TEXT NOT NULL CHECK (check_type IN ('foreign_key', 'full')),
    ok INTEGER NOT NULL DEFAULT 1,
    issues TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0
)
"""

MONITORING_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_monitoring_annotations_time "
    "ON monitoring_annotations(time)",
    "CREATE INDEX IF NOT EXISTS ix_monitoring_annotations_kind "
    "ON monitoring_annotations(kind)",
    "CREATE INDEX IF NOT EXISTS ix_eval_runs_set_created "
    "ON eval_runs(set_name, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_db_integrity_runs_checked "
    "ON db_integrity_runs(checked_at)",
]

#: Dedup index on the pre-existing raw-corpus log (additive, no table rebuild).
UNIQUE_CONTENT_HASH_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_fraw_content_hash "
    "ON foundation_raw_corpus(content_hash)"
)


def _eval_sets_ddl_statements() -> list[str]:
    """Return the canonical ``eval_sets`` / ``eval_cases`` DDL statements.

    ``eval_runs.set_name`` references ``eval_sets(set_name)``, so the parent
    table has to exist before the child DDL runs on a fresh store.  The
    statements come straight from :mod:`zolai.eval.store` so the two definitions
    can never drift apart.
    """
    from zolai.eval.store import _SCHEMA_SQL

    return [stmt.strip() for stmt in _SCHEMA_SQL.split(";") if stmt.strip()]


def create_monitoring_tables(mgr: DatabaseManager) -> dict[str, Any]:
    """Create the L-ops monitoring tables and the content-hash dedup index.

    Creates ``monitoring_annotations`` (Grafana-compatible annotations),
    ``eval_runs`` (one row per ``zolai-eval`` run) and ``db_integrity_runs``
    (foreign-key / integrity check history), plus the unique
    ``ux_fraw_content_hash`` index used to reject duplicate raw imports.

    Returns:
        Dict with ``created`` / ``skipped`` / ``errors`` lists.
    """
    from sqlalchemy import inspect as sa_inspect

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if not mgr._db_url.startswith("sqlite"):
        return {
            "created": [],
            "skipped": [],
            "errors": ["monitoring tables are SQLite-only for now"],
        }

    inspector = sa_inspect(mgr.engine)
    existing = set(inspector.get_table_names())

    statements: list[tuple[str, str]] = []
    if "eval_sets" not in existing or "eval_cases" not in existing:
        # Both halves of the store schema are ensured together: eval_runs.set_name
        # references eval_sets, and eval_cases is the pair table they share.
        statements.extend(("eval_parent", stmt) for stmt in _eval_sets_ddl_statements())
    statements.extend(
        [
            ("monitoring_annotations", MONITORING_ANNOTATIONS_DDL),
            ("eval_runs", EVAL_RUNS_DDL),
            ("db_integrity_runs", DB_INTEGRITY_RUNS_DDL),
        ]
    )

    try:
        with mgr.engine.connect() as conn:
            for name, ddl in statements:
                conn.execute(text(ddl))
                if name != "eval_parent" and name not in existing:
                    created.append(name)
                elif name != "eval_parent":
                    skipped.append(f"{name} (already exists)")
            for idx_sql in MONITORING_INDEXES:
                conn.execute(text(idx_sql))
            _ensure_unique_content_hash_index(conn, created, skipped, errors)
            conn.commit()
    except Exception as exc:
        errors.append(f"monitoring_tables: {exc}")
        return {"created": created, "skipped": skipped, "errors": errors}

    return {"created": created, "skipped": skipped, "errors": errors}


def _ensure_unique_content_hash_index(
    conn: Any, created: list[str], skipped: list[str], errors: list[str]
) -> None:
    """Create ``ux_fraw_content_hash`` unless duplicates already exist."""
    table = conn.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='foundation_raw_corpus'"
        )
    ).first()
    if table is None:
        skipped.append("ux_fraw_content_hash (foundation_raw_corpus missing)")
        return

    index = conn.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='ux_fraw_content_hash'"
        )
    ).first()
    if index is not None:
        skipped.append("ux_fraw_content_hash (already exists)")
        return

    duplicates = conn.execute(
        text(
            "SELECT content_hash FROM foundation_raw_corpus "
            "GROUP BY content_hash HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicates is not None:
        errors.append(
            "ux_fraw_content_hash: duplicate content_hash values present; "
            "deduplicate before enabling"
        )
        return

    conn.execute(text(UNIQUE_CONTENT_HASH_INDEX))
    created.append("ux_fraw_content_hash")


# ---------------------------------------------------------------------------
# API-key auth (ADR-014 / backlog P0-1) — additive only, no legacy table touched.
#
# Reverse (rollback) SQL:
#     DROP TABLE IF EXISTS api_keys;
# ---------------------------------------------------------------------------

API_KEYS_DDL = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scopes TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'cli',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    last_used_at TEXT,
    revoked_at TEXT
)
"""

API_KEYS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_api_keys_revoked ON api_keys(revoked_at)",
]


def create_api_keys_table(mgr: DatabaseManager) -> dict[str, Any]:
    """Create the ``api_keys`` table (API-key auth, ADR-014).

    Idempotent and additive: only runs when the table is missing, so existing
    stores keep whatever they already have.  Only the SHA-256 hash and a
    display prefix are ever stored — never the plaintext key.

    Returns:
        Dict with 'created', 'skipped', 'errors' lists.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(mgr.engine)
    existing_tables = set(inspector.get_table_names())

    if "api_keys" in existing_tables:
        return {"created": [], "skipped": ["api_keys (already exists)"], "errors": []}

    try:
        with mgr.engine.connect() as conn:
            conn.execute(text(API_KEYS_DDL))
            for idx_sql in API_KEYS_INDEXES:
                conn.execute(text(idx_sql))
            conn.commit()
        return {"created": ["api_keys"], "skipped": [], "errors": []}
    except Exception as exc:
        return {"created": [], "skipped": [], "errors": [f"api_keys: {exc}"]}


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
        "user_streaks_table": create_user_streaks_table(mgr),
        "lexicon_pos_columns": add_lexicon_pos_columns(mgr),
        "lexicon_pos_backfill": backfill_pos_canonical(mgr),
        "lexicon_pos_indexes": create_lexicon_pos_indexes(mgr),
        "monitoring_tables": create_monitoring_tables(mgr),
        "api_keys_table": create_api_keys_table(mgr),
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
