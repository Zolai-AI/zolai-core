# Scripts

Data collection, processing, training, and maintenance scripts (~250 total).

**Last Updated:** 2026-09-05

## Directory Structure

| Directory | Files | Purpose |
|-----------|-------|---------|
| `bible/` | 21 | Bible corpus fetching, building, vocab extraction |
| `cleaner/` | 2 | Master dataset cleaning & deduplication |
| `crawlers/` | 8 | Web scrapers — ZomiDaily, Tongsan, RVAsia, TongDot |
| `data/` | 2 | Data audit & pull tools |
| `data_pipeline/` | 59 | Dataset merging, deduplication, versioning, building |
| `deploy/` | 8 | Orchestration, system integration, Kaggle deployment |
| `dev/` | 3 | Test and debugging scripts |
| `dictionary/` | 16 | Dictionary building, enrichment, search, kinship terms |
| `kg/` | 6 | Knowledge graph pipeline |
| `learning/` | 28 | AI learning systems, continuous improvement, expert systems |
| `maintenance/` | 42 | Quality checks, validation, OCR, text filters, analytics |
| `mind/` | 2 | NER and cognitive processing |
| `pipelines/` | 9 | End-to-end pipeline orchestration (USX, linguistics) |
| `server/` | 10 | FastAPI server, data access, caching, CLI |
| `synthesis/` | 2 | Instruction synthesis for fine-tuning |
| `training/` | 21 | Training data export, LoRA merging, Kaggle training |
| `ui/` | 9 | Chat server, menu, routing agent, GTK UI |
| `wiki/` | 15 | Wiki audit, sentence fixing, text refinement |
| `zvs/` | 1 | ZVS 2018 compliance scanner |
| **root** | **10** | Validate/entry scripts (see below) |

## Root Scripts (kept at scripts/)

| Script | Purpose |
|--------|---------|
| `sync-db-from-server.sh` | **POST-DEPLOY:** pull `data/zolai.db` from pcore-server (server → local) |
| `sync-db-to-server.sh` | **PRE-DEPLOY / local data work:** push local `data/zolai.db` to pcore-server (local → server) |
| `gemini_webapi_setup.py` | Gemini WebAPI setup |
| `local_translation_validator.py` | Local translation validation |
| `translation_validator.py` | Translation quality validator |
| `validate_tech_translations_gemini.py` | Tech translation validation via Gemini |
| `validate_zolai_auto_import.py` | Auto-import validation |
| `validate_zolai_gemini_webapi.py` | Gemini WebAPI validation |
| `validate_zolai_official_api.py` | Official API validation |
| `validate_zolai_webapi_fixed.py` | Fixed WebAPI validation |
| `zolai_gemini_tool.py` | Gemini tool integration |
| `zvs_api.py` | ZVS API client |

## DB sync — server ↔ local on every update

**Bidirectional, one direction per update** (never merge automatically — pick the side that
is authoritative for that change):

| Update kind | Command | Direction |
|---|---|---|
| pcore-server deploy / migration / server-side data work | `scripts/sync-db-from-server.sh` | server → local |
| local data work done (clean, import, annotation, review) | `scripts/sync-db-to-server.sh` | local → server |

```bash
scripts/sync-db-from-server.sh --dry-run   # preview, no changes
scripts/sync-db-from-server.sh             # pull server → <workspace>/data/zolai.db
scripts/sync-db-to-server.sh --dry-run     # preview, no changes
scripts/sync-db-to-server.sh               # push <workspace>/data/zolai.db → server (+ container restart + /health gate)
```

Pull safety chain: server-side `sqlite3 .backup` snapshot (never raw-copy a hot WAL file) →
rsync to a temp file → `PRAGMA integrity_check` → back up the local DB first
(`../scripts/backup-zolai.sh`) → atomic replace (stale `-wal`/`-shm` dropped) → remove the
remote snapshot; sizes echoed, `set -euo pipefail`.

Push safety chain: local `sqlite3 .backup` snapshot → integrity check → rsync up → **stop the
api container** (never swap a file the process holds open) → back up the server DB
(`data/backups/zolai-<ts>.db.gz`) → remote integrity check → atomic replace + drop stale
`-wal`/`-shm` → start container → **/health 200 gate** (30s).
Full runbook: `docs/governance/backup-strategy.md`; deploy step: `docs/planning/AI_AGENTS_RBAC_PLAN.md` §F.

## Key Commands

```bash
# Audit data quality
python scripts/data/data_audit.py
python scripts/data/data_audit.py --dir parallel
python scripts/data/data_audit.py --file dict_unified_v1.jsonl

# Pull datasets from HuggingFace
python scripts/data/pull.py --list
python scripts/data/pull.py zolai-tedim-v3

# Build Bible parallel corpus
python scripts/bible/build_parallel_bible.py

# Build dictionary
python scripts/dictionary/build_enriched_dictionary.py
python scripts/dictionary/search_dictionary.py <word>

# Synthesize training instructions
python scripts/training/synthesize_instructions_v6.py

# Build LLM training dataset
python scripts/data_pipeline/build_llm_dataset_v3.py

# Run full pipeline
python scripts/pipelines/run.py

# ZVS compliance scan
python scripts/zvs/scan_content.py

# Knowledge graph smoke test
python scripts/kg/smoke_test.py
```
