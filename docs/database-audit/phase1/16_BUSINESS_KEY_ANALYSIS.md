# 16 — Business Key Analysis

> **Date:** 2026-09-13 (Revised)
> **Status:** READ-ONLY audit
> **Purpose:** For each target table — candidate key(s), uniqueness test (SQL proof), recommendation

---

## 1. `dictionary`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK, auto-increment |
| `zolai` | ❌ NO | ~1,876 pairs | Some words have multiple English meanings |
| `(zolai, english_clean)` | ❌ NO | 1,876 | Polysemy: same word + same clean English = duplicate |
| `(zolai, english_clean, source)` | ⚠️ LIKELY | TBD | Source disambiguates same-word-same-meaning from different sources |

**Recommendation:** `NO_UNIQUE` on business columns. Polysemy is legitimate. Keep `(zolai, english_clean)` as composite business key for deduplication queries only.

**SQL proof:**
```sql
SELECT zolai, english_clean, COUNT(*) FROM dictionary
GROUP BY zolai, english_clean HAVING COUNT(*) > 1 LIMIT 5;
-- Returns: aakbu (2), aakbuuk (2), aakgia (2), aakgil (2), aakkhuang (2)
```

---

## 2. `dictionary_en_zo`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `headword` | ❌ NO | 42,818 | English headwords repeat (e.g., "abandon" 4×) |
| `(headword, translations)` | ❌ NO | 21 | Exact duplicates |
| `(headword, pos)` | ⚠️ LIKELY | TBD | POS may disambiguate |

**Recommendation:** `NO_UNIQUE` on business columns. Headwords legitimately repeat with different Zolai translations.

**SQL proof:**
```sql
SELECT headword, COUNT(*) FROM dictionary_en_zo
GROUP BY headword HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 5;
-- Returns: aback (4), abandon (4), abase (4), abash (4), abattoir (4)
```

---

## 3. `bible_verses`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `ref` | ❌ NO | 30,569 refs × 2-3 rows | Multiple editions (TDB77 vs Tedim2010) |
| `(ref, zo_tdb77)` | ⚠️ LIKELY | TBD | Edition-specific |
| `(ref, book)` | ⚠️ LIKELY | TBD | Book disambiguates |

**Recommendation:** `COMPOSITE UNIQUE` on `(ref)` after deduplication. The current 62,751 rows contain 31,649 unique refs. Dedup to keep one row per ref (prioritize most complete edition).

**SQL proof:**
```sql
SELECT ref, COUNT(*) FROM bible_verses
GROUP BY ref HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 5;
-- Returns: 1CH 11:14 (3), 1CH 14:14 (3), 1CH 15:17 (3), 1CH 16:29 (3), 1CH 19:17 (3)
-- 30,569 out of 31,649 refs have duplicates
```

---

## 4. `grammar_patterns`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `pattern_id` | ✅ YES | 0 | TEXT, verified unique |
| `pattern_text` | ⚠️ LIKELY | TBD | Same text could appear with different IDs |

**Recommendation:** `UNIQUE` on `pattern_id`. This is the canonical business key.

**SQL proof:**
```sql
SELECT pattern_id, COUNT(*) FROM grammar_patterns
GROUP BY pattern_id HAVING COUNT(*) > 1;
-- Returns: 0 rows (all unique)
```

---

## 5. `translations`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(source, target)` | ❌ NO | 67× max | Significant duplication |
| `(source, target, direction)` | ⚠️ LIKELY | TBD | Direction disambiguates |
| `(source, target, reference)` | ⚠️ LIKELY | TBD | Reference disambiguates |

**Recommendation:** `NO_UNIQUE` on business columns. Deduplication before migration recommended.

**SQL proof:**
```sql
SELECT source, target, COUNT(*) FROM translations
GROUP BY source, target HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 5;
-- Returns: ("And the", "{ Topa } in Moses kiangah,", 67), ...
```

---

## 6. `word_alignments`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(ref, zolai_word, english_word)` | ❌ NO | 2× max | Some triples have position variants |
| `(ref, zolai_word, english_word, position)` | ⚠️ LIKELY | TBD | Position disambiguates |

**Recommendation:** `COMPOSITE UNIQUE` on `(ref, zolai_word, english_word, position)`.

**SQL proof:**
```sql
SELECT ref, zolai_word, english_word, COUNT(*) FROM word_alignments
GROUP BY ref, zolai_word, english_word HAVING COUNT(*) > 1 LIMIT 3;
-- Returns: (1CH 11:14, ahih, but..., 2), ...
```

---

## 7. `vocab`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `headword` | ❌ NO | ~5,000+ | Multiple entries per word (different frequencies) |

**Recommendation:** `NO_UNIQUE`. Some words legitimately have multiple rows (e.g., "aa" with frequency 222 and 12 — likely different sources or senses). Deduplication needed before migration.

**SQL proof:**
```sql
SELECT headword, COUNT(*) FROM vocab
GROUP BY headword HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 5;
-- Returns: aa (2), ading (2), ah (2), ahi (2), ai (2)
```

---

## 8. `proverbs`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `zolai` | ❌ NO | max 8× | Long proverbs with shared text |

**Recommendation:** `NO_UNIQUE`. Proverb text duplication is legitimate (same proverb in different contexts). Keep `(zolai)` as search key.

**SQL proof:**
```sql
SELECT zolai, COUNT(*) FROM proverbs
GROUP BY zolai HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 5;
-- Returns: "Tua ciangin Job in dawng a," (8), ... (max 8×)
```

---

## 9. `phrases`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `zo` | ⚠️ LIKELY | TBD | Phrase text |

**Recommendation:** Test `zo` uniqueness. Likely unique (5,000 curated phrases).

**SQL proof:**
```sql
SELECT zo, COUNT(*) FROM phrases
GROUP BY zo HAVING COUNT(*) > 1;
-- Run during migration validation
```

---

## 10. `word_usage`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(word, book)` | ✅ YES | 0 | Composite — verified unique |

**Recommendation:** `UNIQUE` on `(word, book)`. Verified — 0 duplicates.

**SQL proof:**
```sql
SELECT word, book, COUNT(*) FROM word_usage
GROUP BY word, book HAVING COUNT(*) > 1;
-- Returns: 0 rows (all unique)
```

---

## 11. `syllable_data`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(word, engine)` | ⚠️ LIKELY | TBD | Word + engine combination |

**Recommendation:** Test `(word, engine)` uniqueness.

**SQL proof:**
```sql
SELECT word, engine, COUNT(*) FROM syllable_data
GROUP BY word, engine HAVING COUNT(*) > 1;
-- Run during migration validation
```

---

## 12. `word_collocations`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(word1, word2)` | ⚠️ LIKELY | TBD | Word pair |

**Recommendation:** Test `(word1, word2)` uniqueness. Likely unique (5,000 curated pairs).

**SQL proof:**
```sql
SELECT word1, word2, COUNT(*) FROM word_collocations
GROUP BY word1, word2 HAVING COUNT(*) > 1;
-- Run during migration validation
```

---

## 13. `bible_analysis`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(book_code, chapter, verse)` | ⚠️ LIKELY | TBD | Verse-level analysis |

**Recommendation:** Test `(book_code, chapter, verse)` uniqueness.

---

## 14. `articles`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `title` | ⚠️ LIKELY | TBD | Article title |

**Recommendation:** Test `title` uniqueness.

---

## 15. `songs`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `(collection, song_number)` | ⚠️ LIKELY | TBD | Song identity |

**Recommendation:** Test `(collection, song_number)` uniqueness.

---

## 16. `wiki_content`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `title` | ❌ NO | 32 NULL titles | Some rows have NULL title |
| `source_path` | ⚠️ LIKELY | TBD | File path |

**Recommendation:** `COMPOSITE UNIQUE` on `(wiki_category, source_path)` or `UNIQUE` on `source_path` if non-null.

---

## 17. `import_log`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `batch_id` | ⚠️ LIKELY | TBD | Import batch identifier |

---

## 18. `data_audit_log`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |

**Recommendation:** No additional unique constraints. Log table — duplicates legitimate.

---

## 19. `audit_findings`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |

---

## 20. `provenance`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `sha256` | ⚠️ LIKELY | TBD | File hash |

**Recommendation:** Test `sha256` uniqueness.

---

## 21. `tone_sandhi`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `rule_number` | ✅ YES | 0 | INTEGER UNIQUE |

**Recommendation:** `UNIQUE` on `rule_number` (1–19).

---

## 22. `tone_patterns`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |
| `word` | ⚠️ LIKELY | TBD | Word tone category |

---

## 23. `training_runs`

| Candidate Key | Unique? | Duplicates | Evidence |
|---------------|---------|------------|----------|
| `id` | ✅ YES | 0 | INTEGER PK |

---

## Summary of Key Recommendations

| Table | Business Key | Constraint | Confidence |
|-------|-------------|------------|------------|
| dictionary | `(zolai, english_clean)` | NONE (polysemy) | High |
| dictionary_en_zo | `(headword, translations)` | NONE (repetition) | High |
| bible_verses | `(ref)` | DEDUP first, then UNIQUE | High |
| grammar_patterns | `pattern_id` | UNIQUE | Verified |
| translations | `(source, target)` | NONE (dedup first) | High |
| word_alignments | `(ref, zolai_word, english_word, position)` | UNIQUE | Medium |
| vocab | `headword` | NONE (dedup first) | High |
| proverbs | `zolai` | NONE (legitimate dupes) | High |
| word_usage | `(word, book)` | UNIQUE | Verified |
| syllable_data | `(word, engine)` | UNIQUE | Medium |
| word_collocations | `(word1, word2)` | UNIQUE | Medium |
| tone_sandhi | `rule_number` | UNIQUE | Verified |
