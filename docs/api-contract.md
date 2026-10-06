# Zolai Desktop API Contract

**This file is the single source of truth for the HTTP API the Zolai Desktop
UI (`zolai-tauri/frontend/index.html`) consumes.** Both sides must implement
exactly this contract.

- **Backend** — `zolai/api/server.py` + `zolai/api/desktop_router.py` + `zolai/api/jsonl_router.py` must
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

### Database Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/stats` | — | `total_tables`, `total_rows`, `database_size_mb`, `dictionary.total`, `dictionary.coverage_myanmar_pct`, `bible.total_verses`, `training.translation_pairs`, `training.training_exercises`, `training.vocabulary_entries`, `training.phrases`, `training.grammar_patterns`, `training.proverbs`, `training.word_alignments`, `training.word_usage_profiles`, `provenance.audit_log_entries`, `table_details` | `refreshAll()` |
| GET | `/desktop/tables` | — | `tables[]`, `total` | `refreshTables()` |
| GET | `/desktop/query` | `sql` | `columns[]`, `rows[]`, `count` | `runSQL()` |

### Dictionary Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/dict/browse` | `limit` (default 50) | `results[]` | `browseDict()` |
| GET | `/desktop/dict/non-zolai` | — | run-script result | `checkNonZolai()` |
| GET | `/dictionary/search/all` | `q`, `limit` (default 20) | `results[]`, `count` | `searchDict()`, `findEdit()` |
| GET | `/dictionary/search/my` | `q` | `query`, `results[]`, `count` | `searchMyanmar()` |
| POST | `/dictionary/add` | body/query `word`, `english`, `myanmar`, `pos` | `success`, `word` | `addWord()` |
| PUT | `/dictionary/update` | `word`, `field`, `value` | `success`, `word`, `field` | `saveEdit()` |
| DELETE | `/dictionary/delete` | `word` | `success`, `word` | `deleteWord()` |
| GET | `/dictionary/stats` | — | `total`, `with_myanmar`, `english_clean`, `pos_breakdown` | — |

### Bible Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/bible/study` | `book` | run-script result | `studyBook()` |
| GET | `/desktop/bible/learn` | `level` | run-script result | `studyLearn()` |
| GET | `/desktop/bible/context/book` | `book` | run-script result | `cBook()` |
| GET | `/desktop/bible/context/word` | `word` | run-script result | `cWord()` |
| GET | `/desktop/bible/context/topics` | — | run-script result | `cTopics()` |
| GET | `/bible/search` | `q`, `version` (default tdb77), `limit` (default 10) | `query`, `version`, `count`, `results[]` | `searchBible()` |

### Gemini Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/gemini/fill-en` | `limit` (default 50) | run-script result | `gemFillEn()` |
| GET | `/desktop/gemini/fill-my` | `limit` (default 50) | run-script result | `gemFill()` |
| GET | `/desktop/gemini/coverage` | — | run-script result | `gemCoverage()` |
| GET | `/desktop/gemini/fill` | `text`, `lang` (default "my") | run-script result | — |

### Training Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/training/generate` | `type`, `count` (default 1000) | run-script result | `genTraining()` |
| GET | `/desktop/training/generate-sentences` | — | run-script result | `genSentences()` |
| GET | `/desktop/training/validate` | `text` | run-script result | `validateTraining()` |
| GET | `/desktop/training/deep-validate` | `text` | run-script result | `deepValidate()` |
| GET | `/desktop/training/build` | — | run-script result | `buildDataset()` |
| GET | `/desktop/training/build-qwen` | — | run-script result | `buildQwen()` |
| GET | `/desktop/training/export` | `fmt` (default "jsonl") | run-script result | `exportTraining()` |
| GET | `/desktop/training/build-corpus` | — | run-script result | — |
| GET | `/desktop/training/corpus-stats` | — | run-script result | — |

### Test & Quiz Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/test/quiz` | `level` (default A1), `qtype` (default "all") | run-script result | `startQuiz()` |
| GET | `/desktop/test/stats` | — | run-script result | — |

### Grammar Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/grammar/check` | `text` | run-script result | `checkGrammar()` |
| GET | `/desktop/grammar/negation-rules` | — | run-script result | — |

### Paragraph Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/paragraph/analyze` | `text` | run-script result | `analyzeParagraph()` |
| GET | `/desktop/paragraph/style` | `text`, `style` (default "FORMAL") | run-script result | — |
| GET | `/desktop/paragraph/paraphrase` | `text`, `level` (default "Minimal") | run-script result | — |

### ZVS Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/zvs/validate` | `text` | run-script result | `validateZVS()` |
| GET | `/desktop/zvs/forbidden` | — | run-script result | — |

### Pattern Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/pattern/stats` | — | `total`, `by_function{}` | — |
| GET | `/desktop/pattern/learn` | `limit` (default 20) | run-script result (patterns) | — |

### Export Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/export/{data_type}` | path `data_type` ∈ `dictionary,bible,vocabulary,grammar,phrases,exercises` | `table`, `total`, `sample[]` | `exportData()` |

### Audit Tools

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/desktop/audit/recent` | `limit` (default 50) | `results[]` (audit rows) | `loadAudit()`, `refreshAudit()` |

---

## JSONL Pipeline Router (`/desktop/jsonl/...`, `zolai/api/jsonl_router.py`)

### Import Operations

| Method | Path | Params | Returns | Description |
|--------|------|--------|---------|-------------|
| POST | `/desktop/jsonl/import/all` | body: `{batch_id?}` | `{batch_id, status, message}` | Import all canonical JSONL files (async) |
| POST | `/desktop/jsonl/import/file` | body: `{file_path, table_name, batch_id?, version?}` | import result | Import a single JSONL file |
| GET | `/desktop/jsonl/import/status/{batch_id}` | path `batch_id` | status details | Check import progress |
| GET | `/desktop/jsonl/import/log` | `limit` (default 50) | log entries[] | Get import history |

### Export Operations

| Method | Path | Params | Returns | Description |
|--------|------|--------|---------|-------------|
| POST | `/desktop/jsonl/export/table` | body: `{table_name, output_path, batch_id?, version?, clean?, limit?}` | export result | Export a table to JSONL |
| POST | `/desktop/jsonl/export/all` | body: `{output_dir, batch_id?, version?, clean?}` | export results[] | Export all tables |

### Utility

| Method | Path | Params | Returns | Description |
|--------|------|--------|---------|-------------|
| GET | `/desktop/jsonl/tables` | — | `{tables: [{table, rows}]}` | List all import tables with row counts |

---

## Application Routes (`zolai/api/server.py`)

### Health & Monitoring

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| GET | `/health` | — | `status`, `version`, `data_root` | `testConnection()` |
| GET | `/monitor/health` | — | health dict | `refreshMonitor()` |
| GET | `/monitor/coverage` | — | coverage dict | `refreshCoverage()` |
| GET | `/monitor/audit` | `limit` (default 50) | audit entries | `refreshAudit()` |

### Chat (Zolai-aware)

| Method | Path | Params | Returns | UI fn |
|--------|------|--------|---------|-------|
| POST | `/chat/zolai` | body: `{message}` | `zolai_response`, `english_gloss?`, `vocabulary?`, `zvs_compliant?`, `context_source?` | Zolai chat |
| GET | `/chat/models` | — | `models[]` | List models |
| POST | `/chat/chat` | body: `ChatRequest` | `reply` | Generic chat |
| POST | `/chat/chat/stream` | body: `ChatStreamRequest` | SSE stream | Streaming chat |
| GET | `/chat` | `q`, `model` (default) | simple chat response | Simple chat |

---

## Identity Routes (`/api/v1/auth/...`)

Username + password sign-in, beside — never in front of — API-key auth.
Machine clients (`X-API-Key` / `Authorization: Bearer zolai_sk_*`) are
unaffected; an `X-API-Key` wins outright when both credentials are presented.
All three routes are in `rbac.PUBLIC_ROUTES`, so `ZOLAI_API_AUTH=enforce`
never 401s sign-in itself. Responses use the FastAPI `{"detail": {...}}`
envelope, like every other `/api/v1` error.

| Method | Path | Params | Returns | Notes |
|--------|------|--------|---------|-------|
| GET | `/api/v1/auth/me` | header: any credential | `{role, key_prefix, scopes, mode, auth_source, username?, display_name?, user_id?, expires_at?}` | Public, never 401. `role`/`key_prefix`/`scopes`/`mode` are unchanged; `auth_source` ∈ `anonymous｜api_key｜session`. The `username`/`display_name`/`user_id`/`expires_at` keys appear **only** for a session. |
| POST | `/api/v1/auth/login` | body: `{username, password}` | `{token, token_type: "bearer", expires_at, user: {id, username, display_name, role, scopes}}` | Public. `token` is `zolai_ss_*` and is returned **once**. 422 on a bad username/password shape. |
| POST | `/api/v1/auth/logout` | header: `Authorization: Bearer zolai_ss_*` | `{revoked: bool}` | Public. Always 200 (idempotent); revokes **only** the presented session and ignores a presented API key. |

### Login / logout error bodies

| Status | Body | When |
|--------|------|------|
| 401 | `{"detail": {"error": "invalid_credentials"}}` | Unknown user, wrong password **or** disabled account — one shape, no reason, no username echo (no enumeration) |
| 422 | FastAPI validation | `username` outside 1–64 chars of `[a-z0-9._-]`, empty/absent `password`, or missing field |
| 429 | `{"detail": {"error": "rate_limited", "scope": "login", "limit_rpm": N, "retry_after_s": N}}` + `Retry-After` | Per-IP (`ZOLAI_LOGIN_RATE_LIMIT_RPM`, default 5/min) or per-username (`ZOLAI_LOGIN_RATE_LIMIT_USER_RPM`, default 10/min). Checked **before** any argon2 work. |
| 404 | `{"detail": {"error": "session_auth_disabled"}}` | `ZOLAI_AUTH_SESSIONS=off` — the rollback posture, on both routes |

### Scope sets (strict subsets of the frozen 33 actions — no `*`)

| Role | Scopes |
|------|--------|
| `member` | `dataset:read`, `rag:read`, `catalog:read`, `agent:read`, `agent:run` |
| `admin` | member + `apikey:manage`, `settings:read`, `settings:write`, `user:manage`, `role:manage` |

An unknown `users.role` degrades to `member` (never raises). An admin session
therefore reaches `/api/v1/admin/*` with `apikey:manage` and can mint keys.

### Storage and lifetime

- `users.password_hash` — argon2id PHC string (`$argon2id$v=19$m=65536,t=3,p=4$`); a plaintext password is never stored, logged, returned or audited.
- `sessions.token_hash` — SHA-256 hex digest of `zolai_ss_<token_urlsafe(32)>`; the plaintext token exists only in the login response.
- TTL `ZOLAI_SESSION_TTL_HOURS` (default 12); `last_used_at` is a throttled sliding mark.
- Revocation is a `revoked_at` stamp, applied by logout, by `zolai user
  revoke-sessions`, and by any of `disable` / password change for that account.
  Re-enabling an account never resurrects a revoked session.
- Every login, logout, create, disable, enable, password change and session
  revocation appends a `data_audit_log` row carrying usernames/roles/counts
  only — never a password, hash or token.

### Config

| Env var | Default | Purpose |
|---------|---------|---------|
| `ZOLAI_AUTH_SESSIONS` | `on` | `off` → login/logout 404, middleware ignores `zolai_ss_*` |
| `ZOLAI_SESSION_TTL_HOURS` | `12` | Session lifetime in hours |
| `ZOLAI_LOGIN_RATE_LIMIT_RPM` | `5` | Login attempts/minute per IP |
| `ZOLAI_LOGIN_RATE_LIMIT_USER_RPM` | `10` | Login attempts/minute per username |

Accounts are created only by the CLI (`zolai user create|list|disable|enable|password|revoke-sessions`);
there is no self-registration route and no default password.

---

## Frontend API Helper

All calls go through one helper so the base URL always carries the prefix:

```js
async function api(u){ const r = await fetch(API+u); return await r.json(); }
```

Non-GET calls use `fetch(API+path, {method: 'POST'|'PUT'|'DELETE', ...})`.
There is **no** raw `fetch('/...')` without the `API` prefix.

---

## Contract Change Process

1. Add/change the row here.
2. Update `DESKTOP_CONTRACT` in `tests/test_desktop_contract.py`.
3. Update `index.html` to call the contract path via `api()`.
4. Run `python -m pytest tests/test_desktop_contract.py` — must pass.

---

## Type Definitions (TypeScript)

```typescript
// Core response types
interface StatsResponse {
  total_tables: number;
  total_rows: number;
  database_size_mb: number;
  dictionary: { total: number; coverage_myanmar_pct: number };
  bible: { total_verses: number };
  training: {
    translation_pairs: number;
    training_exercises: number;
    vocabulary_entries: number;
    phrases: number;
    grammar_patterns: number;
    proverbs: number;
    word_alignments: number;
    word_usage_profiles: number;
  };
  provenance: { audit_log_entries: number };
  table_details: Record<string, number>;
}

interface HealthResponse {
  status: string;
  version: string;
  data_root: string;
}

interface MonitorHealthResponse {
  database: { path: string; size_mb: number; tables: number; rows: number };
  wal_mode: boolean;
  busy_timeout: number;
  connection_pool: { size: number; checked_out: number };
}

interface AuditEntry {
  id: number;
  table_name: string;
  row_id: number;
  field: string;
  old_value: string | null;
  new_value: string | null;
  changed_at: string;
  reason: string;
  // Derived fields for UI
  action: "created" | "updated" | "deleted";
  entity: number;
  detail: string;
}

interface ChatZolaiResponse {
  zolai_response: string;
  english_gloss?: string;
  vocabulary?: string[];
  zvs_compliant?: boolean;
  context_source?: string;
}

// JSONL Pipeline types
interface ImportResult {
  batch_id: string;
  table: string;
  file: string;
  rows_imported: number;
  sha256: string;
  status: "completed" | "failed";
  error?: string;
}

interface ExportResult {
  table: string;
  output: string;
  rows_written: number;
  batch_id?: string;
  clean: boolean;
}
```

---

## Contract Change Process

1. Add/change the row here.
2. Update `DESKTOP_CONTRACT` in `tests/test_desktop_contract.py`.
3. Update `index.html` to call the contract path via `api()`.
4. Run `python -m pytest tests/test_desktop_contract.py` — must pass.
