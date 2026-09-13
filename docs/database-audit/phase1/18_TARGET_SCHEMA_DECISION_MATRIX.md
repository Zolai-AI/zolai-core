# 18 — Target Schema Decision Matrix

> **Date:** 2026-09-13 (Revised)
> **Status:** READ-ONLY audit
> **Purpose:** Every current table (79) — domain, role, target, action, confidence, risk, evidence

---

## Legend

| Action | Meaning |
|--------|---------|
| **KEEP** | Table stays as-is (possibly renamed) |
| **MERGE** | Data merged into another target table |
| **DERIVE** | New table created from source analysis |
| **ARCHIVE** | Table renamed to `archived_*` (not dropped) |
| **RETIRE** | Table dropped (empty/unused) |

---

## Canonical Tables

| # | Table | Domain | Rows | Role | Target | Action | Confidence | Risk | Evidence |
|---|-------|--------|------|------|--------|--------|------------|------|----------|
| 1 | `dictionary` | Vocabulary | 103,303 | ZO→EN master | `dictionary` | KEEP | High | Low | Business key: (zolai, english_clean). 1,876 dup pairs (polysemy). All match import. |
| 2 | `dictionary_en_zo` | Vocabulary | 113,750 | EN→ZO master | `dictionary_en_zo` | KEEP | High | Low | 42,818 dup headwords (repetition). 0 canonical-only. |
| 3 | `bible_verses` | Corpus | 62,751 | Parallel EN/ZO/MY | `bible_verses` | KEEP | High | Medium | 30,569 refs have 2-3 duplicates. Dedup needed. |
| 4 | `grammar_patterns` | Grammar | 5,547 | Grammar rules | `grammar_patterns` | MERGE (+instructions) | High | Low | pattern_id UNIQUE (0 dups). Merge 16 instructions. |
| 5 | `translations` | Corpus | 212,754 | EN↔ZO sentence pairs | `translations` | KEEP | High | Medium | 67× max dup. Dedup recommended. |
| 6 | `word_alignments` | Corpus | 385,120 | Word-level ZO↔EN | `word_alignments` | KEEP | High | Low | 2× max dup. Import superset (224,696 unique triples). |
| 7 | `vocab` | Vocabulary | 107,979 | Word frequency index | `vocab` | MERGE (+zolai_vocabulary) | High | Medium | COALESCE myanmar. ~5K dup headwords. ~114K target. |
| 8 | `proverbs` | Culture | 7,736 | Proverbs | `proverbs` | MERGE (+zolai_proverbs_idioms) | High | Low | 5,072 overlap. 2,664 proverbs-only (NULL enrichment). |
| 9 | `phrases` | Vocabulary | 5,000 | Multi-word expressions | `phrases` | KEEP | High | Low | Single source, 5,000 curated phrases. |
| 10 | `word_usage` | Corpus | 60,365 | Per-book word profiles | `word_usage` | KEEP | High | Low | (word,book) UNIQUE (0 dups). zolai_word_usage exported. |
| 11 | `syllable_data` | Phonology | 189,554 | Syllable segmentation | `syllable_data` | KEEP | High | Low | Single source, 189K rows. |
| 12 | `word_collocations` | Corpus | 5,000 | Word pair frequencies | `word_collocations` | KEEP | High | Low | Single source, 5K curated pairs. |
| 13 | `articles` | Reference | 6,371 | Reference articles | `articles` | KEEP | High | Low | Single source. |
| 14 | `wiki_lessons` | Reference | 1,688 | Wiki lessons | `wiki_content` | MERGE | High | Low | 1,656 overlap by title. Merge grammar_patterns/vocabulary_list. |
| 15 | `wiki_content` | Reference | 1,688 | Wiki pages | `wiki_content` | MERGE (+wiki_lessons) | High | Low | 32 NULL titles. |
| 16 | `simbu` | Culture | 4,163 | Simbu language data | JSONL archive | ARCHIVE | Medium | Low | Niche, export to JSONL. |
| 17 | `training_exercises` | Training | 81,805 | Generated exercises | JSONL archive | ARCHIVE | Medium | Low | Generated artifact, not source-of-truth. |
| 18 | `training_runs` | Training | 2 | Fine-tuning experiments | `training_runs` | KEEP | High | Low | 2 rows, experimental. |
| 19 | `tone_patterns` | Phonology | 118 | Word tone categories | `tone_patterns` | KEEP | High | Low | Small lookup table. |
| 20 | `corrections` | Audit | 0 | Correction proposals | JSONL archive | RETIRE | High | None | Empty table (0 rows). |
| 21 | `bible_context` | Corpus | 1,228 | Verse context analysis | JSONL archive | ARCHIVE | Medium | Low | Different schema from bible_analysis. |

---

## Enhanced Tables (`zolai_*`)

| # | Table | Domain | Rows | Role | Target | Action | Confidence | Risk | Evidence |
|---|-------|--------|------|------|--------|--------|------------|------|----------|
| 22 | `zolai_vocabulary` | Vocabulary | 112,279 | Master vocabulary (enriched) | `vocab` (merge) | MERGE | High | Medium | Merge into vocab by COALESCE. 6,036 unique entries added. |
| 23 | `zolai_grammar_patterns` | Grammar | 13,519 | Grammar patterns (enriched) | JSONL archive | ARCHIVE | Medium | High | ALL pattern_id=NULL. Cannot join to grammar_patterns. |
| 24 | `zolai_bible_analysis` | Corpus | 30,758 | Bible analysis (enriched) | `bible_analysis` | KEEP | High | Low | Direct rename. |
| 25 | `zolai_word_usage` | Corpus | 85,045 | Word usage (enriched) | JSONL archive | ARCHIVE | Medium | High | Different schema. Only 1,848 overlap with word_usage. |
| 26 | `zolai_proverbs_idioms` | Culture | 4,984 | Proverbs (enriched) | `proverbs` (merge) | MERGE | High | Low | 5,072 overlap. All matched. |
| 27 | `zolai_songs` | Culture | 1,032 | Zolai songs | `songs` | KEEP | High | Low | Rename (drop prefix). |
| 28 | `zolai_tone_sandhi` | Phonology | 19 | Tone sandhi rules | `tone_sandhi` | KEEP | High | Low | Rename (drop prefix). 19 rules. |
| 29 | `zolai_vocabulary_import` | Staging | 12,692 | Vocabulary staging | — | RETIRE | High | None | Staging table. |

---

## Import Tables (`*_import`)

| # | Table | Domain | Rows | Role | Target | Action | Confidence | Risk | Evidence |
|---|-------|--------|------|------|--------|--------|------------|------|----------|
| 30 | `dictionary_import` | Staging | 156,808 | Dictionary staging | — | RETIRE | High | None | Canonical consumed. 44,287 import-only (extras). |
| 31 | `dictionary_en_zo_import` | Staging | 135,276 | EN→ZO staging | — | RETIRE | High | None | 69,573 import-only. |
| 32 | `dictionary_en_my_import` | Staging | 0 | EN→MY staging | — | RETIRE | High | None | Empty. |
| 33 | `dictionary_my_import` | Staging | 7,840 | Myanmar staging | — | RETIRE | High | None | Myanmar translations. |
| 34 | `dictionary_trilingual_import` | Staging | 0 | Trilingual staging | — | RETIRE | High | None | Empty. |
| 35 | `bible_verses_import` | Staging | 62,204 | Bible staging | — | RETIRE | High | None | All refs in canonical. |
| 36 | `bible_book_analysis_import` | Staging | 65 | Book analysis staging | — | RETIRE | High | None | Small staging. |
| 37 | `bible_chapter_analysis_import` | Staging | 1,153 | Chapter analysis staging | — | RETIRE | High | None | Staging. |
| 38 | `grammar_patterns_import` | Staging | 6,983 | Grammar staging | — | RETIRE | High | None | Staging. |
| 39 | `translations_import` | Staging | 135,511 | Translations staging | — | RETIRE | High | None | Subset of canonical. |
| 40 | `word_alignments_import` | Staging | 627,000 | Alignments staging | — | RETIRE | High | None | Superset (224,696 unique). |
| 41 | `vocab_import` | Staging | 180,458 | Vocab staging | — | RETIRE | High | None | Staging. |
| 42 | `phrases_import` | Staging | 15,000 | Phrases staging | — | RETIRE | High | None | Staging. |
| 43 | `phrases_from_bible_import` | Staging | 14,000 | Bible phrases staging | — | RETIRE | High | None | Staging. |
| 44 | `phrase_context_import` | Staging | 45,597 | Phrase context staging | — | RETIRE | High | None | Staging. |
| 45 | `proverbs_import` | Staging | 7,736 | Proverbs staging | — | RETIRE | High | None | Same as canonical. |
| 46 | `word_collocations_import` | Staging | 5,000 | Collocations staging | — | RETIRE | High | None | Same as canonical. |
| 47 | `word_usage_profiles_import` | Staging | 7,384 | Word usage staging | — | RETIRE | High | None | Staging. |
| 48 | `training_exercises_import` | Staging | 81,805 | Training staging | — | RETIRE | High | None | Same as canonical. |
| 49 | `training_corpus_qwen3_import` | Staging | 9,386 | Corpus staging | — | RETIRE | High | None | Staging. |
| 50 | `training_seed_data_import` | Staging | 500 | Seed data staging | — | RETIRE | High | None | Small staging. |
| 51 | `training_valid_sentences_import` | Staging | 4,693 | Valid sentences staging | — | RETIRE | High | None | Staging. |
| 52 | `sentence_patterns_import` | Staging | 65 | Sentence patterns staging | — | RETIRE | High | None | Small staging. |
| 53 | `topic_clusters_import` | Staging | 12 | Topic clusters staging | — | RETIRE | High | None | Tiny staging. |
| 54 | `zvs_corrections_import` | Staging | 44 | ZVS corrections staging | — | RETIRE | High | None | Small staging. |
| 55 | `jsonl_import_log` | Audit | 92 | Import tracking | `import_log` | KEEP | High | Low | Rename (drop prefix). |

---

## Empty / Placeholder Tables

| # | Table | Domain | Rows | Role | Target | Action | Confidence | Risk | Evidence |
|---|-------|--------|------|------|--------|--------|------------|------|----------|
| 56 | `bible_verses_enhanced` | Corpus | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 57 | `dictionary_enhanced` | Vocabulary | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 58 | `proverbs_idioms` | Culture | 0 | Empty (duplicate of zolai_proverbs_idioms) | — | RETIRE | High | None | 0 rows; zolai_proverbs_idioms has 4,984. |
| 59 | `gemini_model_results` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 60 | `knowledge_vectors` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 61 | `morph_verified` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 62 | `ngram` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 63 | `particle_database` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 64 | `pos_gold` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 65 | `pos_verified` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 66 | `verb_database` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 67 | `vocabulary_enhanced` | Vocabulary | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 68 | `word_similarity` | Runtime | 0 | Empty | — | RETIRE | High | None | Never populated. |
| 69 | `training_validation` | Training | 0 | Empty | — | RETIRE | High | None | Never populated. |

---

## FTS Tables

| # | Table | Domain | Rows | Role | Target | Action | Confidence | Risk | Evidence |
|---|-------|--------|------|------|--------|--------|------------|------|----------|
| 70 | `wiki_content_fts` | Reference | — | Full-text search index | Auto-rebuild | RETIRE | High | None | Auto-created from triggers. |
| 71 | `wiki_content_fts_config` | Reference | — | FTS config | Auto-rebuild | RETIRE | High | None | Auto-created. |
| 72 | `wiki_content_fts_data` | Reference | — | FTS data | Auto-rebuild | RETIRE | High | None | Auto-created. |
| 73 | `wiki_content_fts_docsize` | Reference | — | FTS doc size | Auto-rebuild | RETIRE | High | None | Auto-created. |
| 74 | `wiki_content_fts_idx` | Reference | — | FTS index | Auto-rebuild | RETIRE | High | None | Auto-created. |

---

## Runtime / System Tables

| # | Table | Domain | Rows | Role | Target | Action | Confidence | Risk | Evidence |
|---|-------|--------|------|------|--------|--------|------------|------|----------|
| 75 | `data_audit_log` | Audit | 24,762 | Change tracking | `data_audit_log` | KEEP | High | Low | Unchanged. |
| 76 | `audit_findings` | Audit | 713 | Validation results | `audit_findings` | KEEP | High | Low | Unchanged. |
| 77 | `provenance` | Audit | 255 | Source file tracking | `provenance` | KEEP | High | Low | Unchanged. |
| 78 | `grammar_instructions` | Grammar | 16 | Training instructions | `grammar_patterns` (merge) | MERGE | High | Low | 16 rows → instruction_text column. |
| 79 | `sqlite_sequence` | System | — | SQLite auto-increment | — | RETIRE | High | None | System table, auto-managed. |

---

## Summary by Action

| Action | Count | Total Rows | % of Total |
|--------|-------|------------|------------|
| **KEEP** (direct or renamed) | 20 | ~1,010,000 | 31.9% |
| **MERGE** (into another table) | 5 | ~325,000 | 10.3% |
| **ARCHIVE** (export + rename) | 8 | ~195,000 | 6.2% |
| **RETIRE** (drop) | 46 | ~1,535,000 | 48.5% |
| **Auto-rebuild** (FTS) | 5 | — | — |
| **System** | 1 | — | — |
| **Total** | **79** | **~3,162,926** | **100%** |

### Target State: 23 Tables

| Category | Tables | Est. Rows |
|----------|--------|-----------|
| Canonical | 12 | ~1,508,000 |
| Derived/Analysis | 4 | ~40,000 |
| Staging | 1 | 92 |
| Audit | 3 | ~25,730 |
| Reference | 2 | 137 |
| Experimental | 1 | 2 |
| **Total** | **23** | **~1,574,000** |

### High-Risk Items

| Item | Risk | Mitigation |
|------|------|------------|
| `zolai_grammar_patterns` orphaned (13,519 rows) | High | Export to JSONL before archive |
| `zolai_word_usage` schema divergence (85,045 rows) | High | Export to JSONL, different merge strategy needed |
| `bible_verses` ref duplication (30,569 refs) | Medium | Dedup before UNIQUE constraint |
| `translations` extreme duplication (67× max) | Medium | Dedup before migration |
| `vocab` / `zolai_vocabulary` dup headwords | Medium | DISTINCT dedup during merge |
