# Phase 0 Database Audit — Schema Analysis

**Generated:** 2026-09-13 20:31

## Tables Without Primary Key (28)

| Table | Row Count | Columns |
|-------|-----------|---------|
| `bible_book_analysis_import` | 65 | book, book_name, genre, verse_count, unique_words, total_tokens, avg_sentence_length, top_words, book_specific_words, import_batch_id, source_file, version, imported_at |
| `bible_chapter_analysis_import` | 1,153 | book, chapter, verse_count, unique_words, avg_length, top_words, theme_words, import_batch_id, source_file, version, imported_at |
| `bible_verses_import` | 62,204 | book, book_name, chapter, verse, ref, zo_tdb77, zo_tedim2010, en_kJV, import_batch_id, source_file, version, imported_at |
| `dictionary_en_my_import` | 0 | myanmar, zolai, source, import_batch_id, source_file, version, imported_at |
| `dictionary_en_zo_import` | 135,276 | headword, translations, pos, explanations, sources, category, translations_clean, import_batch_id, source_file, version, imported_at, english, zolai, accuracy, book_count, source, all_books, dialect, first_book, zvs_correction, variants, examples, zvs_correct, same_meaning, antonyms, reverse, related, usage_notes, synonyms, example_en, example_zo, note, original_english, cefr, example_zo_2, example_en_2, is_deleted, deleted_at |
| `dictionary_import` | 156,808 | zolai, english, source, pos, entry_version, update_remarks, update_description, zvs_compliance_status, id, raw_json, import_batch_id, source_file, version, imported_at, existing_match, example, sense, myanmar_word, headword, zolai_word, zolai_normalized, translation, first_book, all_books, accuracy, translations, dialect, book_count, is_deleted, deleted_at |
| `dictionary_my_import` | 7,840 | zolai, myanmar, source, import_batch_id, source_file, version, imported_at |
| `dictionary_trilingual_import` | 0 | zolai, myanmar, english, source, import_batch_id, source_file, version, imported_at |
| `grammar_patterns_import` | 6,983 | id, pattern, description, structure, function, frequency, confidence, examples, source, book, import_batch_id, source_file, version, imported_at, category, data, zo, en, ref |
| `phrase_context_import` | 45,597 | phrase, type, frequency, locations, context_words, is_idiomatic, import_batch_id, source_file, version, imported_at |
| `phrases_from_bible_import` | 14,000 | phrase, frequency, type, examples, import_batch_id, source_file, version, imported_at |
| `phrases_import` | 15,000 | zo, frequency, examples, confidence, import_batch_id, source_file, version, imported_at, phrase, verified, source, translation |
| `proverbs_import` | 7,736 | zo, en, book, chapter, verse, ref, type, is_proverbial_book, has_idiom_pattern, import_batch_id, source_file, version, imported_at |
| `sentence_patterns_import` | 65 | book, book_name, genre, verse_count, sov_rate, negation_rate, question_rate, tense_distribution, avg_complexity, import_batch_id, source_file, version, imported_at |
| `sqlite_sequence` | 15 | name, seq |
| `topic_clusters_import` | 12 | topic, keyword_match_count, characteristic_words, chapters, chapter_count, usage_examples, import_batch_id, source_file, version, imported_at |
| `training_corpus_qwen3_import` | 9,386 | messages, task, pattern, source, import_batch_id, source_file, version, imported_at |
| `training_exercises_import` | 81,805 | instruction, input, output, negation_type, reference, confidence, import_batch_id, source_file, version, imported_at, question_type, word_order, grammar_point, error_type |
| `training_seed_data_import` | 500 | zolai, english, source, type, quality, context, import_batch_id, source_file, version, imported_at |
| `training_valid_sentences_import` | 4,693 | zolai, english, pattern, source, deep_score, checks, import_batch_id, source_file, version, imported_at, context_score, corrections, corrected |
| `translations_import` | 135,511 | zolai, english, dialect, source, reference, category, import_batch_id, source_file, version, imported_at |
| `vocab_import` | 180,458 | headword, english, frequency, books, book_count, examples, pos, notes, source, import_batch_id, source_file, version, imported_at, verified |
| `wiki_content_fts` | 1,688 | title, content |
| `word_alignments_import` | 627,000 | zo_word, en_word, en_position, confidence, source, ref, book, import_batch_id, source_file, version, imported_at |
| `word_collocations_import` | 5,000 | collocation, frequency, examples, import_batch_id, source_file, version, imported_at |
| `word_usage_profiles_import` | 7,384 | word, total_freq, books_found, per_book_distribution, meaning_shifts, all_translations, import_batch_id, source_file, version, imported_at |
| `zolai_vocabulary_import` | 12,692 | word, frequency, definition, examples, verified, import_batch_id, source_file, version, imported_at |
| `zvs_corrections_import` | 44 | zolai, old_english_clean, new_english_clean, reason, bible_examples, sample_ref, import_batch_id, source_file, version, imported_at |

## Tables With Composite Primary Keys (1)

| Table | PK Columns |
|-------|------------|
| `wiki_content_fts_idx` | segid, term |

## Tables Without Timestamps (5)

These canonical (non-import, non-system) tables lack `created_at`/`updated_at`/`date` columns:

| Table | Domain | Row Count |
|-------|--------|-----------|
| `bible_context` | Bible | 1,228 |
| `data_audit_log` | Audit | 24,762 |
| `morph_verified` | NLP/Experimental | 0 |
| `pos_verified` | NLP/Experimental | 0 |
| `zolai_songs` | Songs | 1,032 |

## Tables Without Provenance/Source Tracking (6)

These canonical tables with data lack a `source`/`provenance`/`origin` column:

| Table | Domain | Row Count |
|-------|--------|-----------|
| `articles` | Wiki/Content | 6,371 |
| `bible_context` | Bible | 1,228 |
| `data_audit_log` | Audit | 24,762 |
| `training_runs` | Training | 2 |
| `wiki_content` | Wiki/Content | 1,688 |
| `zolai_bible_analysis` | Bible | 30,758 |

## Inconsistent ID Patterns

| Table | ID Columns | Notes |
|-------|------------|-------|
| `articles` | id | |
| `audit_findings` | id, confidence, entry_id | |
| `bible_context` | id | |
| `bible_verses` | id, import_batch_id | |
| `bible_verses_enhanced` | id | |
| `data_audit_log` | id, row_id | |
| `dictionary` | id, import_batch_id | |
| `dictionary_en_zo` | id, import_batch_id | |
| `dictionary_enhanced` | id, confidence | |
| `gemini_model_results` | id, confidence | |
| `grammar_patterns` | id, pattern_id, import_batch_id | |
| `grammar_patterns_enhanced` | id, pattern_id, confidence | |
| `morph_verified` | id | |
| `phrases` | id, import_batch_id | |
| `pos_gold` | id | |
| `pos_verified` | id, confidence | |
| `provenance` | id | |
| `proverbs` | id, import_batch_id | |
| `proverbs_idioms` | id | |
| `syllable_data` | id, confidence, source_id | |
| `tone_patterns` | id | |
| `training_exercises` | id, import_batch_id | |
| `training_runs` | id | |
| `translations` | id, confidence, import_batch_id | |
| `vocab` | id, import_batch_id | |
| `vocabulary_enhanced` | id | |
| `wiki_content` | id | |
| `wiki_lessons` | id | |
| `word_alignments` | id, import_batch_id | |
| `word_collocations` | id, import_batch_id | |
| `word_similarity` | id | |
| `word_usage` | id, import_batch_id | |
| `zolai_bible_analysis` | id | |
| `zolai_grammar_patterns` | id, pattern_id, confidence | |
| `zolai_proverbs_idioms` | id | |
| `zolai_songs` | id | |
| `zolai_tone_sandhi` | id | |
| `zolai_vocabulary` | id, confidence, import_batch_id | |
| `zolai_word_usage` | id | |

## Suspicious Schemas

### Autoincrement without Integer PK
Tables using `INTEGER PRIMARY KEY` (SQLite alias for rowid) vs explicit `id INTEGER PRIMARY KEY AUTOINCREMENT`:

- `audit_findings`: Uses AUTOINCREMENT
- `bible_verses_enhanced`: Uses AUTOINCREMENT
- `dictionary_enhanced`: Uses AUTOINCREMENT
- `gemini_model_results`: Uses AUTOINCREMENT
- `grammar_patterns_enhanced`: Uses AUTOINCREMENT
- `morph_verified`: Uses AUTOINCREMENT
- `pos_gold`: Uses AUTOINCREMENT
- `pos_verified`: Uses AUTOINCREMENT
- `proverbs_idioms`: Uses AUTOINCREMENT
- `syllable_data`: Uses AUTOINCREMENT
- `tone_patterns`: Uses AUTOINCREMENT
- `training_runs`: Uses AUTOINCREMENT
- `vocabulary_enhanced`: Uses AUTOINCREMENT
- `wiki_content`: Uses AUTOINCREMENT
- `wiki_lessons`: Uses AUTOINCREMENT
- `word_similarity`: Uses AUTOINCREMENT
- `zolai_bible_analysis`: Uses AUTOINCREMENT
- `zolai_grammar_patterns`: Uses AUTOINCREMENT
- `zolai_proverbs_idioms`: Uses AUTOINCREMENT
- `zolai_songs`: Uses AUTOINCREMENT
- `zolai_tone_sandhi`: Uses AUTOINCREMENT
- `zolai_vocabulary`: Uses AUTOINCREMENT
- `zolai_word_usage`: Uses AUTOINCREMENT

### Tables with `word` column but different types
The word/lemma column should be consistently typed:

- `audit_findings`: `word` is `TEXT`
- `morph_verified`: `word` is `TEXT`
- `pos_verified`: `word` is `TEXT`
- `syllable_data`: `word` is `TEXT`
- `tone_patterns`: `word` is `TEXT`
- `word_usage`: `word` is `VARCHAR`
- `zolai_word_usage`: `word` is `TEXT`
