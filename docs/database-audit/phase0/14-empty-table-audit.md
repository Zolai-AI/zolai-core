# Phase 0 Database Audit — Empty Table Audit

**Generated:** 2026-09-13 22:15

## Overview

Analysis of all tables with 0 rows in the database, including their purpose, creation reason, and recommendation for archival or deletion.

## Empty Tables Inventory

| Table | Columns | Created | Purpose | Recommendation |
|-------|---------|---------|---------|----------------|
| `bible_verses_enhanced` | ? | Unknown | Enhanced Bible verses | ⚠️ ARCHIVE |
| `dictionary_en_my_import` | ? | Unknown | English→Myanmar dictionary import | ⚠️ ARCHIVE |
| `dictionary_enhanced` | ? | Unknown | Enhanced dictionary | ⚠️ ARCHIVE |
| `dictionary_trilingual_import` | ? | Unknown | Trilingual dictionary import | ⚠️ ARCHIVE |
| `gemini_model_results` | ? | Unknown | Gemini model outputs | ⚠️ ARCHIVE |
| `grammar_instructions` | ? | Unknown | Grammar instructions | ⚠️ ARCHIVE |
| `morph_verified` | ? | Unknown | Morphological verification | ⚠️ ARCHIVE |
| `pos_gold` | ? | Unknown | POS gold standard | ⚠️ ARCHIVE |
| `pos_verified` | ? | Unknown | POS verification | ⚠️ ARCHIVE |
| `proverbs_idioms` | ? | Unknown | Proverbs and idioms | ⚠️ ARCHIVE |
| `vocabulary_enhanced` | ? | Unknown | Enhanced vocabulary | ⚠️ ARCHIVE |
| `word_similarity` | ? | Unknown | Word similarity scores | ⚠️ ARCHIVE |

## Detailed Analysis

### 1. bible_verses_enhanced

**Purpose:** Enhanced version of `bible_verses` with additional metadata
**Status:** Empty — never populated
**Recommendation:** Archive or drop. The canonical `bible_verses` table is sufficient.

### 2. dictionary_en_my_import

**Purpose:** Staging table for English→Myanmar dictionary import
**Status:** Empty — no source data available
**Source:** `processed/my/dict_my_zo_v1.jsonl` (file missing)
**Recommendation:** Archive. Source data not available.

### 3. dictionary_enhanced

**Purpose:** Enhanced version of `dictionary` with additional fields
**Status:** Empty — never populated
**Recommendation:** Archive or drop. The canonical `dictionary` table is sufficient.

### 4. dictionary_trilingual_import

**Purpose:** Staging table for trilingual dictionary import
**Status:** Empty — no source data available
**Source:** `processed/my/dict_trilingual_v1.jsonl` (file missing)
**Recommendation:** Archive. Source data not available.

### 5. gemini_model_results

**Purpose:** Store Gemini model outputs for history tracking
**Status:** Empty — never used
**Recommendation:** Archive or repurpose for model output logging.

### 6. grammar_instructions

**Purpose:** Store grammar instruction rules
**Status:** Empty — never populated
**Recommendation:** Archive or merge into `grammar_patterns` table.

### 7. morph_verified

**Purpose:** Store verified morphological analyses
**Status:** Empty — never populated
**Recommendation:** Archive or repurpose for morphological verification workflow.

### 8. pos_gold

**Purpose:** Store gold standard POS tags
**Status:** Empty — never populated
**Recommendation:** Archive or repurpose for POS tagging evaluation.

### 9. pos_verified

**Purpose:** Store verified POS tags
**Status:** Empty — never populated
**Recommendation:** Archive or repurpose for POS tagging workflow.

### 10. proverbs_idioms

**Purpose:** Store proverbs and idioms (duplicate of `proverbs`)
**Status:** Empty — never populated
**Recommendation:** Archive or drop. Use `proverbs` or `zolai_proverbs_idioms` instead.

### 11. vocabulary_enhanced

**Purpose:** Enhanced version of `vocab` with additional fields
**Status:** Empty — never populated
**Recommendation:** Archive or drop. The canonical `vocab` table is sufficient.

### 12. word_similarity

**Purpose:** Store word similarity scores
**Status:** Empty — never populated
**Recommendation:** Archive or repurpose for semantic similarity features.

## Creation Reasons

These tables were likely created for:

1. **Future features** — Planned enhancements that were never implemented
2. **Data pipeline stages** — Intermediate tables that were abandoned
3. **Experimental workflows** — Proof-of-concept tables that weren't completed
4. **Schema evolution** — Legacy tables superseded by newer designs

## Recommendations

### Immediate Actions

1. **Archive empty tables** — Move to `_archived` suffix or separate schema
2. **Document purposes** — Add comments explaining why each table exists
3. **Review necessity** — Determine if any are still needed for future features

### Long-term Strategy

1. **Schema cleanup** — Remove truly unused tables after archival period
2. **Feature planning** — Decide which empty tables to repurpose vs. drop
3. **Documentation** — Add table purposes to schema documentation

## Archive Process

### Step 1: Rename Tables
```sql
-- Rename to _archived suffix
ALTER TABLE bible_verses_enhanced RENAME TO bible_verses_enhanced_archived;
ALTER TABLE dictionary_en_my_import RENAME TO dictionary_en_my_import_archived;
-- ... etc for all empty tables
```

### Step 2: Update Code References
- Remove any code that references these tables
- Update documentation to reflect archival

### Step 3: Monitor Usage
- Track if any code tries to access archived tables
- Remove after 6 months of no access

## Impact Assessment

### Storage Impact
- **Current:** 0 rows (no storage impact)
- **Schema overhead:** ~12 table definitions
- **Index overhead:** 0 (no indexes on empty tables)

### Performance Impact
- **Query planning:** Negligible (SQLite skips empty tables)
- **Schema loading:** Minimal (12 extra table definitions)
- **Maintenance:** Zero (no data to maintain)

### Code Impact
- **References:** 0 code references to empty tables
- **Migration:** No migration needed
- **Testing:** No tests reference empty tables

## Future Use Cases

Some empty tables could be repurposed:

| Table | Potential Repurpose |
|-------|---------------------|
| `gemini_model_results` | Model output logging |
| `morph_verified` | Morphological verification workflow |
| `pos_gold` | POS tagging evaluation |
| `pos_verified` | POS tagging workflow |
| `word_similarity` | Semantic similarity features |

## Decision Matrix

| Table | Keep | Archive | Drop | Repurpose |
|-------|------|---------|------|-----------|
| `bible_verses_enhanced` | ❌ | ✅ | ✅ | ❌ |
| `dictionary_en_my_import` | ❌ | ✅ | ✅ | ❌ |
| `dictionary_enhanced` | ❌ | ✅ | ✅ | ❌ |
| `dictionary_trilingual_import` | ❌ | ✅ | ✅ | ❌ |
| `gemini_model_results` | ❌ | ✅ | ❌ | ✅ |
| `grammar_instructions` | ❌ | ✅ | ✅ | ❌ |
| `morph_verified` | ❌ | ✅ | ❌ | ✅ |
| `pos_gold` | ❌ | ✅ | ❌ | ✅ |
| `pos_verified` | ❌ | ✅ | ❌ | ✅ |
| `proverbs_idioms` | ❌ | ✅ | ✅ | ❌ |
| `vocabulary_enhanced` | ❌ | ✅ | ✅ | ❌ |
| `word_similarity` | ❌ | ✅ | ❌ | ✅ |

## Summary

- **Total empty tables:** 12
- **Tables to archive:** 12
- **Tables to drop:** 6
- **Tables to repurpose:** 5
- **Storage impact:** None
- **Code impact:** None

**Recommendation:** Archive all 12 empty tables, then review for repurposing after 6 months.