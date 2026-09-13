# Phase 0 Database Audit — Source of Truth

**Generated:** 2026-09-13 20:31

## Recommended Source of Truth per Domain

| Domain | Canonical Table | Row Count | Evidence |
|--------|----------------|-----------|----------|
| Dictionary (ZO→EN) | `dictionary` | 103,303 | Cleaned, 18 columns, 4 indexes, ZVS-2018 aligned |
| Dictionary (EN→ZO) | `dictionary_en_zo` | 113,750 | Cleaned, 18 columns, 3 indexes |
| Bible | `bible_verses` | 62,751 | 18 columns (rich), 4 indexes, parallel EN/ZO/MY |
| Bible Context | `bible_context` | 1,228 | Book-level analysis, 2 indexes |
| Bible Analysis | `zolai_bible_analysis` | 30,758 | Verse-level compound/grammar analysis |
| Translations | `translations` | 212,754 | EN↔ZO sentence pairs, 11 columns |
| Vocabulary | `vocab` | 94,458 | 11 columns, 2 indexes, frequency data |
| Vocabulary (Master) | `zolai_vocabulary` | 112,279 | 31 columns, 6 indexes — richest schema |
| Phrases | `phrases` | 5,000 | 9 columns, 2 indexes |
| Grammar (Base) | `grammar_patterns` | 5,547 | 12 columns, 2 indexes |
| Grammar (Full) | `zolai_grammar_patterns` | 13,519 | 22 columns, 1 index — most comprehensive |
| Word Alignments | `word_alignments` | 385,120 | 10 columns, 3 indexes, verse-level |
| Word Usage | `zolai_word_usage` | 85,045 | 10 columns, 2 indexes — enriched |
| Word Collocations | `word_collocations` | 5,000 | 10 columns, 2 indexes |
| Training | `training_exercises` | 81,805 | 11 columns, 3 indexes |
| Proverbs | `proverbs` | 7,736 | 9 columns, 1 index |
| Proverbs (Enriched) | `zolai_proverbs_idioms` | 4,984 | 11 columns, cultural context |
| Syllable | `syllable_data` | 189,554 | 10 columns, 2 indexes |
| Tone Sandhi | `zolai_tone_sandhi` | 19 | 19 rules, 11 columns |
| Songs | `zolai_songs` | 1,032 | 6 columns |
| Wiki Content | `wiki_content` | 1,688 | 11 columns, 3 indexes |
| Wiki Lessons | `wiki_lessons` | 1,688 | 12 columns, 1 index |
| Articles | `articles` | 6,371 | 8 columns |
| Audit Log | `data_audit_log` | 24,762 | 8 columns, 2 indexes |
| Audit Findings | `audit_findings` | 713 | 10 columns, 2 indexes |
| Provenance | `provenance` | 255 | 11 columns, source tracking |

## Evidence Criteria

A table is recommended as canonical based on:

1. **Data volume** — Larger/cleaner dataset is preferred
2. **Schema richness** — More columns = more metadata
3. **Index coverage** — Better indexes = better query performance
4. **Referenced by code** — Tables referenced in zolai-core code are preferred
5. **Cleaned/validated** — Tables that have gone through cleaning pipelines

## Decision Record

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Vocabulary source of truth | `zolai_vocabulary` over `vocab` | 31 columns vs 11; includes frequency, CEFR, POS |
| Grammar source of truth | `zolai_grammar_patterns` over `grammar_patterns` | 22 columns vs 12; includes source tracking |
| Word usage source of truth | `zolai_word_usage` over `word_usage` | Enriched version with 10 columns |
| Proverbs source of truth | `zolai_proverbs_idioms` over `proverbs` | Enriched with cultural context (11 vs 9 cols) |
| Bible analysis source | `zolai_bible_analysis` | Only table with compound/grammar analysis per verse |
