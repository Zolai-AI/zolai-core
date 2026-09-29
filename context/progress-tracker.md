# Progress Tracker

## 2026-09-13 (Session — Phase A: Foundation Analysis Module)

### Foundation Module Created
- Created `zolai/foundation/` package with:
  - `analysis.py` — `FoundationAnalyzer` orchestrating existing engines (tokenizer, syllable, POS, morphology)
  - `evidence.py` — `Candidate`, `Evidence`, `Confidence` dataclasses + `Verifier` ABC
  - `consensus.py` — Pure-Python majority-vote consensus (no network calls)
  - `verifiers.py` — Default `NullVerifier` + `DictionaryVerifier` implementations
- Gold evaluation set seeded:
  - `data/gold/words.jsonl` — 100 word samples
  - `data/gold/sentences.jsonl` — 50 sentence samples
  - `data/gold/paragraphs.jsonl` — 20 paragraph samples
- `eval/gold_metrics.py` computes accuracy vs gold
- CLI commands added: `zolai foundation analyze` + `zolai foundation gold-eval`
- All 192 ZVS tests passing, ruff clean

**Phase A: ✅ COMPLETE**

---

## 2026-09-14 (Session — Phase B: Canonical Data Layer)

### Database Migration
- Migration `027_foundation_tables.py` creating 13 foundation/canonical tables:
  - **Raw Layer**: `foundation_raw_corpus`, `foundation_raw_llm`
  - **Staging Layer**: `foundation_staging_words`, `foundation_staging_sentences`, `foundation_staging_paragraphs`, `foundation_staging_evidence`
  - **Canonical Layer**: `canonical_words`, `canonical_sentences`, `canonical_paragraphs`
  - **Evidence/Consensus**: `foundation_evidence`, `foundation_verifications`, `foundation_consensus`
  - **Meta**: `foundation_batches`, `foundation_review_queue`, `foundation_metrics`

### Repository Layer
- `zolai/data/repositories/foundation.py` — 13 repositories for all foundation entities
- Full CRUD + batch operations + provenance tracking

### ETL Pipeline
- `zolai/foundation/etl.py` — Raw → Staging → Canonical pipeline
- Configurable batch sizes, concurrency, error handling
- Provenance tracking with SHA256 hashes

**Phase B: ✅ COMPLETE**

---

## 2026-09-15 (Session — Phase C: Adaptive Verification Loop)

### Gemini Client
- `zolai/foundation/gemini_client.py` — Async Gemini API client for batch verification
- Rate limiting, retry logic, cost tracking

### Verification Implementations
- `zolai/foundation/verifiers.py` expanded:
  - `GeminiVerifier` — LLM-based verification with RAG context
  - `DictionaryVerifier` — Cross-reference with dictionary tables
  - `BibleVerifier` — Bible verse attestation
  - `GrammarVerifier` — ZVS 2018 + SOV compliance

### Batch Verification Runner
- `zolai/foundation/verification_runner.py` — Configurable batch size, concurrency
- Adaptive threshold: confidence gate → human review → auto-accept
- Evidence gating: no LLM output reaches canonical without ≥2 independent sources

### Regression Test Suite
- `tests/test_foundation_regression.py` — 45 tests covering:
  - ZVS compliance (8 forbidden forms)
  - Grammar patterns (SOV, negation, questions)
  - Syllable accuracy
  - Tone handling

**Phase C: ✅ COMPLETE**

---

## 2026-09-16 (Session — Phase D: Human Review UI + Production)

### Human Review UI
- `zolai/api/review_router.py` — FastAPI endpoints for review queue
- `/review/queue` — List pending items with filters
- `/review/approve` — Approve and promote to canonical
- `/review/reject` — Reject with reason
- `/review/batch` — Batch approve/reject

### Cost Tracking
- `zolai/foundation/cost_tracker.py` — Per-model, per-operation cost tracking
- `foundation_cost` table for historical analysis
- Dashboard endpoints for cost analytics

### Production Docker
- `docker-compose.prod.yml` — Production deployment config
- Multi-stage Dockerfile for minimal image size
- Health checks, restart policies, volume mounts

### API Enhancements
- `/foundation/analyze` — Full sentence/word analysis
- `/foundation/consensus` — Get consensus for fact
- `/foundation/evidence` — List evidence for fact
- `/foundation/batch` — Trigger batch verification

**Phase D: ✅ COMPLETE**

---

## 2026-09-17 (Session — Phase E: Complete Integration + Documentation)

### Integration
- All foundation modules wired into main CLI
- ETL pipeline tested end-to-end with real data
- Verification loop validated with 1,000+ candidates

### Documentation
- Complete API reference in `docs/api-contract.md`
- Foundation integration guide (`docs/INTEGRATION_GUIDE.md`)
- Scripts documentation (`docs/SCRIPTS_GUIDE.md`)
- Updated all context files (architecture, progress, overview)

### Bug Fixes
- Fixed table browser endpoint (`/desktop/table-data`)
- Fixed chat endpoints (`/chat/zolai`, `/chat/gemini`)
- Fixed proficiency CLI path resolution
- Fixed frontend TypeScript errors

### Pipeline Execution
- Full ETL pipeline executed on Bible + dictionary data
- 50,000+ words processed through verification loop
- 10,000+ sentences verified and promoted to canonical

**Phase E: ✅ COMPLETE**

---

## Data Status (Post Phase E)

| Table | Rows | Status |
|-------|------|--------|
| dictionary (ZO→EN) | 103,303 | ✅ |
| dictionary_en_zo (EN→ZO) | 113,750 | ✅ |
| bible_verses | 62,751 | ✅ |
| canonical_words | 50,000+ | ✅ |
| canonical_sentences | 10,000+ | ✅ |
| foundation_evidence | 150,000+ | ✅ |
| foundation_consensus | 50,000+ | ✅ |

## Live URLs
- Landing: https://zolai.space/ ✅
- MCP: https://mcp.zolai.space/mcp ✅
- API: http://localhost:8000 ✅

## Git Status
- zolai-core: All phases committed to `main`
- Database: Migrated to include foundation tables
- Docker: Production-ready configuration
---

## 2026-09-17 (Session — Desktop Bundling + Gemini WebAPI Integration)

### Desktop Bundling
- Created `zolai/api/desktop_app.py` — Slim FastAPI entrypoint for Tauri sidecar (no ML deps)
- Created `zolai/llm/gemini/cookies.py` — Browser cookie integration for Gemini WebAPI
- Created `zolai/plugins/__init__.py` — Plugin system with auto-discovery
- Created `zolai/plugins/gemini_plugin.py` — Gemini WebAPI plugin with cookie + API key auth
- Updated `pyproject.toml` — Added `desktop`, `webapi`, `server` optional dependency groups
- Created `scripts/desktop/build_sidecar.sh` — PyInstaller build script for desktop binary
- Created `server.py` — Minimal standalone server entrypoint

### Tauri Integration
- Updated `src-tauri/tauri.conf.json` — Added externalBin for sidecar, resources config
- Updated `src-tauri/Cargo.toml` — Added tauri-plugin-shell, reqwest, tokio, log, libc
- Created `src-tauri/src/sidecar.rs` — SidecarManager with start/stop/health_check
- Updated `src-tauri/src/main.rs` — Auto-starts sidecar on app launch, registers api_status/api_start/api_stop commands

### Architecture
- Desktop mode: PyInstaller-bundled Python API binary as Tauri sidecar
- Server mode: Minimal `python server.py` entrypoint
- Plugin system: Abstract base class + auto-discovery via pkgutil
- Gemini WebAPI: Browser cookie extraction + API key fallback
- Sidecar lifecycle: Start on launch, health check polling, graceful shutdown on close

### Git Commits
- zolai-core: `239cb8c` feat(desktop): add Tauri sidecar integration, plugin system, and server mode
- zolai-core: `efc9263` chore(desktop): remove unused imports in desktop_app.py
- zolai-tauri: `c08c383` feat(desktop): add Python API sidecar manager and plugin support
- zolai-tauri: `efb2474` fix(desktop): cleanup sidecar unused import and null stdio

**Desktop Bundling: ✅ COMPLETE**

---

## 2026-09-17 (Session — Multi-Provider LLM + Learning Engine)

### Multi-Provider LLM Abstraction
- Created `zolai/llm/providers/` package with 6 providers:
  - `ollama.py` — Local Ollama HTTP client (no API key needed)
  - `gemini.py` — Gemini API (API key + WebAPI)
  - `openai.py` — OpenAI SDK wrapper
  - `openrouter.py` — OpenRouter via OpenAI-compatible endpoint
  - `webapi.py` — Gemini WebAPI (cookie-based)
  - `base.py` — Abstract LLMProvider class
- `ProviderRegistry` with priority-based selection
- `FallbackChain` for automatic provider failover with rule-based fallback

### Standalone Mode
- Created `zolai/llm/fallback.py` — FallbackChain with rule-based degradation
- Created `zolai/offline/rule_engine.py` — Rule-based translation using SQLite dictionary data
- App works fully offline with Ollama + rule engine fallback

### Learning Engine
- Created `zolai/learning/` package with 6 modules:
  - `data_manager.py` — Import/export JSONL/CSV datasets
  - `grammar_editor.py` — Grammar rule CRUD (aligned with actual DB schema)
  - `dictionary_manager.py` — Dictionary CRUD with attestation check
  - `translation.py` — EN↔ZO translation with dictionary-first lookup
  - `progress.py` — Spaced repetition, CEFR level tracking (aligned with actual DB schema)
  - `trainer.py` — User corrections → improved context prompts (RAG-first)

### Configuration
- Created `zolai/core/llm_providers.yaml` — Provider defaults with priority list
- Created `zolai/core/settings.py` — User settings management (data/settings.json)

### API Integration
- Added `/providers` endpoint (list available providers)
- Added `/settings` GET/POST endpoints
- Added `/learning/grammar`, `/learning/dictionary`, `/learning/translation` endpoints
- Updated `desktop_app.py` to use ProviderRegistry

### Tauri Updates
- Updated `tauri.conf.json` — Mobile targets (iOS, Android)
- Created `frontend/src/lib/providers.ts` — Provider selection logic
- Created `frontend/src/lib/settings.ts` — Settings management
- Created `frontend/src/components/learning/` — Learning UI components:
  - `learning-panel.tsx` — Main learning container
  - `grammar-editor.tsx` — Grammar rule editor
  - `dictionary-manager.tsx` — Dictionary management
  - `translation-trainer.tsx` — Translation practice
  - `progress-dashboard.tsx` — Progress tracking

### Bug Fixes
- Fixed `grammar_editor.py` schema mismatch (pattern_type → function, removed non-existent columns)
- Fixed `progress.py` table references (vocab → vocabulary, aligned columns)
- Fixed `server.py` duplicate ChatMessage class and missing Any import

### Git Commits
- zolai-core: `3f83705` feat(llm): add multi-provider abstraction with 6 providers and fallback chain
- zolai-core: `305cb7c` fix(learning): align grammar_editor and progress with actual DB schema
- zolai-tauri: `11eb5b8` feat(tauri): add mobile targets and learning UI components

**Multi-Provider + Learning Engine: ✅ COMPLETE**

---

## 2026-09-17 (Session — Enhanced Learning Engine + Online Search)

### Learning Engine Enhancements
- Enhanced `GrammarEditor` with `validate_pattern` method for pattern validation
- Enhanced `TranslationEngine` with `translate_batch` and `get_translation_stats` methods
- Enhanced `ProgressTracker` with `get_statistics` method for learning analytics
- Fixed provider auto-registration on import

### Online Search Module
- Created `zolai/learning/online_search.py` — `OnlineSearch` class with:
  - `search_vocabulary()` — Search vocabulary in database
  - `search_grammar()` — Search grammar patterns in database
  - `search_bible()` — Search Bible verses for context
  - `search_all()` — Search all resources simultaneously
  - Caching for performance

### API Endpoints Added
- `POST /learning/grammar/validate` — Validate grammar patterns
- `GET /learning/statistics` — Get learning statistics
- `POST /learning/translate/batch` — Batch translation
- `GET /learning/search` — Search all learning resources
- `GET /learning/search/vocabulary` — Search vocabulary
- `GET /learning/search/grammar` — Search grammar patterns
- `GET /learning/search/bible` — Search Bible verses

### Bug Fixes
- Fixed `translate_batch` method signature (removed max_results parameter)
- Fixed server.py endpoints to use correct parameter names

### Git Commits
- `8b7b00e` feat(learning): enhance learning engine with online search, batch translation, and statistics

**Enhanced Learning Engine: ✅ COMPLETE**

---

## 2026-09-29 (Session — Observability + ACID hardening wave)

**Goal:** Prometheus/Grafana monitoring stack + DB ACID hardening, 6 atomic
commits per `/tmp/opencode/plan-monitoring-acid.md`.

### Commits

| # | SHA | Message |
|---|-----|---------|
| — | `86bdff2` | feat(lexicon): add canonical POS + provenance columns (L1.3) *(pre-existing work, committed first)* |
| — | `44ec6e9` | chore(tests): make the test suite ruff-clean *(pre-existing work)* |
| 1 | `afae5ad` | feat(db): per-connection ACID pragmas, sqlite pooling, BEGIN IMMEDIATE writer + busy retry |
| 2 | `9b966d9` | feat(db): monitoring/eval/integrity tables with FK, checks, dedup unique index |
| 3 | `951c179` | feat(metrics): prometheus instrumentation (http, db, analysis, business, eval) |
| 4 | `0b2eb4b` | feat(api): metrics REST endpoints — exposition, summary, health, eval, alerts, info, annotations |
| 5 | `41f50e6` | feat(ops): prometheus + grafana stack, dashboards, alert rules, parity test |
| 6 | `cae743f` | feat(cli): add `zolai db integrity` subcommand |
| 7 | *this commit* | docs(monitoring): runbook, metric inventory, deferred-FK rationale |

### What shipped

- **ACID:** `foreign_keys`/`busy_timeout`/`journal_mode=WAL`/`synchronous=NORMAL`
  on **every** pooled connection (connect-event listener), `QueuePool`
  10/20/30, `immediate_transaction()` + `write_session()` with busy backoff.
  `zolai/data/integrity.py`: fast `foreign_key_check`, `startup_guard`
  (clean → `ON`, violations → `deferred`), `full_integrity_check` (explicit
  only), runs recorded in `db_integrity_runs`.
- **Schema (additive only):** `monitoring_annotations`, `eval_runs`
  (`set_name REFERENCES eval_sets`), `db_integrity_runs`, plus
  `ux_fraw_content_hash` unique index. Live store: `foreign_key_check` → 0
  violations, 0 `content_hash` duplicates → safe to enforce.
- **Instrumentation:** `zolai/monitoring/` — HTTP middleware (route templates
  only), DB listeners (operation class only), analysis/translation/eval
  hooks, 300s background sampler, local alert evaluator, annotation/health
  store. `prometheus-client==0.26.0` pinned.
- **API:** `/metrics` + 8 `/api/metrics/*` endpoints registered before the
  catch-all; `/health` gains `uptime_s` (additive).
- **Stack:** `docker-compose.monitoring.yml` (Prometheus 3.15.0, Grafana
  13.2.3), `ops/` provisioning (datasource, 3 dashboards, 3 alert rules,
  contact point `zolai-ops`, notification policies), webhook alert target →
  `/api/metrics/annotations`.
- **CLI:** `zolai db integrity [--full] [--json]` (exit 1 on violations).
- **Docs:** `docs/MONITORING.md` runbook incl. metric inventory, alert parity
  procedure, and the deferred-constraint rationale + 12-step table-rebuild path.

### Validation

- `ruff check zolai tests` → clean.
- Full suite: **1329 passed, 7 skipped, 0 failed** (636s).
- Smoke: `/metrics` serves `zolai_*` families; `/api/metrics/summary` 200;
  `zolai db integrity` → ok, 0 issues, 90ms, FK policy `on`.
- Compose: `config --quiet` OK; target **up**; Prometheus rules loaded (3);
  `promtool check rules` SUCCESS; Grafana 13.2.3 healthy; 3 dashboards in the
  Zolai folder; 3 provisioned alert rules + `zolai-ops` contact point +
  severity routes; annotation POST round-trip → id 8.
- Deviations: 8 commits instead of 6 (2 preliminary + CLI split out).

### Open / follow-ups

- Legacy-table `NOT NULL`/`CHECK`/`FK` constraints still deferred — rebuild
  path documented in `docs/MONITORING.md` §7, implementation behind a flag.
- `zolai/pos_tagger` POS-first wave (L1) continues: POS_SPEC, lexicon POS
  annotation, baseline CRF tagger.
- Speaker/advisor recruitment (blocked on founder outreach).
