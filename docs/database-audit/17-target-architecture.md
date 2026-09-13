# Phase 0 Database Audit — Target Architecture

**Generated:** 2026-09-13 22:15

## Overview

Proposed ideal ~33-table schema based on Phase 0 findings, consolidating the current 74 tables into a cleaner, more maintainable architecture.

## Current State

- **Total tables:** 74
- **Canonical tables:** ~30
- **Import/staging tables:** ~26
- **Empty tables:** 12
- **Internal/FTS tables:** 6

## Target Architecture (33 Tables)

### Core Domain Tables (12)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `dictionary` | Zolai→English dictionary | 103K | P0 |
| `dictionary_en_zo` | English→Zolai dictionary | 114K | P0 |
| `bible_verses` | Parallel Bible verses | 63K | P0 |
| `translations` | EN↔ZO sentence pairs | 213K | P0 |
| `vocab` | Vocabulary index with frequency | 108K | P0 |
| `phrases` | Multi-word expressions | 5K | P0 |
| `grammar_patterns` | Grammar rules and patterns | 6K | P0 |
| `proverbs` | Proverbs collection | 8K | P0 |
| `word_alignments` | Word-level ZO↔EN alignment | 385K | P0 |
| `word_usage` | Per-book word profiles | 60K | P0 |
| `word_collocations` | Word pair frequencies | 5K | P0 |
| `syllable_data` | Syllable segmentation | 190K | P1 |

### Enriched Domain Tables (6)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `zolai_vocabulary` | Master vocabulary (enriched) | 112K | P1 |
| `zolai_grammar_patterns` | Grammar patterns (enriched) | 14K | P1 |
| `zolai_bible_analysis` | Verse-level analysis | 31K | P1 |
| `zolai_word_usage` | Word usage (enriched) | 85K | P1 |
| `zolai_proverbs_idioms` | Proverbs with cultural context | 5K | P1 |
| `zolai_tone_sandhi` | Tone sandhi rules | 19 | P2 |

### Training Tables (3)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `training_exercises` | Training exercises (5 types) | 82K | P0 |
| `training_corpus` | Qwen3 training format | 9K | P1 |
| `training_runs` | Training run tracking | ? | P2 |

### Wiki/Content Tables (3)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `articles` | Reference articles | 6K | P1 |
| `wiki_content` | Wiki content for RAG | 2K | P1 |
| `wiki_lessons` | Wiki-driven lessons | 2K | P1 |

### Audit/Provenance Tables (3)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `provenance` | Source file tracking | 255 | P0 |
| `jsonl_import_log` | Import operation log | 92 | P0 |
| `data_audit_log` | Change audit trail | 25K | P0 |

### Specialized Tables (4)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `bible_context` | Book-level analysis | 1K | P1 |
| `tone_patterns` | Tone patterns | ? | P2 |
| `zolai_songs` | Song catalogue | 1K | P2 |
| `simbu` | Simbu data | 4K | P2 |

### FTS Tables (2)

| Table | Purpose | Rows | Priority |
|-------|---------|------|----------|
| `dictionary_fts` | Dictionary full-text search | Virtual | P1 |
| `bible_fts` | Bible full-text search | Virtual | P1 |

## Tables to Archive/Remove (41 tables)

### Import/Staging Tables (26)

| Table | Reason | Action |
|-------|--------|--------|
| `dictionary_import` | Staging for `dictionary` | Archive |
| `dictionary_en_zo_import` | Staging for `dictionary_en_zo` | Archive |
| `bible_verses_import` | Staging for `bible_verses` | Archive |
| `translations_import` | Staging for `translations` | Archive |
| `vocab_import` | Staging for `vocab` | Archive |
| `phrases_import` | Staging for `phrases` | Archive |
| `phrases_from_bible_import` | Staging for `phrases` | Archive |
| `grammar_patterns_import` | Staging for `grammar_patterns` | Archive |
| `proverbs_import` | Staging for `proverbs` | Archive |
| `word_alignments_import` | Staging for `word_alignments` | Archive |
| `word_collocations_import` | Staging for `word_collocations` | Archive |
| `word_usage_profiles_import` | Staging for `word_usage` | Archive |
| `training_exercises_import` | Staging for `training_exercises` | Archive |
| `training_corpus_qwen3_import` | Staging for `training_corpus` | Archive |
| `training_seed_data_import` | Seed data | Archive |
| `training_valid_sentences_import` | Validated sentences | Archive |
| `dictionary_my_import` | Myanmar dictionary import | Archive |
| `dictionary_en_my_import` | Empty table | Archive |
| `dictionary_trilingual_import` | Empty table | Archive |
| `zolai_vocabulary_import` | Staging for `zolai_vocabulary` | Archive |
| `zvs_corrections_import` | ZVS corrections | Archive |
| `bible_book_analysis_import` | Staging for `bible_context` | Archive |
| `bible_chapter_analysis_import` | Staging for `bible_context` | Archive |
| `phrase_context_import` | Staging for `phrases` | Archive |
| `sentence_patterns_import` | Staging for `grammar_patterns` | Archive |
| `topic_clusters_import` | Topic clusters | Archive |

### Empty/Unused Tables (12)

| Table | Reason | Action |
|-------|--------|--------|
| `bible_verses_enhanced` | Empty, never used | Archive |
| `dictionary_enhanced` | Empty, never used | Archive |
| `dictionary_en_my_import` | Empty, no source | Archive |
| `dictionary_trilingual_import` | Empty, no source | Archive |
| `gemini_model_results` | Empty, never used | Archive |
| `grammar_instructions` | Empty, never used | Archive |
| `morph_verified` | Empty, never used | Archive |
| `pos_gold` | Empty, never used | Archive |
| `pos_verified` | Empty, never used | Archive |
| `proverbs_idioms` | Empty, duplicate of `proverbs` | Archive |
| `vocabulary_enhanced` | Empty, never used | Archive |
| `word_similarity` | Empty, never used | Archive |

### Internal/FTS Tables (3)

| Table | Reason | Action |
|-------|--------|--------|
| `wiki_content_fts` | Keep (active FTS) | Keep |
| `wiki_content_fts_data` | Auto-managed by FTS5 | Keep |
| `wiki_content_fts_idx` | Auto-managed by FTS5 | Keep |
| `wiki_content_fts_docsize` | Auto-managed by FTS5 | Keep |
| `wiki_content_fts_config` | Auto-managed by FTS5 | Keep |

## Schema Design Principles

### 1. Single Source of Truth
- One canonical table per domain
- Enriched tables for advanced features
- No duplicate data across tables

### 2. Clear Separation
- Core data (P0) — essential for basic functionality
- Enriched data (P1) — enhanced features
- Specialized data (P2) — advanced/niche features

### 3. Audit Trail
- All changes tracked in `data_audit_log`
- Source files tracked in `provenance`
- Import operations tracked in `jsonl_import_log`

### 4. Performance
- Appropriate indexes for common queries
- FTS5 for full-text search
- WAL mode for concurrent access

## Migration Strategy

### Phase A: Archive Import Tables
1. Rename `*_import` tables to `*_import_archived`
2. Update code to use canonical tables only
3. Verify no breaking changes

### Phase B: Archive Empty Tables
1. Rename empty tables to `*_archived`
2. Remove any code references
3. Update documentation

### Phase C: Create Missing FTS Tables
1. Create `dictionary_fts` virtual table
2. Create `bible_fts` virtual table
3. Populate FTS indexes

### Phase D: Consolidate Enriched Tables
1. Merge `zolai_vocabulary` into `vocab` (if beneficial)
2. Merge `zolai_grammar_patterns` into `grammar_patterns` (if beneficial)
3. Update code references

### Phase E: Optimize Schema
1. Add missing indexes
2. Optimize column types
3. Add constraints for data integrity

## Benefits of Target Architecture

1. **Reduced complexity** — 33 tables vs 74 tables
2. **Clearer data flow** — Single source of truth per domain
3. **Better performance** — Optimized indexes and queries
4. **Easier maintenance** — Fewer tables to manage
5. **Improved auditability** — Clear provenance and change tracking

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking changes | High | Phased migration with testing |
| Data loss | High | Backup before migration |
| Performance regression | Medium | Benchmark before/after |
| Code updates | Medium | Update all references |
| Documentation drift | Low | Update docs with migration |

## Implementation Timeline

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase A: Archive imports | 1 week | None |
| Phase B: Archive empty | 1 week | Phase A |
| Phase C: Create FTS | 1 week | None |
| Phase D: Consolidate | 2 weeks | Phase A, B |
| Phase E: Optimize | 1 week | Phase D |
| **Total** | **6 weeks** | — |

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Total tables | 74 | 33 |
| Import tables | 26 | 0 |
| Empty tables | 12 | 0 |
| Code references | Scattered | Centralized |
| Query performance | Variable | Optimized |
| Maintenance burden | High | Low