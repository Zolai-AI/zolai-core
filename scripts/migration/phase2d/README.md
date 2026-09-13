# Phase 2D — Table Cutover

**Goal:** Atomically switch from old canonical tables to `_v2` tables, then clean up.

## Overview

| Step | Script | Purpose |
|------|--------|---------|
| 1 | `validate_cutover.py` | Pre-flight: compare old vs `_v2` for all 23 pairs |
| 2 | `switch_tables.sql` | Atomic renames: old→`_old`, `_v2`→canonical |
| 3 | `verify_switch.py` | Post-cutover: confirm all 23 canonical tables exist |
| 4 | `cleanup_old.py` | Drop `_old` tables + optional VACUUM |

## Tables Migrated (23 pairs)

| # | Old Table | _v2 Table | New Canonical | Notes |
|---|-----------|-----------|---------------|-------|
| 1 | `dictionary` | `dictionary_v2` | `dictionary` | — |
| 2 | `dictionary_en_zo` | `dictionary_en_zo_v2` | `dictionary_en_zo` | — |
| 3 | `bible_verses` | `bible_verses_v2` | `bible_verses` | — |
| 4 | `grammar_patterns` | `grammar_patterns_v2` | `grammar_patterns` | — |
| 5 | `translations` | `translations_v2` | `translations` | — |
| 6 | `word_alignments` | `word_alignments_v2` | `word_alignments` | — |
| 7 | `vocab` | `vocabulary_v2` | `vocabulary` | Name change |
| 8 | `proverbs` | `proverbs_v2` | `proverbs` | — |
| 9 | `phrases` | `phrases_v2` | `phrases` | — |
| 10 | `word_usage` | `word_usage_v2` | `word_usage` | — |
| 11 | `syllable_data` | `syllable_data_v2` | `syllable_data` | — |
| 12 | `word_collocations` | `word_collocations_v2` | `word_collocations` | — |
| 13 | `bible_context` | `bible_analysis_v2` | `bible_analysis` | Name change |
| 14 | `articles` | `articles_v2` | `articles` | — |
| 15 | `zolai_songs` | `songs_v2` | `songs` | Name change |
| 16 | `wiki_content` | `wiki_content_v2` | `wiki_content` | — |
| 17 | `data_audit_log` | `data_audit_log_v2` | `data_audit_log` | — |
| 18 | `audit_findings` | `audit_findings_v2` | `audit_findings` | — |
| 19 | `provenance` | `provenance_v2` | `provenance` | — |
| 20 | `jsonl_import_log` | `import_log_v2` | `import_log` | Name change |
| 21 | `zolai_tone_sandhi` | `tone_sandhi_v2` | `tone_sandhi` | Name change |
| 22 | `tone_patterns` | `tone_patterns_v2` | `tone_patterns` | — |
| 23 | `training_runs` | `training_runs_v2` | `training_runs` | — |

## Usage

### Step 0: Backup (REQUIRED)

```bash
cd scripts/migration/phase2a
bash backup.sh
# Creates ../data/zolai_backup_YYYYMMDD_HHMMSS.db
```

### Step 1: Validate Pre-Cutover

```bash
cd scripts/migration/phase2d
python3 validate_cutover.py
```

Checks for all 23 pairs:
- Row counts match between old and _v2
- Spot-check 100 random rows per table (key + content comparison)
- Reports any gaps or mismatches

**Must pass before proceeding.**

### Step 2: Run Cutover

```bash
sqlite3 ../../data/zolai.db < switch_tables.sql
```

Each of the 23 pairs is an independent transaction:
1. `ALTER TABLE old_table RENAME TO old_table_old`
2. `ALTER TABLE v2_table RENAME TO canonical_name`
3. `COMMIT`

If any pair fails, only that transaction is rolled back.

### Step 3: Verify Post-Cutover

```bash
python3 verify_switch.py
```

Confirms:
- All 23 canonical table names exist
- No `_v2` suffix tables remain
- Row counts meet minimum thresholds

### Step 4: Cleanup Old Tables

```bash
# Dry run first
python3 cleanup_old.py --dry-run

# Actually drop (interactive confirmation required)
python3 cleanup_old.py

# With VACUUM to reclaim disk space
python3 cleanup_old.py --vacuum
```

## Rollback Strategy

**If cutover fails partway through:**

The SQL script uses per-pair transactions, so partial failures are isolated.
To rollback completed renames:

```sql
-- For each successfully renamed pair, reverse the rename:
ALTER TABLE dictionary RENAME TO dictionary_v2;
ALTER TABLE dictionary_old RENAME TO dictionary;
-- Repeat for all affected pairs
```

**If post-cutover validation fails:**

Run the rollback SQL above, then investigate. The `_old` tables are the
safety net — they contain the original data until explicitly dropped.

**Nuclear option — restore from backup:**

```bash
cp ../data/zolai_backup_YYYYMMDD_HHMMSS.db ../data/zolai.db
```

## Safety Guarantees

- **Atomic per pair:** Each table pair is an independent transaction
- **Non-destructive until cleanup:** `_old` tables preserve original data
- **Validation gates:** Pre-flight and post-flight checks before/after
- **Rollback capable:** Rename _old back to canonical at any time
- **Interactive cleanup:** Requires typing "DROP" to confirm

## Dependencies

- SQLite 3.25+ (for `ALTER TABLE ... RENAME`)
- Python 3.10+

## Notes for Code Updates

After cutover, update these references in application code:

| Old Reference | New Reference |
|---------------|---------------|
| `vocab` table | `vocabulary` table |
| `bible_context` table | `bible_analysis` table |
| `zolai_songs` table | `songs` table |
| `jsonl_import_log` table | `import_log` table |
| `zolai_tone_sandhi` table | `tone_sandhi` table |
