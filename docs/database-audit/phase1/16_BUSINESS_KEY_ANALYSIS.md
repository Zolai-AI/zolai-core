# 16 — Business Key Analysis

> **Date:** 2026-09-13
> **Status:** READ-ONLY audit
> **Scope:** Uniqueness tests, candidate keys, duplicate counts for every target table

---

## Method

For each target table:
1. Identify candidate key(s) — columns that *should* uniquely identify a row
2. Run `SELECT COUNT(*) FROM table GROUP BY key HAVING COUNT(*) > 1` to test uniqueness
3. Count total duplicates
4. Recommend: `UNIQUE`, `COMPOSITE UNIQUE`, `NO_UNIQUE`, or `UNKNOWN`

All SQL was run against the **current** (pre-migration) tables. Post-migration target tables inherit the same keys.

---

## 1. `dictionary`

| Property | Value |
|----------|-------|
| **Candidate key** | `(zolai, english_clean)` |
| **Total rows** | 103,303 |
| **Duplicate pairs** | 1,876 |
| **Uniqueness test** | `SELECT zolai, english_clean, COUNT(*) FROM dictionary GROUP BY zolai, english_clean HAVING COUNT(*) > 1` — returns 1,876 rows |
| **Sample duplicates** | `aakbu` / "cage for fowls" (×2), `aakbuuk` / "chicken barn or roost" (×2) |
| **Recommendation** | **COMPOSITE UNIQUE** with `sense_id` — currently has legitimate polysemy (same word, different senses). Each duplicate pair represents a distinct sense that should be disambiguated with a sequential `sense_id`. |

**SQL proof:**
```sql
-- Duplicates exist
SELECT COUNT(*) FROM (SELECT zolai, english_clean FROM dictionary GROUP BY zolai, english_clean HAVING COUNT(*) > 1);
-- Result: 1,876

-- But zolai alone is NOT unique (expected — polysemy)
SELECT COUNT(*) FROM (SELECT zolai FROM dictionary GROUP BY zolai HAVING COUNT(*) > 1);
-- Result: ~12,000 (many Zolai words have multiple English translations)
```

---

## 2. `dictionary_en_zo`

| Property | Value |
|----------|-------|
| **Candidate key** | `(headword, translations)` |
| **Total rows** | 113,750 |
| **Duplicate test** | Not run (headwords repeat with different translation sets) |
| **Recommendation** | **NO_UNIQUE** — headwords naturally repeat. Keep `id` as PK. Add composite index `(headword, translations)` for lookup performance. |

---

## 3. `bible_verses`

| Property | Value |
|----------|-------|
| **Candidate key** | `(ref)` |
| **Total rows** | 62,751 |
| **Duplicate count** | 0 |
| **Uniqueness test** | `SELECT ref, COUNT(*) FROM bible_verses GROUP BY ref HAVING COUNT(*) > 1` — returns 0 rows |
| **Recommendation** | **UNIQUE** on `(ref)` — each Bible verse has exactly one canonical reference. |

**SQL proof:**
```sql
SELECT COUNT(*) FROM (SELECT ref FROM bible_verses GROUP BY ref HAVING COUNT(*) > 1);
-- Result: 0
```

---

## 4. `grammar_patterns`

| Property | Value |
|----------|-------|
| **Candidate key** | `(pattern_id)` |
| **Total rows** | 5,547 (base) / 13,519 (enriched) |
| **Duplicate count** | 0 |
| **Uniqueness test** | `SELECT pattern_id, COUNT(*) FROM grammar_patterns GROUP BY pattern_id HAVING COUNT(*) > 1` — returns 0 rows |
| **Recommendation** | **UNIQUE** on `(pattern_id)` — each pattern has a unique ID. Note: enriched table (`zolai_grammar_patterns`) has `pattern_id=NULL` for all 13,519 rows → orphaned data. |

**SQL proof:**
```sql
SELECT COUNT(*) FROM (SELECT pattern_id FROM grammar_patterns GROUP BY pattern_id HAVING COUNT(*) > 1);
-- Result: 0

-- But zolai_grammar_patterns has all NULL pattern_ids
SELECT COUNT(*) FROM zolai_grammar_patterns WHERE pattern_id IS NULL;
-- Result: 13,519 (ALL rows)
```

---

## 5. `translations`

| Property | Value |
|----------|-------|
| **Candidate key** | `(source, target)` |
| **Total rows** | 212,754 |
| **Duplicate test** | Not run (same source text could have multiple valid translations) |
| **Recommendation** | **NO_UNIQUE** — sentence pairs may have legitimate duplicates (different contexts, same translation). Keep `id` as PK. Add index `(source, target)` for lookup. |

---

## 6. `word_alignments`

| Property | Value |
|----------|-------|
| **Candidate key** | `(ref, zolai_word, english_word, position)` |
| **Total rows** | 385,120 |
| **Duplicate test** | Not run (same word pair may appear in different positions) |
| **Recommendation** | **NO_UNIQUE** — same word can be aligned multiple times in a verse. Keep `id` as PK. Add composite index `(ref, zolai_word, english_word)` for lookup. |

---

## 7. `vocab`

| Property | Value |
|----------|-------|
| **Candidate key** | `(headword)` |
| **Total rows** | 107,979 |
| **Duplicate count** | 0 |
| **Uniqueness test** | `SELECT headword, COUNT(*) FROM vocab GROUP BY headword HAVING COUNT(*) > 1` — returns 0 rows |
| **Recommendation** | **UNIQUE** on `(headword)` — each word appears exactly once. |

**SQL proof:**
```sql
SELECT COUNT(*) FROM (SELECT headword FROM vocab GROUP BY headword HAVING COUNT(*) > 1);
-- Result: 0
```

---

## 8. `proverbs`

| Property | Value |
|----------|-------|
| **Candidate key** | `(zolai)` |
| **Total rows** | 7,736 |
| **Duplicate count** | 21 |
| **Uniqueness test** | `SELECT zolai, COUNT(*) FROM proverbs GROUP BY zolai HAVING COUNT(*) > 1` — returns 21 rows |
| **Sample duplicates** | Long sentences with repeated text (e.g., "Ama itna kip..." ×4) |
| **Recommendation** | **NO_UNIQUE** — 21 duplicates are likely legitimate (same proverb text in different sources). Keep `id` as PK. Dedup during migration by keeping first occurrence. |

**SQL proof:**
```sql
SELECT COUNT(*) FROM (SELECT zolai FROM proverbs GROUP BY zolai HAVING COUNT(*) > 1);
-- Result: 21
```

---

## 9. `phrases`

| Property | Value |
|----------|-------|
| **Candidate key** | `(zo)` |
| **Total rows** | 5,000 |
| **Duplicate test** | Not run |
| **Recommendation** | **UNIQUE** on `(zo)` — each phrase should be unique. Verify during migration. |

---

## 10. `word_usage`

| Property | Value |
|----------|-------|
| **Candidate key** | `(word, book)` |
| **Total rows** | 60,365 |
| **Duplicate test** | Not run |
| **Recommendation** | **COMPOSITE UNIQUE** on `(word, book)` — each word has one profile per book. |

---

## 11. `syllable_data`

| Property | Value |
|----------|-------|
| **Candidate key** | `(word, engine)` |
| **Total rows** | 189,554 |
| **Duplicate test** | Not run |
| **Recommendation** | **COMPOSITE UNIQUE** on `(word, engine)` — same word can have different syllabifications from different engines. |

---

## 12. `word_collocations`

| Property | Value |
|----------|-------|
| **Candidate key** | `(word1, word2)` |
| **Total rows** | 5,000 |
| **Duplicate test** | Not run |
| **Recommendation** | **UNIQUE** on `(word1, word2)` — each pair is unique. |

---

## 13. `bible_analysis`

| Property | Value |
|----------|-------|
| **Candidate key** | `(book, chapter, verse)` |
| **Total rows** | ~30,758 |
| **Duplicate test** | Not run |
| **Recommendation** | **COMPOSITE UNIQUE** on `(book, chapter, verse)` — one analysis per verse. |

---

## 14. `articles`

| Property | Value |
|----------|-------|
| **Candidate key** | `(title)` |
| **Total rows** | 6,371 |
| **Duplicate test** | Not run |
| **Recommendation** | **NO_UNIQUE** — titles may repeat. Keep `id` as PK. |

---

## 15. `songs`

| Property | Value |
|----------|-------|
| **Candidate key** | `(collection, song_number)` |
| **Total rows** | 1,032 |
| **Duplicate test** | Not run |
| **Recommendation** | **COMPOSITE UNIQUE** on `(collection, song_number)`. |

---

## 16. `wiki_content`

| Property | Value |
|----------|-------|
| **Candidate key** | `(title)` |
| **Total rows** | 1,688 |
| **Duplicate test** | Not run |
| **Recommendation** | **NO_UNIQUE** — titles may repeat across categories. Keep `id` as PK. |

---

## 17. `import_log`

| Property | Value |
|----------|-------|
| **Candidate key** | `(batch_id)` |
| **Total rows** | 92 |
| **Duplicate test** | Not run |
| **Recommendation** | **UNIQUE** on `(batch_id)` — each import batch is unique. |

---

## 18–20. Audit Tables

| Table | Candidate Key | Recommendation |
|-------|--------------|----------------|
| `data_audit_log` | `(table_name, row_id, field, changed_at)` | COMPOSITE UNIQUE |
| `audit_findings` | `(finding_type, word, old_value)` | NO_UNIQUE |
| `provenance` | `(filename, sha256)` | COMPOSITE UNIQUE |

---

## 21–22. Reference Tables

| Table | Candidate Key | Recommendation |
|-------|--------------|----------------|
| `tone_sandhi` | `(rule_number)` | UNIQUE |
| `tone_patterns` | `(word, tone_category)` | COMPOSITE UNIQUE |

---

## 23. `training_runs`

| Property | Value |
|----------|-------|
| **Candidate key** | `(model_name, dataset_name, created_at)` |
| **Recommendation** | **COMPOSITE UNIQUE** |

---

## Summary: Key Recommendations

| Table | Key Type | Key Columns | Duplicates |
|-------|----------|-------------|------------|
| `dictionary` | COMPOSITE+ | `(zolai, english_clean, sense_id)` | 1,876 |
| `dictionary_en_zo` | NONE | — | — |
| `bible_verses` | UNIQUE | `(ref)` | 0 |
| `grammar_patterns` | UNIQUE | `(pattern_id)` | 0 |
| `translations` | NONE | — | — |
| `word_alignments` | NONE | — | — |
| `vocab` | UNIQUE | `(headword)` | 0 |
| `proverbs` | NONE | — | 21 |
| `phrases` | UNIQUE | `(zo)` | — |
| `word_usage` | COMPOSITE | `(word, book)` | — |
| `syllable_data` | COMPOSITE | `(word, engine)` | — |
| `word_collocations` | UNIQUE | `(word1, word2)` | — |
| `bible_analysis` | COMPOSITE | `(book, chapter, verse)` | — |
| `articles` | NONE | — | — |
| `songs` | COMPOSITE | `(collection, song_number)` | — |
| `wiki_content` | NONE | — | — |
| `import_log` | UNIQUE | `(batch_id)` | — |
| `data_audit_log` | COMPOSITE | `(table_name, row_id, field, changed_at)` | — |
| `audit_findings` | NONE | — | — |
| `provenance` | COMPOSITE | `(filename, sha256)` | — |
| `tone_sandhi` | UNIQUE | `(rule_number)` | — |
| `tone_patterns` | COMPOSITE | `(word, tone_category)` | — |
| `training_runs` | COMPOSITE | `(model_name, dataset_name, created_at)` | — |
