# Phase 0 Database Audit — Migration Roadmap

**Generated:** 2026-09-13 22:15

## Overview

Safe, incremental migration phases to consolidate the database from 74 tables to ~33 tables, with zero downtime and full rollback capability.

## Migration Principles

1. **Incremental** — Small, reversible steps
2. **Non-breaking** — No downtime or data loss
3. **Tested** — Each phase validated before proceeding
4. **Documented** — Clear rollback procedures
5. **Monitored** — Performance and integrity checks

## Phase Overview

| Phase | Description | Duration | Risk | Rollback |
|-------|-------------|----------|------|----------|
| A | Archive import tables | 1 week | Low | Rename back |
| B | Archive empty tables | 1 week | Low | Rename back |
| C | Create FTS indexes | 1 week | Medium | Drop tables |
| D | Consolidate enriched tables | 2 weeks | Medium | Restore from backup |
| E | Optimize schema | 1 week | Low | Revert changes |
| F | Update code references | 2 weeks | Medium | Revert commits |
| G | Update documentation | 1 week | Low | Revert commits |
| H | Performance testing | 1 week | Low | Revert changes |
| I | Final cleanup | 1 week | Low | Restore from backup |

## Detailed Phases

### Phase A: Archive Import Tables (1 week)

**Goal:** Move all `*_import` tables to `_import_archived` suffix

**Tables to archive (26):**
```sql
-- Rename all import tables
ALTER TABLE dictionary_import RENAME TO dictionary_import_archived;
ALTER TABLE dictionary_en_zo_import RENAME TO dictionary_en_zo_import_archived;
ALTER TABLE bible_verses_import RENAME TO bible_verses_import_archived;
ALTER TABLE translations_import RENAME TO translations_import_archived;
ALTER TABLE vocab_import RENAME TO vocab_import_archived;
ALTER TABLE phrases_import RENAME TO phrases_import_archived;
ALTER TABLE phrases_from_bible_import RENAME TO phrases_from_bible_import_archived;
ALTER TABLE grammar_patterns_import RENAME TO grammar_patterns_import_archived;
ALTER TABLE proverbs_import RENAME TO proverbs_import_archived;
ALTER TABLE word_alignments_import RENAME TO word_alignments_import_archived;
ALTER TABLE word_collocations_import RENAME TO word_collocations_import_archived;
ALTER TABLE word_usage_profiles_import RENAME TO word_usage_profiles_import_archived;
ALTER TABLE training_exercises_import RENAME TO training_exercises_import_archived;
ALTER TABLE training_corpus_qwen3_import RENAME TO training_corpus_qwen3_import_archived;
ALTER TABLE training_seed_data_import RENAME TO training_seed_data_import_archived;
ALTER TABLE training_valid_sentences_import RENAME TO training_valid_sentences_import_archived;
ALTER TABLE dictionary_my_import RENAME TO dictionary_my_import_archived;
ALTER TABLE dictionary_en_my_import RENAME TO dictionary_en_my_import_archived;
ALTER TABLE dictionary_trilingual_import RENAME TO dictionary_trilingual_import_archived;
ALTER TABLE zolai_vocabulary_import RENAME TO zolai_vocabulary_import_archived;
ALTER TABLE zvs_corrections_import RENAME TO zvs_corrections_import_archived;
ALTER TABLE bible_book_analysis_import RENAME TO bible_book_analysis_import_archived;
ALTER TABLE bible_chapter_analysis_import RENAME TO bible_chapter_analysis_import_archived;
ALTER TABLE phrase_context_import RENAME TO phrase_context_import_archived;
ALTER TABLE sentence_patterns_import RENAME TO sentence_patterns_import_archived;
ALTER TABLE topic_clusters_import RENAME TO topic_clusters_import_archived;
```

**Validation:**
- [ ] All import tables renamed
- [ ] No code references to old names
- [ ] All tests pass
- [ ] API endpoints work correctly

**Rollback:**
```sql
-- Rename back to original names
ALTER TABLE dictionary_import_archived RENAME TO dictionary_import;
-- ... etc for all tables
```

### Phase B: Archive Empty Tables (1 week)

**Goal:** Move all empty tables to `_archived` suffix

**Tables to archive (12):**
```sql
ALTER TABLE bible_verses_enhanced RENAME TO bible_verses_enhanced_archived;
ALTER TABLE dictionary_enhanced RENAME TO dictionary_enhanced_archived;
ALTER TABLE gemini_model_results RENAME TO gemini_model_results_archived;
ALTER TABLE grammar_instructions RENAME TO grammar_instructions_archived;
ALTER TABLE morph_verified RENAME TO morph_verified_archived;
ALTER TABLE pos_gold RENAME TO pos_gold_archived;
ALTER TABLE pos_verified RENAME TO pos_verified_archived;
ALTER TABLE proverbs_idioms RENAME TO proverbs_idioms_archived;
ALTER TABLE vocabulary_enhanced RENAME TO vocabulary_enhanced_archived;
ALTER TABLE word_similarity RENAME TO word_similarity_archived;
```

**Validation:**
- [ ] All empty tables renamed
- [ ] No code references to old names
- [ ] All tests pass

**Rollback:**
```sql
ALTER TABLE bible_verses_enhanced_archived RENAME TO bible_verses_enhanced;
-- ... etc for all tables
```

### Phase C: Create FTS Indexes (1 week)

**Goal:** Create missing FTS5 virtual tables for search

**Steps:**
1. Create `dictionary_fts` virtual table
2. Create `bible_fts` virtual table
3. Populate FTS indexes
4. Add auto-sync triggers

**SQL:**
```sql
-- Create dictionary FTS
CREATE VIRTUAL TABLE dictionary_fts USING fts5(
    zolai, english, 
    content='dictionary', 
    content_rowid='id'
);

-- Create Bible FTS
CREATE VIRTUAL TABLE bible_fts USING fts5(
    zo_tdb77, zo_tedim2010, en_kJV, 
    content='bible_verses', 
    content_rowid='id'
);

-- Populate indexes
INSERT INTO dictionary_fts(dictionary_fts) VALUES('rebuild');
INSERT INTO bible_fts(bible_fts) VALUES('rebuild');

-- Add auto-sync triggers
CREATE TRIGGER dictionary_ai AFTER INSERT ON dictionary BEGIN
    INSERT INTO dictionary_fts(rowid, zolai, english) 
    VALUES (new.id, new.zolai, new.english);
END;

CREATE TRIGGER dictionary_ad AFTER DELETE ON dictionary BEGIN
    INSERT INTO dictionary_fts(dictionary_fts, rowid, zolai, english) 
    VALUES ('delete', old.id, old.zolai, old.english);
END;
```

**Validation:**
- [ ] FTS tables created
- [ ] Search works correctly
- [ ] Performance improved

**Rollback:**
```sql
DROP TABLE dictionary_fts;
DROP TABLE bible_fts;
DROP TRIGGER dictionary_ai;
DROP TRIGGER dictionary_ad;
```

### Phase D: Consolidate Enriched Tables (2 weeks)

**Goal:** Merge enriched tables into canonical tables where beneficial

**Step 1: Analyze overlap**
- Compare `zolai_vocabulary` vs `vocab`
- Compare `zolai_grammar_patterns` vs `grammar_patterns`
- Compare `zolai_bible_analysis` vs `bible_verses`
- Compare `zolai_word_usage` vs `word_usage`
- Compare `zolai_proverbs_idioms` vs `proverbs`

**Step 2: Merge if beneficial**
- Add missing columns from enriched to canonical
- Migrate data
- Update code references
- Archive enriched tables

**Step 3: Keep specialized tables**
- `zolai_tone_sandhi` — Unique data, keep as-is
- `zolai_songs` — Unique data, keep as-is
- `simbu` — Unique data, keep as-is

**Validation:**
- [ ] No data loss
- [ ] All queries work correctly
- [ ] Performance maintained or improved

**Rollback:**
- Restore from backup
- Revert code changes

### Phase E: Optimize Schema (1 week)

**Goal:** Add missing indexes and optimize column types

**Steps:**
1. Add indexes for common queries
2. Optimize column types (TEXT → INTEGER where appropriate)
3. Add constraints for data integrity
4. Update statistics

**SQL:**
```sql
-- Add missing indexes
CREATE INDEX IF NOT EXISTS idx_dictionary_zolai ON dictionary(zolai);
CREATE INDEX IF NOT EXISTS idx_dictionary_english ON dictionary(english);
CREATE INDEX IF NOT EXISTS idx_bible_verses_book_chapter ON bible_verses(book, chapter);
CREATE INDEX IF NOT EXISTS idx_translations_zolai ON translations(zolai);
CREATE INDEX IF NOT EXISTS idx_vocab_word ON vocab(word);

-- Optimize column types
ALTER TABLE dictionary ADD COLUMN is_deleted INTEGER DEFAULT 0;
ALTER TABLE dictionary ADD COLUMN deleted_at TEXT;
ALTER TABLE dictionary ADD COLUMN deleted_by TEXT;

-- Update statistics
ANALYZE;
```

**Validation:**
- [ ] Indexes created
- [ ] Query performance improved
- [ ] No breaking changes

**Rollback:**
```sql
-- Drop added indexes
DROP INDEX IF EXISTS idx_dictionary_zolai;
-- ... etc
```

### Phase F: Update Code References (2 weeks)

**Goal:** Update all code to use new table names and structures

**Steps:**
1. Update Python code references
2. Update TypeScript/Frontend references
3. Update test files
4. Update documentation

**Files to update:**
- `zolai/data/database.py`
- `zolai/api/server.py`
- `zolai/api/desktop_router.py`
- `zolai/core/jsonl_pipeline_v2.py`
- `scripts/*.py`
- `tests/*.py`
- `zolai-tauri/frontend/src/lib/zolai-core/contract.ts`

**Validation:**
- [ ] All code references updated
- [ ] All tests pass
- [ ] API endpoints work correctly
- [ ] Frontend works correctly

**Rollback:**
- Revert git commits

### Phase G: Update Documentation (1 week)

**Goal:** Update all documentation to reflect new architecture

**Steps:**
1. Update schema documentation
2. Update API documentation
3. Update architecture diagrams
4. Update migration guides

**Files to update:**
- `docs/database-audit/*.md`
- `context/architecture.md`
- `README.md`
- `AGENTS.md`

**Validation:**
- [ ] Documentation accurate
- [ ] No outdated references

**Rollback:**
- Revert git commits

### Phase H: Performance Testing (1 week)

**Goal:** Validate performance improvements

**Steps:**
1. Run benchmark tests
2. Compare before/after performance
3. Identify any regressions
4. Optimize if needed

**Metrics to measure:**
- Query latency
- Index size
- Database size
- Memory usage
- Import speed

**Validation:**
- [ ] Performance maintained or improved
- [ ] No regressions

**Rollback:**
- Revert changes if regressions found

### Phase I: Final Cleanup (1 week)

**Goal:** Remove archived tables and finalize migration

**Steps:**
1. Verify all archived tables are unused
2. Drop archived tables
3. Vacuum database
4. Update final documentation

**SQL:**
```sql
-- Drop archived tables
DROP TABLE IF EXISTS dictionary_import_archived;
-- ... etc for all archived tables

-- Vacuum database
VACUUM;
```

**Validation:**
- [ ] All archived tables dropped
- [ ] Database optimized
- [ ] All tests pass
- [ ] Documentation complete

**Rollback:**
- Restore from backup (last chance)

## Risk Mitigation

### Backup Strategy
1. **Full backup** before each phase
2. **Incremental backups** during migration
3. **Test restore** procedure regularly

### Testing Strategy
1. **Unit tests** — Run after each change
2. **Integration tests** — Run after each phase
3. **Performance tests** — Run before/after each phase
4. **User acceptance testing** — Run after Phase I

### Monitoring Strategy
1. **Database size** — Monitor growth
2. **Query performance** — Track latency
3. **Error rates** — Monitor for issues
4. **User feedback** — Collect reports

## Success Criteria

| Metric | Current | Target |
|--------|---------|--------|
| Total tables | 74 | 33 |
| Import tables | 26 | 0 |
| Empty tables | 12 | 0 |
| FTS tables | 0 | 2 |
| Code references | Scattered | Centralized |
| Query performance | Variable | Optimized |
| Documentation | Outdated | Current |

## Timeline Summary

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1 | A | Import tables archived |
| 2 | B | Empty tables archived |
| 3 | C | FTS indexes created |
| 4-5 | D | Enriched tables consolidated |
| 6 | E | Schema optimized |
| 7-8 | F | Code references updated |
| 9 | G | Documentation updated |
| 10 | H | Performance validated |
| 11 | I | Final cleanup complete |

**Total Duration:** 11 weeks (3 months)

## Post-Migration Maintenance

1. **Monitor performance** — Weekly checks for 1 month
2. **Review metrics** — Monthly performance reviews
3. **Update documentation** — As needed
4. **Plan future improvements** — Based on usage patterns