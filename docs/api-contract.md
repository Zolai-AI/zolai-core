# Zolai Desktop API Contract

**This file is the single source of truth for the HTTP API the Zolai Desktop
UI (`zolai-tauri/frontend/index.html`) consumes.** Both sides must implement
exactly this contract.

- **Backend** — `zolai/api/server.py` + `zolai/api/desktop_router.py` must
  expose exactly these routes.
- **Frontend** — `zolai-tauri/frontend/index.html` must call exactly these
  routes via `api(path)` (or `fetch(API+path)` for non-GET).
- **Enforcement** — `tests/test_desktop_contract.py` asserts every route in
  the table below is registered on the app. If backend drifts, the test fails.

> When adding an endpoint: add it here, add it to `DESKTOP_CONTRACT` in
> `tests/test_desktop_contract.py`, then add a UI call in `index.html` that
> uses `api(path)`. Never call a path that is not in this table.

---

## Legend

| Column | Meaning |
|--------|---------|
| Method | HTTP verb |
| Path | Route — `{param}` is a path segment, `?query=` params below |
| Params | Query / path / body params in the UI call |
| Returns | Key JSON fields the UI reads |
| UI fn | Function in `index.html` that calls it |

All GET/POST/PUT/DELETE calls are made against `API` (the base URL from
`#settings-url`, default `http://localhost:8000`), e.g. `API + "/desktop/stats"`.

---

## Desktop Router (`/desktop/...`, `zolai/api/desktop_router.py`)

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/stats` | — | `total_tables`, `total_rows`, `database_size_mb`, `dictionary.total`, `dictionary.coverage_myanmar_pct`, `bible.total_verses`, `training.translation_pairs`, `training.training_exercises`, `training.vocabulary_entries`, `training.phrases`, `training.grammar_patterns`, `training.proverbs`, `training.word_alignments`, `training.word_usage_profiles`, `provenance.audit_log_entries`, `table_details` | `refreshAll()` |
| GET | `/desktop/tables` | — | `tables[]`, `total` | `refreshTables()` |
| GET | `/desktop/query` | `sql` | `columns[]`, `rows[]`, `count` | `runSQL()` |
| GET | `/desktop/dict/browse` | `limit` (default 50) | `results[]` | `browseDict()` |
| GET | `/desktop/dict/non-zolai` | — | run-script result | `checkNonZolai()` |
| GET | `/desktop/bible/study` | `book` | run-script result | `studyBook()` |
| GET | `/desktop/bible/learn` | `level` | run-script result | `studyLearn()` |
| GET | `/desktop/bible/context/book` | `book` | run-script result | `cBook()` |
| GET | `/desktop/bible/context/word` | `word` | run-script result | `cWord()` |
| GET | `/desktop/bible/context/topics` | — | run-script result | `cTopics()` |
| GET | `/desktop/gemini/fill-en` | `limit` (default 50) | run-script result | `gemFillEn()` |
| GET | `/desktop/gemini/fill-my` | `limit` (default 50) | run-script result | `gemFill()` |
| GET | `/desktop/gemini/coverage` | — | run-script result | `gemCoverage()` |
| GET | `/desktop/training/generate` | `type`, `count` | run-script result | `genTraining()` |
| GET | `/desktop/training/build` | — | run-script result | `buildDataset()` |
| GET | `/desktop/training/build-qwen` | — | run-script result | `buildQwen()` |
| GET | `/desktop/export/{data_type}` | path `data_type` ∈ `dictionary,bible,vocabulary,grammar,phrases,exercises` | run-script result | `exportData()` |
| GET | `/desktop/test/quiz` | `level` (default A1) | run-script result | `startQuiz()` |
| GET | `/desktop/grammar/check` | `text` | run-script result | `checkGrammar()` |
| GET | `/desktop/paragraph/analyze` | `text` | run-script result | `analyzeParagraph()` |
| GET | `/desktop/zvs/validate` | `text` | run-script result | `validateZVS()` |
| GET | `/desktop/audit/recent` | — | run-script result | `loadAudit()`, `refreshAudit()` |

`run-script result` = `{"success": true}` | `{"error": ...}` | parsed JSON |
`{"output": ...}` returned by `run_script()`. It is intentionally schemaless
because the desktop router shells out to independent analysis scripts.

---

## Application routes (`zolai/api/server.py`)

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/health` | — | `status`, `version`, `data_root` | `testConnection()` |
| GET | `/bible/search` | `q`, `version` (default tdb77), `limit` (default 10) | `query`, `version`, `count`, `results[]` | `searchBible()` |
| GET | `/dictionary/search/all` | `q`, `limit` (default 20) | `results[]`, `count` | `searchDict()`, `findEdit()` |
| GET | `/dictionary/search/my` | `q` | `query`, `results[]`, `count` | `searchMyanmar()` |
| POST | `/dictionary/add` | body/query `word`, `english`, `myanmar`, `pos` | `success`, `word` | `addWord()` |
| PUT | `/dictionary/update` | `word`, `field`, `value` | `success`, `word`, `field` | `saveEdit()` |
| DELETE | `/dictionary/delete` | `word` | `success`, `word` | `deleteWord()` |
| GET | `/monitor/health` | — | health dict | `refreshMonitor()` |
| GET | `/monitor/coverage` | — | coverage dict | `refreshCoverage()` |
| GET | `/monitor/audit` | `limit` (default 50) | audit entries | `refreshAudit()` |

---

## Frontend API helper

All calls go through one helper so the base URL always carries the prefix:

```js
async function api(u){ const r = await fetch(API+u); return await r.json(); }
```

Non-GET calls use `fetch(API+path, {method: 'POST'|'PUT'|'DELETE', ...})`.
There is **no** raw `fetch('/...')` without the `API` prefix.

---

## Contract change process

1. Add/change the row here.
2. Update `DESKTOP_CONTRACT` in `tests/test_desktop_contract.py`.
3. Update `index.html` to call the contract path via `api()`.
4. Run `python -m pytest tests/test_desktop_contract.py` — must pass.