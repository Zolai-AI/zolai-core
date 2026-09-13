# Phase 2B — Constraints, Indexes + 11 Derived Tables

**NON-DESTRUCTIVE**: adds constraints/indexes to existing `_v2` tables and creates 11 new derived `_v2` tables. Old source tables are never modified.

## Target: 23 total `_v2` tables

| Category | Tables | Rows |
|----------|--------|------|
| 12 core (from Phase 2A) | dictionary, dictionary_en_zo, bible_verses, grammar_patterns, translations, word_alignments, vocabulary, proverbs, phrases, word_usage, syllable_data, word_collocations | ~1.27M |
| 11 derived (Phase 2B) | bible_analysis, articles, songs, wiki_content, data_audit_log, audit_findings, provenance, import_log, tone_sandhi, tone_patterns, training_runs | ~37K |

## What Phase 2B Adds

### `create_constraints.sql`
- **5 UNIQUE constraints** on zero-duplicate tables:
  - `grammar_patterns_v2(pattern_id)`
  - `phrases_v2(zolai)`
  - `word_usage_v2(word, book, meaning_shifts)`
  - `syllable_data_v2(word)`
  - `word_collocations_v2(word1, word2)`
- **15 indexes** on all 12 core tables (some already existed from Phase 2A; IF NOT EXISTS makes this idempotent)

### `create_derived_tables.sql`
Creates 11 new `_v2` tables with `version`, `created_at`, `updated_at`, `content_hash` columns plus per-table indexes.

### `migrate_derived.py`
Copies all data from source tables to the 11 derived `_v2` tables with SHA256 content hashing.

### `validate_all.py`
Validates all 23 `_v2` tables: row counts, key uniqueness, NULL checks, version/hash columns, index existence.

## Usage

```bash
cd scripts/migration/phase2b

# Step 1: Create constraints + indexes (idempotent)
sqlite3 ../../../data/zolai.db < create_constraints.sql

# Step 2: Create derived tables
sqlite3 ../../../data/zolai.db < create_derived_tables.sql

# Step 3: Migrate derived data
python3 migrate_derived.py

# Step 4: Validate everything
python3 validate_all.py
```

## Phase 2C (Next)

- API-key auth + per-key/organization limits
- PostgreSQL migration layer via `database_layer.py`
- Soft-delete + versioning enforcement
- Audit trigger for all writes

## File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `create_constraints.sql` | ~85 | UNIQUE constraints + indexes |
| `create_derived_tables.sql` | ~220 | DDL for 11 derived _v2 tables |
| `migrate_derived.py` | ~310 | Data migration + SHA256 hashing |
| `validate_all.py` | ~230 | 23-table validation suite |
| `README.md` | this file | Usage + architecture notes |
