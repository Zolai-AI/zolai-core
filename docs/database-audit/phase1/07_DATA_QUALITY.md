# 07 — Data Quality

NULL rates, empty fields, encoding issues.

---

## NULL Rate Summary

### Tables with High NULL Rates (>50% in any column)

| Table | Column | NULL Count | NULL Rate | Severity |
|-------|--------|-----------|-----------|----------|
| `dictionary_import` | `dialect` | ~150K | 95% | LOW (optional) |
| `dictionary_import` | `myanmar_word` | ~155K | 98% | LOW (optional) |
| `dictionary_import` | `headword` | ~155K | 98% | LOW (optional) |
| `dictionary_import` | `example` | ~156K | 99% | LOW (optional) |
| `translations` | `myanmar` | ~180K | 85% | LOW (optional) |
| `word_alignments` | `myanmar` | ~380K | 99% | LOW (optional) |
| `zolai_vocabulary` | `myanmar` | ~110K | 98% | LOW (optional) |
| `articles` | `date` | 4,163 | 65% | MEDIUM |
| `articles` | `link` | 4,163 | 65% | MEDIUM |
| `articles` | `categories` | 6 | 0.1% | LOW |
| `bible_context` | various | varies | varies | LOW |

### Tables with Low NULL Rates (<10%)

| Table | Status |
|-------|--------|
| `dictionary` | ✅ Clean — all required fields populated |
| `bible_verses` | ✅ Clean — all required fields populated |
| `translations` | ✅ Clean (except optional `myanmar`) |
| `vocab` | ✅ Clean — all required fields populated |
| `grammar_patterns` | ✅ Clean — all required fields populated |
| `training_exercises` | ✅ Clean — all required fields populated |

---

## Empty String Analysis

| Table | Column | Empty Count | Rate |
|-------|--------|-------------|------|
| `articles` | `date` | 4,163 | 65% |
| `articles` | `link` | 4,163 | 65% |
| `dictionary_import` | `update_remarks` | ~150K | 95% |
| `dictionary_import` | `update_description` | ~155K | 98% |

---

## Encoding Issues

### Potential Problems

1. **HTML entities in `articles.content`:**
   - Sample shows `&#8230;` (ellipsis), `&#8220;`/`&#8221;` (quotes)
   - Action: HTML-decode during canonical build

2. **Mixed scripts in dictionary:**
   - Zolai fields: ✅ Clean `[a-z\-]+` only (after 2026-09-13 cleaning)
   - English fields: ✅ Clean
   - Myanmar fields: Present in optional `myanmar` column only

3. **Unicode in Bible verses:**
   - Zolai: ✅ Clean
   - English: ✅ Clean
   - Myanmar: ✅ Clean (when present)

---

## Data Freshness

| Table | Last Import | Staleness |
|-------|------------|-----------|
| `dictionary` | 2026-09-13 | Fresh |
| `bible_verses` | 2026-09-13 | Fresh |
| `translations` | 2026-09-13 | Fresh |
| `vocab` | 2026-09-13 | Fresh |
| `training_exercises` | 2026-09-13 | Fresh |
| `articles` | Unknown | May be stale |

---

## Primary Key Analysis

| Table | PK | Unique? | Auto-increment? |
|-------|-----|---------|----------------|
| `dictionary` | `id` | ✅ | ✅ |
| `dictionary_en_zo` | `id` | ✅ | ✅ |
| `bible_verses` | `id` | ✅ | ✅ |
| `translations` | `id` | ✅ | ✅ |
| `vocab` | `id` | ✅ | ✅ |
| `word_alignments` | `id` | ✅ | ✅ |
| `grammar_patterns` | `id` | ✅ | ✅ |
| `proverbs` | `id` | ✅ | ✅ |
| `training_exercises` | `id` | ✅ | ✅ |
| `syllable_data` | `id` | ✅ | ✅ |

**All canonical tables have proper auto-increment primary keys.**

---

## Recommended Data Quality Improvements

1. **Add NOT NULL constraints** on critical fields:
   - `dictionary.zolai`, `dictionary.english`
   - `bible_verses.zolai`, `bible_verses.english`
   - `translations.source`, `translations.target`

2. **Add CHECK constraints** for value ranges:
   - `training_exercises.exercise_type IN ('negation', 'question', 'pronoun', 'error', 'conditional')`
   - `translations.direction IN ('en_to_zo', 'zo_to_en')`

3. **Add UNIQUE constraints** on business keys:
   - `dictionary.zolai + dictionary.english` (with source)
   - `bible_verses.book + chapter + verse + version`

4. **Enable foreign keys** where referential integrity matters
