# Phase 0 Database Audit — Executive Summary

**Database:** `data/zolai.db` (SQLite WAL)
**Generated:** 2026-09-13 20:31
**Size:** 1162.3 MB

## Current Database Architecture

| Metric | Value |
|--------|-------|
| Total tables | 72 |
| Total rows | 3,145,224 |
| Database size | 1162.3 MB |
| Canonical tables | 30 |
| Import staging tables | 27 |
| Empty/planned tables | 11 |
| Tables without PK | 28 |

### Domain Distribution

| Domain | Canonical Rows | Tables |
|--------|---------------|--------|
| Dictionary (ZO→EN + EN→ZO) | 217,053 | 2 |
| Bible | 63,979 | 2 |
| Translations | 212,754 | 1 |
| Vocabulary | 206,737 | 2 |
| Phrases | 5,000 | 1 |
| Grammar | 24,663 | 3 |
| Word Alignments | 385,120 | 1 |
| Word Usage | 145,410 | 2 |
| Training | 81,805 | 1 |
| Syllable | 189,554 | 1 |
| Proverbs | 12,720 | 2 |
| Wiki/Content | 8,059 | 2 |

## Major Problems Found

### P1 — Table Proliferation (72 tables)
The database has **72 tables** where approximately **25-30 canonical tables** would suffice.
- **27 import staging tables** exist as permanent fixtures rather than transient staging areas.
- **14 "enhanced" / "zolai_" prefixed tables** duplicate canonical data with different schemas.
- Many tables are **completely empty** (11 tables with 0 rows) — likely planned features never implemented.

### P2 — No Foreign Keys
SQLite has no enforced foreign keys. All relationships are implicit via column naming conventions.
This means:
- No referential integrity guarantees
- Orphaned rows can accumulate silently
- JOIN correctness depends entirely on application code

### P3 — Inconsistent Schema Design
- Tables without primary keys: 28
- Many tables lack `created_at`/`updated_at` timestamps
- No standard `source` or `provenance` columns on derived tables
- Mixed naming conventions (`snake_case` and `camelCase` columns in some tables)

### P4 — Triple-Duplication Pattern
Multiple domains have three versions of essentially the same data:
1. `_import` staging table (raw pipeline output)
2. Canonical table (cleaned/enriched)
3. `zolai_` prefixed or `_enhanced` table (further enriched)

Example: `grammar_patterns` (5,547) → `grammar_patterns_enhanced` (5,597) → `zolai_grammar_patterns` (13,519)

### P5 — Ghost Tables (Empty but Structured)
Several tables have full schemas but zero rows:
- `bible_verses_enhanced` — planned enhancement never completed
- `dictionary_enhanced`, `vocabulary_enhanced` — same
- `gemini_model_results` — tracking table never used
- `pos_gold`, `pos_verified`, `morph_verified` — NLP pipeline never run
- `word_similarity` — experimental table never populated

## Major Strengths

### S1 — Comprehensive Data Coverage
The database covers all major language learning domains:
- 103K+ dictionary entries (ZO→EN) + 113K+ (EN→ZO)
- 62K+ Bible verses with parallel EN/ZO
- 385K+ word alignments
- 212K+ translation pairs
- 81K+ training exercises
- 189K+ syllable entries

### S2 — Audit Trail Infrastructure
`data_audit_log` (24,762 rows) and `audit_findings` (713 rows) provide change tracking.
`provenance` (255 rows) tracks source file lineage.

### S3 — Wiki Integration
`wiki_content` + `wiki_lessons` (1,688 each) with FTS search index for knowledge base integration.

### S4 — ZVS Validation
`zvs_corrections_import` and domain-specific columns show ZVS 2018 orthography enforcement.

## Biggest Risks

| Risk | Severity | Description |
|------|----------|-------------|
| Orphaned data | High | No FK enforcement means import failures leave partial data |
| Schema drift | High | 3 versions per domain will diverge over time |
| Empty table maintenance | Medium | Empty tables add confusion and slow schema introspection |
| Import table bloat | Medium | Import tables (some with 627K rows) consume space but aren't needed at runtime |
| No migration framework | High | Schema changes are ad-hoc; no versioning or rollback |

## Recommended Direction

1. **Consolidate to ~30 canonical tables.** Archive import staging tables. Merge `zolai_` and `_enhanced` variants into canonical tables.
2. **Add `source` column to all derived tables** for traceability.
3. **Document table relationships** in a `SCHEMA.md` as a FK surrogate.
4. **Drop or archive empty tables** that represent abandoned features.
5. **Consider PostgreSQL migration** for FK support, concurrent writes, and JSONB for flexible schemas (as flagged in progress tracker).
6. **Add migration framework** (e.g., Alembic) before any schema changes.
