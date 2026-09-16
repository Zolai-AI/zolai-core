# Zolai Core — Architecture

## Overview

Zolai Core is a Python toolkit + RAG Knowledge Brain for Tedim Zolai (ZVS 2018). It provides translation, dictionary, chat, and RAG retrieval services consumed by web and desktop applications.

## Foundation Intelligence Engine

The Foundation Intelligence Engine is the core data processing pipeline that transforms raw linguistic data into verified, canonical knowledge.

### Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                    FOUNDATION INTELLIGENCE ENGINE                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│  │   SOURCE    │───▶│  EVIDENCE   │───▶│   ENGINES   │        │
│  │   LAYER     │    │   LAYER     │    │   LAYER     │        │
│  ├─────────────┤    ├─────────────┤    ├─────────────┤        │
│  │ Bible USX   │    │ Tiered      │    │ Foundation  │        │
│  │ Web Crawl   │    │ Evidence    │    │ Analyzer    │        │
│  │ PDF Extract │    │ Collection  │    │ (POS, Morph,│        │
│  │ Dict JSONL  │    │ Provenance  │    │  Syllable)  │        │
│  └─────────────┘    └─────────────┘    └─────────────┘        │
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│  │ VERIFICATION│───▶│ CONSENSUS   │───▶│ CANONICAL   │        │
│  │ LOOP        │    │ BUILDING    │    │ LAYER       │        │
│  ├─────────────┤    ├─────────────┤    ├─────────────┤        │
│  │ Adaptive    │    │ Majority    │    │ Verified    │        │
│  │ Threshold   │    │ Vote +      │    │ Versioned   │        │
│  │ Human Review│    │ Evidence    │    │ Evidence-   │        │
│  │ Auto-Accept │    │ Weighting   │    │ Gated       │        │
│  └─────────────┘    └─────────────┘    └─────────────┘        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. FoundationAnalyzer (`zolai/foundation/analysis.py`)
Orchestrates existing engines (tokenizer, syllable, POS tagger, morphology) to produce comprehensive word/sentence/paragraph analyses.

```python
from zolai.foundation import FoundationAnalyzer

analyzer = FoundationAnalyzer()
word_analysis = analyzer.analyze_word("pasian")
sentence_analysis = analyzer.analyze_sentence("Pasian in vantung a piangsak hi.")
```

#### 2. ETL Pipeline (`zolai/foundation/etl.py`)
Transforms raw data through three layers:
- **Raw Layer**: Append-only, immutable, provenance-tracked
- **Staging Layer**: Cleaned, normalized, analyzed (transient)
- **Canonical Layer**: Verified, versioned, evidence-gated (serving reads)

```python
from zolai.foundation.etl import ETLPipeline

pipeline = ETLPipeline(batch_size=1000)
pipeline.run_full_pipeline()  # Raw → Staging → Canonical
```

#### 3. Verification Loop (`zolai/foundation/verification_runner.py`)
Adaptive verification with three paths:
- **Auto-Accept**: confidence ≥ 0.95 → promote directly
- **Batch Verify**: confidence ≥ 0.70 → majority vote + evidence weighting
- **Human Review**: confidence < 0.70 → review queue

```python
from zolai.foundation.verification_runner import VerificationRunner

runner = VerificationRunner(batch_size=100)
runner.run_batchVerification()
```

#### 4. Review UI (`zolai/api/review_router.py`)
FastAPI endpoints for human review of low-confidence candidates:
- `GET /review/queue` — List pending items
- `POST /review/approve` — Approve and promote
- `POST /review/reject` — Reject with reason
- `POST /review/batch` — Batch operations

### Data Flow

```
Raw Sources (Bible, Web, PDF, Dict)
       │
       ▼
┌──────────────────┐
│ foundation_raw_* │  (immutable, provenance-tracked)
└──────────────────┘
       │
       ▼
┌──────────────────────┐
│ foundation_staging_* │  (cleaned, normalized, analyzed)
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│ foundation_evidence  │  (tiered, provenance_hash, confidence)
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│ foundation_consensus │  (decision + method + confidence)
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│ canonical_words      │  ← SERVING LAYER
│ canonical_sentences  │  (read-only for API)
└──────────────────────┘
       │
       ▼
┌──────────────────┐
│  pcore-brain     │  (RAG context injection)
│  API (zolai)     │
└──────────────────┘
```

## System Architecture

### Runtime Flow
```
zolai-wiki (knowledge) → zolai-core (RAG retrieval + n-gram prediction) →
consumed by zolai-web (online) and zolai-tauri (offline, bundled Ollama/GGUF).
```

### API Stack
- **Framework**: FastAPI + Uvicorn
- **CLI**: Typer + Rich
- **Database**: SQLite (WAL mode) for dev, PostgreSQL for production
- **Cache**: Redis (optional)
- **Task Queue**: Celery (optional)

### Key Modules

| Module | Purpose |
|--------|---------|
| `zolai/api/` | FastAPI endpoints (REST API) |
| `zolai/foundation/` | Foundation Intelligence Engine |
| `zolai/data/` | Database layer (repositories, services) |
| `zolai/analyzer/` | Text analysis (word, sentence, paragraph) |
| `zolai/syllable/` | Syllable segmentation (SylBreak4All) |
| `zolai/pos_tagger/` | Part-of-speech tagging |
| `zolai/morphology/` | Morphological analysis |
| `zolai/embeddings/` | Word embeddings (fastText) |
| `zolai/mt/` | Machine translation (EN↔ZO) |
| `zolai/ner/` | Named entity recognition |
| `zolai/classifier/` | Text classification |
| `zolai/qa/` | Question answering |
| `zolai/summarizer/` | Text summarization |

### Database Schema

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

### Invariants

1. **No direct LLM → Canonical** — Every canonical fact requires ≥2 independent evidence sources
2. **Evidence Gating** — LLM outputs are candidates only; verification is mandatory
3. **Versioning** — All canonical tables carry `version`, `source_hash`, `verified_at`
4. **RAW → Staging → Canonical** — Clear separation; no shortcuts
5. **ZVS 2018 Compliance** — All output enforces orthography rules
6. **SOV + Ergative `in`** — All generated text satisfies grammar rules
7. **Secrets from env only** — Never hardcoded in code

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
```

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/analyze` | POST | Full sentence analysis |
| `/dictionary/search` | POST | Dictionary lookup |
| `/bible/search` | GET | Bible verse search |
| `/foundation/analyze` | POST | Foundation analysis |
| `/foundation/consensus` | GET | Get consensus for fact |
| `/foundation/evidence` | GET | List evidence |
| `/foundation/batch` | POST | Trigger batch verification |
| `/review/queue` | GET | Human review queue |
| `/review/approve` | POST | Approve candidate |
| `/review/reject` | POST | Reject candidate |

## Deployment

### Development
```bash
pip install -e ".[dev]"
zolai api serve --reload
```

### Production (Docker)
```bash
docker-compose -f docker-compose.prod.yml up -d
```

### Environment Variables
```bash
# Database
ZOLAI_PG_URL=postgresql://user:pass@localhost:5432/zolai

# API Keys
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key

# Foundation Engine
FOUNDATION_BATCH_SIZE=1000
FOUNDATION_CONCURRENCY=4
FOUNDATION_AUTO_THRESHOLD=0.95
FOUNDATION_REVIEW_THRESHOLD=0.70
```