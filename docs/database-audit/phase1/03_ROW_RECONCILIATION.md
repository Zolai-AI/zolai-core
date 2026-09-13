# 03 — Row Reconciliation

Import → canonical row deltas for all table families where both staging and canonical exist.

---

## Dictionary (ZO→EN)

| Metric | Value |
|--------|-------|
| `dictionary` (canonical) | 103,303 |
| `dictionary_import` (staging) | 156,808 |
| Delta | **-53,505** (import is larger) |
| Distinct Zolai keys in import | 89,019 |
| Distinct Zolai keys in canonical | 84,490 |
| Overlap (by `zolai` key) | 135,543 |
| Dictionary-only entries | 0 |
| Import-only entries | 44,287 |

**Interpretation:** The canonical `dictionary` table is a curated subset of `dictionary_import`. The import contains 44,287 entries that were filtered out during canonical build — likely duplicate, low-quality, or non-ZVS-compliant entries. The canonical table has 0 entries absent from import, confirming import is the superset.

**Import sources:** database_master (144,894), dalsuum (7,841), Bible-Verse-Study (2,683), parallel_en_zo (468), wiki_grammar_v3 (164), verse_breakdown_v1 (146), verse_complete_v1 (147), and others.

---

## Translations

| Metric | Value |
|--------|-------|
| `translations` (canonical) | 212,754 |
| `translations_import` (staging) | 135,511 |
| Delta | **+77,243** (canonical is larger) |
| Canonical directions | en_to_zo (29,255), zo_to_en (29,439), en→my (154,060) |

**Interpretation:** The canonical `translations` table has 77,243 MORE rows than the import staging. This means translations were enriched by scripts that generated pairs directly from Bible analysis (not through the JSONL pipeline). The import staging was only a partial source.

**Import sources:** TDB_KJV (60,389), TB77_KJV (28,764), T2010_KJV (26,530), TBR17_KJV (19,814).

---

## Word Alignments

| Metric | Value |
|--------|-------|
| `word_alignments` (canonical) | 385,120 |
| `word_alignments_import` (staging) | 627,000 |
| Delta | **-241,880** (import is larger) |
| Canonical distinct refs | 28,903 |
| Import distinct refs | 15,665 |
| Canonical rows not in import | 160,106 |

**Interpretation:** The canonical table has 241,880 fewer rows than the import, meaning a heavy filter was applied. However, the canonical table covers 13,238 MORE distinct refs (28,903 vs 15,665), indicating that alignments were also generated independently from Bible analysis. The import's 627K rows come from a single source: `dict_match`.

---

## Vocab

| Metric | Value |
|--------|-------|
| `vocab` (canonical) | 94,458 |
| `vocab_import` (staging) | 180,458 |
| Delta | **-86,000** (import is larger) |

**Interpretation:** Vocab import contains nearly double the canonical. Filtering likely removed entries without frequency data or with incomplete metadata.

---

## Grammar Patterns

| Metric | Value |
|--------|-------|
| `grammar_patterns` (canonical) | 5,547 |
| `grammar_patterns_import` (staging) | 6,983 |
| Delta | **-1,436** (import is larger) |
| `grammar_patterns_enhanced` | 5,597 |

**Interpretation:** Modest filtering from import to canonical. The `enhanced` table adds ~50 patterns beyond the canonical.

---

## Proverbs

| Metric | Value |
|--------|-------|
| `proverbs` (canonical) | 7,736 |
| `proverbs_import` (staging) | 7,736 |
| Delta | **0** (1:1 match) |

**Interpretation:** Proverbs import was copied directly to canonical with no filtering.

---

## Bible Verses

| Metric | Value |
|--------|-------|
| `bible_verses` (canonical) | 62,751 |
| `bible_verses_import` (staging) | 62,204 |
| Delta | **+547** (canonical is slightly larger) |

**Interpretation:** Nearly 1:1; the 547 extra canonical rows were likely added by a separate enrichment pass.

---

## Summary

| Domain | Import → Canonical | Filter Applied? | Enriched Beyond Import? |
|--------|-------------------|-----------------|------------------------|
| Dictionary | 156K → 103K | YES (-34%) | No |
| Translations | 135K → 212K | No | YES (+57%) |
| Word Alignments | 627K → 385K | YES (-39%) | YES (+28K unique refs) |
| Vocab | 180K → 94K | YES (-48%) | No |
| Grammar | 7K → 5.5K | YES (-21%) | No |
| Proverbs | 7.7K → 7.7K | No | No |
| Bible Verses | 62.2K → 62.7K | No | Yes (+547) |
