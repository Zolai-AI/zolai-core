# 13 — Proposed Target Schema

Ideal ~30-table schema design based on Phase 1 evidence.

---

## Current vs Target

| Category | Current | Target | Reduction |
|----------|---------|--------|-----------|
| Canonical runtime | 24 | 20 | -4 (merge duplicates) |
| Derived/enhanced | 6 | 4 | -2 (merge into canonical) |
| Import staging | 24 | 0 | -24 (drop after stable) |
| Empty placeholders | 11 | 0 | -11 (drop) |
| NLP experimental | 5 | 0 | -5 (drop) |
| Wiki FTS | 5 | 5 | 0 (keep) |
| Infrastructure | 4 | 4 | 0 (keep) |
| **Total** | **71** | **33** | **-38** |

---

## Target Schema (33 tables)

### Core Domain Tables (20)

```sql
-- 1. Dictionary (ZO→EN)
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai VARCHAR NOT NULL,
    english TEXT NOT NULL,
    english_clean VARCHAR,
    source VARCHAR NOT NULL,
    pos VARCHAR NOT NULL,
    myanmar TEXT,
    entry_version TEXT DEFAULT 'v1.0',
    zvs_compliance_status TEXT DEFAULT 'pending',
    updated_at TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(zolai, english, source)
);

-- 2. Dictionary (EN→ZO)
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    english VARCHAR NOT NULL,
    zolai TEXT NOT NULL,
    source VARCHAR NOT NULL,
    pos VARCHAR NOT NULL,
    myanmar TEXT,
    entry_version TEXT DEFAULT 'v1.0',
    zvs_compliance_status TEXT DEFAULT 'pending',
    updated_at TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(english, zolai, source)
);

-- 3. Bible Verses
CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book VARCHAR NOT NULL,
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    version VARCHAR NOT NULL,
    zolai TEXT NOT NULL,
    english TEXT NOT NULL,
    myanmar TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version_num INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(book, chapter, verse, version)
);

-- 4. Vocabulary (merged: vocab + zolai_vocabulary)
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL UNIQUE,
    english TEXT,
    myanmar TEXT,
    pos TEXT,
    tone_category TEXT,
    meaning_t1 TEXT,
    meaning_t3 TEXT,
    meaning_t4 TEXT,
    is_compound INTEGER DEFAULT 0,
    compound_parts TEXT,
    root_word TEXT,
    derivation TEXT,
    register TEXT,
    frequency_bible INTEGER DEFAULT 0,
    frequency_corpus INTEGER DEFAULT 0,
    frequency_songs INTEGER DEFAULT 0,
    bible_books TEXT,
    example_zo TEXT,
    example_en TEXT,
    source_priority INTEGER DEFAULT 999,
    source_category TEXT,
    source_file TEXT,
    confidence REAL DEFAULT 1.0,
    zvs_compliant INTEGER DEFAULT 1,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Grammar Patterns (merged: grammar_patterns + zolai_grammar_patterns)
CREATE TABLE grammar_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL UNIQUE,
    pattern_name TEXT,
    pattern_text TEXT,
    description TEXT,
    function TEXT,
    structure TEXT,
    examples TEXT,
    zolai_example TEXT,
    english_translation TEXT,
    morpheme_breakdown TEXT,
    tone_pattern TEXT,
    pattern_type TEXT,
    tense TEXT,
    aspect TEXT,
    negation_type TEXT,
    question_type TEXT,
    agreement TEXT,
    ergative TEXT,
    frequency INTEGER,
    myanmar TEXT,
    source_category TEXT,
    source_file TEXT,
    import_batch_id TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 6. Translations
CREATE TABLE translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    direction VARCHAR NOT NULL,
    reference VARCHAR NOT NULL,
    confidence FLOAT NOT NULL,
    myanmar TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(source, target, direction, reference)
);

-- 7. Word Alignments
CREATE TABLE word_alignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref VARCHAR NOT NULL,
    zolai_word VARCHAR NOT NULL,
    english_word VARCHAR NOT NULL,
    position INTEGER NOT NULL,
    myanmar TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 8. Phrases
CREATE TABLE phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT NOT NULL,
    source VARCHAR NOT NULL,
    category TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(zolai, english, source)
);

-- 9. Proverbs (merged with zolai_proverbs_idioms columns)
CREATE TABLE proverbs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL UNIQUE,
    english TEXT,
    english_translation TEXT,
    literal_translation TEXT,
    morpheme_breakdown TEXT,
    source VARCHAR NOT NULL,
    category VARCHAR,
    theme TEXT,
    cultural_context TEXT,
    source_category TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 10. Word Usage
CREATE TABLE word_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    book TEXT NOT NULL,
    frequency INTEGER,
    meanings TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(word, book)
);

-- 11. Training Exercises
CREATE TABLE training_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_type TEXT NOT NULL,
    zolai TEXT NOT NULL,
    english TEXT NOT NULL,
    difficulty TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 12. Syllable Data
CREATE TABLE syllable_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    syllables TEXT NOT NULL,
    syllable_count INTEGER,
    source TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 13. Word Collocations
CREATE TABLE word_collocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word1 TEXT NOT NULL,
    word2 TEXT NOT NULL,
    frequency INTEGER,
    source TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT,
    UNIQUE(word1, word2)
);

-- 14. Bible Context
CREATE TABLE bible_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL,
    chapter INTEGER,
    topic TEXT,
    summary TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 15. Articles
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    excerpt TEXT,
    categories TEXT,
    date TEXT,
    link TEXT,
    language TEXT DEFAULT 'zolai'
);

-- 16. Wiki Content
CREATE TABLE wiki_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 17. Wiki Lessons
CREATE TABLE wiki_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    level TEXT,
    category TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 18. Zolai Songs
CREATE TABLE zolai_songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    lyrics TEXT NOT NULL,
    artist TEXT,
    source TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 19. Tone Patterns
CREATE TABLE tone_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    description TEXT,
    example TEXT,
    source TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 20. Tone Sandhi
CREATE TABLE zolai_tone_sandhi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule TEXT NOT NULL,
    description TEXT,
    example TEXT,
    source TEXT
);
```

### Derived Tables (4)

```sql
-- 21. Bible Analysis
CREATE TABLE zolai_bible_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verse_id INTEGER,
    compounds TEXT,
    grammar TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 22. Word Usage Analysis
CREATE TABLE zolai_word_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    book TEXT NOT NULL,
    frequency INTEGER,
    meanings TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 23. Grammar Analysis
CREATE TABLE zolai_grammar_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT,
    analysis TEXT,
    confidence REAL,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);

-- 24. Proverb Analysis
CREATE TABLE zolai_proverb_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proverb_id INTEGER,
    morpheme_breakdown TEXT,
    cultural_context TEXT,
    theme TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);
```

### Wiki FTS (5)

```sql
-- 25-29. Wiki FTS (auto-generated by SQLite)
-- wiki_content_fts, wiki_content_fts_config, wiki_content_fts_data,
-- wiki_content_fts_docsize, wiki_content_fts_idx
```

### Infrastructure (4)

```sql
-- 30. Data Audit Log
CREATE TABLE data_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    record_id INTEGER,
    action TEXT NOT NULL,
    old_values TEXT,
    new_values TEXT,
    changed_by TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 31. Provenance
CREATE TABLE provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    table_name TEXT NOT NULL,
    sha256 TEXT,
    row_count INTEGER,
    version INTEGER,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 32. JSONL Import Log
CREATE TABLE jsonl_import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    source_file TEXT,
    table_name TEXT,
    rows_imported INTEGER,
    sha256 TEXT,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version INTEGER,
    status TEXT,
    error_message TEXT
);

-- 33. Audit Findings
CREATE TABLE audit_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    recommendation TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Key Schema Changes

1. **Merged `vocab` + `zolai_vocabulary` → `vocabulary`** with UNIQUE on `zolai`
2. **Merged `grammar_patterns` + `zolai_grammar_patterns` → `grammar_patterns`** with UNIQUE on `pattern_id`
3. **Merged `zolai_proverbs_idioms` columns into `proverbs`** with UNIQUE on `zolai`
4. **Added UNIQUE constraints** on all business keys
5. **Dropped 38 tables** (24 staging + 11 empty + 3 superseded)
6. **Kept FTS tables** for search performance
7. **Kept infrastructure tables** for audit trail

---

## Estimated Size Reduction

| Metric | Current | Target | Reduction |
|--------|---------|--------|-----------|
| Tables | 71 | 33 | -54% |
| Rows | ~3.1M | ~2.5M | -19% |
| Size | 1,162 MB | ~800 MB | -31% |
