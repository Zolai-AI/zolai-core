# 08 — Source of Truth

Validated/revised canonical table per domain with evidence.

---

## Canonical Tables (Runtime — App Reads These)

| Domain | Canonical Table | Rows | Evidence | Status |
|--------|----------------|------|----------|--------|
| **Dictionary (ZO→EN)** | `dictionary` | 103,303 | Curated subset of 157K import; rich schema with ZVS compliance | ✅ VALIDATED |
| **Dictionary (EN→ZO)** | `dictionary_en_zo` | 113,750 | Separate canonical; import has 135K entries | ✅ VALIDATED |
| **Bible Verses** | `bible_verses` | 62,751 | Parallel EN/ZO/MY; enriched beyond import (+547) | ✅ VALIDATED |
| **Grammar** | `zolai_grammar_patterns` | 13,519 | Superset of `grammar_patterns` (5,547); richer analysis | ✅ VALIDATED |
| **Vocabulary** | `zolai_vocabulary` | 112,279 | Superset of `vocab` (94,458); more entries + metadata | ✅ VALIDATED |
| **Translations** | `translations` | 212,754 | Enriched beyond import (+77K); 3 directions | ✅ VALIDATED |
| **Word Alignments** | `word_alignments` | 385,120 | Enriched beyond import; covers 28,903 verses | ✅ VALIDATED |
| **Proverbs** | `proverbs` | 7,736 | Master table; `zolai_proverbs_idioms` adds metadata for 4,984 | ✅ VALIDATED |
| **Phrases** | `phrases` | 5,000 | Curated from 15K import | ✅ VALIDATED |
| **Training** | `training_exercises` | 81,805 | 1:1 copy from import; 5 exercise types | ✅ VALIDATED |
| **Syllables** | `syllable_data` | 189,554 | Syllable segmentation engine output | ✅ VALIDATED |
| **Word Usage** | `word_usage` | 60,365 | Per-book word profiles | ✅ VALIDATED |
| **Word Collocations** | `word_collocations` | 5,000 | Word pair frequencies | ✅ VALIDATED |
| **Bible Context** | `bible_context` | 1,228 | Per-book/chapter/topic analysis | ✅ VALIDATED |
| **Articles** | `articles` | 6,371 | Reference articles (web-scraped) | ✅ VALIDATED |
| **Wiki Content** | `wiki_content` | 1,688 | Wiki-derived content | ✅ VALIDATED |
| **Wiki Lessons** | `wiki_lessons` | 1,688 | Wiki-driven lessons | ✅ VALIDATED |
| **Songs** | `zolai_songs` | 1,032 | Song catalogue | ✅ VALIDATED |
| **Tone Patterns** | `tone_patterns` | 118 | Tone analysis patterns | ✅ VALIDATED |
| **Tone Sandhi** | `zolai_tone_sandhi` | 19 | Tone sandhi rules (rule-based) | ✅ VALIDATED |

## Derived Tables (Enhanced — Built by Analysis Scripts)

| Domain | Derived Table | Rows | Source Tables | Status |
|--------|--------------|------|---------------|--------|
| **Bible Analysis** | `zolai_bible_analysis` | 30,758 | `bible_verses` + `word_alignments` | ✅ VALIDATED |
| **Word Usage** | `zolai_word_usage` | 85,045 | `word_usage` + `bible_verses` | ✅ VALIDATED |
| **Proverbs** | `zolai_proverbs_idioms` | 4,984 | `proverbs` (enrichment) | ✅ VALIDATED |

## Tables with Disputed Source of Truth

| Table | Rows | Issue | Recommendation |
|-------|------|-------|----------------|
| `grammar_patterns` | 5,547 | Subset of `zolai_grammar_patterns` | Deprecate in favor of `zolai_grammar_patterns` |
| `vocab` | 94,458 | Subset of `zolai_vocabulary` | Deprecate in favor of `zolai_vocabulary` |
| `dictionary_import` | 156,808 | Superset of `dictionary` (44K filtered) | Keep as staging only |
| `translations_import` | 135,511 | Subset of `translations` (+77K enriched) | Keep as staging only |
| `word_alignments_import` | 627,000 | Superset of `word_alignments` (241K filtered) | Keep as staging only |
| `vocab_import` | 180,458 | Superset of `vocab` (+86K filtered) | Keep as staging only |

## Infrastructure Tables

| Table | Rows | Purpose | Status |
|-------|------|---------|--------|
| `data_audit_log` | 24,762 | Change tracking | ✅ VALIDATED |
| `provenance` | 255 | Source file tracking | ✅ VALIDATED |
| `jsonl_import_log` | 92 | Import pipeline log | ✅ VALIDATED |
| `audit_findings` | 713 | Audit findings | ✅ VALIDATED |
| `training_runs` | 2 | Training run metadata | ✅ VALIDATED |
