# Phase 0 Database Audit — Table Classification

**Generated:** 2026-09-13 20:31

## Classification Types

| Type | Count | Description |
|------|-------|-------------|
| CANONICAL | ~30 | Primary source of truth for each domain |
| IMPORT_STAGING | 27 | Temporary pipeline output (should be transient) |
| SEARCH_INDEX | 5 | FTS virtual tables for wiki search |
| EMPTY_PLANNED | 9 | Structured but never populated |
| SYSTEM | 1 | SQLite internal (sqlite_sequence) |

## Domain Classification

### Audit
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `audit_findings` | 713 | Audit | Canonical |
| `data_audit_log` | 24,762 | Audit | Canonical |
| `jsonl_import_log` | 92 | IMPORT_STAGING | Staging |
| **Total** | **25,567** | | |

### Bible
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `bible_book_analysis_import` | 65 | IMPORT_STAGING | Staging |
| `bible_chapter_analysis_import` | 1,153 | IMPORT_STAGING | Staging |
| `bible_context` | 1,228 | Bible | Canonical |
| `bible_verses` | 62,751 | Bible | Canonical |
| `bible_verses_enhanced` | 0 | EMPTY_PLANNED | Empty/Planned |
| `bible_verses_import` | 62,204 | IMPORT_STAGING | Staging |
| `zolai_bible_analysis` | 30,758 | Bible | Enhanced/Derived |
| **Total** | **158,159** | | |

### Dictionary
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `dictionary` | 103,303 | Dictionary | Canonical |
| `dictionary_en_my_import` | 0 | IMPORT_STAGING | Staging |
| `dictionary_en_zo` | 113,750 | Dictionary | Canonical |
| `dictionary_en_zo_import` | 135,276 | IMPORT_STAGING | Staging |
| `dictionary_enhanced` | 0 | EMPTY_PLANNED | Empty/Planned |
| `dictionary_import` | 156,808 | IMPORT_STAGING | Staging |
| `dictionary_my_import` | 7,840 | IMPORT_STAGING | Staging |
| `dictionary_trilingual_import` | 0 | IMPORT_STAGING | Staging |
| **Total** | **516,977** | | |

### Grammar
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `grammar_patterns` | 5,547 | Grammar | Canonical |
| `grammar_patterns_enhanced` | 5,597 | Grammar | Enhanced/Derived |
| `grammar_patterns_import` | 6,983 | IMPORT_STAGING | Staging |
| `sentence_patterns_import` | 65 | IMPORT_STAGING | Staging |
| `zolai_grammar_patterns` | 13,519 | Grammar | Enhanced/Derived |
| **Total** | **31,711** | | |

### ModelTracking
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `gemini_model_results` | 0 | EMPTY_PLANNED | Empty/Planned |
| **Total** | **0** | | |

### NLP/Experimental
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `morph_verified` | 0 | EMPTY_PLANNED | Empty/Planned |
| `pos_gold` | 0 | EMPTY_PLANNED | Empty/Planned |
| `pos_verified` | 0 | EMPTY_PLANNED | Empty/Planned |
| `topic_clusters_import` | 12 | IMPORT_STAGING | Staging |
| `word_similarity` | 0 | EMPTY_PLANNED | Empty/Planned |
| **Total** | **12** | | |

### Phrase
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `phrase_context_import` | 45,597 | IMPORT_STAGING | Staging |
| `phrases` | 5,000 | Phrase | Canonical |
| `phrases_from_bible_import` | 14,000 | IMPORT_STAGING | Staging |
| `phrases_import` | 15,000 | IMPORT_STAGING | Staging |
| **Total** | **79,597** | | |

### Provenance
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `provenance` | 255 | Provenance | Canonical |
| **Total** | **255** | | |

### Proverbs
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `proverbs` | 7,736 | Proverbs | Canonical |
| `proverbs_idioms` | 0 | EMPTY_PLANNED | Empty/Planned |
| `proverbs_import` | 7,736 | IMPORT_STAGING | Staging |
| `zolai_proverbs_idioms` | 4,984 | Proverbs | Enhanced/Derived |
| **Total** | **20,456** | | |

### SearchIndex
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `wiki_content_fts` | 1,688 | SEARCH_INDEX | Canonical |
| `wiki_content_fts_config` | 1 | SEARCH_INDEX | Canonical |
| `wiki_content_fts_data` | 4,141 | SEARCH_INDEX | Canonical |
| `wiki_content_fts_docsize` | 1,688 | SEARCH_INDEX | Canonical |
| `wiki_content_fts_idx` | 3,186 | SEARCH_INDEX | Canonical |
| **Total** | **10,704** | | |

### Songs
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `zolai_songs` | 1,032 | Songs | Enhanced/Derived |
| **Total** | **1,032** | | |

### Syllable
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `syllable_data` | 189,554 | Syllable | Canonical |
| **Total** | **189,554** | | |

### System
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `sqlite_sequence` | 15 | SYSTEM | Canonical |
| **Total** | **15** | | |

### Tone
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `tone_patterns` | 118 | Tone | Canonical |
| `zolai_tone_sandhi` | 19 | Tone | Enhanced/Derived |
| **Total** | **137** | | |

### Training
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `training_corpus_qwen3_import` | 9,386 | IMPORT_STAGING | Staging |
| `training_exercises` | 81,805 | Training | Canonical |
| `training_exercises_import` | 81,805 | IMPORT_STAGING | Staging |
| `training_runs` | 2 | Training | Canonical |
| `training_seed_data_import` | 500 | IMPORT_STAGING | Staging |
| `training_valid_sentences_import` | 4,693 | IMPORT_STAGING | Staging |
| **Total** | **178,191** | | |

### Translation
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `translations` | 212,754 | Translation | Canonical |
| `translations_import` | 135,511 | IMPORT_STAGING | Staging |
| **Total** | **348,265** | | |

### Vocabulary
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `vocab` | 94,458 | Vocabulary | Canonical |
| `vocab_import` | 180,458 | IMPORT_STAGING | Staging |
| `vocabulary_enhanced` | 0 | EMPTY_PLANNED | Empty/Planned |
| `zolai_vocabulary` | 112,279 | Vocabulary | Enhanced/Derived |
| `zolai_vocabulary_import` | 12,692 | IMPORT_STAGING | Staging |
| **Total** | **399,887** | | |

### Wiki/Content
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `articles` | 6,371 | Wiki/Content | Canonical |
| `wiki_content` | 1,688 | Wiki/Content | Canonical |
| `wiki_lessons` | 1,688 | Wiki/Content | Canonical |
| **Total** | **9,747** | | |

### WordAlignment
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `word_alignments` | 385,120 | WordAlignment | Canonical |
| `word_alignments_import` | 627,000 | IMPORT_STAGING | Staging |
| `word_collocations` | 5,000 | WordAlignment | Canonical |
| `word_collocations_import` | 5,000 | IMPORT_STAGING | Staging |
| **Total** | **1,022,120** | | |

### WordUsage
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `word_usage` | 60,365 | WordUsage | Canonical |
| `word_usage_profiles_import` | 7,384 | IMPORT_STAGING | Staging |
| `zolai_word_usage` | 85,045 | WordUsage | Enhanced/Derived |
| **Total** | **152,794** | | |

### ZVS
| Table | Rows | Classification | Status |
|-------|------|----------------|--------|
| `zvs_corrections_import` | 44 | IMPORT_STAGING | Staging |
| **Total** | **44** | | |

