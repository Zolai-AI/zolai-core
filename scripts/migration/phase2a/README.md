# Phase 2A — Migration Scripts

**Goal:** Create 12 `_v2` canonical tables alongside existing tables (NON-DESTRUCTIVE).

## Overview

| Step | Script | Purpose |
|------|--------|---------|
| 1 | `backup.sh` | Full database backup before any changes |
| 2 | `create_tables.sql` | DDL for 12 `_v2` tables with versioning columns |
| 3 | `migrate_data.py` | Copy data from source → `_v2` tables with content hashing |
| 4 | `validate.py` | Validate row counts, key uniqueness, NULL keys |
| 5 | `rollback.sh` | Drop all `_v2` tables (if needed) |

## Tables Migrated

| # | Source Table | Target `_v2` Table | Key Column |
|---|-------------|-------------------|------------|
| 1 | `dictionary` | `dictionary_v2` | `zolai` |
| 2 | `dictionary_en_zo` | `dictionary_en_zo_v2` | `headword` |
| 3 | `bible_verses` | `bible_verses_v2` | `ref` |
| 4 | `grammar_patterns` | `grammar_patterns_v2` | `pattern_id` |
| 5 | `translations` | `translations_v2` | `source` |
| 6 | `word_alignments` | `word_alignments_v2` | `ref` |
| 7 | `vocab` | `vocabulary_v2` | `headword` |
| 8 | `proverbs` | `proverbs_v2` | `zolai` |
| 9 | `phrases` | `phrases_v2` | `zolai` (renamed from `zo`) |
| 10 | `word_usage` | `word_usage_v2` | `word` |
| 11 | `syllable_data` | `syllable_data_v2` | `word` |
| 12 | `word_collocations` | `word_collocations_v2` | `word1` |

## Versioning Columns

All `_v2` tables include:

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `version` | INTEGER | 1 | Row version counter (increment on edit) |
| `created_at` | TEXT | `datetime('now')` | Creation timestamp |
| `updated_at` | TEXT | `datetime('now')` | Last modification timestamp |
| `content_hash` | TEXT | — | SHA256 hash of content columns |

## Usage

### Step 1: Backup (REQUIRED)

```bash
cd scripts/migration/phase2a
bash backup.sh
```

Creates `../data/zolai_backup_YYYYMMDD_HHMMSS.db`.

### Step 2: Create Tables

```bash
sqlite3 ../../data/zolai.db < create_tables.sql
```

### Step 3: Migrate Data

```bash
python3 migrate_data.py
```

Uses SQLite's built-in `sha256()` function (available in SQLite 3.45+).
Falls back to Python hashing if not available.

### Step 4: Validate

```bash
python3 validate.py
```

Checks:
- Row counts (target > 0)
- Key uniqueness (no duplicates)
- NULL key values
- Content hash coverage

### Step 5 (Optional): Rollback

```bash
bash rollback.sh
```

Drops all `_v2` tables. Original tables remain untouched.

## Safety Guarantees

- **NON-DESTRUCTIVE:** Original tables are NEVER modified
- **Idempotent:** `CREATE TABLE IF NOT EXISTS` prevents duplicate creation
- **Rollback:** Drop `_v2` tables at any time
- **Audit trail:** `content_hash` enables change detection
- **Version tracking:** `version` column for optimistic locking

## Dependencies

- SQLite 3.25+ (for `VACUUM INTO`)
- SQLite 3.45+ (for `sha256()`, optional — Python fallback available)
- Python 3.10+

## Next Steps

After Phase 2A:
1. **Phase 2B:** Add constraints + indexes to `_v2` tables
2. **Phase 2C:** Switch application code to read/write `_v2` tables
3. **Phase 2D:** Drop old tables (after validation period)
