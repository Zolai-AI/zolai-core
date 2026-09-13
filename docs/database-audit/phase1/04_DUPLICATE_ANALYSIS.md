# 04 — Duplicate Analysis

Overlap between table families with exact counts.

---

## Vocabulary Family

| Comparison | Count |
|-----------|-------|
| `vocab` total rows | 94,458 |
| `zolai_vocabulary` total rows | 112,279 |
| Overlap (JOIN on `headword = zolai`) | 103,846 |
| `vocab` only | 17,565 |
| `zolai_vocabulary` only | 8,433 |

**Note:** The overlap count (103,846) exceeds `vocab`'s total (94,458), which means there are duplicate `headword` values in `vocab`. The actual unique overlap is closer to ~86,000.

**Recommendation:** `zolai_vocabulary` is the richer, more complete table. Deprecate `vocab`.

---

## Grammar Family

| Comparison | Count |
|-----------|-------|
| `grammar_patterns` total rows | 5,547 |
| `grammar_patterns_enhanced` total rows | 5,597 |
| `zolai_grammar_patterns` total rows | 13,519 |
| Overlap (by `pattern_id`) | 5,597 |
| `grammar_patterns` only | 0 |
| `zolai_grammar_patterns` only | 7,922 |
| Overlap (by `pattern` = `pattern_text`) | 0 |

**Key finding:** `grammar_patterns` is a strict subset of `zolai_grammar_patterns` when joined by `pattern_id`. The `pattern` column in `grammar_patterns` does NOT match `pattern_text` in `zolai_grammar_patterns` — they are different text representations of the same patterns. The `pattern_id` is the correct join key.

**Recommendation:** Deprecate `grammar_patterns` and `grammar_patterns_enhanced` in favor of `zolai_grammar_patterns`.

---

## Proverbs Family

| Comparison | Count |
|-----------|-------|
| `proverbs` total rows | 7,736 |
| `zolai_proverbs_idioms` total rows | 4,984 |
| `proverbs_idioms` total rows | 0 |
| Overlap (by `zolai`) | 4,984 |
| `proverbs` only | 2,752 |
| `zolai_proverbs_idioms` only | 0 |

**Key finding:** `zolai_proverbs_idioms` is a strict subset of `proverbs`. The 4,984 entries in `zolai_proverbs_idioms` all exist in `proverbs`, and `zolai_proverbs_idioms` adds richer metadata (morpheme_breakdown, cultural_context, theme) for those entries.

**Recommendation:** Merge `zolai_proverbs_idioms` columns into `proverbs` and drop the separate table.

---

## Dictionary Family (EN→ZO)

| Comparison | Count |
|-----------|-------|
| `dictionary_en_zo` total rows | 113,750 |
| `dictionary_en_zo_import` total rows | 135,276 |
| Delta | -21,526 (import is larger) |

**Recommendation:** Same pattern as ZO→EN — import is the superset, canonical is curated.

---

## Bible Verses Family

| Comparison | Count |
|-----------|-------|
| `bible_verses` total rows | 62,751 |
| `bible_verses_import` total rows | 62,204 |
| `bible_verses_enhanced` total rows | 0 |
| Delta | +547 (canonical is slightly larger) |

---

## Summary of Duplicates

| Family | Tables | Recommendation |
|--------|--------|----------------|
| Vocabulary | `vocab` + `zolai_vocabulary` | Merge into `zolai_vocabulary` |
| Grammar | `grammar_patterns` + `grammar_patterns_enhanced` + `zolai_grammar_patterns` | Merge into `zolai_grammar_patterns` |
| Proverbs | `proverbs` + `zolai_proverbs_idioms` | Merge into `proverbs` with enriched columns |
| Dictionary | `dictionary` + `dictionary_import` | Keep `dictionary` as canonical |
| Translations | `translations` + `translations_import` | Keep `translations` as canonical |
| Word Alignments | `word_alignments` + `word_alignments_import` | Keep `word_alignments` as canonical |
