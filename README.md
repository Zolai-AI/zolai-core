# zolai-core

Python toolkit + RAG Knowledge Brain for Tedim Zolai (ZVS 2018).

## Quick Start

```bash
pip install -e .
```

```python
from zolai.syllable import SyllableSegmenter
from zolai.pos_tagger import ZolaiPOSTagger
from zolai.morphology import ZolaiMorphology
from zolai.embeddings import ZolaiWordEmbeddings
from zolai.mt import ZolaiMT
from zolai.summarizer import ZolaiSummarizer
from zolai.qa import ZolaiQA
from zolai.ner import ZolaiNER
from zolai.classifier import ZolaiClassifier
from zolai.dependency import ZolaiDependency

# Syllable segmentation
seg = SyllableSegmenter()
seg.segment("vantung")  # ['van', 'tung']

# POS tagging
pos = ZolaiPOSTagger()
pos.tag("Pasian in vantung a piangsak hi")

# Morphological analysis
morph = ZolaiMorphology()
morph.analyze("piangsak")  # {'root': 'piang', 'suffix': 'sak', ...}

# Machine Translation
mt = ZolaiMT()
await mt.translate_en_zo("God created the earth")

# Question Answering
qa = ZolaiQA()
await qa.answer("Who created the earth?", "Pasian in vantung a piangsak hi")
```

## Features

### Syllable Segmentation (SylBreak4All)
- **Rule-based**: Deterministic onset-nucleus-coda (C)(C)V(C)
- **CRF-based**: Trained on 10K gold standard (99.92% F1)
- **Tone-aware**: 4-tone system (T1-T4) with 19 sandhi rules
- **Compound handling**: 200+ built-in Bible compounds

### POS Tagging
- 13 tag categories (NOUN, VERB, ADJ, ADV, PRON, DET, POST, CONJ, PART, NUM, INTJ, PUNCT, X)
- Dictionary-backed with Bible proper noun detection
- Confidence scoring

### Morphological Analysis
- Prefix/suffix stripping
- Compound detection (200+ built-in)
- Tone-dependent meaning disambiguation
- 65 high-frequency Bible roots + 7 particles

### Word Embeddings
- fastText skipgram/CBOW
- Negative sampling
- Cosine similarity + word analogies
- JSONL export

### NLP Pipeline
- **NER**: 6 entity types (PER, LOC, ORG, DATE, NUM, BOOK)
- **Classifier**: 8 topics (religion, education, news, story, grammar, song, proverb)
- **MT**: EN↔ZO with dictionary fallback + Gemini ensemble
- **Summarizer**: Extractive + AI
- **QA**: Bible-context aware
- **Dependency**: SOV-aware parser

### ZVS 2018 Validator
- Enforces orthography rules
- Bible-only historical exceptions
- 192 tests passing

## Database

All data lives in `data/zolai.db` (SQLite WAL, ~1.2GB, 72 tables, ~3.1M rows).

```python
from zolai.config import Config
from zolai.data import DatabaseManager

config = Config()
db = DatabaseManager(config)

# Search dictionary
results = db.search_dictionary("pasian")

# Search Bible
verses = db.search_bible("vantung")

# Get word usage
usage = db.get_word_usage("khem")
```

### Key Tables

| Table | Rows | Purpose |
|-------|------|---------|
| dictionary | 103,303 | Zolai→English |
| dictionary_en_zo | 113,750 | English→Zolai |
| bible_verses | 62,751 | Parallel EN/ZO/MY |
| grammar_patterns | 5,547 | Sentence patterns |
| vocab | 94,458 | Vocabulary index |
| syllable_data | 189,554 | Syllable segmentation |

Source corpora (Bible translations, TongDot/TongSan dictionaries, web-scraped corpus)
are processed into our own cleaned, ZVS-2018-aligned database at `data/zolai.db`. See
`data/CREDITS.md` for full attribution.

## API Server

```bash
zolai-api serve --host 0.0.0.0 --port 8000
```

Endpoints:
- `GET /health` — Health check
- `POST /analyze` — Full sentence analysis
- `POST /dictionary/search` — Dictionary lookup
- `GET /bible/search` — Bible verse search
- `POST /dictionary/add` — Add entry
- `POST /chat/zolai` — Zolai chat

## Tests

```bash
pytest tests/
```

## SylBreak4All Milestones (10/10 Complete)

| Milestone | Deliverable |
|-----------|-------------|
| M1 | Audit — `docs/ZOLAI_SYLLABLE_AUDIT.md` |
| M2 | Design — `docs/ZOLAI_SYLLABLE_DESIGN.md` |
| M3 | Rule segmenter — `zolai/syllable/segmenter.py` |
| M4 | Gold dataset — `data/syllable/gold.jsonl` (10K) |
| M5 | CRF segmenter — `zolai/syllable/crf_segmenter.py` |
| M6 | Evaluation — `zolai/syllable/evaluation.py` |
| M7 | NLP integration — `zolai/syllable/integration.py` |
| M8 | Tokenizer training — `zolai/syllable/tokenizer_training.py` |
| M9 | E2E testing — `zolai/syllable/e2e_test.py` |
| M10 | Docs + release — README, CHANGELOG, RELEASE_NOTES |

## License

MIT — Open source for Zomi language preservation.
