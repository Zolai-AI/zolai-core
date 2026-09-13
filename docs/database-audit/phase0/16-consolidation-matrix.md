# Phase 0 Database Audit — Consolidation Matrix

**Generated:** 2026-09-13 20:31

## Action Legend

| Action | Meaning |
|--------|---------|
| KEEP_CANONICAL | Keep as-is; primary source of truth |
| KEEP_DERIVED | Keep as-is; useful derived/cache table |
| KEEP_IMPORT | Keep as-is; needed for pipeline |
| CONSOLIDATE_LATER | Merge into base table during migration |
| ARCHIVE_LATER | Move to archive schema; safe to drop eventually |

## Full Matrix

| Table | Rows | Action | Rationale |
|-------|------|--------|-----------|
| `articles` | 6,371 | **KEEP_CANONICAL** | Reference articles |
| `audit_findings` | 713 | **KEEP_CANONICAL** | Audit findings |
| `bible_book_analysis_import` | 65 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `bible_chapter_analysis_import` | 1,153 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `bible_context` | 1,228 | **KEEP_CANONICAL** | Primary source of truth |
| `bible_verses` | 62,751 | **KEEP_CANONICAL** | Primary Bible parallel text |
| `bible_verses_enhanced` | 0 | **ARCHIVE_LATER** | Empty planned table |
| `bible_verses_import` | 62,204 | **ARCHIVE_LATER** | Import staging |
| `data_audit_log` | 24,762 | **KEEP_CANONICAL** | Change tracking |
| `dictionary` | 103,303 | **KEEP_CANONICAL** | Primary ZO→EN dictionary |
| `dictionary_en_my_import` | 0 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `dictionary_en_zo` | 113,750 | **KEEP_CANONICAL** | Primary EN→ZO dictionary |
| `dictionary_en_zo_import` | 135,276 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `dictionary_enhanced` | 0 | **ARCHIVE_LATER** | Empty planned table |
| `dictionary_import` | 156,808 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `dictionary_my_import` | 7,840 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `dictionary_trilingual_import` | 0 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `gemini_model_results` | 0 | **ARCHIVE_LATER** | Empty tracking table |
| `grammar_patterns` | 5,547 | **KEEP_CANONICAL** | Base grammar patterns |
| `grammar_patterns_enhanced` | 5,597 | **CONSOLIDATE_LATER** | Merge 1 extra column into grammar_patterns |
| `grammar_patterns_import` | 6,983 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `jsonl_import_log` | 92 | **KEEP_CANONICAL** | Import run tracking |
| `morph_verified` | 0 | **ARCHIVE_LATER** | Empty NLP pipeline output |
| `phrase_context_import` | 45,597 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `phrases` | 5,000 | **KEEP_CANONICAL** | Primary phrases |
| `phrases_from_bible_import` | 14,000 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `phrases_import` | 15,000 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `pos_gold` | 0 | **ARCHIVE_LATER** | Empty NLP pipeline output |
| `pos_verified` | 0 | **ARCHIVE_LATER** | Empty NLP pipeline output |
| `provenance` | 255 | **KEEP_CANONICAL** | Source file tracking |
| `proverbs` | 7,736 | **KEEP_CANONICAL** | Primary proverbs |
| `proverbs_idioms` | 0 | **ARCHIVE_LATER** | Empty planned table |
| `proverbs_import` | 7,736 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `sentence_patterns_import` | 65 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `sqlite_sequence` | 15 | **KEEP_CANONICAL** | SQLite system table |
| `syllable_data` | 189,554 | **KEEP_CANONICAL** | Syllable segmentation data |
| `tone_patterns` | 118 | **KEEP_CANONICAL** | Extended tone patterns |
| `topic_clusters_import` | 12 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `training_corpus_qwen3_import` | 9,386 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `training_exercises` | 81,805 | **KEEP_CANONICAL** | Primary training data |
| `training_exercises_import` | 81,805 | **ARCHIVE_LATER** | Exact duplicate of canonical |
| `training_runs` | 2 | **KEEP_CANONICAL** | Training run metadata |
| `training_seed_data_import` | 500 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `training_valid_sentences_import` | 4,693 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `translations` | 212,754 | **KEEP_CANONICAL** | Primary translation pairs |
| `translations_import` | 135,511 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `vocab` | 94,458 | **KEEP_CANONICAL** | Primary vocabulary with frequency |
| `vocab_import` | 180,458 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `vocabulary_enhanced` | 0 | **ARCHIVE_LATER** | Empty planned table |
| `wiki_content` | 1,688 | **KEEP_CANONICAL** | Primary wiki content |
| `wiki_content_fts` | 1,688 | **KEEP_CANONICAL** | SQLite FTS5 auto-managed |
| `wiki_content_fts_config` | 1 | **KEEP_CANONICAL** | SQLite FTS5 auto-managed |
| `wiki_content_fts_data` | 4,141 | **KEEP_CANONICAL** | SQLite FTS5 auto-managed |
| `wiki_content_fts_docsize` | 1,688 | **KEEP_CANONICAL** | SQLite FTS5 auto-managed |
| `wiki_content_fts_idx` | 3,186 | **KEEP_CANONICAL** | SQLite FTS5 auto-managed |
| `wiki_lessons` | 1,688 | **KEEP_CANONICAL** | Wiki lesson mapping |
| `word_alignments` | 385,120 | **KEEP_CANONICAL** | Primary word alignments |
| `word_alignments_import` | 627,000 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `word_collocations` | 5,000 | **KEEP_CANONICAL** | Primary collocations |
| `word_collocations_import` | 5,000 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `word_similarity` | 0 | **ARCHIVE_LATER** | Empty experimental table |
| `word_usage` | 60,365 | **KEEP_CANONICAL** | Primary word usage |
| `word_usage_profiles_import` | 7,384 | **ARCHIVE_LATER** | Import staging — archive after migration |
| `zolai_bible_analysis` | 30,758 | **KEEP_CANONICAL** | Standalone enriched table |
| `zolai_grammar_patterns` | 13,519 | **CONSOLIDATE_LATER** | Merge 22-col enriched version into grammar_patterns |
| `zolai_proverbs_idioms` | 4,984 | **CONSOLIDATE_LATER** | Merge enriched proverbs into proverbs |
| `zolai_songs` | 1,032 | **KEEP_CANONICAL** | Song catalogue |
| `zolai_tone_sandhi` | 19 | **KEEP_CANONICAL** | Tone sandhi rules (19 rules) |
| `zolai_vocabulary` | 112,279 | **KEEP_CANONICAL** | Master vocabulary (richer schema) |
| `zolai_vocabulary_import` | 12,692 | **ARCHIVE_LATER** | Import staging |
| `zolai_word_usage` | 85,045 | **CONSOLIDATE_LATER** | Merge enriched columns into word_usage |
| `zvs_corrections_import` | 44 | **ARCHIVE_LATER** | Import staging — archive after migration |

## Action Summary

| Action | Count | % of 72 tables |
|--------|-------|-----------|
| KEEP_CANONICAL | 33 | 46% |
| KEEP_DERIVED | 0 | 0% |
| KEEP_IMPORT | 0 | 0% |
| CONSOLIDATE_LATER | 4 | 6% |
| ARCHIVE_LATER | 35 | 49% |

## Migration Priority

### Phase 1: Archive Import Staging (27 tables)
Move all `*_import` tables to an `_archive` schema or drop them entirely.
These are permanent fixtures of a pipeline that was designed to be transient.

### Phase 2: Consolidate Duplicates (5 merges)
1. `grammar_patterns_enhanced` → `grammar_patterns`
2. `zolai_grammar_patterns` → `grammar_patterns`
3. `zolai_word_usage` → `word_usage`
4. `zolai_proverbs_idioms` → `proverbs`
5. `training_exercises_import` → drop (exact duplicate)

### Phase 3: Drop Empty Tables (11 tables)
Review and drop all 0-row tables that represent abandoned features.

### Phase 4: Add Missing Infrastructure
- Add `created_at`/`updated_at` to all canonical tables
- Add `source`/`provenance` column to all derived tables
- Document relationships in a `SCHEMA.md`
