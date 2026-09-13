# Phase 0 Database Audit — Data Quality

**Generated:** 2026-09-13 20:31

## Tables With High NULL Rates (>50% in any column)

| Table | Worst Column | NULL % | NULL Count | Total Rows |
|-------|-------------|--------|------------|------------|
| `dictionary` | `import_batch_id` | 100.0% | 103,303 | 103,303 |
| `dictionary_en_zo` | `updated_at` | 100.0% | 113,750 | 113,750 |
| `dictionary_en_zo_import` | `deleted_at` | 100.0% | 135,276 | 135,276 |
| `dictionary_import` | `deleted_at` | 100.0% | 156,808 | 156,808 |
| `grammar_patterns` | `myanmar` | 100.0% | 5,547 | 5,547 |
| `grammar_patterns_enhanced` | `pattern_id` | 100.0% | 5,597 | 5,597 |
| `phrases` | `import_batch_id` | 100.0% | 5,000 | 5,000 |
| `proverbs` | `import_batch_id` | 100.0% | 7,736 | 7,736 |
| `tone_patterns` | `meaning_t1` | 100.0% | 118 | 118 |
| `training_exercises` | `myanmar` | 100.0% | 81,805 | 81,805 |
| `translations` | `myanmar` | 100.0% | 212,754 | 212,754 |
| `vocab` | `import_batch_id` | 100.0% | 94,458 | 94,458 |
| `word_alignments` | `myanmar` | 100.0% | 385,120 | 385,120 |
| `word_collocations` | `myanmar` | 100.0% | 5,000 | 5,000 |
| `word_usage` | `myanmar` | 100.0% | 60,365 | 60,365 |
| `zolai_bible_analysis` | `morpheme_analysis` | 100.0% | 30,758 | 30,758 |
| `zolai_grammar_patterns` | `pattern_id` | 100.0% | 13,519 | 13,519 |
| `zolai_proverbs_idioms` | `literal_translation` | 100.0% | 4,984 | 4,984 |
| `zolai_tone_sandhi` | `examples` | 100.0% | 19 | 19 |
| `zolai_vocabulary` | `compound_parts` | 100.0% | 112,279 | 112,279 |
| `zolai_word_usage` | `co_occurring` | 100.0% | 85,045 | 85,045 |
| `grammar_patterns_import` | `data` | 100.0% | 6,982 | 6,983 |
| `training_valid_sentences_import` | `corrections` | 99.9% | 4,690 | 4,693 |
| `training_exercises_import` | `word_order` | 99.8% | 81,602 | 81,805 |
| `audit_findings` | `entry_id` | 98.2% | 700 | 713 |
| `phrases_import` | `phrase` | 66.7% | 10,000 | 15,000 |
| `vocab_import` | `verified` | 65.1% | 117,458 | 180,458 |
| `bible_verses` | `zo_hcl06` | 55.1% | 34,570 | 62,751 |

## Tables With High Empty String Rates (>30%)

| Table | Column | Empty % | Empty Count | Total Rows |
|-------|--------|---------|-------------|------------|
| `dictionary_en_zo` | `update_description` | 100.0% | 113,750 | 113,750 |
| `wiki_lessons` | `update_remarks` | 100.0% | 1,688 | 1,688 |
| `vocab_import` | `notes` | 100.0% | 180,449 | 180,458 |
| `zolai_vocabulary` | `myanmar` | 100.0% | 112,271 | 112,279 |
| `vocab_import` | `pos` | 99.8% | 180,182 | 180,458 |
| `dictionary` | `update_description` | 99.8% | 103,094 | 103,303 |
| `audit_findings` | `verified_by` | 98.2% | 700 | 713 |
| `dictionary` | `pos` | 95.5% | 98,663 | 103,303 |
| `dictionary_import` | `pos` | 92.4% | 144,894 | 156,808 |
| `dictionary_import` | `update_remarks` | 92.2% | 144,589 | 156,808 |
| `dictionary_import` | `update_description` | 92.2% | 144,589 | 156,808 |
| `provenance` | `updated_at` | 89.0% | 227 | 255 |
| `data_audit_log` | `old_value` | 88.2% | 21,847 | 24,762 |
| `dictionary` | `update_remarks` | 85.6% | 88,464 | 103,303 |
| `grammar_patterns_import` | `book` | 78.5% | 5,482 | 6,983 |
| `zolai_vocabulary` | `pos` | 77.9% | 87,446 | 112,279 |
| `dictionary_en_zo` | `translations_clean` | 77.8% | 88,547 | 113,750 |
| `audit_findings` | `old_value` | 70.1% | 500 | 713 |
| `audit_findings` | `new_value` | 70.1% | 500 | 713 |
| `articles` | `date` | 65.3% | 4,163 | 6,371 |

## Empty Tables (0 rows, 11 total)

| Table | Columns | Domain | Notes |
|-------|---------|--------|-------|
| `bible_verses_enhanced` | 12 | Bible | Planned enhancement |
| `dictionary_en_my_import` | 7 | Dictionary |  |
| `dictionary_enhanced` | 15 | Dictionary | Planned enhancement |
| `dictionary_trilingual_import` | 8 | Dictionary |  |
| `gemini_model_results` | 9 | ModelTracking | Model tracking never used |
| `morph_verified` | 8 | NLP/Experimental | NLP pipeline never run |
| `pos_gold` | 7 | NLP/Experimental | NLP pipeline never run |
| `pos_verified` | 7 | NLP/Experimental | NLP pipeline never run |
| `proverbs_idioms` | 9 | Proverbs | Planned table |
| `vocabulary_enhanced` | 15 | Vocabulary | Planned enhancement |
| `word_similarity` | 9 | NLP/Experimental | Experimental |

## Suspicious Data Patterns

### Import tables with more rows than canonical
This indicates data loss during import/cleaning:
- `dictionary_import` (156,808) vs `dictionary` (103,303): Import has 53K more rows (deduplication in canonical)
- `dictionary_en_zo_import` (135,276) vs `dictionary_en_zo` (113,750): Import has 21K more rows
- `word_alignments_import` (627,000) vs `word_alignments` (385,120): Import has 242K more rows (cleaning/filtering)
- `translations_import` (135,511) vs `translations` (212,754): Import has 77K fewer rows (canonical is larger — unexpected)
- `vocab_import` (180,458) vs `vocab` (94,458): Import has 86K more rows
- `proverbs_import` (7,736) vs `proverbs` (7,736): Equal rows — import is pure duplicate

### Duplicate row counts between import and canonical
- `training_exercises` = `training_exercises_import` = 81,805 rows exactly
- `proverbs` = `proverbs_import` = 7,736 rows exactly
- `word_collocations` = `word_collocations_import` = 5,000 rows exactly

These imports appear to be exact copies of canonical data, not true staging tables.

### Tables with potential encoding issues
- Some tables may contain HTML entities or mixed encoding (see progress tracker notes)
- Zolai fields should be strictly `[a-z\-]+` pattern
