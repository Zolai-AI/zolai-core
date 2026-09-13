# 02 — Code Dependencies

Per-table code references extracted by grepping all `.py` files in `zolai-core/`.
**Total references found:** 17,422 across 45 tables with code references.

---

## Summary by Operation Type

| Op Type | Count | Description |
|---------|-------|-------------|
| READ | ~9,000 | SELECT, query, get, fetch, find, read, count |
| WRITE | ~4,000 | INSERT, UPDATE, DELETE, CREATE |
| PIPELINE | ~2,500 | Import, load, process |
| REFERENCE | ~1,500 | String references (docs, comments, etc.) |
| META | ~400 | PRAGMA, index, schema |

## Tables with Highest Code Reference Count

| Table | References | Primary Op | Key Files |
|-------|-----------|------------|-----------|
| `dictionary` | 800+ | READ | `zolai/core/brain.py`, `zolai/core/rag_pipeline.py` |
| `bible_verses` | 700+ | READ | `zolai/core/bible_engine.py`, `zolai/core/rag_pipeline.py` |
| `translations` | 600+ | READ | `zolai/core/rag_pipeline.py`, `zolai/api/routes/` |
| `vocab` | 550+ | READ | `zolai/core/rag_pipeline.py`, `zolai/core/brain.py` |
| `word_alignments` | 500+ | READ | `zolai/core/alignment.py`, `zolai/core/bible_engine.py` |
| `grammar_patterns` | 450+ | READ | `zolai/core/grammar_checker.py`, `zolai/core/rag_pipeline.py` |
| `training_exercises` | 400+ | READ/WRITE | `zolai/training/`, `zolai/core/training_generator.py` |
| `syllable_data` | 350+ | READ | `zolai/core/syllable_engine.py` |
| `word_usage` | 300+ | READ | `zolai/core/word_profiler.py` |
| `proverbs` | 250+ | READ | `zolai/core/rag_pipeline.py` |

## Tables with Zero Code References

These tables exist in the database but have no references in Python code:

- `audit_findings`
- `bible_book_analysis_import`
- `bible_chapter_analysis_import`
- `dictionary_en_my_import`
- `dictionary_my_import`
- `dictionary_trilingual_import`
- `morph_verified`
- `phrase_context_import`
- `pos_gold`
- `pos_verified`
- `sentence_patterns_import`
- `topic_clusters_import`
- `training_corpus_qwen3_import`
- `training_seed_data_import`
- `training_valid_sentences_import`
- `word_similarity`
- `word_usage_profiles_import`
- `zvs_corrections_import`

**Implication:** 18 tables are orphaned from code — they are either unused staging tables or placeholders for features not yet implemented.

## Key Module → Table Dependencies

| Module | Tables Read | Tables Written |
|--------|-------------|----------------|
| `zolai/core/brain.py` | dictionary, vocab, bible_verses, translations, grammar_patterns | — |
| `zolai/core/rag_pipeline.py` | dictionary, bible_verses, translations, vocab, grammar_patterns, phrases, proverbs | — |
| `zolai/core/bible_engine.py` | bible_verses, word_alignments, zolai_bible_analysis | — |
| `zolai/core/grammar_checker.py` | grammar_patterns, zolai_grammar_patterns | — |
| `zolai/core/syllable_engine.py` | syllable_data | — |
| `zolai/core/training_generator.py` | bible_verses, grammar_patterns, vocab | training_exercises |
| `zolai/data/import_pipeline.py` | — | all `*_import` tables |
| `zolai/data/build_canonical.py` | `*_import` tables | canonical tables |
