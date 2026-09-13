# 13 — Proposed Target Schema V2

> **Date:** 2026-09-13 (Revised)
> **Status:** READ-ONLY audit — no changes applied
> **Source:** 79 current tables, ~3.16M total rows
> **Target:** Domain-based naming, consolidation by function

---

## Overview

Current state: **79 tables** — 27 `*_import` staging tables (1,517,304 rows), 17 empty/placeholder tables, 5 FTS tables, 1 `sqlite_sequence`, and ~30 active tables across canonical, enhanced, runtime, and reference domains.

Target state: **~22 tables** organized by domain. No `zolai_` prefix unless collision. FTS tables auto-created from triggers. Import staging dropped after migration.

---

## A. Canonical Tables (12)

### 1. `dictionary` (ZO→EN master)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto-increment |
| `zolai` | TEXT NOT NULL | Business key component |
| `english` | TEXT | Raw English translation |
| `english_clean` | TEXT | Normalized English |
| `pos` | TEXT | Part of speech |
| `myanmar` | TEXT | Myanmar translation |
| `source` | TEXT | Source provenance |
| `zvs_compliance` | TEXT | ZVS 2018 check status |
| `entry_version` | INTEGER DEFAULT 1 | Version tracking |
| `is_deleted` | INTEGER DEFAULT 0 | Soft delete |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

**Business key:** `(zolai, english_clean)` — 1,876+ duplicates detected. Some Zolai words map to multiple English meanings (polysemy). UNIQUE constraint NOT recommended on full pair — keep composite business key for deduplication only.
**Source tables:** `dictionary` (103,303) + `dictionary_import` (156,808). All 103,303 canonical rows match import. 44,287 import-only entries (extras from multiple source files).

### 2. `dictionary_en_zo` (EN→ZO master)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `headword` | TEXT NOT NULL | English headword |
| `translations` | TEXT | Zolai translations (JSON or delimited) |
| `pos` | TEXT | |
| `source` | TEXT | |
| `myanmar` | TEXT | |
| `zvs_compliance` | TEXT | |
| `is_deleted` | INTEGER DEFAULT 0 | |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

**Business key:** `(headword, translations)` — 21 exact duplicates. Headwords repeat with different translation sets (e.g., "abandon" appears 4× with different Zolai translations). UNIQUE constraint NOT recommended.
**Source tables:** `dictionary_en_zo` (113,750) + `dictionary_en_zo_import` (135,276). 127,695 matched by headword. 69,573 import-only entries. 0 canonical-only (full coverage).

### 3. `bible_verses` (Parallel corpus)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `ref` | TEXT NOT NULL | e.g., "GEN 1:1" |
| `book` | TEXT | Book code |
| `book_name` | TEXT | Human-readable |
| `chapter` | INTEGER | |
| `verse` | INTEGER | |
| `zo_tdb77` | TEXT | Tedim Bible 1977 |
| `zo_tedim2010` | TEXT | Tedim 2010 |
| `en_kjv` | TEXT | English KJV |
| `myanmar` | TEXT | Myanmar |
| `zo_hcl06` | TEXT | Hakha Chin 2006 |
| `zo_fcl` | TEXT | Falam Chin |
| `created_at` | TIMESTAMP | |

**Business key:** `(ref)` — 31,649 unique refs but 30,569 refs have 2–3 duplicate rows (different book editions). Recommend UNIQUE on `(ref, book)` or `(ref, zo_tdb77)` to enforce one row per edition. **62,751 total rows.**
**Source tables:** `bible_verses` (62,751) + `bible_verses_import` (62,204). All 30,569 import refs exist in canonical. 0 import-only refs.

### 4. `grammar_patterns` (Grammar rules)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `pattern_id` | TEXT NOT NULL | e.g., "pat_0001" |
| `pattern_text` | TEXT NOT NULL | The grammar pattern |
| `description` | TEXT | |
| `function` | TEXT | e.g., "negation", "question" |
| `examples` | TEXT | |
| `frequency` | INTEGER | |
| `myanmar` | TEXT | |
| `tense` | TEXT | From enhanced |
| `aspect` | TEXT | |
| `negation_type` | TEXT | |
| `question_type` | TEXT | |
| `source_category` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(pattern_id)` — UNIQUE, 0 duplicates in base table (5,547 rows).
**Critical finding:** `zolai_grammar_patterns` (13,519 rows) has `pattern_id = NULL` for ALL rows — zero overlap with `grammar_patterns` by pattern_id. `grammar_patterns_enhanced` (5,597 rows) also has `pattern_id = NULL` for ALL rows. These are enriched variants with no foreign key to the base table.
**Source tables:** `grammar_patterns` (5,547) + `grammar_patterns_enhanced` (5,597) + `zolai_grammar_patterns` (13,519) + `grammar_patterns_import` (6,983) + `grammar_instructions` (16).
**Merge strategy:** `grammar_patterns` as canonical. `grammar_instructions` (16 rows) merged as `instruction_text` column. `grammar_patterns_enhanced` and `zolai_grammar_patterns` exported to archive (no joinable FK).

### 5. `translations` (Sentence pairs)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `source` | TEXT NOT NULL | Zolai text |
| `target` | TEXT NOT NULL | English text |
| `direction` | TEXT | "zo_en" or "en_zo" |
| `reference` | TEXT | Bible ref or source |
| `confidence` | REAL | |
| `myanmar` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(source, target)` — **212,754 canonical rows**. Significant duplication: 67× for `("And the", "{ Topa } in Moses kiangah,")`. Recommend deduplication before migration.
**Source tables:** `translations` (212,754) + `translations_import` (135,511). Import has 135,511 rows with `zolai`/`english` column names (mapped to `source`/`target`).

### 6. `word_alignments` (Word-level ZO↔EN)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `ref` | TEXT NOT NULL | Bible verse ref |
| `zolai_word` | TEXT NOT NULL | |
| `english_word` | TEXT NOT NULL | |
| `position` | INTEGER | Word position in verse |
| `myanmar` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(ref, zolai_word, english_word)` — composite. **385,120 canonical rows.** Some duplication (e.g., 2× for certain triples). Import (627,000 rows, 224,696 unique triples) is a superset.
**Source tables:** `word_alignments` (385,120) + `word_alignments_import` (627,000). Import column names: `zo_word`, `en_word`, `ref`.

### 7. `vocab` (Word frequency index)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `headword` | TEXT NOT NULL | |
| `english` | TEXT | |
| `frequency` | INTEGER | |
| `books` | TEXT | JSON array of book codes |
| `examples` | TEXT | |
| `myanmar` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(headword)` — some duplicates exist (e.g., "aa" appears 2× with different frequencies). **107,979 total rows.** 106,243 overlap with `zolai_vocabulary.zolai`. 1,736 vocab-only, 6,036 zolai_vocabulary-only.
**Source tables:** `vocab` (107,979) + `vocab_import` (180,458) + `zolai_vocabulary` (112,279 — enriched metadata, merge by COALESCE).

### 8. `proverbs` (Proverbs & idioms)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `zolai` | TEXT NOT NULL | |
| `english` | TEXT | |
| `literal_translation` | TEXT | |
| `morpheme_breakdown` | TEXT | |
| `category` | TEXT | |
| `theme` | TEXT | |
| `cultural_context` | TEXT | |
| `source_category` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(zolai)` — duplicates exist (max 8× for long proverbs with shared text). **7,736 canonical rows.** 5,072 overlap with `zolai_proverbs_idioms` (4,984 rows). 2,664 proverbs-only (no enriched data).
**Source tables:** `proverbs` (7,736) + `zolai_proverbs_idioms` (4,984) + `proverbs_import` (7,736). Import uses `zo` column (mapped to `zolai`).

### 9. `phrases` (Multi-word expressions)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `zo` | TEXT NOT NULL | |
| `english` | TEXT | |
| `frequency` | INTEGER | |
| `examples` | TEXT | |
| `is_idiomatic` | INTEGER | |
| `source` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(zo)` — 5,000 canonical rows. Import staging: `phrases_import` (15,000), `phrases_from_bible_import` (14,000), `phrase_context_import` (45,597).

### 10. `word_usage` (Per-book word profiles)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `word` | TEXT NOT NULL | |
| `book` | TEXT NOT NULL | |
| `total_freq` | INTEGER | |
| `meaning_shifts` | TEXT | JSON |
| `co_occurring_words` | TEXT | JSON |
| `myanmar` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(word, book)` — composite, 0 duplicates. **60,365 canonical rows.**
**Critical finding:** `zolai_word_usage` (85,045 rows) has DIFFERENT schema (`book_code`, `frequency`, `meanings`, `co_occurring`, `contexts`, `source_category`). Only 1,848 rows overlap by (word+book). 58,517 word_usage-only, 83,197 zolai_word_usage-only. Merge NOT recommended — export `zolai_word_usage` separately.
**Source tables:** `word_usage` (60,365) + `word_usage_profiles_import` (7,384).

### 11. `syllable_data` (Syllable segmentation)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `word` | TEXT NOT NULL | |
| `syllables` | TEXT | Space-separated syllables |
| `syllable_count` | INTEGER | |
| `engine` | TEXT | "syltk" or "zolai" |
| `confidence` | REAL | |
| `source_table` | TEXT | Origin table |
| `source_id` | INTEGER | Origin row ID |
| `created_at` | TIMESTAMP | |

**Business key:** `(word, engine)` — 189,554 rows. Single source, no reconciliation needed.

### 12. `word_collocations` (Word pair frequencies)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `word1` | TEXT NOT NULL | |
| `word2` | TEXT NOT NULL | |
| `frequency` | INTEGER | |
| `myanmar` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(word1, word2)` — 5,000 rows. Import: `word_collocations_import` (5,000 — same data, different schema).

---

## B. Derived/Analysis Tables (4)

### 13. `bible_analysis` (Verse-level analysis)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `book_code` | TEXT | |
| `book_name` | TEXT | |
| `chapter` | INTEGER | |
| `verse` | INTEGER | |
| `zolai` | TEXT | |
| `english` | TEXT | |
| `morpheme_analysis` | TEXT | JSON |
| `grammar_tags` | TEXT | JSON |
| `tone_analysis` | TEXT | JSON |
| `compounds_found` | TEXT | |
| `rare_words` | TEXT | |
| `created_at` | TIMESTAMP | |

**Source tables:** `zolai_bible_analysis` (30,758) — single source. `bible_context` (1,228) has different schema (book/chapter/analysis_type/data) and exported separately. Import: `bible_book_analysis_import` (65), `bible_chapter_analysis_import` (1,153).

### 14. `articles` (Reference articles)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `title` | TEXT | |
| `content` | TEXT | |
| `excerpt` | TEXT | |
| `categories` | TEXT | |
| `date` | TEXT | |
| `link` | TEXT | |
| `language` | TEXT | |
| `created_at` | TIMESTAMP | |

**Source table:** `articles` (6,371) — single source.

### 15. `songs` (Zolai songs)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `collection` | TEXT | |
| `song_number` | INTEGER | |
| `title` | TEXT | |
| `text` | TEXT | |
| `source` | TEXT | |
| `created_at` | TIMESTAMP | |

**Source table:** `zolai_songs` (1,032) — renamed to drop prefix.

### 16. `wiki_content` (Knowledge base pages)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `wiki_category` | TEXT | |
| `source_path` | TEXT | |
| `title` | TEXT | |
| `content` | TEXT | |
| `word_count` | INTEGER | |
| `section_count` | INTEGER | |
| `content_hash` | TEXT | |
| `entry_version` | INTEGER | |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

**Source tables:** `wiki_content` (1,688) + `wiki_lessons` (1,688). 1,656 overlap by title. 32 wiki_content rows have NULL title. `wiki_lessons` has different schema (lesson_type, grammar_patterns, vocabulary_list). Merge: keep `wiki_content` as canonical, merge `wiki_lessons` grammar_patterns/vocabulary_list as JSON columns.

---

## C. Staging / Import Tables (1 — keep for pipeline)

### 17. `import_log` (Pipeline tracking)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `batch_id` | TEXT | |
| `source_file` | TEXT | |
| `table_name` | TEXT | |
| `rows_imported` | INTEGER | |
| `sha256` | TEXT | |
| `imported_at` | TIMESTAMP | |
| `version` | INTEGER | |
| `status` | TEXT | |
| `error_message` | TEXT | |

**Source table:** `jsonl_import_log` (92 rows) — renamed.

---

## D. Audit Tables (3)

### 18. `data_audit_log` (Change tracking)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `table_name` | TEXT | |
| `row_id` | INTEGER | |
| `field` | TEXT | |
| `old_value` | TEXT | |
| `new_value` | TEXT | |
| `changed_at` | TIMESTAMP | |
| `reason` | TEXT | |

**Source table:** `data_audit_log` (24,762 rows) — unchanged.

### 19. `audit_findings` (Validation results)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `finding_type` | TEXT | |
| `word` | TEXT | |
| `old_value` | TEXT | |
| `new_value` | TEXT | |
| `source` | TEXT | |
| `confidence` | REAL | |
| `created_at` | TIMESTAMP | |
| `verified_by` | TEXT | |

**Source table:** `audit_findings` (713 rows).

### 20. `provenance` (Source file tracking)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `filename` | TEXT | |
| `size_bytes` | INTEGER | |
| `sha256` | TEXT | |
| `row_count` | INTEGER | |
| `source` | TEXT | |
| `generator_script` | TEXT | |
| `version` | INTEGER | |
| `status` | TEXT | |
| `updated_at` | TIMESTAMP | |

**Source table:** `provenance` (255 rows).

---

## E. Lookup / Reference Tables (2)

### 21. `tone_sandhi` (Tone rules)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `rule_number` | INTEGER UNIQUE | 1–19 |
| `rule_name` | TEXT | |
| `underlying_pattern` | TEXT | |
| `surface_pattern` | TEXT | |
| `condition` | TEXT | |
| `examples` | TEXT | |
| `domain` | TEXT | |

**Source table:** `zolai_tone_sandhi` (19 rows).

### 22. `tone_patterns` (Word tone categories)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `word` | TEXT | |
| `tone_category` | TEXT | T1/T2/T3/T4 |
| `meaning_t1` | TEXT | |
| `meaning_t3` | TEXT | |
| `meaning_t4` | TEXT | |
| `sandhi_rules` | TEXT | |
| `source_category` | TEXT | |

**Source table:** `tone_patterns` (118 rows).

---

## F. Experimental / Placeholder Tables (1)

### 23. `training_runs` (Fine-tuning experiments)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `model_name` | TEXT | |
| `dataset_name` | TEXT | |
| `entry_count` | INTEGER | |
| `metrics_json` | TEXT | |
| `status` | TEXT | |
| `created_at` | TIMESTAMP | |

**Source table:** `training_runs` (2 rows).

---

## Consolidated Target Summary

| Category | Target Tables | Total Target Rows |
|----------|--------------|-------------------|
| Canonical | 12 | ~1,508,000 |
| Derived/Analysis | 4 | ~40,000 |
| Staging | 1 | 92 |
| Audit | 3 | ~25,730 |
| Reference | 2 | 137 |
| Experimental | 1 | 2 |
| **Total** | **23** | **~1,574,000** |

### Tables Dropped (no migration)

| Table | Rows | Reason |
|-------|------|--------|
| `*_import` (27 tables) | 1,517,304 | Staging data already consumed by canonical |
| Empty placeholders (17) | 0 | Never populated: `bible_verses_enhanced`, `corrections`, `dictionary_en_my_import`, `dictionary_enhanced`, `dictionary_trilingual_import`, `gemini_model_results`, `knowledge_vectors`, `morph_verified`, `ngram`, `particle_database`, `pos_gold`, `pos_verified`, `proverbs_idioms`, `training_validation`, `verb_database`, `vocabulary_enhanced`, `word_similarity` |
| FTS tables (5) | — | Auto-rebuilt from triggers |
| `grammar_patterns_enhanced` | 5,597 | All pattern_id=NULL; exported to JSONL archive |
| `zolai_grammar_patterns` | 13,519 | All pattern_id=NULL; exported to JSONL archive |
| `zolai_word_usage` | 85,045 | Different schema from word_usage; exported to JSONL archive |
| `training_exercises` | 81,805 | Generated artifact; export to JSONL for training |
| `simbu` | 4,163 | Niche; archive to JSONL |
| `grammar_instructions` | 16 | Merged into grammar_patterns as instruction_text |
| `bible_context` | 1,228 | Exported to JSONL; different schema from bible_analysis |
| `zvs_corrections_import` | 44 | Pipeline artifact |
| `wiki_lessons` | 1,688 | Merged into wiki_content |
| `zolai_vocabulary_import` | 12,692 | Staging |
| `sentence_patterns_import` | 65 | Staging |
| `topic_clusters_import` | 12 | Staging |

> **Note:** `training_exercises` (81,805 rows) is recommended for export to JSONL + table drop, as it's a generated artifact, not a source-of-truth table. Keeping it during Phase 1 avoids data loss.

---

## Naming Convention

- **No `zolai_` prefix** — domain tables use plain English names
- **Exception:** None needed — all 23 tables have unique names
- **Import tables:** Drop entirely (data already in canonical)
- **Timestamps:** `created_at` / `updated_at` (not `imported_at`)
- **Soft deletes:** `is_deleted` flag (not physical delete)
