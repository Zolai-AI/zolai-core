# Phase 0 Database Audit — Import Pipeline

**Generated:** 2026-09-13 22:15

## Overview

The import pipeline uses JSONL files as staging data, importing them into `*_import` tables in `data/zolai.db`. The canonical tables are then populated from these import tables via separate processing scripts.

## Import Tables Inventory

| Import Table | Rows | Source JSONL Files | Status |
|--------------|------|-------------------|--------|
| `bible_book_analysis_import` | 65 | `bible/context/per_book_analysis.jsonl` | ✅ ACTIVE |
| `bible_chapter_analysis_import` | 1,153 | `bible/context/per_chapter_analysis.jsonl` | ✅ ACTIVE |
| `bible_verses_import` | 62,204 | `bible/parallel_corpus_v1.jsonl` | ✅ ACTIVE |
| `dictionary_import` | 156,808 | `dictionary/processed/dict_zo_en_master_v1.jsonl`, `dict__merged.jsonl`, `dict_bible_combined_v1.jsonl` | ✅ ACTIVE |
| `dictionary_en_zo_import` | 135,276 | `dictionary/processed/dict_canonical_clean.jsonl` | ✅ ACTIVE |
| `dictionary_my_import` | 7,840 | `processed/my/dict_zo_my_v1.jsonl` | ✅ ACTIVE |
| `dictionary_en_my_import` | 0 | `processed/my/dict_my_zo_v1.jsonl` | ⚠️ EMPTY |
| `dictionary_trilingual_import` | 0 | `processed/my/dict_trilingual_v1.jsonl` | ⚠️ EMPTY |
| `grammar_patterns_import` | 6,983 | `bible/grammar_patterns_v2.jsonl`, `dictionary/processed/sentence_patterns_verified.jsonl` | ✅ ACTIVE |
| `phrase_context_import` | 45,597 | `bible/context/phrase_context_map.jsonl` | ✅ ACTIVE |
| `phrases_from_bible_import` | 14,000 | `bible/phrases_from_bible.jsonl` | ✅ ACTIVE |
| `phrases_import` | 15,000 | `bible/phrases_v1.jsonl`, `dictionary/processed/phrases_verified.jsonl` | ✅ ACTIVE |
| `proverbs_import` | 7,736 | `bible/proverbs.jsonl` | ✅ ACTIVE |
| `sentence_patterns_import` | 65 | `bible/context/sentence_patterns.jsonl` | ✅ ACTIVE |
| `topic_clusters_import` | 12 | `bible/context/topic_clusters.jsonl` | ✅ ACTIVE |
| `training_corpus_qwen3_import` | 9,386 | `training/pipeline_output/training_corpus_qwen3.jsonl` | ✅ ACTIVE |
| `training_exercises_import` | 81,805 | `bible/negation_exercises.jsonl`, `question_exercises.jsonl`, `pronoun_exercises.jsonl`, `error_correction_exercises.jsonl`, `conditional_exercises.jsonl` | ✅ ACTIVE |
| `training_seed_data_import` | 500 | `training/seed_data_500_fixed.jsonl` | ✅ ACTIVE |
| `training_valid_sentences_import` | 4,693 | `training/pipeline_output/valid_sentences.jsonl` | ✅ ACTIVE |
| `translations_import` | 135,511 | `bible/translation_pairs_v1.jsonl`, `parallel/zo_en_pairs_combined_v1.jsonl` | ✅ ACTIVE |
| `vocab_import` | 180,458 | `bible/vocab_index_full.jsonl`, `dictionary/processed/vocab_verified.jsonl` | ✅ ACTIVE |
| `word_alignments_import` | 627,000 | `bible/word_alignments_v1.jsonl` | ✅ ACTIVE |
| `word_collocations_import` | 5,000 | `bible/word_collocations.jsonl` | ✅ ACTIVE |
| `word_usage_profiles_import` | 7,384 | `bible/context/word_usage_profiles.jsonl`, `bible/word_usage_profiles.jsonl` | ✅ ACTIVE |
| `zolai_vocabulary_import` | 12,692 | `bible/vocab_from_bible.jsonl` | ✅ ACTIVE |
| `zvs_corrections_import` | 44 | `dictionary/processed/dict_corrections.jsonl` | ✅ ACTIVE |

## Pipeline Architecture

```
JSONL Files (data/)
    ↓
jsonl_pipeline_v2.py (import)
    ↓
*_import tables (staging)
    ↓
Processing scripts (canonical population)
    ↓
Canonical tables (dictionary, bible_verses, etc.)
```

## Key Scripts

| Script | Purpose | Tables Affected |
|--------|---------|-----------------|
| `zolai/core/jsonl_pipeline_v2.py` | Main import pipeline | All `*_import` tables |
| `zolai/api/jsonl_router.py` | API for import operations | `jsonl_import_log` |
| `scripts/dictionary/build_dictionary_db.py` | Dictionary processing | `dictionary`, `dictionary_en_zo` |
| `scripts/bible/bible_vocab_pipeline.py` | Bible vocabulary extraction | `vocab`, `zolai_vocabulary` |

## Import Log (`jsonl_import_log`)

- **Rows:** 92 import runs tracked
- **Columns:** `id`, `batch_id`, `source_file`, `table_name`, `rows_imported`, `sha256`, `imported_at`, `version`, `status`, `error_message`
- **Purpose:** Track every JSONL import for audit and reproducibility

## Safe to Archive

| Table | Recommendation | Reason |
|-------|----------------|--------|
| `dictionary_en_my_import` | ✅ ARCHIVE | Empty, no source data available |
| `dictionary_trilingual_import` | ✅ ARCHIVE | Empty, no source data available |
| All other `*_import` tables | ⏸️ KEEP | Active staging tables for pipeline |

## Recommendations

1. **Archive empty import tables** — `dictionary_en_my_import` and `dictionary_trilingual_import` can be archived or dropped
2. **Monitor import freshness** — Check `jsonl_import_log` for stale imports (>30 days)
3. **Add row count validation** — Pipeline should verify row counts match expectations
4. **Consider incremental imports** — Current pipeline does full reloads; incremental would reduce I/O

## Table Population Flow

### Dictionary Pipeline
```
dict_zo_en_master_v1.jsonl → dictionary_import → dictionary (ZO→EN)
dict_canonical_clean.jsonl → dictionary_en_zo_import → dictionary_en_zo (EN→ZO)
dict_zo_my_v1.jsonl → dictionary_my_import → dictionary (Myanmar field)
```

### Bible Pipeline
```
parallel_corpus_v1.jsonl → bible_verses_import → bible_verses
word_alignments_v1.jsonl → word_alignments_import → word_alignments
grammar_patterns_v2.jsonl → grammar_patterns_import → grammar_patterns
```

### Training Pipeline
```
*_exercises.jsonl → training_exercises_import → training_exercises
training_corpus_qwen3.jsonl → training_corpus_qwen3_import → training_corpus_qwen3 (future)
```

## Data Lineage

All imports are tracked in `jsonl_import_log` with:
- **SHA256 hash** of source file for integrity
- **Batch ID** for grouping related imports
- **Timestamp** for freshness tracking
- **Version number** for schema evolution
- **Status** for error tracking