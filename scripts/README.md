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
