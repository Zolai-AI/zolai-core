-- Phase 2B: Create 11 derived _v2 tables
-- NON-DESTRUCTIVE: new tables only, source tables untouched
-- All add: version, created_at, updated_at, content_hash

-- ============================================================
-- 1. bible_analysis_v2 (from bible_context)
-- ============================================================
CREATE TABLE IF NOT EXISTS bible_analysis_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL,
    chapter INTEGER,
    analysis_type TEXT NOT NULL,
    data TEXT NOT NULL,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_ba_v2_book ON bible_analysis_v2 (book);
CREATE INDEX IF NOT EXISTS idx_ba_v2_type ON bible_analysis_v2 (analysis_type);

-- ============================================================
-- 2. articles_v2 (from articles)
-- ============================================================
CREATE TABLE IF NOT EXISTS articles_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT,
    excerpt TEXT,
    categories TEXT,
    date TEXT,
    link TEXT,
    language TEXT DEFAULT 'zolai',
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_v2_lang ON articles_v2 (language);

-- ============================================================
-- 3. songs_v2 (from zolai_songs)
-- ============================================================
CREATE TABLE IF NOT EXISTS songs_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT,
    song_number INTEGER,
    title TEXT,
    text TEXT,
    source TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_songs_v2_coll ON songs_v2 (collection);

-- ============================================================
-- 4. wiki_content_v2 (from wiki_content)
-- ============================================================
CREATE TABLE IF NOT EXISTS wiki_content_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wiki_category TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    title TEXT,
    content TEXT NOT NULL,
    word_count INTEGER,
    section_count INTEGER,
    content_hash TEXT NOT NULL,
    entry_version TEXT DEFAULT 'v1.0',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_wiki_v2_cat ON wiki_content_v2 (wiki_category);

-- ============================================================
-- 5. data_audit_log_v2 (from data_audit_log)
-- ============================================================
CREATE TABLE IF NOT EXISTS data_audit_log_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    row_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_dal_v2_table ON data_audit_log_v2 (table_name);
CREATE INDEX IF NOT EXISTS idx_dal_v2_row ON data_audit_log_v2 (row_id);

-- ============================================================
-- 6. audit_findings_v2 (from audit_findings)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_findings_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_type TEXT NOT NULL,
    word TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source TEXT,
    confidence TEXT,
    verified_by TEXT DEFAULT 'system',
    entry_id INTEGER,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_af_v2_type ON audit_findings_v2 (finding_type);
CREATE INDEX IF NOT EXISTS idx_af_v2_word ON audit_findings_v2 (word);

-- ============================================================
-- 7. provenance_v2 (from provenance)
-- ============================================================
CREATE TABLE IF NOT EXISTS provenance_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    source TEXT NOT NULL,
    generator_script TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    change_log TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at_v2 TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_prov_v2_sha ON provenance_v2 (sha256);

-- ============================================================
-- 8. import_log_v2 (from jsonl_import_log)
-- ============================================================
CREATE TABLE IF NOT EXISTS import_log_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    table_name TEXT NOT NULL,
    rows_imported INTEGER NOT NULL,
    sha256 TEXT,
    imported_at TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_imp_v2_batch ON import_log_v2 (batch_id);
CREATE INDEX IF NOT EXISTS idx_imp_v2_table ON import_log_v2 (table_name);

-- ============================================================
-- 9. tone_sandhi_v2 (from zolai_tone_sandhi)
-- ============================================================
CREATE TABLE IF NOT EXISTS tone_sandhi_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_number TEXT,
    rule_name TEXT,
    underlying_pattern TEXT,
    surface_pattern TEXT,
    condition TEXT,
    examples TEXT,
    domain TEXT,
    source_category TEXT,
    source_file TEXT,
    import_batch_id TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);

-- ============================================================
-- 10. tone_patterns_v2 (from tone_patterns)
-- ============================================================
CREATE TABLE IF NOT EXISTS tone_patterns_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    tone_category TEXT,
    meaning_t1 TEXT,
    meaning_t3 TEXT,
    meaning_t4 TEXT,
    sandhi_rules TEXT,
    source_category TEXT,
    source_file TEXT,
    import_batch_id TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_tp_v2_word ON tone_patterns_v2 (word);

-- ============================================================
-- 11. training_runs_v2 (from training_runs)
-- ============================================================
CREATE TABLE IF NOT EXISTS training_runs_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT,
    dataset_name TEXT,
    entry_count INTEGER,
    entry_version TEXT DEFAULT 'v1.0',
    update_remarks TEXT DEFAULT '',
    metrics_json TEXT,
    status TEXT DEFAULT 'pending',
    completed_at TEXT,
    test_type TEXT,
    test_date TEXT,
    total_tests INTEGER,
    passed_tests INTEGER,
    score REAL,
    details TEXT,
    import_batch_id TEXT,
    source_file TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_tr_v2_status ON training_runs_v2 (status);
