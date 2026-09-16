# Zolai Core

Python package + FastAPI + CLI exposing the Zolai language toolkit: translation
(EN⇄Tedim Zolai), dictionary, chat, and RAG retrieval. The runtime dependency for
the web and desktop apps.

## Goals
1. Serve a stable REST API (translation, dictionary, chat) consumed by web + tauri.
2. Provide the `zolai` CLI for corpus/dictionary/training ops.
3. Keep all secrets in `.env`; no hardcoded keys.
4. Enforce ZVS 2018 orthography on all output.
5. Build the Foundation Intelligence Engine for canonical knowledge management.

## Boundaries
- Owns: `zolai/`, pyproject, tests, config, Docker, agents, skills.
- Consumes: wiki (RAG feed), datasets (HF/Kaggle).

## Current Capabilities

### Core NLP Engine
| Feature | Description | Status |
|---------|-------------|--------|
| **Syllable Segmentation** | SylBreak4All (rule + CRF, 99.92% F1) | ✅ Complete |
| **POS Tagging** | 13 categories, dictionary-backed | ✅ Complete |
| **Morphological Analysis** | Prefix/suffix stripping, compound detection | ✅ Complete |
| **Word Embeddings** | fastText skipgram/CBOW | ✅ Complete |
| **Machine Translation** | EN↔ZO with dictionary fallback + Gemini | ✅ Complete |
| **Named Entity Recognition** | 6 entity types (PER, LOC, ORG, DATE, NUM, BOOK) | ✅ Complete |
| **Text Classification** | 8 topics (religion, education, news, etc.) | ✅ Complete |
| **Question Answering** | Bible-context aware | ✅ Complete |
| **Text Summarization** | Extractive + AI | ✅ Complete |

### Foundation Intelligence Engine
| Component | Description | Status |
|-----------|-------------|--------|
| **FoundationAnalyzer** | Orchestrates all NLP engines | ✅ Complete |
| **Evidence Collection** | Tiered evidence (T1-T5) with provenance | ✅ Complete |
| **Consensus Building** | Majority vote + evidence weighting | ✅ Complete |
| **ETL Pipeline** | Raw → Staging → Canonical | ✅ Complete |
| **Verification Loop** | Adaptive threshold + human review | ✅ Complete |
| **Review UI** | Human review queue with approve/reject | ✅ Complete |
| **Cost Tracking** | Per-model, per-operation cost tracking | ✅ Complete |

### API Endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/analyze` | POST | Full sentence analysis |
| `/dictionary/search` | POST | Dictionary lookup |
| `/dictionary/add` | POST | Add dictionary entry |
| `/bible/search` | GET | Bible verse search |
| `/chat/zolai` | POST | Zolai chat (Ollama) |
| `/chat/gemini` | POST | Gemini chat |
| `/foundation/analyze` | POST | Foundation analysis |
| `/foundation/consensus` | GET | Get consensus for fact |
| `/foundation/evidence` | GET | List evidence |
| `/foundation/batch` | POST | Trigger batch verification |
| `/review/queue` | GET | Human review queue |
| `/review/approve` | POST | Approve candidate |
| `/review/reject` | POST | Reject candidate |
| `/desktop/table-data` | POST | Table browser data |
| `/desktop/ai/models` | GET | List AI models |

### CLI Commands
```bash
# Foundation Engine
zolai foundation analyze <word|sentence|paragraph>
zolai foundation gold-eval
zolai foundation etl [--batch-size=1000]
zolai foundation verify [--batch-size=100] [--concurrency=4]
zolai foundation review list
zolai foundation review approve <id>
zolai foundation review reject <id>

# Core Operations
zolai api serve --host 0.0.0.0 --port 8000
zolai dictionary search <word>
zolai bible search <query>
zolai analyze sentence <text>
zolai syllable segment <word>
zolai pos tag <text>
zolai morphology analyze <word>
```

### Database Schema Summary

**Canonical Data Store**: `data/zolai.db` (SQLite WAL, ~1.2GB, 72+ tables, ~3.1M rows)

#### Foundation Tables (13 new tables)
| Layer | Tables | Purpose |
|-------|--------|---------|
| Raw | `foundation_raw_corpus`, `foundation_raw_llm` | Immutable source data |
| Staging | `foundation_staging_words/sentences/paragraphs/evidence` | Cleaned, normalized |
| Canonical | `canonical_words`, `canonical_sentences`, `canonical_paragraphs` | Verified, serving |
| Evidence | `foundation_evidence`, `foundation_verifications`, `foundation_consensus` | Evidence tracking |
| Meta | `foundation_batches`, `foundation_review_queue`, `foundation_metrics` | Operations |

#### Existing Core Tables
| Table | Rows | Purpose |
|-------|------|---------|
| dictionary | 103,303 | ZO→EN master |
| dictionary_en_zo | 113,750 | EN→ZO master |
| bible_verses | 62,751 | Parallel EN/ZO/MY |
| grammar_patterns | 5,547 | Sentence patterns |
| vocab | 94,458 | Vocabulary index |
| translations | 212,754 | EN↔ZO pairs |
| word_alignments | 385,120 | Word-level alignment |
| syllable_data | 189,554 | Syllable segmentation |

## Quick Start

### Installation
```bash
pip install -e ".[dev]"
```

### Run API Server
```bash
zolai api serve --host 0.0.0.0 --port 8000
```

### Foundation Engine
```bash
# Analyze a word
zolai foundation analyze word pasian

# Run ETL pipeline
zolai foundation etl --batch-size 1000

# Run verification
zolai foundation verify --batch-size 100

# View review queue
zolai foundation review list
```

### Python API
```python
from zolai.foundation import FoundationAnalyzer
from zolai.foundation.etl import ETLPipeline
from zolai.foundation.verification_runner import VerificationRunner

# Analyze text
analyzer = FoundationAnalyzer()
result = analyzer.analyze_sentence("Pasian in vantung a piangsak hi.")

# Run ETL
pipeline = ETLPipeline(batch_size=1000)
pipeline.run_full_pipeline()

# Run verification
runner = VerificationRunner(batch_size=100)
runner.run_batch_verification()
```

## Development

### Testing
```bash
pytest tests/
```

### Linting
```bash
ruff check .
ruff format .
```

### Docker
```bash
# Development
docker-compose up -d

# Production
docker-compose -f docker-compose.prod.yml up -d
```

## Documentation

- **API Contract**: `docs/api-contract.md`
- **Foundation Integration**: `docs/INTEGRATION_GUIDE.md`
- **Scripts Guide**: `docs/SCRIPTS_GUIDE.md`
- **Foundation Roadmap**: `docs/foundation/00-FOUNDATION_ROADMAP.md`
- **Foundation Principles**: `docs/foundation/01-PRINCIPLES.md`
- **Target Schema**: `docs/foundation/02-TARGET_SCHEMA.md`

## License

MIT — Open source for Zomi language preservation.