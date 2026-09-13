# Phase 0 Database Audit — Tauri/Web Audit

**Generated:** 2026-09-13 22:15

## Overview

Analysis of the zolai-tauri desktop app frontend code to identify which database tables are referenced and which API endpoints are called.

## Frontend Architecture

- **Framework:** React + TypeScript
- **UI Components:** Custom UI library (`src/components/ui/`)
- **Features:** Modular feature panels (`src/features/`)
- **API Client:** `src/lib/zolai-core/contract.ts` (route definitions)

## API Endpoint Usage

### Desktop Router Endpoints (Called by Frontend)

| Endpoint | Frontend Component | Description |
|----------|-------------------|-------------|
| `GET /desktop/stats` | `dashboard-panel.tsx` | Database statistics dashboard |
| `GET /desktop/tables` | `tables-panel.tsx` | List all tables |
| `GET /desktop/query` | `query-panel.tsx` | Run SQL queries |
| `GET /desktop/dict/browse` | `dictionary-panel.tsx` | Browse dictionary |
| `GET /desktop/dict/non-zolai` | `dictionary-panel.tsx` | Check non-Zolai words |
| `GET /desktop/bible/study` | `bible-panel.tsx` | Bible book study |
| `GET /desktop/bible/learn` | `bible-panel.tsx` | Progressive learning |
| `GET /desktop/bible/context/book` | `bible-panel.tsx` | Book context analysis |
| `GET /desktop/bible/context/word` | `bible-panel.tsx` | Word usage profile |
| `GET /desktop/bible/context/topics` | `bible-panel.tsx` | Topic clusters |
| `GET /desktop/bible/books` | `bible-panel.tsx` | List Bible books |
| `GET /desktop/bible/chapters` | `bible-panel.tsx` | List chapters |
| `GET /desktop/bible/verses` | `bible-panel.tsx` | Get verses |
| `GET /desktop/gemini/fill-en` | `gemini-panel.tsx` | Fill English translations |
| `GET /desktop/gemini/fill-my` | `gemini-panel.tsx` | Fill Myanmar translations |
| `GET /desktop/gemini/coverage` | `gemini-panel.tsx` | Translation coverage |
| `GET /desktop/training/generate` | `training-panel.tsx` | Generate training data |
| `GET /desktop/training/build` | `training-panel.tsx` | Build training dataset |
| `GET /desktop/training/build-qwen` | `training-panel.tsx` | Build Qwen3 format |
| `GET /desktop/export/{type}` | `export-panel.tsx` | Export data |
| `GET /desktop/test/quiz` | `quiz-panel.tsx` | Proficiency quiz |
| `GET /desktop/grammar/check` | `grammar-panel.tsx` | Check grammar |
| `GET /desktop/paragraph/analyze` | `paragraph-panel.tsx` | Analyze paragraph |
| `GET /desktop/zvs/validate` | `zvs-panel.tsx` | Validate ZVS compliance |
| `GET /desktop/audit/recent` | `audit-panel.tsx` | Recent audit log |
| `GET /desktop/table-data` | `table-data-panel.tsx` | Paginated table data |
| `GET /desktop/table-schema` | `table-schema-panel.tsx` | Table schema |

### Application Router Endpoints (Called by Frontend)

| Endpoint | Frontend Component | Description |
|----------|-------------------|-------------|
| `GET /health` | `health-check.tsx` | Health check |
| `GET /bible/search` | `bible-panel.tsx` | Search Bible verses |
| `GET /dictionary/search/all` | `dictionary-panel.tsx` | Search dictionary |
| `GET /dictionary/search/my` | `myanmar-panel.tsx` | Search Myanmar dictionary |
| `POST /dictionary/add` | `dictionary-panel.tsx` | Add dictionary entry |
| `PUT /dictionary/update` | `dictionary-panel.tsx` | Update dictionary entry |
| `DELETE /dictionary/delete` | `dictionary-panel.tsx` | Delete dictionary entry |
| `GET /monitor/health` | `monitor-panel.tsx` | Database health |
| `GET /monitor/coverage` | `monitor-panel.tsx` | Translation coverage |
| `GET /monitor/audit` | `monitor-panel.tsx` | Audit log |
| `POST /chat/zolai` | `chat-panel.tsx` | Zolai chat |
| `POST /chat/gemini` | `chat-panel.tsx` | Gemini chat |

## Database Tables Referenced in Frontend

### Direct Table References

| Table | Frontend Location | Usage |
|-------|-------------------|-------|
| `dictionary` | `dictionary-panel.tsx`, `dashboard-panel.tsx` | Dictionary browsing, stats |
| `bible_verses` | `bible-panel.tsx`, `dashboard-panel.tsx` | Bible navigation, stats |
| `data_audit_log` | `audit-panel.tsx` | Audit log display |
| `training_exercises` | `dashboard-panel.tsx` | Training stats |
| `translations` | `dashboard-panel.tsx` | Translation stats |
| `vocab` | `dashboard-panel.tsx` | Vocabulary stats |
| `phrases` | `dashboard-panel.tsx` | Phrase stats |
| `grammar_patterns` | `dashboard-panel.tsx` | Grammar stats |
| `proverbs` | `dashboard-panel.tsx` | Proverb stats |
| `word_alignments` | `dashboard-panel.tsx` | Alignment stats |
| `word_usage` | `dashboard-panel.tsx` | Word usage stats |

### Indirect Table References (via API)

| Table | API Endpoint | Frontend Component |
|-------|--------------|-------------------|
| `dictionary` | `/dictionary/search/all` | `dictionary-panel.tsx` |
| `dictionary_en_zo` | `/dictionary/search/all` | `dictionary-panel.tsx` |
| `bible_verses` | `/bible/search`, `/desktop/bible/*` | `bible-panel.tsx` |
| `grammar_patterns` | `/desktop/pattern/*` | `grammar-panel.tsx` |
| `data_audit_log` | `/monitor/audit`, `/desktop/audit/recent` | `audit-panel.tsx` |

## Frontend Features and Data Flow

### Dictionary Feature
```
dictionary-panel.tsx
    ↓
/dict/browse → dictionary table
/dict/non-zolai → runs check_non_zolai.py
/dictionary/search/all → dictionary + dictionary_en_zo tables
/dictionary/add → dictionary table
/dictionary/update → dictionary table
/dictionary/delete → dictionary + dictionary_en_zo + data_audit_log tables
```

### Bible Feature
```
bible-panel.tsx
    ↓
/bible/books → bible_verses table
/bible/chapters → bible_verses table
/bible/verses → bible_verses table
/bible/search → bible_verses table
/bible/study → runs bible_engine.py
/bible/learn → runs bible_engine.py
/bible/context/* → runs context_deep_learner.py
```

### Training Feature
```
training-panel.tsx
    ↓
/training/generate → runs generate_training_data.py
/training/build → runs build_training_corpus.py
/training/build-qwen → runs format_training_data.py
/export/{type} → various tables
```

### Dashboard Feature
```
dashboard-panel.tsx
    ↓
/desktop/stats → all tables (aggregated stats)
    ↓
Displays:
- dictionary.total
- bible.total_verses
- training.translation_pairs
- training.training_exercises
- training.vocabulary_entries
- training.phrases
- training.grammar_patterns
- training.proverbs
- training.word_alignments
- training.word_usage_profiles
- provenance.audit_log_entries
```

## Type Definitions

The frontend uses TypeScript interfaces defined in `src/lib/zolai-core/types.ts`:

- `StatsResponse` — Dashboard statistics
- `DictionaryStats` — Dictionary coverage
- `BibleStats` — Bible verse counts
- `TrainingStats` — Training data counts
- `ProvenanceStats` — Audit log entries
- `DictEntry` — Dictionary entry structure
- `BibleVerseResult` — Bible verse result
- `AuditEntry` — Audit log entry

## Security Considerations

1. **No Auth** — Frontend has no authentication mechanism
2. **SQL Injection** — `/desktop/query` endpoint accepts raw SQL
3. **Script Execution** — Many endpoints run external scripts
4. **CORS** — Backend allows all origins

## Recommendations

1. **Add authentication** — Implement API key or JWT auth
2. **Input validation** — Sanitize all user inputs
3. **Rate limiting** — Protect against abuse
4. **Error handling** — Improve error messages for users
5. **Offline support** — Implement local caching for desktop app

## Frontend Component Inventory

| Component | File | Purpose |
|-----------|------|---------|
| DictionaryPanel | `features/dictionary/dictionary-panel.tsx` | Dictionary browsing/editing |
| BiblePanel | `features/bible/bible-panel.tsx` | Bible navigation/study |
| MyanmarPanel | `features/myanmar/myanmar-panel.tsx` | Myanmar dictionary |
| TrainingPanel | `features/training/training-panel.tsx` | Training data generation |
| ExportPanel | `features/export/export-panel.tsx` | Data export |
| DashboardPanel | `features/dashboard/dashboard-panel.tsx` | Statistics dashboard |
| AuditPanel | `features/audit/audit-panel.tsx` | Audit log viewer |
| ChatPanel | `features/chat/chat-panel.tsx` | Zolai/Gemini chat |
| QueryPanel | `features/query/query-panel.tsx` | SQL query runner |
| TableDataPanel | `features/table-data/table-data-panel.tsx` | Table data browser |
| TableSchemaPanel | `features/table-schema/table-schema-panel.tsx` | Schema viewer |
| SettingsPanel | `features/settings/settings-panel.tsx` | App settings |