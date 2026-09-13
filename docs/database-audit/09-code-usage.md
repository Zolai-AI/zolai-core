# Phase 0 Database Audit — Code Usage Analysis

**Generated:** 2026-09-13 22:15

## Overview

Analysis of which database tables are referenced in the zolai-core codebase, classified by usage status.

## Table Usage Classification

### ACTIVE Tables (Referenced in Code)

| Table | Files | Classification | Evidence |
|-------|-------|----------------|----------|
| `dictionary` | 148 | **ACTIVE** | Core dictionary, referenced everywhere |
| `vocab` | 92 | **ACTIVE** | Vocabulary index, used in learning features |
| `translations` | 87 | **ACTIVE** | EN↔ZO sentence pairs, translation features |
| `phrases` | 43 | **ACTIVE** | Multi-word expressions, phrase matching |
| `articles` | 35 | **ACTIVE** | Wiki content, reference articles |
| `dictionary_en_zo` | 26 | **ACTIVE** | EN→ZO dictionary, reverse lookup |
| `grammar_patterns` | 25 | **ACTIVE** | Grammar rules, pattern matching |
| `bible_verses` | 25 | **ACTIVE** | Bible corpus, verse search/analysis |
| `provenance` | 17 | **ACTIVE** | Source tracking, audit trail |
| `word_usage` | 16 | **ACTIVE** | Per-book word profiles |
| `proverbs` | 14 | **ACTIVE** | Proverbs collection |
| `training_exercises` | 12 | **ACTIVE** | Training data for exercises |
| `word_alignments` | 12 | **ACTIVE** | Word-level ZO↔EN alignment |
| `bible_context` | 11 | **ACTIVE** | Book-level analysis |
| `data_audit_log` | 10 | **ACTIVE** | Change tracking |
| `word_collocations` | 10 | **ACTIVE** | Word pair frequencies |
| `jsonl_import_log` | 5 | **ACTIVE** | Import pipeline tracking |
| `simbu` | 4 | **ACTIVE** | Simbu data (specific use case) |
| `zolai_vocabulary` | 4 | **ACTIVE** | Master vocabulary (enriched) |
| `training_corpus_qwen3_import` | 3 | **ACTIVE** | Qwen3 training format |
| `syllable_data` | 3 | **ACTIVE** | Syllable segmentation |
| `wiki_content` | 3 | **ACTIVE** | Wiki content for RAG |
| `wiki_lessons` | 3 | **ACTIVE** | Wiki-driven lessons |
| `zolai_bible_analysis` | 3 | **ACTIVE** | Verse-level analysis |
| `zolai_grammar_patterns` | 3 | **ACTIVE** | Enriched grammar patterns |
| `zolai_proverbs_idioms` | 3 | **ACTIVE** | Enriched proverbs |
| `zolai_songs` | 2 | **ACTIVE** | Song catalogue |
| `zolai_tone_sandhi` | 2 | **ACTIVE** | Tone sandhi rules |
| `zolai_word_usage` | 2 | **ACTIVE** | Enriched word usage |
| `tone_patterns` | 2 | **ACTIVE** | Tone patterns |
| `audit_findings` | 2 | **ACTIVE** | Audit findings |
| `training_runs` | 2 | **ACTIVE** | Training run tracking |

### LEGACY Tables (Referenced but Deprecated)

| Table | Files | Classification | Evidence |
|-------|-------|----------------|----------|
| `dictionary_import` | 1 | **LEGACY** | Staging table, canonical is `dictionary` |
| `dictionary_en_zo_import` | 1 | **LEGACY** | Staging table, canonical is `dictionary_en_zo` |
| `bible_verses_import` | 1 | **LEGACY** | Staging table, canonical is `bible_verses` |
| `grammar_patterns_import` | 1 | **LEGACY** | Staging table, canonical is `grammar_patterns` |
| `phrases_import` | 1 | **LEGACY** | Staging table, canonical is `phrases` |
| `vocab_import` | 1 | **LEGACY** | Staging table, canonical is `vocab` |
| `translations_import` | 1 | **LEGACY** | Staging table, canonical is `translations` |
| `training_exercises_import` | 1 | **LEGACY** | Staging table, canonical is `training_exercises` |
| `word_alignments_import` | 1 | **LEGACY** | Staging table, canonical is `word_alignments` |
| `word_collocations_import` | 1 | **LEGACY** | Staging table, canonical is `word_collocations` |
| `word_usage_profiles_import` | 1 | **LEGACY** | Staging table, canonical is `word_usage` |
| `proverbs_import` | 1 | **LEGACY** | Staging table, canonical is `proverbs` |

### UNUSED Tables (No Code References)

| Table | Files | Classification | Evidence |
|-------|-------|----------------|----------|
| `bible_verses_enhanced` | 0 | **UNUSED** | Empty, no code references |
| `dictionary_enhanced` | 0 | **UNUSED** | Empty, no code references |
| `gemini_model_results` | 0 | **UNUSED** | Empty, no code references |
| `grammar_instructions` | 0 | **UNUSED** | Empty, no code references |
| `morph_verified` | 0 | **UNUSED** | Empty, no code references |
| `pos_gold` | 0 | **UNUSED** | Empty, no code references |
| `pos_verified` | 0 | **UNUSED** | Empty, no code references |
| `proverbs_idioms` | 0 | **UNUSED** | Empty, no code references |
| `vocabulary_enhanced` | 0 | **UNUSED** | Empty, no code references |
| `word_similarity` | 0 | **UNUSED** | Empty, no code references |
| `dictionary_en_my_import` | 0 | **UNUSED** | Empty, no code references |
| `dictionary_trilingual_import` | 0 | **UNUSED** | Empty, no code references |
| `sqlite_sequence` | 0 | **UNUSED** | SQLite internal table |

## Usage Hotspots

### Top 10 Most Referenced Tables
1. `dictionary` — 148 files (core dictionary)
2. `vocab` — 92 files (vocabulary index)
3. `translations` — 87 files (sentence pairs)
4. `phrases` — 43 files (multi-word expressions)
5. `articles` — 35 files (wiki content)
6. `dictionary_en_zo` — 26 files (reverse dictionary)
7. `grammar_patterns` — 25 files (grammar rules)
8. `bible_verses` — 25 files (Bible corpus)
9. `provenance` — 17 files (audit trail)
10. `word_usage` — 16 files (word profiles)

### Domain Coverage

| Domain | Active Tables | Coverage |
|--------|---------------|----------|
| Dictionary | `dictionary`, `dictionary_en_zo` | ✅ Complete |
| Bible | `bible_verses`, `bible_context`, `zolai_bible_analysis` | ✅ Complete |
| Training | `training_exercises`, `training_runs`, `training_corpus_qwen3_import` | ✅ Complete |
| Vocabulary | `vocab`, `zolai_vocabulary` | ✅ Complete |
| Grammar | `grammar_patterns`, `zolai_grammar_patterns` | ✅ Complete |
| Wiki | `articles`, `wiki_content`, `wiki_lessons` | ✅ Complete |
| Proverbs | `proverbs`, `zolai_proverbs_idioms` | ✅ Complete |
| Audit | `data_audit_log`, `audit_findings`, `provenance` | ✅ Complete |

## Recommendations

1. **Archive unused empty tables** — 12 tables with 0 rows and no code references can be archived
2. **Consolidate legacy import tables** — Consider dropping import tables after canonical population
3. **Monitor usage drift** — Tables with decreasing references may be candidates for deprecation
4. **Document table purposes** — Add comments to database schema for undocumented tables

## Code Reference Locations

References found in:
- `zolai/` — Core library (API, data, knowledge, etc.)
- `scripts/` — Pipeline and utility scripts
- `tests/` — Test suite
- `eval/` — Evaluation benchmarks

All references are from Python files in the zolai-core repository.