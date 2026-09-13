# 14 — Recommended Next Phase

Phase 2 plan based on Phase 1 evidence.

---

## Phase 2: Schema Consolidation & Cleanup

**Goal:** Reduce 71 tables → ~33 tables by merging duplicates, dropping staging/empty tables, and adding constraints.

**Duration:** 2-3 days  
**Risk Level:** MEDIUM (all changes are additive except drops)

---

## Phase 2 Tasks

### Task 1: Document Canonical Build Logic (Day 1, Morning)

**Why:** Phase 1 found 44K dictionary entries filtered from import to canonical, 241K word alignments filtered, and 77K translations added outside pipeline — all without documentation.

**Actions:**
1. Read `build_canonical.py` and document:
   - Dictionary dedup/cleaning logic
   - Word alignment confidence threshold
   - Translation enrichment source
2. Add inline comments to `build_canonical.py`
3. Create `docs/CANONICAL_BUILD.md` with flowchart

**Output:** Documented pipeline with reproducible build steps.

---

### Task 2: Merge Vocabulary Tables (Day 1, Afternoon)

**Why:** `vocab` (94K) is a subset of `zolai_vocabulary` (112K) with different schema. Two tables for the same domain causes confusion.

**Actions:**
1. Create new `vocabulary` table with merged schema (from `13_PROPOSED_TARGET_SCHEMA.md`)
2. INSERT from `zolai_vocabulary` (master) with dedup by `zolai`
3. UPDATE any references in code from `vocab` → `vocabulary`
4. DROP `vocab`, `vocab_import`, `zolai_vocabulary_import`
5. Run tests to verify no regressions

**Output:** Single `vocabulary` table with 112K entries.

---

### Task 3: Merge Grammar Tables (Day 2, Morning)

**Why:** Three grammar tables (`grammar_patterns`, `grammar_patterns_enhanced`, `zolai_grammar_patterns`) with overlapping data.

**Actions:**
1. Create new `grammar_patterns` table with merged schema
2. INSERT from `zolai_grammar_patterns` (master, 13K entries)
3. UPDATE any references in code
4. DROP `grammar_patterns_enhanced`, `grammar_patterns_import`
5. Rename `zolai_grammar_patterns` → `grammar_patterns`

**Output:** Single `grammar_patterns` table with 13K entries.

---

### Task 4: Merge Proverbs Tables (Day 2, Afternoon)

**Why:** `zolai_proverbs_idioms` (4,984) is a subset of `proverbs` (7,736) with richer metadata.

**Actions:**
1. ALTER `proverbs` to add columns from `zolai_proverbs_idioms`:
   - `english_translation`, `literal_translation`, `morpheme_breakdown`
   - `theme`, `cultural_context`, `source_category`
2. UPDATE `proverbs` with enriched data from `zolai_proverbs_idioms`
3. DROP `zolai_proverbs_idioms`, `proverbs_import`, `proverbs_idioms`

**Output:** Single `proverbs` table with 7,736 entries + enriched metadata.

---

### Task 5: Drop Empty/Staging Tables (Day 3, Morning)

**Why:** 35 tables with no runtime value waste schema space and confuse developers.

**Tables to drop (35):**
- 24 `*_import` staging tables
- 11 empty placeholder tables
- 3 superseded tables (`grammar_patterns_enhanced`, `proverbs_idioms`, `vocabulary_enhanced`)

**Actions:**
1. Verify no code references to these tables (grep)
2. DROP each table
3. Run full test suite

**Output:** 35 fewer tables in schema.

---

### Task 6: Add Constraints (Day 3, Afternoon)

**Why:** No foreign keys, no UNIQUE constraints on business keys, no CHECK constraints.

**Actions:**
1. Enable foreign keys: `PRAGMA foreign_keys = ON`
2. Add UNIQUE constraints:
   - `dictionary(zolai, english, source)`
   - `dictionary_en_zo(english, zolai, source)`
   - `bible_verses(book, chapter, verse, version)`
   - `translations(source, target, direction, reference)`
   - `vocabulary(zolai)`
   - `grammar_patterns(pattern_id)`
   - `proverbs(zolai)`
3. Add CHECK constraints:
   - `training_exercises.exercise_type IN ('negation', 'question', 'pronoun', 'error', 'conditional')`
   - `translations.direction IN ('en_to_zo', 'zo_to_en')`
4. Run integrity check: `PRAGMA integrity_check`

**Output:** Database with proper constraints.

---

### Task 7: Update Documentation (Day 3, End)

**Actions:**
1. Update `context/architecture.md` with new table count (33)
2. Update `AGENTS.md` with new table list
3. Update `data/DATA_INDEX.md` with revised inventory
4. Create `docs/database-audit/phase2/` with before/after comparison

**Output:** All documentation reflects new schema.

---

## Success Criteria

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Table count | 71 | 33 | -54% |
| Staging tables | 24 | 0 | -100% |
| Empty tables | 11 | 0 | -100% |
| Duplicate domains | 3 | 0 | -100% |
| UNIQUE constraints | 0 | 7 | +7 |
| Foreign keys enabled | No | Yes | Yes |
| All tests passing | ✅ | ✅ | ✅ |

---

## Phase 3 (Future): Performance & Indexing

After Phase 2 stabilizes:
1. Add indexes on frequently queried columns
2. Analyze query performance with `EXPLAIN QUERY PLAN`
3. Consider partitioning `bible_verses` by book if query time is high
4. Add full-text search indexes beyond wiki FTS

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Breaking changes to code | Search all `.py` files for table references before each DROP |
| Data loss | Backup `zolai.db` before each phase |
| Test failures | Run full test suite after each task |
| Rollback needed | Keep git commits atomic (one per task) |
