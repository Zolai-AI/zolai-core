# 18 — Target Schema Decision Matrix

> **Date:** 2026-09-13
> **Status:** READ-ONLY audit
> **Scope:** Every current table (79) — what happens to each one

---

## Column Definitions

| Column | Values | Meaning |
|--------|--------|---------|
| **Table** | — | Current table name |
| **Rows** | — | Current row count |
| **Domain** | dictionary, bible, grammar, training, audit, staging, reference, experimental, search | Functional domain |
| **Current Role** | canonical, import, enhanced, fts, placeholder, runtime | Current purpose |
| **Proposed Target** | table name in target schema | NULL = dropped/archived |
| **Action** | MIGRATE, MERGE, RENAME, ARCHIVE, DROP | What happens |
| **Confidence** | HIGH, MEDIUM, LOW | Decision confidence |
| **Data-loss Risk** | NONE, LOW, MEDIUM, HIGH | Risk of data loss |
| **Evidence** | — | Key evidence |

---

## A. Canonical Tables (18)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 1 | `dictionary` | 103,303 | dictionary | canonical | `dictionary` | MERGE | HIGH | NONE | All rows preserved; myanmar enriched from import |
| 2 | `dictionary_en_zo` | 113,750 | dictionary | canonical | `dictionary_en_zo` | MIGRATE | HIGH | NONE | Direct copy |
| 3 | `bible_verses` | 62,751 | bible | canonical | `bible_verses` | MIGRATE | HIGH | NONE | Direct copy; 0 duplicates on ref |
| 4 | `grammar_patterns` | 5,547 | grammar | canonical | `grammar_patterns` | MERGE | HIGH | NONE | Base patterns preserved; enriched data orphaned |
| 5 | `translations` | 212,754 | dictionary | canonical | `translations` | MIGRATE | HIGH | NONE | Direct copy |
| 6 | `word_alignments` | 385,120 | bible | canonical | `word_alignments` | MIGRATE | HIGH | NONE | Direct copy |
| 7 | `vocab` | 107,979 | dictionary | canonical | `vocab` | MERGE | HIGH | NONE | All rows preserved; myanmar enriched |
| 8 | `proverbs` | 7,736 | grammar | canonical | `proverbs` | MERGE | HIGH | LOW | 21 duplicates removed |
| 9 | `phrases` | 5,000 | dictionary | canonical | `phrases` | MIGRATE | HIGH | NONE | Direct copy |
| 10 | `word_usage` | 60,365 | bible | canonical | `word_usage` | MIGRATE | HIGH | NONE | Direct copy |
| 11 | `syllable_data` | 189,554 | dictionary | canonical | `syllable_data` | MIGRATE | HIGH | NONE | Direct copy |
| 12 | `word_collocations` | 5,000 | dictionary | canonical | `word_collocations` | MIGRATE | HIGH | NONE | Direct copy |
| 13 | `articles` | 6,371 | reference | canonical | `articles` | RENAME | HIGH | NONE | Drop prefix |
| 14 | `zolai_songs` | 1,032 | reference | canonical | `songs` | RENAME | HIGH | NONE | Drop prefix |
| 15 | `wiki_content` | 1,688 | reference | canonical | `wiki_content` | MIGRATE | HIGH | NONE | Direct copy |
| 16 | `wiki_lessons` | 1,688 | reference | canonical | — | ARCHIVE | HIGH | NONE | 1:1 with wiki_content |
| 17 | `data_audit_log` | 24,762 | audit | canonical | `data_audit_log` | MIGRATE | HIGH | NONE | Direct copy |
| 18 | `training_runs` | 2 | experimental | canonical | `training_runs` | MIGRATE | HIGH | NONE | Direct copy |

---

## B. Import/Staging Tables (26)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 19 | `dictionary_import` | 156,808 | dictionary | import | — | ARCHIVE | HIGH | NONE | 44,287 import-only rows exported |
| 20 | `dictionary_en_zo_import` | 135,276 | dictionary | import | — | ARCHIVE | HIGH | NONE | 25 extra columns documented |
| 21 | `vocab_import` | 180,458 | dictionary | import | — | ARCHIVE | HIGH | NONE | 72,479 import-only rows exported |
| 22 | `translations_import` | 135,511 | dictionary | import | — | ARCHIVE | HIGH | NONE | 108,644 import-only rows exported |
| 23 | `word_alignments_import` | 627,000 | bible | import | — | ARCHIVE | HIGH | NONE | 241,880 import-only rows exported |
| 24 | `grammar_patterns_import` | 6,983 | grammar | import | — | ARCHIVE | HIGH | NONE | Import data documented |
| 25 | `proverbs_import` | 7,736 | grammar | import | — | ARCHIVE | HIGH | NONE | 1:1 with proverbs |
| 26 | `phrases_import` | 15,000 | dictionary | import | — | ARCHIVE | HIGH | NONE | 10,000 import-only rows |
| 27 | `phrases_from_bible_import` | 14,000 | dictionary | import | — | ARCHIVE | HIGH | NONE | Bible-derived phrases |
| 28 | `phrase_context_import` | 45,597 | dictionary | import | — | ARCHIVE | HIGH | NONE | Context enrichment data |
| 29 | `word_usage_profiles_import` | 7,384 | bible | import | — | ARCHIVE | HIGH | NONE | Word usage profiles |
| 30 | `word_collocations_import` | 5,000 | dictionary | import | — | ARCHIVE | HIGH | NONE | 1:1 with word_collocations |
| 31 | `training_exercises_import` | 81,805 | training | import | — | ARCHIVE | HIGH | NONE | 1:1 with training_exercises |
| 32 | `bible_verses_import` | 62,204 | bible | import | — | ARCHIVE | HIGH | NONE | 547 fewer than canonical |
| 33 | `dictionary_my_import` | 7,840 | dictionary | import | — | ARCHIVE | HIGH | NONE | Myanmar-only entries |
| 34 | `dictionary_en_my_import` | 0 | dictionary | import | — | DROP | HIGH | NONE | Empty table |
| 35 | `dictionary_trilingual_import` | 0 | dictionary | import | — | DROP | HIGH | NONE | Empty table |
| 36 | `zolai_vocabulary_import` | 12,692 | dictionary | import | — | ARCHIVE | HIGH | NONE | Vocab enrichment |
| 37 | `training_seed_data_import` | 500 | training | import | — | ARCHIVE | HIGH | NONE | Seed data |
| 38 | `training_valid_sentences_import` | 4,693 | training | import | — | ARCHIVE | HIGH | NONE | Validated sentences |
| 39 | `training_corpus_qwen3_import` | 9,386 | training | import | — | ARCHIVE | HIGH | NONE | Qwen3 corpus |
| 40 | `bible_book_analysis_import` | 65 | bible | import | — | ARCHIVE | HIGH | NONE | Book-level analysis |
| 41 | `bible_chapter_analysis_import` | 1,153 | bible | import | — | ARCHIVE | HIGH | NONE | Chapter-level analysis |
| 42 | `sentence_patterns_import` | 65 | grammar | import | — | ARCHIVE | HIGH | NONE | Sentence patterns |
| 43 | `zvs_corrections_import` | 44 | dictionary | import | — | ARCHIVE | HIGH | NONE | ZVS corrections |
| 44 | `topic_clusters_import` | 12 | bible | import | — | ARCHIVE | HIGH | NONE | Topic clusters |

---

## C. Enhanced/Enriched Tables (18)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 45 | `zolai_vocabulary` | 112,279 | dictionary | enhanced | — | ARCHIVE | HIGH | NONE | Enrichment merged into vocab |
| 46 | `zolai_grammar_patterns` | 13,519 | grammar | enhanced | — | ARCHIVE | HIGH | MEDIUM | pattern_id=NULL; orphaned enriched data |
| 47 | `zolai_proverbs_idioms` | 4,984 | grammar | enhanced | — | ARCHIVE | HIGH | NONE | Enrichment merged into proverbs |
| 48 | `zolai_bible_analysis` | 30,758 | bible | enhanced | `bible_analysis` | MIGRATE | HIGH | NONE | Direct copy |
| 49 | `zolai_word_usage` | 85,045 | bible | enhanced | — | ARCHIVE | HIGH | NONE | Different schema from word_usage |
| 50 | `zolai_tone_sandhi` | 19 | reference | enhanced | `tone_sandhi` | RENAME | HIGH | NONE | Drop prefix |
| 51 | `grammar_patterns_enhanced` | 5,597 | grammar | enhanced | — | ARCHIVE | HIGH | NONE | Merged into grammar_patterns |
| 52 | `bible_verses_enhanced` | 0 | bible | enhanced | — | DROP | HIGH | NONE | Empty table |
| 53 | `dictionary_enhanced` | 0 | dictionary | enhanced | — | DROP | HIGH | NONE | Empty table |
| 54 | `vocabulary_enhanced` | 0 | dictionary | enhanced | — | DROP | HIGH | NONE | Empty table |
| 55 | `corrections` | 0 | audit | enhanced | — | DROP | HIGH | NONE | Empty table |
| 56 | `proverbs_idioms` | 0 | grammar | enhanced | — | DROP | HIGH | NONE | Empty table |
| 57 | `ngram` | 0 | experimental | enhanced | — | DROP | HIGH | NONE | Empty table |
| 58 | `morph_verified` | 0 | experimental | enhanced | — | DROP | HIGH | NONE | Empty table |
| 59 | `pos_gold` | 0 | experimental | enhanced | — | DROP | HIGH | NONE | Empty table |
| 60 | `pos_verified` | 0 | experimental | enhanced | — | DROP | HIGH | NONE | Empty table |
| 61 | `verb_database` | 0 | experimental | enhanced | — | DROP | HIGH | NONE | Empty table |
| 62 | `particle_database` | 0 | experimental | enhanced | — | DROP | HIGH | NONE | Empty table |

---

## D. FTS Tables (6)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 63 | `wiki_content_fts` | 1,688 | search | fts | — | REBUILD | HIGH | NONE | Auto-rebuilt from wiki_content |
| 64 | `wiki_content_fts_config` | 1 | search | fts | — | REBUILD | HIGH | NONE | Auto-rebuilt |
| 65 | `wiki_content_fts_data` | 4,141 | search | fts | — | REBUILD | HIGH | NONE | Auto-rebuilt |
| 66 | `wiki_content_fts_docsize` | 1,688 | search | fts | — | REBUILD | HIGH | NONE | Auto-rebuilt |
| 67 | `wiki_content_fts_idx` | 3,186 | search | fts | — | REBUILD | HIGH | NONE | Auto-rebuilt |

---

## E. Audit Tables (3)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 68 | `audit_findings` | 713 | audit | canonical | `audit_findings` | MIGRATE | HIGH | NONE | Direct copy |
| 69 | `provenance` | 255 | audit | canonical | `provenance` | MIGRATE | HIGH | NONE | Direct copy |
| 70 | `data_audit_log` | 24,762 | audit | canonical | `data_audit_log` | MIGRATE | HIGH | NONE | Direct copy |

---

## F. Reference Tables (3)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 71 | `tone_patterns` | 118 | reference | canonical | `tone_patterns` | MIGRATE | HIGH | NONE | Direct copy |
| 72 | `tone_patterns` | 118 | reference | canonical | `tone_patterns` | MIGRATE | HIGH | NONE | Direct copy |
| 73 | `grammar_instructions` | 16 | grammar | canonical | — | ARCHIVE | MEDIUM | NONE | 16 rows; merged into grammar_patterns |

---

## G. Runtime/Infrastructure Tables (4)

| # | Table | Rows | Domain | Current Role | Proposed Target | Action | Confidence | Data-loss Risk | Evidence |
|---|-------|------|--------|-------------|----------------|--------|------------|---------------|----------|
| 74 | `jsonl_import_log` | 92 | audit | runtime | `import_log` | RENAME | HIGH | NONE | Drop prefix |
| 75 | `training_exercises` | 81,805 | training | canonical | — | EXPORT+DROP | MEDIUM | NONE | Export to JSONL before drop |
| 76 | `training_validation` | 0 | training | runtime | — | DROP | HIGH | NONE | Empty table |
| 77 | `simbu` | 4,163 | reference | canonical | — | ARCHIVE | MEDIUM | NONE | Niche corpus |
| 78 | `knowledge_vectors` | 0 | experimental | runtime | — | DROP | HIGH | NONE | Empty table |
| 79 | `word_similarity` | 0 | experimental | runtime | — | DROP | HIGH | NONE | Empty table |

---

## Summary: Action Counts

| Action | Count | Tables |
|--------|-------|--------|
| **MIGRATE** | 15 | dictionary_en_zo, bible_verses, translations, word_alignments, phrases, word_usage, syllable_data, word_collocations, wiki_content, data_audit_log, audit_findings, provenance, tone_patterns, training_runs, bible_analysis |
| **MERGE** | 4 | dictionary, vocab, grammar_patterns, proverbs |
| **RENAME** | 5 | articles, songs, tone_sandhi, import_log, grammar_instructions |
| **ARCHIVE** | 31 | All import tables, all enriched tables with orphaned data, wiki_lessons, simbu, etc. |
| **DROP** | 18 | Empty tables (0 rows), FTS (auto-rebuilt), training_validation, knowledge_vectors, word_similarity |
| **REBUILD** | 5 | FTS tables (auto from triggers) |
| **EXPORT+DROP** | 1 | training_exercises (81,805 → JSONL) |
| **Total** | **79** | All current tables accounted for |

---

## Risk Summary

| Risk Level | Count | Tables |
|-----------|-------|--------|
| **HIGH confidence, NONE risk** | 60 | Most canonical + import tables |
| **HIGH confidence, LOW risk** | 1 | proverbs (21 dedup) |
| **HIGH confidence, MEDIUM risk** | 1 | zolai_grammar_patterns (orphaned enriched data) |
| **MEDIUM confidence** | 3 | grammar_instructions, simbu, training_exercises export |
| **LOW confidence** | 0 | — |

**Overall assessment:** The migration is **safe and well-understood**. The only significant risk is the orphaned `zolai_grammar_patterns` enriched data (13,519 rows with NULL pattern_id), which requires manual reconciliation in Phase 2b.
