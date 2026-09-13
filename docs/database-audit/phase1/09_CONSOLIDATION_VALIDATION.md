# 09 — Consolidation Validation

Validates Phase 0 consolidation matrix with confidence levels.

---

## Phase 0 Assumptions vs Phase 1 Evidence

### Assumption 1: "72 tables in database"
**Phase 1 Finding:** 71 tables (not 72). The discrepancy may be due to counting `sqlite_sequence` or FTS auxiliary tables differently.
**Confidence:** ✅ CONFIRMED (minor discrepancy)

### Assumption 2: "~3.1M total rows"
**Phase 1 Finding:** Sum of all table rows = ~3.1M (confirmed by ranking data).
**Confidence:** ✅ CONFIRMED

### Assumption 3: "dictionary has 103,303 entries"
**Phase 1 Finding:** Exact match — `dictionary` has 103,303 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 4: "dictionary_en_zo has 113,750 entries"
**Phase 1 Finding:** Exact match — `dictionary_en_zo` has 113,750 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 5: "bible_verses has 62,751 entries"
**Phase 1 Finding:** 62,751 rows (includes all versions: TDB77, Tedim2010, etc.).
**Confidence:** ✅ CONFIRMED

### Assumption 6: "grammar_patterns has 5,482 entries"
**Phase 1 Finding:** 5,547 rows (not 5,482). Difference of 65 entries.
**Confidence:** ⚠️ PARTIALLY CONFIRMED (off by 65)

### Assumption 7: "phrases has 5,000 entries"
**Phase 1 Finding:** Exact match — `phrases` has 5,000 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 8: "vocab has 20,929 entries"
**Phase 1 Finding:** 94,458 rows (not 20,929). The 20,929 figure may refer to a filtered subset or older count.
**Confidence:** ❌ INCORRECT (off by 4.5x)

### Assumption 9: "word_usage has 7,384 entries"
**Phase 1 Finding:** 60,365 rows (not 7,384). The 7,384 figure matches `word_usage_profiles_import`.
**Confidence:** ❌ INCORRECT (confused import with canonical)

### Assumption 10: "training_exercises has 81,805 entries"
**Phase 1 Finding:** Exact match — 81,805 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 11: "word_alignments has 385,120 entries"
**Phase 1 Finding:** Exact match — 385,120 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 12: "syllable_data has 189,554 entries"
**Phase 1 Finding:** 189,554 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 13: "proverbs has 7,736 entries"
**Phase 1 Finding:** Exact match — 7,736 rows.
**Confidence:** ✅ CONFIRMED

### Assumption 14: "data_audit_log has 24,762 entries"
**Phase 1 Finding:** 24,762 rows.
**Confidence:** ✅ CONFIRMED

---

## Consolidation Matrix (Revised)

| Domain | Phase 0 Tables | Phase 1 Recommendation | Confidence |
|--------|---------------|----------------------|------------|
| Dictionary | `dictionary`, `dictionary_import` | Keep `dictionary` as canonical | HIGH |
| Dictionary (EN→ZO) | `dictionary_en_zo`, `dictionary_en_zo_import` | Keep `dictionary_en_zo` as canonical | HIGH |
| Bible | `bible_verses`, `bible_verses_import` | Keep `bible_verses` as canonical | HIGH |
| Vocabulary | `vocab`, `vocab_import`, `zolai_vocabulary` | Merge into `zolai_vocabulary` | HIGH |
| Grammar | `grammar_patterns`, `grammar_patterns_import`, `grammar_patterns_enhanced`, `zolai_grammar_patterns` | Merge into `zolai_grammar_patterns` | HIGH |
| Translations | `translations`, `translations_import` | Keep `translations` as canonical | HIGH |
| Word Alignments | `word_alignments`, `word_alignments_import` | Keep `word_alignments` as canonical | HIGH |
| Proverbs | `proverbs`, `proverbs_import`, `zolai_proverbs_idioms` | Merge `zolai_proverbs_idioms` into `proverbs` | HIGH |
| Phrases | `phrases`, `phrases_import`, `phrases_from_bible_import` | Keep `phrases` as canonical | HIGH |
| Training | `training_exercises`, `training_exercises_import` | Keep `training_exercises` as canonical | HIGH |
| Syllables | `syllable_data` | Keep as-is | HIGH |
| Word Usage | `word_usage`, `word_usage_profiles_import` | Keep `word_usage` as canonical | HIGH |
| Word Collocations | `word_collocations`, `word_collocations_import` | Keep `word_collocations` as canonical | HIGH |

---

## Corrected Assumptions

| Original Assumption | Corrected Value | Impact |
|--------------------|----------------|--------|
| `grammar_patterns` = 5,482 | 5,547 | Minor |
| `vocab` = 20,929 | 94,458 | Major — vocab is 4.5x larger than assumed |
| `word_usage` = 7,384 | 60,365 | Major — word_usage is 8x larger than assumed |
