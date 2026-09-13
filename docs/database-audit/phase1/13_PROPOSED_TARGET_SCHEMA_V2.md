# 13 — Proposed Target Schema V2

> **Date:** 2026-09-13
> **Status:** READ-ONLY audit — no changes applied
> **Source:** 79 current tables, ~3.16M rows
> **Target:** Domain-based naming, consolidation by function

---

## Overview

Current state: **79 tables** (18 canonical, 26 `*_import` staging, 18 `zolai_*` enhanced, 6 FTS, 11 empty/placeholder, 2 runtime).

Target state: **~22 tables** organized by domain — no `zolai_` prefix unless the name would otherwise collide. FTS tables follow SQLite convention (auto-created from triggers).

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

**Business key:** `(zolai, english_clean)` — 1,876 duplicates detected (some Zolai words map to multiple English meanings).
**Source tables:** `dictionary` (103,303), `dictionary_import` (156,808 — 44,287 import-only entries).

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

**Business key:** `(headword, translations)` — functional, but headwords can repeat with different translation sets.
**Source tables:** `dictionary_en_zo` (113,750), `dictionary_en_zo_import` (135,276).

### 3. `bible_verses` (Parallel corpus)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `ref` | TEXT NOT NULL UNIQUE | e.g., "GEN 1:1" |
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

**Business key:** `(ref)` — UNIQUE. 62,751 rows.
**Source tables:** `bible_verses` (62,751), `bible_verses_import` (62,204).

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

**Business key:** `(pattern_id)` — unique in base table (5,547 rows, 0 duplicates). `zolai_grammar_patterns` has 13,519 rows with `pattern_id=NULL` — these are enriched variants.
**Source tables:** `grammar_patterns` (5,547), `zolai_grammar_patterns` (13,519), `grammar_patterns_enhanced` (5,597).

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

**Business key:** `(source, target)` — 212,754 canonical rows. 26,867 matched with import by `(source=zolai, target=english)`.
**Source tables:** `translations` (212,754), `translations_import` (135,511).

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

**Business key:** `(ref, zolai_word, english_word)` — composite. 385,120 canonical. 628,722 matched with import (import is a superset).
**Source tables:** `word_alignments` (385,120), `word_alignments_import` (627,000).

### 7. `vocab` (Word frequency index)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `headword` | TEXT NOT NULL UNIQUE | |
| `english` | TEXT | |
| `frequency` | INTEGER | |
| `books` | TEXT | JSON array of book codes |
| `examples` | TEXT | |
| `myanmar` | TEXT | |
| `created_at` | TIMESTAMP | |

**Business key:** `(headword)` — UNIQUE, 0 duplicates. 107,979 rows. All 107,979 match `zolai_vocabulary.zolai`; 4,300 `zolai_vocabulary` entries have no freq data.
**Source tables:** `vocab` (107,979), `vocab_import` (180,458), `zolai_vocabulary` (112,279 — enriched metadata merged in).

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

**Business key:** `(zolai)` — 21 duplicates detected (long sentences with repeated text). 7,736 canonical rows; 5,072 matched with `zolai_proverbs_idioms` (4,984 rows).
**Source tables:** `proverbs` (7,736), `zolai_proverbs_idioms` (4,984).

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

**Business key:** `(zo)` — 5,000 canonical. Import tables: `phrases_import` (15,000), `phrases_from_bible_import` (14,000), `phrase_context_import` (45,597).
**Source tables:** `phrases` (5,000) + import staging.

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

**Business key:** `(word, book)` — composite. `word_usage` (60,365) + `zolai_word_usage` (85,045) — different schemas; merge needed.
**Source tables:** `word_usage` (60,365), `zolai_word_usage` (85,045).

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
| `book` | TEXT | |
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

**Source tables:** `zolai_bible_analysis` (30,758), `bible_context` (1,228), `bible_book_analysis_import` (65), `bible_chapter_analysis_import` (1,153).

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
| `created_at` | TIMESTAMP | |

**Source tables:** `wiki_content` (1,688), `wiki_lessons` (1,688 — same data, 1:1).

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
| `*_import` (26 tables) | ~1,773,000 | Staging data already consumed |
| `*_enhanced` (4 tables) | ~5,597 | Empty or merged into canonical |
| FTS tables (5) | — | Auto-rebuilt from triggers |
| Empty placeholders (11) | 0 | Never populated |
| `training_exercises` | 81,805 | See migration note below |
| `grammar_instructions` | 16 | Merged into grammar_patterns |
| `simbu` | 4,163 | Niche; archive to JSONL |
| `training_runs` | 2 | Keep as-is (Table 23) |

> **Note:** `training_exercises` (81,805 rows) is recommended for export to JSONL + table drop in Phase 2, as it's a generated artifact, not a source-of-truth table. Keeping it during Phase 1 avoids data loss.

---

## Naming Convention

- **No `zolai_` prefix** — domain tables use plain English names
- **Exception:** None needed — all 23 tables have unique names
- **Import tables:** Drop entirely (data already in canonical)
- **Timestamps:** `created_at` / `updated_at` (not `imported_at`)
- **Soft deletes:** `is_deleted` flag (not physical delete)
