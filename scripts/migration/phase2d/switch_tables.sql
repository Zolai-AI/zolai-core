-- ============================================================
-- Phase 2D: Table Cutover — Atomic Rename Script
-- ============================================================
-- Strategy: For each of 23 table pairs:
--   1. BEGIN TRANSACTION
--   2. ALTER TABLE old_table RENAME TO old_table_old
--   3. ALTER TABLE v2_table RENAME TO canonical_name
--   4. COMMIT
--
-- Each pair is an independent transaction so failures are isolated.
-- If any rename fails, that transaction is rolled back and reported.
--
-- Usage:
--   sqlite3 ../data/zolai.db < switch_tables.sql
--   (run from this directory)
-- ============================================================

-- ============================================================
-- 1. dictionary (ZO→EN)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE dictionary RENAME TO dictionary_old;
ALTER TABLE dictionary_v2 RENAME TO dictionary;
COMMIT;

-- ============================================================
-- 2. dictionary_en_zo (EN→ZO)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE dictionary_en_zo RENAME TO dictionary_en_zo_old;
ALTER TABLE dictionary_en_zo_v2 RENAME TO dictionary_en_zo;
COMMIT;

-- ============================================================
-- 3. bible_verses
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE bible_verses RENAME TO bible_verses_old;
ALTER TABLE bible_verses_v2 RENAME TO bible_verses;
COMMIT;

-- ============================================================
-- 4. grammar_patterns
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE grammar_patterns RENAME TO grammar_patterns_old;
ALTER TABLE grammar_patterns_v2 RENAME TO grammar_patterns;
COMMIT;

-- ============================================================
-- 5. translations
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE translations RENAME TO translations_old;
ALTER TABLE translations_v2 RENAME TO translations;
COMMIT;

-- ============================================================
-- 6. word_alignments
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE word_alignments RENAME TO word_alignments_old;
ALTER TABLE word_alignments_v2 RENAME TO word_alignments;
COMMIT;

-- ============================================================
-- 7. vocab → vocabulary  (table name changes)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE vocab RENAME TO vocab_old;
ALTER TABLE vocabulary_v2 RENAME TO vocabulary;
COMMIT;

-- ============================================================
-- 8. proverbs
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE proverbs RENAME TO proverbs_old;
ALTER TABLE proverbs_v2 RENAME TO proverbs;
COMMIT;

-- ============================================================
-- 9. phrases
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE phrases RENAME TO phrases_old;
ALTER TABLE phrases_v2 RENAME TO phrases;
COMMIT;

-- ============================================================
-- 10. word_usage
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE word_usage RENAME TO word_usage_old;
ALTER TABLE word_usage_v2 RENAME TO word_usage;
COMMIT;

-- ============================================================
-- 11. syllable_data
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE syllable_data RENAME TO syllable_data_old;
ALTER TABLE syllable_data_v2 RENAME TO syllable_data;
COMMIT;

-- ============================================================
-- 12. word_collocations
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE word_collocations RENAME TO word_collocations_old;
ALTER TABLE word_collocations_v2 RENAME TO word_collocations;
COMMIT;

-- ============================================================
-- 13. bible_context → bible_analysis  (table name changes)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE bible_context RENAME TO bible_context_old;
ALTER TABLE bible_analysis_v2 RENAME TO bible_analysis;
COMMIT;

-- ============================================================
-- 14. articles
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE articles RENAME TO articles_old;
ALTER TABLE articles_v2 RENAME TO articles;
COMMIT;

-- ============================================================
-- 15. zolai_songs → songs  (table name changes)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE zolai_songs RENAME TO zolai_songs_old;
ALTER TABLE songs_v2 RENAME TO songs;
COMMIT;

-- ============================================================
-- 16. wiki_content
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE wiki_content RENAME TO wiki_content_old;
ALTER TABLE wiki_content_v2 RENAME TO wiki_content;
COMMIT;

-- ============================================================
-- 17. data_audit_log
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE data_audit_log RENAME TO data_audit_log_old;
ALTER TABLE data_audit_log_v2 RENAME TO data_audit_log;
COMMIT;

-- ============================================================
-- 18. audit_findings
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE audit_findings RENAME TO audit_findings_old;
ALTER TABLE audit_findings_v2 RENAME TO audit_findings;
COMMIT;

-- ============================================================
-- 19. provenance
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE provenance RENAME TO provenance_old;
ALTER TABLE provenance_v2 RENAME TO provenance;
COMMIT;

-- ============================================================
-- 20. jsonl_import_log → import_log  (table name changes)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE jsonl_import_log RENAME TO jsonl_import_log_old;
ALTER TABLE import_log_v2 RENAME TO import_log;
COMMIT;

-- ============================================================
-- 21. zolai_tone_sandhi → tone_sandhi  (table name changes)
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE zolai_tone_sandhi RENAME TO zolai_tone_sandhi_old;
ALTER TABLE tone_sandhi_v2 RENAME TO tone_sandhi;
COMMIT;

-- ============================================================
-- 22. tone_patterns
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE tone_patterns RENAME TO tone_patterns_old;
ALTER TABLE tone_patterns_v2 RENAME TO tone_patterns;
COMMIT;

-- ============================================================
-- 23. training_runs
-- ============================================================
BEGIN TRANSACTION;
ALTER TABLE training_runs RENAME TO training_runs_old;
ALTER TABLE training_runs_v2 RENAME TO training_runs;
COMMIT;
