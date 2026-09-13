# Phase 0 Database Audit — Risk Register

**Generated:** 2026-09-13 20:31

## Risk Matrix

| # | Risk | Severity | Probability | Impact | Mitigation |
|---|------|----------|-------------|--------|------------|
| R1 | No foreign keys → orphaned data accumulation | **Critical** | High | High | Add application-level FK validation; plan PostgreSQL migration |
| R2 | Table proliferation (72 tables) → developer confusion | **High** | High | Medium | Consolidation matrix (doc 16); target 30 tables |
| R3 | Triple-duplication (import/canonical/enriched) → schema drift | **High** | High | High | Merge enriched tables; archive imports |
| R4 | No migration framework → schema changes risky | **High** | Medium | High | Adopt Alembic before any schema changes |
| R5 | Empty tables → maintenance burden, confusion | **Medium** | High | Low | Drop all 0-row tables after team review |
| R6 | Import tables as permanent fixtures → wasted space | **Medium** | High | Medium | Archive all `*_import` tables |
| R7 | Inconsistent column naming → JOIN failures | **Medium** | Medium | Medium | Standardize naming in consolidation phase |
| R8 | No timestamps on derived tables → no change tracking | **Medium** | Medium | Low | Add `created_at`/`updated_at` to all canonical tables |
| R9 | `bible_verses` has 62K rows but no `verse_id` PK | **Low** | Medium | Low | Add explicit PK during migration |
| R10 | SQLite WAL contention under heavy writes | **Medium** | Low | High | Consider PostgreSQL for concurrent access |
| R11 | No data validation constraints (CHECK, UNIQUE) | **Medium** | Medium | Medium | Add constraints during migration |
| R12 | Large import tables (627K word_alignments_import) | **Low** | High | Low | Archive immediately |
| R13 | Mixed encoding in historical data | **Low** | Low | Medium | Already cleaned per progress tracker |
| R14 | No backup/restore testing | **High** | Low | Critical | Implement regular backup + restore testing |

## Risk Detail

### R1 — No Foreign Keys (Critical)

**Description:** SQLite has no enforced foreign keys. Import failures, partial deletions, or application bugs can leave orphaned rows.

**Evidence:**
- No table has `PRAGMA foreign_key_list` results
- Import staging tables (627K rows) reference canonical tables but with no enforcement

**Mitigation:**
1. Immediate: Document all implied FK relationships (doc 03)
2. Short-term: Add application-level FK validation in zolai-core
3. Long-term: PostgreSQL migration with enforced FKs

### R2 — Table Proliferation (High)

**Description:** 72 tables where ~30 would suffice. Makes schema hard to understand and maintain.

**Evidence:**
- 17 import staging tables
- 5 empty "planned" tables
- 14 "enhanced"/"zolai_" prefixed duplicates
- 5 FTS system tables (auto-managed)

### R4 — No Migration Framework (High)

**Description:** All schema changes have been ad-hoc. No versioning, no rollback, no up/down scripts.

**Impact:** Any schema change risks breaking the production database with no undo path.

### R14 — No Backup/Restore Testing (High)

**Description:** The 1.2GB database has no documented backup or restore procedure.

**Impact:** Data loss event would be catastrophic — 103K dictionary entries, 62K Bible verses, etc.
