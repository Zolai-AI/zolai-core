-- Phase 2A: Create 12 core canonical _v2 tables
-- Strategy: NON-DESTRUCTIVE — new tables alongside old
-- All _v2 tables add: version, created_at, updated_at, content_hash

-- 1. dictionary_v2
CREATE TABLE IF NOT EXISTS dictionary_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT,
    english_clean TEXT,
    myanmar TEXT,
    pos TEXT,
    source TEXT,
    entry_version TEXT DEFAULT 'v1.0',
    update_remarks TEXT DEFAULT '',
    update_description TEXT DEFAULT '',
    zvs_compliance_status TEXT DEFAULT 'pending',
    is_deleted INTEGER DEFAULT 0,
    deleted_at TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_dict_v2_zolai ON dictionary_v2 (zolai);
CREATE INDEX IF NOT EXISTS idx_dict_v2_source ON dictionary_v2 (zolai, source);

-- 2. dictionary_en_zo_v2
CREATE TABLE IF NOT EXISTS dictionary_en_zo_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    translations TEXT,
    translations_clean TEXT,
    pos TEXT,
    source TEXT,
    myanmar TEXT,
    entry_version TEXT DEFAULT 'v1.0',
    update_remarks TEXT DEFAULT '',
    update_description TEXT DEFAULT '',
    zvs_compliance_status TEXT DEFAULT 'pending',
    is_deleted INTEGER DEFAULT 0,
    deleted_at TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_enzo_v2_headword ON dictionary_en_zo_v2 (headword);

-- 3. bible_verses_v2
-- Consolidated: picks best Zolai version (tdb77 primary, tedim2010 fallback)
-- Adds confidence column for quality tracking
CREATE TABLE IF NOT EXISTS bible_verses_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    zo_tdb77 TEXT,
    zo_tedim2010 TEXT,
    en_kJV TEXT,
    myanmar TEXT,
    zo_tedim1932 TEXT,
    zo_hcl06 TEXT,
    zo_fcl TEXT,
    myanmar_judson TEXT,
    book_name TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_bible_v2_ref ON bible_verses_v2 (ref);
CREATE INDEX IF NOT EXISTS idx_bible_v2_book ON bible_verses_v2 (book);
CREATE INDEX IF NOT EXISTS idx_bible_v2_book_cv ON bible_verses_v2 (book, chapter, verse);

-- 4. grammar_patterns_v2
CREATE TABLE IF NOT EXISTS grammar_patterns_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL,
    pattern TEXT NOT NULL,
    description TEXT,
    function TEXT NOT NULL,
    examples TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_gp_v2_pid ON grammar_patterns_v2 (pattern_id);

-- 5. translations_v2
CREATE TABLE IF NOT EXISTS translations_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    direction TEXT NOT NULL,
    reference TEXT NOT NULL,
    confidence REAL NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_trans_v2_dir ON translations_v2 (direction, reference);

-- 6. word_alignments_v2
CREATE TABLE IF NOT EXISTS word_alignments_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    zolai_word TEXT NOT NULL,
    english_word TEXT NOT NULL,
    position INTEGER NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_wa_v2_ref ON word_alignments_v2 (ref);

-- 7. vocabulary_v2 (from vocab table)
CREATE TABLE IF NOT EXISTS vocabulary_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    english TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    books TEXT NOT NULL,
    examples TEXT NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_vocab_v2_hw ON vocabulary_v2 (headword);

-- 8. proverbs_v2
CREATE TABLE IF NOT EXISTS proverbs_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT,
    source TEXT NOT NULL,
    category TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);

-- 9. phrases_v2
-- Source table uses `zo`, target renames to `zolai` for consistency
CREATE TABLE IF NOT EXISTS phrases_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    examples TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_phrases_v2_zo ON phrases_v2 (zolai);

-- 10. word_usage_v2
CREATE TABLE IF NOT EXISTS word_usage_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    book TEXT NOT NULL,
    total_freq INTEGER NOT NULL,
    meaning_shifts TEXT NOT NULL,
    co_occurring_words TEXT NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_wu_v2_word ON word_usage_v2 (word);
CREATE INDEX IF NOT EXISTS idx_wu_v2_wb ON word_usage_v2 (word, book);

-- 11. syllable_data_v2
CREATE TABLE IF NOT EXISTS syllable_data_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    syllables TEXT NOT NULL,
    syllable_count INTEGER NOT NULL,
    engine TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    source_table TEXT,
    source_id INTEGER,
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_syl_v2_word ON syllable_data_v2 (word);

-- 12. word_collocations_v2
CREATE TABLE IF NOT EXISTS word_collocations_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word1 TEXT NOT NULL,
    word2 TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    pmiproxy REAL NOT NULL,
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_wc_v2_w1 ON word_collocations_v2 (word1);
