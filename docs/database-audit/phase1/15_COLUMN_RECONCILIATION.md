# 15 — Column Reconciliation

> **Date:** 2026-09-13
> **Status:** READ-ONLY audit
> **Scope:** Every MERGE operation — column-level source→target mapping

---

## How to Read This Document

For each target table that merges data from multiple sources, we list every column and trace it back to the source table(s). The `Decision` column indicates how the column is populated:

- **Direct** — column exists in source with same name, copied as-is
- **COALESCE** — first non-NULL value from multiple sources
- **Derived** — computed during migration (e.g., count, concatenation)
- **Default** — set to a fixed value when source is unavailable
- **NULL** — column has no source mapping (will be NULL in target)

---

## 1. `dictionary` (from `dictionary` + `dictionary_import`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | dictionary | `id` | Direct | Auto-increment PK |
| `zolai` | dictionary | `zolai` | Direct | 103,303 rows; 0 NULLs |
| `english` | dictionary | `english` | Direct | Raw English |
| `english_clean` | dictionary | `english_clean` | Direct | Normalized English |
| `pos` | dictionary | `pos` | Direct | Part of speech |
| `myanmar` | dictionary → dictionary_import | `myanmar` → `myanmar_word` | COALESCE | Dictionary has 0% myanmar; import has partial coverage |
| `source` | dictionary | `source` | Direct | Source provenance |
| `zvs_compliance` | dictionary | `zvs_compliance_status` | Direct | Renamed for clarity |
| `entry_version` | dictionary | `entry_version` | Direct | Version tracking |
| `is_deleted` | dictionary | `is_deleted` | Direct | Soft delete flag |
| `created_at` | dictionary | `updated_at` | Direct | Use existing timestamp as created_at |
| `updated_at` | dictionary | `updated_at` | Direct | |

**Merge rule:** `INSERT INTO dictionary_v2 SELECT ... FROM dictionary d LEFT JOIN dictionary_import i ON d.zolai = i.zolai`

**Business key:** `(zolai, english_clean)` — 1,876 duplicate pairs detected. Recommendation: keep all (polysemy), add `sense_id` in Phase 2b if needed.

---

## 2. `dictionary_en_zo` (from `dictionary_en_zo` + `dictionary_en_zo_import`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | dictionary_en_zo | `id` | Direct | |
| `headword` | dictionary_en_zo | `headword` | Direct | English headword |
| `translations` | dictionary_en_zo | `translations` | Direct | Zolai translations |
| `pos` | dictionary_en_zo | `pos` | Direct | |
| `source` | dictionary_en_zo | `source` | Direct | |
| `myanmar` | dictionary_en_zo | `myanmar` | Direct | |
| `zvs_compliance` | dictionary_en_zo | `zvs_compliance_status` | Direct | Renamed |
| `is_deleted` | dictionary_en_zo | `is_deleted` | Direct | |
| `created_at` | dictionary_en_zo | `updated_at` | Direct | |
| `updated_at` | dictionary_en_zo | `updated_at` | Direct | |

**Merge rule:** `INSERT INTO dictionary_en_zo_v2 SELECT ... FROM dictionary_en_zo`

**Note:** Import table (135,276 rows) contains extra columns (38 total) not in canonical. Only canonical columns are migrated; import extras are documented in `dictionary_en_zo_import` archive.

---

## 3. `vocab` (from `vocab` + `zolai_vocabulary`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | vocab | `id` | Direct | |
| `headword` | vocab | `headword` | Direct | 107,979 rows; 0 duplicates |
| `english` | vocab | `english` | Direct | |
| `frequency` | vocab | `frequency` | Direct | |
| `books` | vocab | `books` | Direct | JSON array of book codes |
| `examples` | vocab | `examples` | Direct | |
| `myanmar` | vocab → zolai_vocabulary | `myanmar` → `myanmar` | COALESCE | Vocab has 0% myanmar; zolai_vocab has partial |
| `created_at` | vocab | `imported_at` | Direct | Use import timestamp |
| `updated_at` | vocab | `imported_at` | Direct | |

**Enrichment from `zolai_vocabulary` (4,300 extra rows):**
- `pos`, `tone_category`, `meaning_t1`, `meaning_t3`, `meaning_t4` → stored as JSON in `metadata` column (not in target schema; available in archive)
- `is_compound`, `compound_parts`, `root_word` → compound analysis (available in archive)
- `frequency_bible`, `frequency_corpus`, `frequency_songs` → frequency breakdown (available in archive)

**Merge rule:** `INSERT INTO vocab_v2 SELECT v.id, v.headword, v.english, v.frequency, v.books, v.examples, COALESCE(v.myanmar, z.myanmar) as myanmar, ... FROM vocab v LEFT JOIN zolai_vocabulary z ON v.headword = z.zolai`

---

## 4. `grammar_patterns` (from `grammar_patterns` + `zolai_grammar_patterns` + `grammar_patterns_enhanced`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | grammar_patterns | `id` | Direct | |
| `pattern_id` | grammar_patterns | `pattern_id` | Direct | "pat_0001" format; 0 duplicates |
| `pattern_text` | grammar_patterns → zolai_grammar_patterns | `pattern` → `pattern_text` | COALESCE | Rename for clarity |
| `description` | grammar_patterns | `description` | Direct | |
| `function` | grammar_patterns | `function` | Direct | |
| `examples` | grammar_patterns | `examples` | Direct | |
| `frequency` | grammar_patterns | `frequency` | Direct | |
| `myanmar` | grammar_patterns | `myanmar` | Direct | |
| `tense` | zolai_grammar_patterns | `tense` | Direct | From enriched table |
| `aspect` | zolai_grammar_patterns | `aspect` | Direct | |
| `negation_type` | zolai_grammar_patterns | `negation_type` | Direct | |
| `question_type` | zolai_grammar_patterns | `question_type` | Direct | |
| `source_category` | zolai_grammar_patterns | `source_category` | Direct | |
| `created_at` | zolai_grammar_patterns | `created_at` | Direct | |

**Merge rule:** 
```sql
INSERT INTO grammar_patterns_v2 
SELECT g.id, g.pattern_id, COALESCE(g.pattern, z.pattern_text) as pattern_text,
       g.description, g.function, g.examples, g.frequency, g.myanmar,
       z.tense, z.aspect, z.negation_type, z.question_type, z.source_category,
       COALESCE(z.created_at, CURRENT_TIMESTAMP)
FROM grammar_patterns g
LEFT JOIN zolai_grammar_patterns z ON g.pattern_id = z.pattern_id
```

**Issue:** `zolai_grammar_patterns` has `pattern_id=NULL` for all 13,519 rows → LEFT JOIN produces NULLs. These enriched rows have no matching base pattern and are lost in this merge. **Recommendation:** Export orphaned enriched rows to `archived_zolai_grammar_patterns` for Phase 2 review.

---

## 5. `translations` (from `translations` + `translations_import`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | translations | `id` | Direct | |
| `source` | translations | `source` | Direct | Zolai text |
| `target` | translations | `target` | Direct | English text |
| `direction` | translations | `direction` | Direct | "zo_en" or "en_zo" |
| `reference` | translations | `reference` | Direct | Bible ref |
| `confidence` | translations | `confidence` | Direct | |
| `myanmar` | translations | `myanmar` | Direct | |
| `created_at` | translations | `imported_at` | Direct | |

**Merge rule:** `INSERT INTO translations_v2 SELECT ... FROM translations`

**Note:** Import table (135,511 rows) uses different column names (`zolai`, `english`). Only canonical rows are migrated; import extras documented in `translations_import` archive. 26,867 rows matched by `(source=zolai, target=english)`.

---

## 6. `word_alignments` (from `word_alignments` + `word_alignments_import`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | word_alignments | `id` | Direct | |
| `ref` | word_alignments | `ref` | Direct | Bible verse ref |
| `zolai_word` | word_alignments | `zolai_word` | Direct | |
| `english_word` | word_alignments | `english_word` | Direct | |
| `position` | word_alignments | `position` | Direct | Word position in verse |
| `myanmar` | word_alignments | `myanmar` | Direct | |
| `created_at` | word_alignments | `imported_at` | Direct | |

**Merge rule:** `INSERT INTO word_alignments_v2 SELECT ... FROM word_alignments`

**Note:** Import (627,000 rows) is a superset of canonical (385,120). 628,722 matched by `(ref, zolai_word=zo_word, english_word=en_word)`. Import contains 241,880 extra rows not in canonical → export to archive.

---

## 7. `proverbs` (from `proverbs` + `zolai_proverbs_idioms`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | proverbs | `id` | Direct | |
| `zolai` | proverbs | `zolai` | Direct | |
| `english` | proverbs | `english` | Direct | |
| `literal_translation` | zolai_proverbs_idioms | `literal_translation` | Direct | Not in base proverbs |
| `morpheme_breakdown` | zolai_proverbs_idioms | `morpheme_breakdown` | Direct | Not in base proverbs |
| `category` | proverbs | `category` | Direct | |
| `theme` | zolai_proverbs_idioms | `theme` | Direct | Not in base proverbs |
| `cultural_context` | zolai_proverbs_idioms | `cultural_context` | Direct | Not in base proverbs |
| `source_category` | zolai_proverbs_idioms | `source_category` | Direct | Not in base proverbs |
| `created_at` | proverbs | `imported_at` | Direct | |

**Merge rule:**
```sql
INSERT INTO proverbs_v2
SELECT p.id, p.zolai, p.english, 
       z.literal_translation, z.morpheme_breakdown,
       p.category, z.theme, z.cultural_context, z.source_category,
       p.imported_at
FROM proverbs p
LEFT JOIN zolai_proverbs_idioms z ON p.zolai = z.zolai
```

**Business key issue:** 21 duplicate `zolai` values (long sentences). Dedup by keeping first occurrence.

---

## 8. `word_usage` (from `word_usage` + `zolai_word_usage`)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | word_usage | `id` | Direct | |
| `word` | word_usage | `word` | Direct | |
| `book` | word_usage | `book` | Direct | |
| `total_freq` | word_usage | `total_freq` | Direct | |
| `meaning_shifts` | word_usage | `meaning_shifts` | Direct | JSON |
| `co_occurring_words` | word_usage | `co_occurring_words` | Direct | JSON |
| `myanmar` | word_usage | `myanmar` | Direct | |
| `created_at` | word_usage | `imported_at` | Direct | |

**Merge rule:** `INSERT INTO word_usage_v2 SELECT ... FROM word_usage`

**Note:** `zolai_word_usage` (85,045 rows) has different schema (`book_code`, `meanings`, `co_occurring`, `contexts`). Export to archive; canonical `word_usage` (60,365 rows) is the source.

---

## 9. `bible_analysis` (from `zolai_bible_analysis` + `bible_context` + imports)

| Target Column | Source Table(s) | Source Column(s) | Decision | Evidence |
|--------------|-----------------|------------------|----------|----------|
| `id` | zolai_bible_analysis | `id` | Direct | |
| `book` | zolai_bible_analysis | `book_code` | Direct | Renamed |
| `chapter` | zolai_bible_analysis | `chapter` | Direct | |
| `verse` | zolai_bible_analysis | `verse` | Direct | |
| `zolai` | zolai_bible_analysis | `zolai` | Direct | |
| `english` | zolai_bible_analysis | `english` | Direct | |
| `morpheme_analysis` | zolai_bible_analysis | `morpheme_analysis` | Direct | JSON |
| `grammar_tags` | zolai_bible_analysis | `grammar_tags` | Direct | JSON |
| `tone_analysis` | zolai_bible_analysis | `tone_analysis` | Direct | JSON |
| `compounds_found` | zolai_bible_analysis | `compounds_found` | Direct | |
| `rare_words` | zolai_bible_analysis | `rare_words` | Direct | |
| `created_at` | zolai_bible_analysis | `created_at` | Direct | |

**Merge rule:** `INSERT INTO bible_analysis_v2 SELECT ... FROM zolai_bible_analysis`

**Note:** `bible_context` (1,228 rows) has different structure (`analysis_type`, `data` JSON). Export to archive; not merged into canonical.

---

## Tables with Single Source (no merge needed)

| Target Table | Source Table | Rows | Notes |
|-------------|-------------|------|-------|
| `bible_verses` | `bible_verses` | 62,751 | Direct copy |
| `syllable_data` | `syllable_data` | 189,554 | Direct copy |
| `word_collocations` | `word_collocations` | 5,000 | Direct copy |
| `articles` | `articles` | 6,371 | Rename |
| `songs` | `zolai_songs` | 1,032 | Rename |
| `wiki_content` | `wiki_content` | 1,688 | Direct copy |
| `import_log` | `jsonl_import_log` | 92 | Rename |
| `data_audit_log` | `data_audit_log` | 24,762 | Direct copy |
| `audit_findings` | `audit_findings` | 713 | Direct copy |
| `provenance` | `provenance` | 255 | Direct copy |
| `tone_sandhi` | `zolai_tone_sandhi` | 19 | Rename |
| `tone_patterns` | `tone_patterns` | 118 | Rename |
| `training_runs` | `training_runs` | 2 | Direct copy |

---

## Summary: Merge Operations

| Merge | Source 1 | Source 2 | Source 3 | Target | Columns Affected |
|-------|----------|----------|----------|--------|-----------------|
| 1 | `dictionary` (103K) | `dictionary_import` (157K) | — | `dictionary` | myanmar |
| 2 | `dictionary_en_zo` (114K) | `dictionary_en_zo_import` (135K) | — | `dictionary_en_zo` | — (no extra cols) |
| 3 | `vocab` (108K) | `zolai_vocabulary` (112K) | — | `vocab` | myanmar |
| 4 | `grammar_patterns` (5.5K) | `zolai_grammar_patterns` (13.5K) | `grammar_patterns_enhanced` (5.6K) | `grammar_patterns` | pattern_text, tense, aspect, negation_type, question_type, source_category |
| 5 | `proverbs` (7.7K) | `zolai_proverbs_idioms` (5K) | — | `proverbs` | literal_translation, morpheme_breakdown, theme, cultural_context, source_category |
| 6 | `word_usage` (60K) | `zolai_word_usage` (85K) | — | `word_usage` | — (zolai_word_usage exported, not merged) |
| 7 | `bible_analysis` (30.8K) | `bible_context` (1.2K) | — | `bible_analysis` | — (bible_context exported) |

**Total merges:** 7
**Columns with COALESCE:** 3 (myanmar in dictionary, vocab; pattern_text in grammar)
**Columns with enrichment:** 6 (proverbs +5 cols, grammar +6 cols)
