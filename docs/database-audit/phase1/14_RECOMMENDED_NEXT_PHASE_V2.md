# 14 — Recommended Next Phase V2

> **Date:** 2026-09-13
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

## Phase 2a: Backup (Day 1)

```sql
-- Full database backup
VACUUM INTO '../data/zolai_backup_20260913.db';
-- Verify backup
SELECT COUNT(*) FROM zolai_backup_20260913.sqlite_master WHERE type='table';
```

**Checkpoint:** Backup exists, verifiable, matches source.

---

## Phase 2b: Create Target Tables (Day 1–2)

For each target table:

```sql
BEGIN TRANSACTION;
-- 1. Create target table (from 13_PROPOSED_TARGET_SCHEMA_V2.md)
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

-- 2. Insert data with deduplication
INSERT INTO dictionary_v2 (zolai, english, english_clean, pos, myanmar, source, zvs_compliance, entry_version, is_deleted, created_at)
SELECT DISTINCT
    d.zolai,
    d.english,
    d.english_clean,
    d.pos,
    COALESCE(d.myanmar, di.myanmar) as myanmar,
    d.source,
    d.zvs_compliance_status,
    d.entry_version,
    d.is_deleted,
    d.updated_at
FROM dictionary d
LEFT JOIN dictionary_import di ON d.zolai = di.zolai;

-- 3. Validate
SELECT COUNT(*) as target_count FROM dictionary_v2;
-- Expected: ~103,303 (canonical only, no import-only extras)

COMMIT;
```

**Estimated time per table:** 5–30 seconds (depends on row count and join complexity).

---

## Phase 2c: Migration Order

Tables are migrated in dependency order:

| Step | Target Table | Source Tables | Est. Rows | Est. Time |
|------|-------------|---------------|-----------|-----------|
| 1 | `bible_verses` | `bible_verses` | 62,751 | 5s |
| 2 | `dictionary` | `dictionary` + `dictionary_import` | 103,303 | 10s |
| 3 | `dictionary_en_zo` | `dictionary_en_zo` + `dictionary_en_zo_import` | 113,750 | 10s |
| 4 | `grammar_patterns` | `grammar_patterns` + `zolai_grammar_patterns` + `grammar_patterns_enhanced` | ~13,519 | 10s |
| 5 | `translations` | `translations` + `translations_import` | 212,754 | 15s |
| 6 | `word_alignments` | `word_alignments` + `word_alignments_import` | ~400,000 | 20s |
| 7 | `vocab` | `vocab` + `zolai_vocabulary` | 112,279 | 10s |
| 8 | `proverbs` | `proverbs` + `zolai_proverbs_idioms` | ~7,736 | 5s |
| 9 | `phrases` | `phrases` + imports | ~5,000 | 5s |
| 10 | `word_usage` | `word_usage` + `zolai_word_usage` | ~85,045 | 10s |
| 11 | `syllable_data` | `syllable_data` | 189,554 | 10s |
| 12 | `word_collocations` | `word_collocations` | 5,000 | 5s |
| 13 | `bible_analysis` | `zolai_bible_analysis` + `bible_context` | ~32,000 | 10s |
| 14 | `articles` | `articles` | 6,371 | 5s |
| 15 | `songs` | `zolai_songs` | 1,032 | 5s |
| 16 | `wiki_content` | `wiki_content` | 1,688 | 5s |
| 17 | `import_log` | `jsonl_import_log` | 92 | 1s |
| 18 | `data_audit_log` | `data_audit_log` | 24,762 | 5s |
| 19 | `audit_findings` | `audit_findings` | 713 | 1s |
| 20 | `provenance` | `provenance` | 255 | 1s |
| 21 | `tone_sandhi` | `zolai_tone_sandhi` | 19 | 1s |
| 22 | `tone_patterns` | `tone_patterns` | 118 | 1s |
| 23 | `training_runs` | `training_runs` | 2 | 1s |

**Total estimated time:** ~3 minutes

---

## Phase 2d: Validation (Day 2)

After all tables are created, run these checks:

```sql
-- 1. Row count comparison
SELECT 
    'dictionary_v2' as target, 
    (SELECT COUNT(*) FROM dictionary_v2) as target_rows,
    (SELECT COUNT(*) FROM dictionary) as source_rows;

-- 2. Uniqueness checks
SELECT zolai, COUNT(*) FROM dictionary_v2 GROUP BY zolai HAVING COUNT(*) > 1;

-- 3. Null key checks
SELECT COUNT(*) FROM dictionary_v2 WHERE zolai IS NULL;

-- 4. Cross-table referential integrity
SELECT COUNT(*) FROM word_alignments_v2 wa
LEFT JOIN bible_verses_v2 bv ON wa.ref = bv.ref
WHERE bv.ref IS NULL;
```

---

## Phase 2e: View Layer (Day 2)

Create convenience views that map old table names to new:

```sql
-- Backward compatibility views
CREATE VIEW dictionary_old AS SELECT * FROM dictionary;
CREATE VIEW vocab_old AS SELECT * FROM vocab;
-- etc.
```

---

## Phase 3: Archive (Day 3–5, manual approval)

Only after 1 week of production use:

```sql
-- Rename old tables (non-destructive)
ALTER TABLE dictionary RENAME TO archived_dictionary;
ALTER TABLE dictionary_import RENAME TO archived_dictionary_import;
-- etc.
```

---

## Phase 4: Cleanup (Day 30+, manual approval)

Only after confirming no rollback needed:

```sql
-- Drop archived tables
DROP TABLE archived_dictionary;
DROP TABLE archived_dictionary_import;
-- etc.
```

---

## Rollback Strategy

| Phase | Rollback Action | Data Loss Risk |
|-------|----------------|----------------|
| 2a (Backup) | Delete backup file | None |
| 2b (Create target) | DROP new tables | None |
| 2c (Migrate) | DROP new tables, old tables untouched | None |
| 2d (Validate) | No changes made | None |
| 2e (Views) | DROP views | None |
| 3 (Archive) | ALTER TABLE ... RENAME back | None |
| 4 (Cleanup) | Restore from backup | Full restore required |

**Key invariant:** Old tables are never modified until Phase 3. The migration is purely additive.

---

## Estimated Timeline

| Phase | Duration | Dependencies |
|-------|----------|-------------|
| 2a (Backup) | 1 hour | None |
| 2b + 2c (Create + Migrate) | 1 day | Phase 2a |
| 2d (Validate) | 0.5 day | Phase 2c |
| 2e (Views) | 0.5 day | Phase 2c |
| **Phase 2 Total** | **2–3 days** | |
| Phase 3 (Archive) | 1 day | 1 week after Phase 2 |
| Phase 4 (Cleanup) | 0.5 day | 30 days after Phase 3 |
| **Full Migration** | **~5 weeks** | |

---

## Risk Mitigation

1. **Backup verified** before any write
2. **Transaction per table** — atomic migrations
3. **Row count validation** — catches silent data loss
4. **Old tables preserved** — full rollback capability
5. **No schema changes to source** — only new tables created
6. **Staging tables preserved** — import pipeline continues to work
