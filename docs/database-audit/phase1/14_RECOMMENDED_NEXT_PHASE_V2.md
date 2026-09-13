# 14 — Recommended Next Phase V2

> **Date:** 2026-09-13 (Revised)
> **Status:** Plan — READ-ONLY phase complete
> **Scope:** Non-destructive migration from 79 tables → 23 tables

---

## Strategy: Non-Destructive, Idempotent Migration

The migration must be **safe to run repeatedly** and **fully reversible** until the final cleanup phase.

### Principles
1. **No data deletion** during migration — only CREATE TABLE + INSERT ... SELECT + VIEW creation
2. **Validate before commit** — every target table gets a row-count check
3. **Archive, never drop** — old tables are renamed to `archived_*` (not dropped) until Phase 3
4. **One table at a time** — each table migration is an independent transaction
5. **Rollback at every step** — if any table fails, the entire migration rolls back

---

## Phase A: Backup (Day 1)

```sql
-- Full database backup
VACUUM INTO '../data/zolai_backup_20260913.db';
-- Verify backup
SELECT COUNT(*) FROM zolai_backup_20260913.sqlite_master WHERE type='table';
```

**Checkpoint:** Backup exists, verifiable, matches source (79 tables, ~3.16M rows).

---

## Phase B: Create New Canonical Tables (Additive)

All new tables use `_v2` suffix during migration to avoid name collisions with existing tables. After validation, views are created to map old names to new.

### Migration Order (Dependency-Based)

| Step | Target Table | Source Tables | Est. Rows | Est. Time | Merge Strategy |
|------|-------------|---------------|-----------|-----------|----------------|
| 1 | `bible_verses_v2` | `bible_verses` | 62,751 | 5s | Direct copy + dedup by (ref) |
| 2 | `dictionary_v2` | `dictionary` + `dictionary_import` | 103,303 | 10s | COALESCE(myanmar) |
| 3 | `dictionary_en_zo_v2` | `dictionary_en_zo` + `dictionary_en_zo_import` | 113,750 | 10s | COALESCE(myanmar) |
| 4 | `grammar_patterns_v2` | `grammar_patterns` + `grammar_instructions` | ~5,563 | 10s | MERGE instructions as instruction_text |
| 5 | `translations_v2` | `translations` + `translations_import` | ~200,000 | 15s | Dedup by (source, target) |
| 6 | `word_alignments_v2` | `word_alignments` + `word_alignments_import` | ~400,000 | 20s | COALESCE(position) |
| 7 | `vocab_v2` | `vocab` + `zolai_vocabulary` | ~114,000 | 10s | COALESCE(myanmar) |
| 8 | `proverbs_v2` | `proverbs` + `zolai_proverbs_idioms` | ~7,736 | 5s | COALESCE enriched fields |
| 9 | `phrases_v2` | `phrases` | 5,000 | 5s | Direct copy |
| 10 | `word_usage_v2` | `word_usage` | 60,365 | 5s | Direct copy |
| 11 | `syllable_data_v2` | `syllable_data` | 189,554 | 10s | Direct copy |
| 12 | `word_collocations_v2` | `word_collocations` | 5,000 | 5s | Direct copy |
| 13 | `bible_analysis_v2` | `zolai_bible_analysis` | 30,758 | 10s | Direct copy |
| 14 | `articles_v2` | `articles` | 6,371 | 5s | Direct copy |
| 15 | `songs_v2` | `zolai_songs` | 1,032 | 5s | Rename (drop prefix) |
| 16 | `wiki_content_v2` | `wiki_content` + `wiki_lessons` | ~1,688 | 5s | MERGE grammar_patterns/vocabulary_list |
| 17 | `import_log_v2` | `jsonl_import_log` | 92 | 1s | Rename |
| 18 | `data_audit_log_v2` | `data_audit_log` | 24,762 | 5s | Direct copy |
| 19 | `audit_findings_v2` | `audit_findings` | 713 | 1s | Direct copy |
| 20 | `provenance_v2` | `provenance` | 255 | 1s | Direct copy |
| 21 | `tone_sandhi_v2` | `zolai_tone_sandhi` | 19 | 1s | Rename |
| 22 | `tone_patterns_v2` | `tone_patterns` | 118 | 1s | Direct copy |
| 23 | `training_runs_v2` | `training_runs` | 2 | 1s | Direct copy |

**Total estimated time:** ~3 minutes

### Key Migration SQL Examples

```sql
-- Step 2: Dictionary (with COALESCE for myanmar)
BEGIN TRANSACTION;
CREATE TABLE dictionary_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT,
    english_clean TEXT,
    pos TEXT,
    myanmar TEXT,
    source TEXT,
    zvs_compliance TEXT,
    entry_version INTEGER DEFAULT 1,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO dictionary_v2 (zolai, english, english_clean, pos, myanmar, source, zvs_compliance, entry_version, is_deleted, created_at)
SELECT DISTINCT
    d.zolai,
    d.english,
    d.english_clean,
    d.pos,
    COALESCE(d.myanmar, di.myanmar_word) as myanmar,
    d.source,
    d.zvs_compliance_status,
    d.entry_version,
    d.is_deleted,
    d.imported_at as created_at
FROM dictionary d
LEFT JOIN dictionary_import di ON d.zolai = di.zolai;
SELECT COUNT(*) as target_count FROM dictionary_v2;  -- Expected: ~103,303
COMMIT;

-- Step 7: Vocab (with COALESCE from zolai_vocabulary)
BEGIN TRANSACTION;
CREATE TABLE vocab_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    english TEXT,
    frequency INTEGER,
    books TEXT,
    examples TEXT,
    myanmar TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO vocab_v2 (headword, english, frequency, books, examples, myanmar)
SELECT DISTINCT
    v.headword,
    v.english,
    v.frequency,
    v.books,
    v.examples,
    COALESCE(v.myanmar, z.myanmar) as myanmar
FROM vocab v
LEFT JOIN zolai_vocabulary z ON v.headword = z.zolai;
SELECT COUNT(*) as target_count FROM vocab_v2;  -- Expected: ~114,000
COMMIT;

-- Step 4: Grammar patterns (with instruction merge)
BEGIN TRANSACTION;
CREATE TABLE grammar_patterns_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL,
    pattern_text TEXT NOT NULL,
    description TEXT,
    function TEXT,
    examples TEXT,
    frequency INTEGER,
    myanmar TEXT,
    tense TEXT,
    aspect TEXT,
    negation_type TEXT,
    question_type TEXT,
    source_category TEXT,
    instruction_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO grammar_patterns_v2 (pattern_id, pattern_text, description, function, examples, frequency, myanmar, created_at)
SELECT pattern_id, pattern, description, function, examples, frequency, myanmar, imported_at as created_at
FROM grammar_patterns;
-- Merge grammar_instructions as rows with pattern_id = 'INSTRUCTION_N'
INSERT INTO grammar_patterns_v2 (pattern_id, pattern_text, description, instruction_text, created_at)
SELECT
    'INSTRUCTION_' || id,
    instruction,
    'Grammar instruction from training data',
    instruction,
    imported_at as created_at
FROM grammar_instructions;
COMMIT;
```

---

## Phase C: Copy + Validate Data

After each table is created:
1. **Row count check** — target count must match expected (±1%)
2. **NULL key check** — business key columns must not be NULL
3. **Uniqueness check** — business key must be unique (where expected)

```sql
-- Validation template for each table
SELECT
    'dictionary_v2' as table_name,
    (SELECT COUNT(*) FROM dictionary_v2) as target_count,
    (SELECT COUNT(*) FROM dictionary) as source_count,
    CASE WHEN (SELECT COUNT(*) FROM dictionary_v2) = (SELECT COUNT(*) FROM dictionary)
         THEN 'PASS' ELSE 'FAIL' END as status;

-- NULL key checks
SELECT COUNT(*) FROM dictionary_v2 WHERE zolai IS NULL;  -- Expected: 0
SELECT COUNT(*) FROM vocab_v2 WHERE headword IS NULL;     -- Expected: 0
SELECT COUNT(*) FROM bible_verses_v2 WHERE ref IS NULL;    -- Expected: 0
```

---

## Phase D: Add Versioning Columns

After all target tables exist, add versioning columns:

```sql
-- Add versioning to all target tables
ALTER TABLE dictionary_v2 ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE vocab_v2 ADD COLUMN version INTEGER DEFAULT 1;
-- ... (all 23 tables)
```

---

## Phase E: Update Application Layer

1. **Create views** mapping old names to new:
```sql
CREATE VIEW dictionary AS SELECT * FROM dictionary_v2;
CREATE VIEW vocab AS SELECT * FROM vocab_v2;
-- ... (all 23 tables)
```

2. **Update zolai-core config** to use new table names
3. **Run smoke tests** — dictionary lookup, bible verse search, vocab quiz

---

## Phase F: Archive Old Tables (Rename, Not Drop)

Only after 1 week of production use and manual approval:

```sql
-- Rename old tables (non-destructive)
ALTER TABLE dictionary RENAME TO archived_dictionary;
ALTER TABLE dictionary_import RENAME TO archived_dictionary_import;
ALTER TABLE zolai_vocabulary RENAME TO archived_zolai_vocabulary;
-- ... (all old tables)
```

**Tables to archive (not drop):**
- 27 `*_import` staging tables
- 17 empty/placeholder tables
- `grammar_patterns_enhanced` (5,597 rows)
- `zolai_grammar_patterns` (13,519 rows)
- `zolai_word_usage` (85,045 rows)
- `training_exercises` (81,805 rows)
- `simbu` (4,163 rows)
- `bible_context` (1,228 rows)
- `wiki_lessons` (1,688 rows)
- 5 FTS tables

---

## Phase G: Export from Canonical

Before dropping archived tables, export unique data to JSONL:

| Export | Source | Format | Est. Size |
|--------|--------|--------|-----------|
| `zolai_grammar_enriched.jsonl` | `zolai_grammar_patterns` | JSONL | ~13K rows |
| `word_usage_enriched.jsonl` | `zolai_word_usage` | JSONL | ~85K rows |
| `training_exercises.jsonl` | `training_exercises` | JSONL | ~82K rows |
| `simbu.jsonl` | `simbu` | JSONL | ~4K rows |
| `bible_context.jsonl` | `bible_context` | JSONL | ~1.2K rows |

---

## Rollback Strategy

| Phase | Rollback Action | Data Loss Risk |
|-------|----------------|----------------|
| A (Backup) | Delete backup file | None |
| B (Create target) | DROP _v2 tables | None |
| C (Copy+Validate) | DROP _v2 tables, old tables untouched | None |
| D (Versioning) | ALTER TABLE DROP COLUMN | None |
| E (Views) | DROP views | None |
| F (Archive) | ALTER TABLE ... RENAME back | None |
| G (Export) | Restore from backup | Full restore required |

**Key invariant:** Old tables are never modified until Phase F. The migration is purely additive.

---

## Estimated Timeline

| Phase | Duration | Dependencies |
|-------|----------|-------------|
| A (Backup) | 1 hour | None |
| B + C (Create + Migrate) | 1 day | Phase A |
| D (Versioning) | 0.5 day | Phase C |
| E (Views) | 0.5 day | Phase C |
| **Phase 2 Total** | **2–3 days** | |
| F (Archive) | 1 day | 1 week after Phase 2 |
| G (Export) | 0.5 day | Phase F |
| **Full Migration** | **~5 weeks** | |

---

## Risk Mitigation

1. **Backup verified** before any write
2. **Transaction per table** — atomic migrations
3. **Row count validation** — catches silent data loss
4. **Old tables preserved** — full rollback capability
5. **No schema changes to source** — only new tables created
6. **Staging tables preserved** — import pipeline continues to work
7. **Critical findings documented** — grammar_patterns FK gap, bible_verses ref duplication, zolai_word_usage schema divergence
