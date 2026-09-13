# Phase 0 Database Audit — Duplicate Analysis

**Generated:** 2026-09-13 20:31

## Table Family Comparison

### Dictionary (ZO→EN)

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `dictionary` | 103,303 | 18 | Canonical |
| `dictionary_import` | 156,808 | 30 | Import staging (raw pipeline output) |
| `dictionary_enhanced` | 0 | 15 | Enhanced version (extended columns) |

### Dictionary (EN→ZO)

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `dictionary_en_zo` | 113,750 | 18 | Canonical |
| `dictionary_en_zo_import` | 135,276 | 38 | Import staging (raw pipeline output) |

### Dictionary (EN→MY)

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `dictionary_my_import` | 7,840 | 7 | Import staging (raw pipeline output) |
| `dictionary_en_my_import` | 0 | 7 | Import staging (raw pipeline output) |

### Dictionary (Trilingual)

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `dictionary_trilingual_import` | 0 | 8 | Import staging (raw pipeline output) |

### Bible Verses

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `bible_verses` | 62,751 | 18 | Canonical |
| `bible_verses_import` | 62,204 | 12 | Import staging (raw pipeline output) |
| `bible_verses_enhanced` | 0 | 12 | Enhanced version (extended columns) |

### Translations

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `translations` | 212,754 | 11 | Canonical |
| `translations_import` | 135,511 | 10 | Import staging (raw pipeline output) |

### Vocabulary

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `vocab` | 94,458 | 11 | Canonical |
| `vocab_import` | 180,458 | 14 | Import staging (raw pipeline output) |
| `zolai_vocabulary` | 112,279 | 31 | Zolai-prefixed enriched version |
| `zolai_vocabulary_import` | 12,692 | 9 | Import staging (raw pipeline output) |
| `vocabulary_enhanced` | 0 | 15 | Enhanced version (extended columns) |

**Schema overlap:**
- Common columns across canonical tables: 7
- Unique to `vocab`: books, examples, frequency, headword
- Unique to `zolai_vocabulary`: bible_books, compound_parts, confidence, created_at, derivation, example_en, example_zo, frequency_bible, frequency_corpus, frequency_songs, is_compound, meaning_t1, meaning_t3, meaning_t4, notes, pos, register, root_word, source_category, source_priority, tone_category, updated_at, zolai, zvs_compliant

### Phrases

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `phrases` | 5,000 | 9 | Canonical |
| `phrases_import` | 15,000 | 12 | Import staging (raw pipeline output) |
| `phrases_from_bible_import` | 14,000 | 8 | Import staging (raw pipeline output) |
| `phrase_context_import` | 45,597 | 10 | Import staging (raw pipeline output) |

### Grammar

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `grammar_patterns` | 5,547 | 12 | Canonical |
| `grammar_patterns_import` | 6,983 | 19 | Import staging (raw pipeline output) |
| `grammar_patterns_enhanced` | 5,597 | 13 | Enhanced version (extended columns) |
| `zolai_grammar_patterns` | 13,519 | 22 | Zolai-prefixed enriched version |
| `sentence_patterns_import` | 65 | 13 | Import staging (raw pipeline output) |

**Schema overlap:**
- Common columns across canonical tables: 4
- Unique to `grammar_patterns`: description, examples, function, import_batch_id, imported_at, myanmar, pattern, version
- Unique to `grammar_patterns_enhanced`: page_section, tone_category
- Unique to `zolai_grammar_patterns`: agreement, aspect, ergative, morpheme_breakdown, negation_type, pattern_name, question_type, source_line, structure, tense, tone_pattern

### Word Alignments

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `word_alignments` | 385,120 | 10 | Canonical |
| `word_alignments_import` | 627,000 | 11 | Import staging (raw pipeline output) |

### Word Collocations

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `word_collocations` | 5,000 | 10 | Canonical |
| `word_collocations_import` | 5,000 | 7 | Import staging (raw pipeline output) |

### Word Usage

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `word_usage` | 60,365 | 11 | Canonical |
| `word_usage_profiles_import` | 7,384 | 10 | Import staging (raw pipeline output) |
| `zolai_word_usage` | 85,045 | 10 | Zolai-prefixed enriched version |

**Schema overlap:**
- Common columns across canonical tables: 3
- Unique to `word_usage`: book, co_occurring_words, import_batch_id, imported_at, meaning_shifts, myanmar, total_freq, version
- Unique to `zolai_word_usage`: book_code, co_occurring, contexts, created_at, frequency, meanings, source_category

### Training

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `training_exercises` | 81,805 | 11 | Canonical |
| `training_exercises_import` | 81,805 | 14 | Import staging (raw pipeline output) |
| `training_seed_data_import` | 500 | 10 | Import staging (raw pipeline output) |
| `training_valid_sentences_import` | 4,693 | 13 | Import staging (raw pipeline output) |
| `training_corpus_qwen3_import` | 9,386 | 8 | Import staging (raw pipeline output) |
| `training_runs` | 2 | 17 | Canonical |

**Schema overlap:**
- Common columns across canonical tables: 1
- Unique to `training_exercises`: difficulty, english, exercise_type, import_batch_id, imported_at, myanmar, source, source_file, version, zolai
- Unique to `training_runs`: completed_at, created_at, dataset_name, details, entry_count, entry_version, metrics_json, model_name, passed_tests, score, status, test_date, test_type, total_tests, update_remarks, updated_at

### Proverbs

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `proverbs` | 7,736 | 9 | Canonical |
| `proverbs_import` | 7,736 | 13 | Import staging (raw pipeline output) |
| `proverbs_idioms` | 0 | 9 | Canonical |
| `zolai_proverbs_idioms` | 4,984 | 11 | Zolai-prefixed enriched version |

**Schema overlap:**
- Common columns across canonical tables: 4
- Unique to `proverbs`: english, import_batch_id, imported_at, source, version
- Unique to `zolai_proverbs_idioms`: created_at, cultural_context, english_translation, literal_translation, morpheme_breakdown, source_category, theme

### Wiki

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `wiki_content` | 1,688 | 11 | Canonical |
| `wiki_lessons` | 1,688 | 12 | Canonical |

**Schema overlap:**
- Common columns across canonical tables: 6
- Unique to `wiki_content`: content, content_hash, section_count, source_path, wiki_category
- Unique to `wiki_lessons`: content_summary, grammar_patterns, lesson_type, source_file, update_remarks, vocabulary_list

### Syllable

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `syllable_data` | 189,554 | 10 | Canonical |

### Tone

| Table | Rows | Columns | Purpose |
|-------|------|---------|--------|
| `zolai_tone_sandhi` | 19 | 11 | Zolai-prefixed enriched version |
| `tone_patterns` | 118 | 10 | Canonical |

**Schema overlap:**
- Common columns across canonical tables: 4
- Unique to `zolai_tone_sandhi`: condition, domain, examples, rule_name, rule_number, surface_pattern, underlying_pattern
- Unique to `tone_patterns`: meaning_t1, meaning_t3, meaning_t4, sandhi_rules, tone_category, word

## Key Findings

### Dictionary Family (5 tables)
- `dictionary` (103K) is the canonical ZO→EN table
- `dictionary_import` (156K) is the raw import — larger due to deduplication on canonical
- `dictionary_enhanced` (0 rows) — planned enrichment never implemented
- Recommendation: Keep `dictionary` as canonical, archive `*_import`, drop `dictionary_enhanced`

### Bible Verses Family (3 tables)
- `bible_verses` (62K) is canonical with 18 columns (rich schema)
- `bible_verses_import` (62K) is the raw import with 12 columns
- `bible_verses_enhanced` (0 rows) — planned enrichment never implemented
- Recommendation: Keep `bible_verses`, archive `bible_verses_import`, drop `bible_verses_enhanced`

### Grammar Family (5 tables)
- `grammar_patterns` (5.5K) is the base canonical
- `grammar_patterns_enhanced` (5.5K) adds 1 extra column
- `zolai_grammar_patterns` (13.5K) is a Zolai-prefixed enriched version (2.4x larger!)
- `grammar_patterns_import` (6.9K) is the raw import
- `sentence_patterns_import` (65) is a small supplementary import
- Recommendation: Merge all three canonical into `grammar_patterns`, keep Zolai-prefixed columns

### Training Family (6 tables!)
- `training_exercises` (81K) is canonical
- `training_exercises_import` (81K) is an exact duplicate
- Three additional import tables for different data sources
- `training_runs` (2 rows) tracks training metadata
- Recommendation: Keep `training_exercises`, archive all `*_import` tables

### Proverbs Family (4 tables)
- `proverbs` (7.7K) is canonical
- `proverbs_idioms` (0 rows) — empty planned table
- `zolai_proverbs_idioms` (4.9K) is the enriched version
- `proverbs_import` (7.7K) is an exact duplicate
- Recommendation: Merge `zolai_proverbs_idioms` into `proverbs`, archive `proverbs_import`, drop `proverbs_idioms`

### Wiki Family (2 canonical + 5 FTS tables)
- `wiki_content` (1.6K) is the canonical content
- `wiki_lessons` (1.6K) is the lesson mapping
- Five FTS tables are auto-managed by SQLite FTS5
- Recommendation: Keep as-is; FTS tables are SQLite-managed
