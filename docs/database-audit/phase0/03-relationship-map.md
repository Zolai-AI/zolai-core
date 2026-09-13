# Phase 0 Database Audit — Relationship Map

**Generated:** 2026-09-13 20:31

**Note:** SQLite has no explicit foreign keys. All relationships below are implied by column name patterns and data analysis.

## Key Relationships

### Bible Domain
```
bible_verses.verse_id → word_alignments.verse_id
bible_verses.verse_id → zolai_bible_analysis.verse_id
bible_verses → translations (via verse content matching)
bible_verses → bible_context (via book/chapter grouping)
bible_verses_import → bible_verses (staging → canonical)
```

### Dictionary Domain
```
dictionary (word) → zolai_vocabulary (word)
dictionary (word) → word_usage (word)
dictionary (word) → word_alignments (word_zo)
dictionary (word) → vocab (word)
dictionary_en_zo (zolai) → dictionary (word) [reverse lookup]
dictionary_import → dictionary (staging → canonical)
dictionary_en_zo_import → dictionary_en_zo (staging → canonical)
```

### Translation Domain
```
translations → bible_verses (via translated sentence matching)
translations_import → translations (staging → canonical)
```

### Vocabulary Domain
```
vocab (word) → dictionary (word)
vocab_import → vocab (staging → canonical)
zolai_vocabulary_import → zolai_vocabulary (staging → canonical)
```

### Phrase Domain
```
phrases → dictionary (via word components)
phrases_import → phrases (staging → canonical)
phrases_from_bible_import → phrases (Bible-sourced staging)
phrase_context_import → phrases (context staging)
```

### Grammar Domain
```
grammar_patterns → grammar_patterns_enhanced → zolai_grammar_patterns
grammar_patterns_import → grammar_patterns (staging → canonical)
sentence_patterns_import → grammar_patterns (staging → canonical)
```

### Word Alignment Domain
```
word_alignments.verse_id → bible_verses.verse_id
word_alignments (word_zo) → dictionary (word)
word_alignments_import → word_alignments (staging → canonical)
word_collocations → dictionary (via word columns)
word_collocations_import → word_collocations (staging → canonical)
```

### Word Usage Domain
```
word_usage (word) → dictionary (word)
word_usage_profiles_import → word_usage (staging → canonical)
zolai_word_usage → word_usage (enhanced version)
```

### Training Domain
```
training_exercises_import → training_exercises (staging → canonical)
training_seed_data_import → training_exercises (seed staging)
training_valid_sentences_import → translations (sentence staging)
training_corpus_qwen3_import → training_exercises (model staging)
training_runs → training_exercises (run tracking)
```

### Proverbs Domain
```
proverbs_import → proverbs (staging → canonical)
proverbs_idioms (empty) → planned version
zolai_proverbs_idioms → proverbs (enhanced version)
```

### Wiki Domain
```
wiki_content → wiki_lessons (content → lesson mapping)
wiki_content_fts* → wiki_content (FTS search index)
```

### Audit Domain
```
data_audit_log → all tables (change tracking)
audit_findings → data_audit_log (finding details)
jsonl_import_log → import staging tables (import tracking)
provenance → source files (file tracking)
```

## Shared Column Index

Columns that appear across 3+ canonical tables (potential join keys):

| Column | Tables |
|--------|--------|
| `word` | `audit_findings`, `syllable_data`, `tone_patterns`, `word_usage`, `zolai_word_usage` |
| `book` | `bible_context`, `bible_verses`, `word_usage` |
| `chapter` | `bible_context`, `bible_verses`, `zolai_bible_analysis` |
| `category` | `proverbs`, `zolai_proverbs_idioms` |
| `source` | `audit_findings`, `dictionary`, `dictionary_en_zo`, `provenance`, `proverbs`, `training_exercises`, `translations`, `zolai_songs` |

## Data Flow Diagram

```
                    ┌─────────────────────────────────────┐
                    │         IMPORT SOURCES              │
                    ├─────────────────────────────────────┤
                    │ *_import tables (staging)           │
                    │ jsonl_import_log (92 runs)          │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │       CANONICAL TABLES              │
                    ├─────────────────────────────────────┤
                    │ dictionary / dictionary_en_zo       │
                    │ bible_verses                        │
                    │ translations                        │
                    │ vocab / zolai_vocabulary            │
                    │ grammar_patterns + enhanced         │
                    │ word_alignments / word_usage        │
                    │ phrases / proverbs                  │
                    │ training_exercises                  │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    ┌─────────▼─────────┐ ┌───────▼───────┐ ┌─────────▼─────────┐
    │   zolai-core      │ │   zolai-web   │ │  zolai-tauri      │
    │   (RAG + ngram)   │ │   (online)    │ │  (offline)        │
    └───────────────────┘ └───────────────┘ └───────────────────┘
```
