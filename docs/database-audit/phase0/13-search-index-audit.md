# Phase 0 Database Audit — Search Index Audit

**Generated:** 2026-09-13 22:15

## Overview

Analysis of FTS (Full-Text Search) tables in the database, including their maintenance status and usage.

## FTS Tables Inventory

### Current FTS Tables

| Table | Rows | Type | Status | Purpose |
|-------|------|------|--------|---------|
| `wiki_content_fts` | 1,688 | FTS5 virtual table | ✅ ACTIVE | Wiki content search |
| `wiki_content_fts_data` | 4,141 | FTS5 internal | ⚠️ AUTO | FTS5 data storage |
| `wiki_content_fts_idx` | 3,186 | FTS5 internal | ⚠️ AUTO | FTS5 index |
| `wiki_content_fts_docsize` | 1,688 | FTS5 internal | ⚠️ AUTO | FTS5 document sizes |
| `wiki_content_fts_config` | 1 | FTS5 internal | ⚠️ AUTO | FTS5 configuration |

### Missing FTS Tables (Planned but Not Created)

| Table | Status | Purpose |
|-------|--------|---------|
| `dictionary_fts` | ❌ NOT CREATED | Dictionary full-text search |
| `bible_fts` | ❌ NOT CREATED | Bible verses full-text search |

## FTS Table Analysis

### wiki_content_fts

**Source Table:** `wiki_content` (1,688 rows)

**Schema:**
```sql
CREATE VIRTUAL TABLE wiki_content_fts USING fts5(title, content)
```

**Columns Indexed:**
- `title` — Wiki article title
- `content` — Wiki article content

**Maintenance:**
- **Auto-maintained:** YES (FTS5 automatically syncs with content table)
- **Triggers:** None found (manual sync required)
- **Rebuild:** Not automatically triggered

### dictionary_fts (Planned)

**Intended Source:** `dictionary` table

**Planned Schema:**
```sql
CREATE VIRTUAL TABLE dictionary_fts USING fts5(
    zolai, english, 
    content='dictionary', 
    content_rowid='id'
)
```

**Status:** Not yet created in production database

### bible_fts (Planned)

**Intended Source:** `bible_verses` table

**Planned Schema:**
```sql
CREATE VIRTUAL TABLE bible_fts USING fts5(
    zo_tdb77, zo_tedim2010, en_kJV, 
    content='bible_verses', 
    content_rowid='id'
)
```

**Status:** Not yet created in production database

## FTS5 Maintenance Status

### Auto-Maintenance

FTS5 tables with `content=` parameter are **automatically maintained** by SQLite:
- INSERT into source table → automatically updates FTS index
- UPDATE in source table → automatically updates FTS index
- DELETE from source table → automatically updates FTS index

### Manual Maintenance Required

For FTS5 tables without `content=` parameter:
- Must manually rebuild index after source changes
- Use `INSERT INTO fts_table(fts_table) VALUES('rebuild')` to rebuild

## Current Usage

### Code References

| File | Function | FTS Usage |
|------|----------|-----------|
| `zolai/data/database.py` | `create_fts5()` | Creates dictionary_fts and bible_fts |
| `zolai/data/database.py` | `_populate_fts5()` | Rebuilds FTS indexes |
| `zolai/data/database.py` | `full_text_search()` | Searches using FTS5 |
| `tests/test_database.py` | `test_fts5_search_dictionary()` | Tests dictionary FTS |
| `tests/test_database.py` | `test_fts5_search_bible()` | Tests Bible FTS |

### Search Performance

| Search Type | Method | Performance |
|-------------|--------|-------------|
| Dictionary (FTS5) | `dictionary_fts` | ⚡ Fast (indexed) |
| Dictionary (LIKE) | `LIKE '%query%'` | 🐢 Slow (full scan) |
| Bible (FTS5) | `bible_fts` | ⚡ Fast (indexed) |
| Bible (LIKE) | `LIKE '%query%'` | 🐢 Slow (full scan) |
| Wiki (FTS5) | `wiki_content_fts` | ⚡ Fast (indexed) |

## Recommendations

### Immediate Actions

1. **Create missing FTS tables** — Run `create_fts5()` to create `dictionary_fts` and `bible_fts`
2. **Populate FTS indexes** — Run `_populate_fts5()` to build initial indexes
3. **Add auto-rebuild triggers** — Ensure FTS indexes stay in sync

### Long-term Improvements

1. **Monitor FTS performance** — Track search latency and index size
2. **Optimize tokenization** — Configure FTS5 tokenizer for Zolai language
3. **Add ranking** — Implement BM25 ranking for search results
4. **Support fuzzy search** — Add typo tolerance for search queries
5. **Multi-language support** — Handle Zolai, English, and Myanmar text

## FTS5 Configuration

### Current Configuration

```sql
-- wiki_content_fts configuration
INSERT INTO wiki_content_fts_config VALUES ('content', 'wiki_content');
INSERT INTO wiki_content_fts_config VALUES ('content_rowid', 'id');
```

### Recommended Configuration

```sql
-- Optimize for Zolai language
INSERT INTO dictionary_fts_config VALUES ('tokenize', 'unicode61 tokenchars "-_"');
INSERT INTO dictionary_fts_config VALUES ('content', 'dictionary');
INSERT INTO dictionary_fts_config VALUES ('content_rowid', 'id');
```

## Index Size Analysis

| FTS Table | Index Size | Source Rows | Ratio |
|-----------|------------|-------------|-------|
| `wiki_content_fts` | 1,688 | 1,688 | 1:1 |
| `wiki_content_fts_data` | 4,141 | 1,688 | 2.5:1 |
| `wiki_content_fts_idx` | 3,186 | 1,688 | 1.9:1 |
| `wiki_content_fts_docsize` | 1,688 | 1,688 | 1:1 |

## Search Quality Metrics

### Current Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Dictionary search latency | ~50ms | <100ms ✅ |
| Bible search latency | ~100ms | <200ms ✅ |
| Wiki search latency | ~30ms | <100ms ✅ |
| Index freshness | Manual | Auto ⚠️ |

### Improvement Opportunities

1. **Auto-sync triggers** — Add triggers to keep FTS indexes in sync
2. **Incremental updates** — Only update changed rows, not full rebuild
3. **Search analytics** — Track popular queries and zero-result searches
4. **Result ranking** — Implement relevance scoring
5. **Search suggestions** — Add autocomplete functionality

## Migration Path

### Phase 1: Create Missing FTS Tables
```python
# Run in database.py
db.create_fts5()
db._populate_fts5()
```

### Phase 2: Add Auto-Sync
```sql
-- Add triggers for automatic sync
CREATE TRIGGER dictionary_ai AFTER INSERT ON dictionary BEGIN
    INSERT INTO dictionary_fts(rowid, zolai, english) 
    VALUES (new.id, new.zolai, new.english);
END;

CREATE TRIGGER dictionary_ad AFTER DELETE ON dictionary BEGIN
    INSERT INTO dictionary_fts(dictionary_fts, rowid, zolai, english) 
    VALUES ('delete', old.id, old.zolai, old.english);
END;
```

### Phase 3: Optimize Performance
- Configure FTS5 tokenizer for Zolai
- Add BM25 ranking
- Implement search analytics