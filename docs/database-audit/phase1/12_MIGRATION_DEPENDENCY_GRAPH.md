# 12 — Migration Dependency Graph

Table dependency chain: which must be migrated first.

---

## Dependency Levels

```
Level 0 (Foundation — no dependencies):
├── bible_verses
├── dictionary
├── dictionary_en_zo
├── zolai_tone_sandhi
└── tone_patterns

Level 1 (Depends on Level 0):
├── word_alignments (depends on: bible_verses)
├── translations (depends on: bible_verses)
├── vocab (depends on: dictionary, bible_verses)
├── grammar_patterns (depends on: dictionary, bible_verses)
├── phrases (depends on: dictionary, bible_verses)
├── proverbs (depends on: dictionary, bible_verses)
├── word_usage (depends on: bible_verses)
├── syllable_data (depends on: dictionary)
└── articles (independent)

Level 2 (Depends on Level 1):
├── word_collocations (depends on: word_alignments)
├── training_exercises (depends on: grammar_patterns, vocab, bible_verses)
├── bible_context (depends on: bible_verses)
├── wiki_content (independent)
└── wiki_lessons (depends on: wiki_content)

Level 3 (Derived — depends on multiple Level 1-2 tables):
├── zolai_vocabulary (depends on: vocab, dictionary, zolai_vocabulary_import)
├── zolai_bible_analysis (depends on: bible_verses, word_alignments)
├── zolai_word_usage (depends on: word_usage, bible_verses)
├── zolai_grammar_patterns (depends on: grammar_patterns, grammar_patterns_import)
├── zolai_proverbs_idioms (depends on: proverbs)
└── zolai_songs (independent)

Level 4 (Infrastructure — depends on all):
├── data_audit_log
├── provenance
├── jsonl_import_log
└── audit_findings
```

## Migration Order (Recommended)

### Phase A: Foundation Tables (migrate first)

| Priority | Table | Reason |
|----------|-------|--------|
| 1 | `bible_verses` | Foundation for translations, alignments, analysis |
| 2 | `dictionary` | Foundation for vocabulary, grammar, phrases |
| 3 | `dictionary_en_zo` | Foundation for EN→ZO lookups |
| 4 | `zolai_tone_sandhi` | Foundation for tone analysis |
| 5 | `tone_patterns` | Foundation for tone analysis |

### Phase B: Level 1 Tables (depend on foundation)

| Priority | Table | Dependencies |
|----------|-------|-------------|
| 6 | `word_alignments` | `bible_verses` |
| 7 | `translations` | `bible_verses` |
| 8 | `vocab` | `dictionary`, `bible_verses` |
| 9 | `grammar_patterns` | `dictionary`, `bible_verses` |
| 10 | `phrases` | `dictionary`, `bible_verses` |
| 11 | `proverbs` | `dictionary`, `bible_verses` |
| 12 | `word_usage` | `bible_verses` |
| 13 | `syllable_data` | `dictionary` |
| 14 | `articles` | None |

### Phase C: Level 2 Tables

| Priority | Table | Dependencies |
|----------|-------|-------------|
| 15 | `word_collocations` | `word_alignments` |
| 16 | `training_exercises` | `grammar_patterns`, `vocab`, `bible_verses` |
| 17 | `bible_context` | `bible_verses` |
| 18 | `wiki_content` | None |
| 19 | `wiki_lessons` | `wiki_content` |

### Phase D: Derived Tables

| Priority | Table | Dependencies |
|----------|-------|-------------|
| 20 | `zolai_vocabulary` | `vocab`, `dictionary` |
| 21 | `zolai_bible_analysis` | `bible_verses`, `word_alignments` |
| 22 | `zolai_word_usage` | `word_usage`, `bible_verses` |
| 23 | `zolai_grammar_patterns` | `grammar_patterns` |
| 24 | `zolai_proverbs_idioms` | `proverbs` |
| 25 | `zolai_songs` | None |

### Phase E: Infrastructure

| Priority | Table | Dependencies |
|----------|-------|-------------|
| 26 | `data_audit_log` | All |
| 27 | `provenance` | All |
| 28 | `jsonl_import_log` | All |
| 29 | `audit_findings` | All |

## Tables to Drop (No Migration Needed)

| Table | Reason |
|-------|--------|
| `dictionary_import` | Staging only; canonical is subset |
| `dictionary_en_zo_import` | Staging only |
| `dictionary_my_import` | Staging only |
| `dictionary_en_my_import` | Empty |
| `dictionary_trilingual_import` | Empty |
| `bible_verses_import` | Staging only |
| `grammar_patterns_import` | Staging only |
| `phrases_import` | Staging only |
| `phrases_from_bible_import` | Staging only |
| `vocab_import` | Staging only |
| `translations_import` | Staging only |
| `word_alignments_import` | Staging only |
| `word_collocations_import` | Staging only |
| `training_exercises_import` | Staging only |
| `word_usage_profiles_import` | Staging only |
| `bible_book_analysis_import` | Staging only |
| `bible_chapter_analysis_import` | Staging only |
| `sentence_patterns_import` | Staging only |
| `topic_clusters_import` | Staging only |
| `training_corpus_qwen3_import` | Staging only |
| `training_seed_data_import` | Staging only |
| `training_valid_sentences_import` | Staging only |
| `zolai_vocabulary_import` | Staging only |
| `zvs_corrections_import` | Staging only |
| `proverbs_import` | Staging only |
| `grammar_patterns_enhanced` | Superseded by `zolai_grammar_patterns` |
| `pos_gold` | Empty placeholder |
| `pos_verified` | Empty placeholder |
| `morph_verified` | Empty placeholder |
| `word_similarity` | Empty placeholder |
| `gemini_model_results` | Empty placeholder |
| `dictionary_enhanced` | Empty placeholder |
| `vocabulary_enhanced` | Empty placeholder |
| `proverbs_idioms` | Empty placeholder |
| `bible_verses_enhanced` | Empty placeholder |
| `training_runs` | Metadata only |
| `wiki_content_fts*` (5 tables) | Auto-generated by SQLite FTS |
