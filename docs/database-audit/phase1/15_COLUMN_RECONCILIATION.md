# 15 — Column Reconciliation

> **Date:** 2026-09-13 (Revised)
> **Status:** READ-ONLY audit
> **Purpose:** For each MERGE operation — map source columns to target columns with decisions and evidence

---

## Merge 1: `dictionary` (dictionary + dictionary_import)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `dictionary` | `id` | `id` | direct | Auto-increment PK |
| `dictionary` | `zolai` | `zolai` | direct | Business key component |
| `dictionary` | `english` | `english` | direct | Raw translation |
| `dictionary` | `english_clean` | `english_clean` | direct | Normalized translation |
| `dictionary` | `pos` | `pos` | direct | Part of speech |
| `dictionary` | `myanmar` | `myanmar` | COALESCE | Left: dictionary.myanmar (sparse) → Right: dictionary_import.myanmar_word |
| `dictionary` | `source` | `source` | direct | Source provenance |
| `dictionary` | `zvs_compliance_status` | `zvs_compliance` | rename | Column renamed for clarity |
| `dictionary` | `entry_version` | `entry_version` | direct | Version tracking |
| `dictionary` | `is_deleted` | `is_deleted` | direct | Soft delete flag |
| `dictionary` | `imported_at` | `created_at` | rename | Timestamp for creation |

**Join:** `dictionary.zolai = dictionary_import.zolai`
**Matched:** 103,303 (all canonical rows match import)
**Canonical only:** 0
**Import only:** 44,287 (extra entries from multiple source files)
**Issue:** `dictionary_import.myanmar_word` is the Myanmar column in import; `dictionary.myanmar` is sparse. COALESCE picks non-null.

---

## Merge 2: `dictionary_en_zo` (dictionary_en_zo + dictionary_en_zo_import)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `dictionary_en_zo` | `id` | `id` | direct | |
| `dictionary_en_zo` | `headword` | `headword` | direct | English headword |
| `dictionary_en_zo` | `translations` | `translations` | direct | Zolai translations |
| `dictionary_en_zo` | `pos` | `pos` | direct | |
| `dictionary_en_zo` | `source` | `source` | direct | |
| `dictionary_en_zo` | `myanmar` | `myanmar` | direct | |
| `dictionary_en_zo` | `zvs_compliance_status` | `zvs_compliance` | rename | |
| `dictionary_en_zo` | `is_deleted` | `is_deleted` | direct | |
| `dictionary_en_zo` | `imported_at` | `created_at` | rename | |
| `dictionary_en_zo` | `imported_at` | `updated_at` | rename | No separate updated_at |

**Join:** `dictionary_en_zo.headword = dictionary_en_zo_import.headword`
**Matched:** 127,695
**Canonical only:** 0
**Import only:** 69,573
**Issue:** Import has 38 columns; only canonical 10 are migrated. Extras (dialect, cefr, antonyms, etc.) archived.

---

## Merge 3: `vocab` (vocab + zolai_vocabulary)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `vocab` | `id` | `id` | direct | |
| `vocab` | `headword` | `headword` | direct | Business key |
| `vocab` | `english` | `english` | direct | |
| `vocab` | `frequency` | `frequency` | direct | |
| `vocab` | `books` | `books` | direct | JSON array |
| `vocab` | `examples` | `examples` | direct | |
| `vocab` | `myanmar` | `myanmar` | COALESCE | Left: vocab.myanmar (sparse) → Right: zolai_vocabulary.myanmar |
| `vocab` | `imported_at` | `created_at` | rename | |
| `vocab` | `imported_at` | `updated_at` | rename | |

**Join:** `vocab.headword = zolai_vocabulary.zolai`
**Overlap:** 106,243
**Vocab only:** 1,736 (freq data without enriched metadata)
**zolai_vocabulary only:** 6,036 (enriched metadata without freq data)
**Issue:** Both tables have duplicate headwords (vocab: "aa" 2×; zolai_vocabulary: "khem" 4×). DISTINCT needed.

---

## Merge 4: `grammar_patterns` (grammar_patterns + grammar_instructions)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `grammar_patterns` | `id` | `id` | direct | |
| `grammar_patterns` | `pattern_id` | `pattern_id` | direct | UNIQUE |
| `grammar_patterns` | `pattern` | `pattern_text` | rename | Renamed for clarity |
| `grammar_patterns` | `description` | `description` | direct | |
| `grammar_patterns` | `function` | `function` | direct | |
| `grammar_patterns` | `examples` | `examples` | direct | |
| `grammar_patterns` | `frequency` | `frequency` | direct | |
| `grammar_patterns` | `myanmar` | `myanmar` | direct | |
| `grammar_patterns` | `imported_at` | `created_at` | rename | |
| `grammar_instructions` | `instruction` | `instruction_text` | MERGE | 16 rows → new pattern_id = 'INSTRUCTION_N' |
| `grammar_instructions` | `input` | — | DROP | Input context, not needed |
| `grammar_instructions` | `output` | — | DROP | Output text merged into instruction_text |

**Join:** N/A — instructions are appended as new rows
**Pattern rows:** 5,547
**Instruction rows:** 16
**Total target:** ~5,563
**Critical:** `zolai_grammar_patterns` (13,519) and `grammar_patterns_enhanced` (5,597) have `pattern_id = NULL` — cannot be joined. Exported to archive.

---

## Merge 5: `proverbs` (proverbs + zolai_proverbs_idioms)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `proverbs` | `id` | `id` | direct | |
| `proverbs` | `zolai` | `zolai` | direct | Business key component |
| `proverbs` | `english` | `english` | direct | |
| `proverbs` | `category` | `category` | direct | |
| `zolai_proverbs_idioms` | `literal_translation` | `literal_translation` | direct | Enriched field |
| `zolai_proverbs_idioms` | `morpheme_breakdown` | `morpheme_breakdown` | direct | Enriched field |
| `zolai_proverbs_idioms` | `theme` | `theme` | direct | Enriched field |
| `zolai_proverbs_idioms` | `cultural_context` | `cultural_context` | direct | Enriched field |
| `zolai_proverbs_idioms` | `source_category` | `source_category` | direct | |
| `proverbs` | `imported_at` | `created_at` | rename | |

**Join:** `proverbs.zolai = zolai_proverbs_idioms.zolai`
**Overlap:** 5,072
**Proverbs only:** 2,664 (no enriched data)
**zolai_proverbs_idioms only:** 0 (all matched)
**Issue:** `proverbs_idioms` (not `zolai_` prefix) is EMPTY (0 rows). Only `zolai_proverbs_idioms` has data.

---

## Merge 6: `translations` (translations + translations_import)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `translations` | `id` | `id` | direct | |
| `translations` | `source` | `source` | direct | Zolai text |
| `translations` | `target` | `target` | direct | English text |
| `translations` | `direction` | `direction` | direct | |
| `translations` | `reference` | `reference` | direct | |
| `translations` | `confidence` | `confidence` | direct | |
| `translations` | `myanmar` | `myanmar` | direct | |
| `translations` | `imported_at` | `created_at` | rename | |
| `translations_import` | `zolai` | — | DROP | Same as source |
| `translations_import` | `english` | — | DROP | Same as target |
| `translations_import` | `dialect` | — | DROP | Metadata only |
| `translations_import` | `source` | — | DROP | Metadata only |
| `translations_import` | `reference` | — | DROP | Same as reference |

**Join:** `translations.source = translations_import.zolai AND translations.target = translations_import.english`
**Matched:** 135,511 (import is subset of canonical)
**Canonical only:** 77,243 (212,754 − 135,511)
**Import only:** 0
**Issue:** 67× duplication for `("And the", "{ Topa } in Moses kiangah,")`. Deduplication needed.

---

## Merge 7: `word_alignments` (word_alignments + word_alignments_import)

| Source Table | Source Column | Target Column | Decision | Evidence |
|-------------|---------------|---------------|----------|----------|
| `word_alignments` | `id` | `id` | direct | |
| `word_alignments` | `ref` | `ref` | direct | Bible verse ref |
| `word_alignments` | `zolai_word` | `zolai_word` | direct | |
| `word_alignments` | `english_word` | `english_word` | direct | |
| `word_alignments` | `position` | `position` | direct | Word position |
| `word_alignments` | `myanmar` | `myanmar` | direct | |
| `word_alignments` | `imported_at` | `created_at` | rename | |
| `word_alignments_import` | `zo_word` | — | DROP | Same as zolai_word |
| `word_alignments_import` | `en_word` | — | DROP | Same as english_word |
| `word_alignments_import` | `ref` | — | DROP | Same as ref |
| `word_alignments_import` | `confidence` | — | DROP | Metadata only |

**Join:** `word_alignments.ref = word_alignments_import.ref AND word_alignments.zolai_word = word_alignments_import.zo_word AND word_alignments.english_word = word_alignments_import.en_word`
**Matched:** 385,120 (canonical is subset of import's 224,696 unique triples)
**Import only:** ~242,000 unique triples not in canonical

---

## Single-Source Migrations (No Merge)

| Target | Source | Action | Rows |
|--------|--------|--------|------|
| `bible_verses` | `bible_verses` | Direct copy + dedup | 62,751 |
| `phrases` | `phrases` | Direct copy | 5,000 |
| `syllable_data` | `syllable_data` | Direct copy | 189,554 |
| `word_collocations` | `word_collocations` | Direct copy | 5,000 |
| `articles` | `articles` | Direct copy | 6,371 |
| `songs` | `zolai_songs` | Rename (drop prefix) | 1,032 |
| `wiki_content` | `wiki_content` + `wiki_lessons` | MERGE grammar/vocab columns | 1,688 |
| `import_log` | `jsonl_import_log` | Rename | 92 |
| `data_audit_log` | `data_audit_log` | Direct copy | 24,762 |
| `audit_findings` | `audit_findings` | Direct copy | 713 |
| `provenance` | `provenance` | Direct copy | 255 |
| `tone_sandhi` | `zolai_tone_sandhi` | Rename | 19 |
| `tone_patterns` | `tone_patterns` | Direct copy | 118 |
| `training_runs` | `training_runs` | Direct copy | 2 |
| `bible_analysis` | `zolai_bible_analysis` | Direct copy | 30,758 |
| `word_usage` | `word_usage` | Direct copy | 60,365 |
