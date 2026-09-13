# 05 — Data Lineage

Import pipeline trace: which script populates which table, from what source.

---

## Pipeline Flow

```
Source Files (JSONL)
    ↓
jsonl_import.py (import_pipeline.py)
    ↓
*_import tables (24 staging tables)
    ↓
build_canonical.py (cleanup/dedup/merge)
    ↓
Canonical tables (24 runtime tables)
    ↓
Analysis scripts (zolai_bible_analysis, zolai_grammar_patterns, etc.)
    ↓
Derived tables (6 enhanced tables)
```

## Import Log Summary (92 import runs)

| Target Table | Total Rows Imported | Runs | Status | Source Files |
|-------------|-------------------|------|--------|-------------|
| `translations_import` | 105,511 | 1 | ✅ completed | bible translations (TDB, TB77, T2010, TBR17) |
| `dictionary_import` | 100,808 | 5 | ✅ completed | dictionary processed JSONLs |
| `vocab_import` | 94,458 | 1 | ✅ completed | vocab JSONL |
| `dictionary_en_zo_import` | 85,276 | 1 | ✅ completed | EN→ZO dictionary JSONL |
| `training_exercises_import` | 81,805 | 5 | ✅ completed | negation/question/pronoun/error/conditional exercises |
| `bible_verses_import` | 62,204 | 2 | ✅ completed | parallel corpus JSONLs |
| `phrase_context_import` | 45,597 | 1 | ✅ completed | phrase context JSONL |
| `bible_verses` (direct) | 31,102 | 2 | 1 failed | parallel corpus (direct to canonical) |
| `phrases_import` | 15,000 | 4 | ✅ completed | phrases verified JSONLs |
| `phrases_from_bible_import` | 14,000 | 2 | ✅ completed | Bible-derived phrases |
| `zolai_vocabulary_import` | 12,692 | 2 | ✅ completed | vocabulary JSONLs |
| `word_alignments_import` | ~627,000 | 1 | ✅ completed | word alignments JSONL |
| `grammar_patterns_import` | 6,983 | 1 | ✅ completed | sentence patterns JSONL |
| `word_collocations_import` | 5,000 | 1 | ✅ completed | word collocations JSONL |
| `word_usage_profiles_import` | 7,384 | 1 | ✅ completed | word usage profiles JSONL |
| `proverbs_import` | 7,736 | 1 | ✅ completed | proverbs JSONL |
| `dictionary_my_import` | 7,840 | 1 | ✅ completed | ZO→MY dictionary JSONL |
| `dictionary_en_my_import` | 7,840 | 1 | ✅ completed | EN→MY dictionary JSONL |
| `dictionary_trilingual_import` | 7,840 | 1 | ✅ completed | trilingual dictionary JSONL |
| `training_corpus_qwen3_import` | 9,386 | 1 | ✅ completed | Qwen3 training corpus |
| `training_valid_sentences_import` | 4,693 | 1 | ✅ completed | valid sentences JSONL |
| `training_seed_data_import` | 500 | 1 | ✅ completed | seed data JSONL |
| `bible_chapter_analysis_import` | 1,153 | 1 | ✅ completed | Bible chapter analysis JSONL |
| `bible_book_analysis_import` | 65 | 1 | ✅ completed | Bible book analysis JSONL |
| `sentence_patterns_import` | 65 | 1 | ✅ completed | sentence patterns JSONL |
| `topic_clusters_import` | 12 | 1 | ✅ completed | topic clusters JSONL |
| `zvs_corrections_import` | 44 | 1 | ✅ completed | ZVS corrections JSONL |

## Failed Imports

| Table | Source | Error |
|-------|--------|-------|
| `bible_verses` | `bible/parallel_corpus_v1.jsonl` | Broken pipe (id=1) |
| `word_alignments` | `bible/word_alignments_v1.jsonl` | Broken pipe (id=2) |
| `grammar_patterns` | `dictionary/processed/sentence_patterns_verified.jsonl` | Broken pipe (id=3) |
| `phrases` | `dictionary/processed/phrases_verified.jsonl` | Broken pipe (id=4) |

All broken pipe errors occurred in the initial batch (batch_id: `43a5c4a7-...`), likely during the first pipeline run. Subsequent runs succeeded.

## Source File → Canonical Table Mapping

| Source Category | Canonical Table | Import Table | Enrichment Notes |
|----------------|----------------|-------------|-----------------|
| Bible translations | `bible_verses` | `bible_verses_import` | EN/ZO/MY parallel alignment |
| Dictionary (ZO→EN) | `dictionary` | `dictionary_import` | Curated subset (103K of 157K) |
| Dictionary (EN→ZO) | `dictionary_en_zo` | `dictionary_en_zo_import` | Curated subset |
| Bible verse translations | `translations` | `translations_import` | Enriched beyond import (+77K) |
| Word alignments | `word_alignments` | `word_alignments_import` | Filtered and enriched |
| Vocabulary | `vocab` | `vocab_import` | Curated subset (94K of 180K) |
| Grammar patterns | `grammar_patterns` | `grammar_patterns_import` | Curated subset |
| Proverbs | `proverbs` | `proverbs_import` | 1:1 copy |
| Phrases | `phrases` | `phrases_import` | Curated subset |
| Training exercises | `training_exercises` | `training_exercises_import` | 1:1 copy |
| Word collocations | `word_collocations` | `word_collocations_import` | 1:1 copy |
