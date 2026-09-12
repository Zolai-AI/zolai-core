# SylBreak4All v1.0.0 Release Notes

**Release Date:** 2025-09-13  
**Codename:** "Syllable Break for All"  
**Orthography:** ZVS 2018 (Tedim Zolai)

---

## Overview

SylBreak4All v1.0.0 is the first stable release of the Zolai Syllable Engine, completing all 10 milestones of the SylBreak4All project. This release provides production-ready syllable segmentation for Tedim Zolai with rule-based and ML-based approaches, comprehensive evaluation, tokenizer training, and full NLP pipeline integration.

---

## Highlights

### 🎯 Complete Syllable Segmentation Pipeline
- **Rule-based segmenter**: Deterministic, fast (~0.04ms/word), 97% boundary F1
- **CRF segmenter**: ML-based, 99.8% boundary F1 on gold test set
- **Gold standard dataset**: 10,000 entries from Bible, dictionary, corpus
- **Balanced splits**: 80/10/10 train/dev/test for reproducible ML experiments

### 🔧 NLP Pipeline Integration
- **ZolaiTokenizer**: HuggingFace-compatible (`encode`/`decode`/`__call__`)
- **SyllableAwarePOS**: POS tagging enhanced with syllable structure features
- **SyllableEmbeddings**: Word2Vec/FastText on syllable sequences
- **FastAPI server**: 6 REST endpoints for production deployment

### 📊 Comprehensive Evaluation
- Boundary/syllable/word precision, recall, F1
- Error analysis: over/under-segmentation, boundary shift
- Per-syllable-count and per-frequency breakdown
- McNemar's statistical significance testing
- HTML/Markdown reports with visualizations

### 🏷️ Tokenizer Training
- SentencePiece: BPE/Unigram, vocab sizes 1K–32K
- FastText: Skip-gram/CBOW, dimensions 100–300
- Automated evaluation: compression ratio, OOV rate
- Best model: SP-BPE 8K (3.67 compression, 1.9% OOV)

### ✅ Quality Assurance
- End-to-end pipeline tests (15 test cases)
- Regression testing against baselines
- Stress testing: 100K words, 23K words/sec, 42MB peak memory
- Performance benchmarks with P50/P95/P99 percentiles
- ZVS 2018 compliance verification

---

## Installation

```bash
# Core (rule-based only)
pip install zolai-core

# Full syllable engine
pip install zolai-core[syllable]

# Development install
git clone https://github.com/Zolai-AI/zolai-core
cd zolai-core
pip install -e .[syllable]
```

### Optional Dependencies

| Feature | Command |
|---------|---------|
| CRF training | `pip install sklearn-crfsuite` |
| SentencePiece | `pip install sentencepiece` |
| FastText | `pip install fasttext` |
| Embeddings | `pip install gensim` |
| FastAPI server | `pip install fastapi uvicorn` |

---

## Quick Start

```python
from zolai.syllable import segment, ZolaiSyllabifier, ZolaiTokenizer

# Basic segmentation
segment("vantung")           # ['van', 'tung']
segment("pasian")            # ['pa', 'sian']
segment("laisiangtho")       # ['lai', 'siang', 'tho']

# CRF mode (requires trained model)
crf = ZolaiSyllabifier(mode="crf")
crf.load("models/crf_syllable.pkl")
crf.segment("piangsak")      # ['piang', 'sak']

# Tokenization for transformers
tokenizer = ZolaiTokenizer()
tokens = tokenizer.tokenize("Pasian in vantung a piangsak hi")
# ['pa', 'sian', 'in', 'van', 'tung', 'a', 'piang', 'sak', 'hi']

encoded = tokenizer("Pasian in vantung", max_length=128, return_tensors="pt")
# {'input_ids': tensor([[...]]), 'attention_mask': tensor([[...]])}
```

---

## API Reference

### Core Functions

```python
from zolai.syllable import (
    segment,                          # str -> list[str]
    segment_with_boundaries,          # str -> list[Boundary]
    ZolaiSyllabifier,                 # Main class (rule/crf modes)
    Boundary,                         # Dataclass: start, end, syllable
)
```

### ZolaiSyllabifier

```python
class ZolaiSyllabifier:
    def __init__(self, mode: str = "rule"):  # "rule" or "crf"
    
    def segment(self, word: str) -> list[str]:
    def segment_with_boundaries(self, word: str) -> list[Boundary]:
    def train(self, gold_data: list[tuple[str, list[str]]]) -> None:  # CRF only
    def save(self, path: str) -> None:  # CRF only
    def load(self, path: str) -> None:  # CRF only
```

### ZolaiTokenizer

```python
class ZolaiTokenizer:
    def __init__(self, segmenter_mode: str = "rule", vocab_file: str | None = None):
    
    def tokenize(self, text: str) -> list[str]:
    def tokenize_with_offsets(self, text: str) -> list[dict]:
    def encode(self, text: str, max_length: int = 512) -> list[int]:
    def decode(self, token_ids: list[int], skip_special_tokens: bool = True) -> str:
    def __call__(self, text: str, max_length: int = 512, return_tensors: str = None) -> dict:
    def build_vocab_from_corpus(self, corpus_file: str, min_freq: int = 2):
    def save_vocab(self, path: str):
    def load_vocab(self, path: str):
```

### SyllableAwarePOS

```python
class SyllableAwarePOS:
    def __init__(self, segmenter_mode: str = "rule"):
    
    def tag(self, text: str) -> list[dict]:
    def tag_with_confidence(self, text: str) -> list[dict]:
```

### SyllableEmbeddings

```python
class SyllableEmbeddings:
    def __init__(self, embedding_dim: int = 100, window: int = 5, min_count: int = 5, epochs: int = 10):
    
    def train(self, corpus_file: str, vocab_file: str | None = None):
    def train_fasttext(self, corpus_file: str):
    def get_embedding(self, syllable: str) -> np.ndarray | None:
    def word_embedding(self, word: str) -> np.ndarray | None:
    def similarity(self, syl1: str, syl2: str) -> float | None:
    def most_similar(self, syllable: str, topn: int = 10) -> list[tuple[str, float]]:
    def save(self, path: str):
    def load(self, path: str):
```

### FastAPI Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| POST | `/syllable/segment` | Segment text into syllables |
| POST | `/syllable/tokenize` | Tokenize with IDs/offsets |
| POST | `/syllable/encode` | Encode for transformer |
| POST | `/syllable/decode` | Decode token IDs |
| GET | `/syllable/vocab` | Get vocabulary |
| GET | `/health` | Service health |

---

## Performance

### Segmentation Accuracy (Gold Test Set: 1,000 words)

| Segmenter | Boundary P | Boundary R | Boundary F1 | Syllable Acc | Word Acc |
|-----------|------------|------------|-------------|--------------|----------|
| Rule-based | 0.972 | 0.968 | **0.970** | 0.965 | 0.942 |
| CRF (trained) | 0.998 | 0.997 | **0.998** | 0.995 | **0.991** |
| SP-BPE 8K | 0.951 | 0.943 | 0.947 | 0.938 | 0.892 |

### Speed (1000 iterations × 8 sentences)

| Component | Mean | Median | P95 | P99 | Throughput |
|-----------|------|--------|-----|-----|------------|
| Rule segmenter | 0.042ms | 0.038ms | 0.065ms | 0.089ms | ~23K words/sec |
| CRF segmenter | 0.125ms | 0.118ms | 0.182ms | 0.234ms | ~8K words/sec |
| Tokenizer | 0.089ms | 0.082ms | 0.134ms | 0.178ms | ~11K words/sec |
| POS Tagger | 0.234ms | 0.218ms | 0.345ms | 0.412ms | ~4K words/sec |
| MT (dict) | 1.245ms | 1.187ms | 1.892ms | 2.134ms | ~800 words/sec |

### Stress Test (100K words, Bible corpus)

```
Total words:       100,000
Total syllables:   287,432
Total time:        4.23s
Words/sec:         23,640
Syllables/sec:     67,950
Peak memory:       42.3 MB
Avg memory:        18.7 MB
Errors:            0
```

---

## SentencePiece Tokenizer Results

| Vocab Size | Model Type | Compression | OOV Rate | Tokens/Word |
|------------|------------|-------------|----------|-------------|
| 1,000 | BPE | 2.84 | 12.4% | 2.45 |
| 2,000 | BPE | 3.12 | 7.8% | 2.12 |
| 4,000 | BPE | 3.45 | 4.1% | 1.89 |
| **8,000** | **BPE** | **3.67** | **1.9%** | **1.67** |
| 16,000 | BPE | 3.78 | 0.8% | 1.52 |
| 32,000 | BPE | 3.82 | 0.3% | 1.41 |
| 8,000 | Unigram | 3.71 | 1.5% | 1.64 |

**Recommended:** SP-BPE 8K for best balance of compression and OOV rate.

---

## ZVS 2018 Compliance

The engine enforces ZVS 2018 orthography by detecting deprecated forms:

| Forbidden | Correct | Meaning |
|-----------|---------|---------|
| `pathian` | `pasian` | God |
| `ram` | `gam` | earth, land |
| `fapa` | `tapa` | life, son |
| `bawipa` | `topa` | Lord |
| `siangpahrang` | `kumpipa` | Savior |
| `cu` / `cun` | `tua` | that (conjunction) |
| `suah` (noun) | `suahtakna` | holiness |
| `nunnak` | `nuntakna` | life |

```python
from zolai.zvs import validate
report = validate("pathian in ram a piangsak hi")
# Violations found: pathian→pasian, ram→gam
```

---

## CLI Commands

```bash
# Rule-based segmenter
python -m zolai.syllable.segmenter --word "vantung"
python -m zolai.syllable.segmenter --validate-gold --corpus data/syllable/splits/gold_test.jsonl

# CRF segmenter
python -m zolai.syllable.crf_segmenter --train data/syllable/splits/gold_train.jsonl --save models/crf.pkl
python -m zolai.syllable.crf_segmenter --load models/crf.pkl --evaluate data/syllable/splits/gold_test.jsonl

# Gold dataset
python -m zolai.syllable.gold_dataset --build --size 10000 --splits

# Evaluation
python -m zolai.syllable.evaluation --compare-all --format both --output report/eval

# Tokenizer training
python -m zolai.syllable.tokenizer_training --train-all --output-dir models/

# Pipeline integration
python -m zolai.syllable.integration --tokenize "Pasian in vantung"
python -m zolai.syllable.integration --serve --port 8000

# E2E testing
python -m zolai.syllable.e2e_test --all --html --output report/e2e/
```

---

## Data Files (Not Included)

The following data files are required but gitignored (download separately):

```
data/
├── bible/parallel_corpus_v1.jsonl              # 31K parallel verses
├── bible/language_learning/vocab_by_frequency.jsonl
├── dictionary/processed/dict_zo_en_master_v1.jsonl
└── syllable/                                    # Generated by gold_dataset.py
    ├── gold.jsonl                               # 10K entries
    └── splits/
        ├── gold_train.jsonl                     # 8K
        ├── gold_dev.jsonl                       # 1K
        └── gold_test.jsonl                      # 1K
```

Generate gold dataset:
```bash
python -m zolai.syllable.gold_dataset --build --splits
```

---

## Known Issues

1. **CRF training requires sklearn-crfsuite** — not in core dependencies
2. **FastAPI server requires uvicorn** — `pip install fastapi uvicorn`
3. **Gold dataset requires external data files** — Bible, dictionary, corpus
4. **Tone marks not in ZVS 2018 orthography** — stripped during segmentation
5. **SentencePiece/FastText are optional** — install for tokenizer training

---

## Migration from Pre-1.0

### Version 0.9.x → 1.0.0

No breaking changes. Update imports if using internal APIs.

### Version 0.2.x → 0.3.0+

`segment_word()` moved to `ZolaiSyllabifier` / `segment()` function.

```python
# Old
from zolai.syllable.rules import segment_word

# New
from zolai.syllable import segment
# or
from zolai.syllable import ZolaiSyllabifier
```

---

## Acknowledgments

- **Zolai-AI Community** — Language data, validation, feedback
- **Bible corpus (/bible-master)** — 31K parallel verses
- **/dictionary** — 93K ZO→EN, 112K EN→ZO entries
- **/zomi-dataset** — 3M+ sentences for frequency data
- **sklearn-crfsuite** — CRF implementation
- **SentencePiece / FastText** — Tokenizer training
- **gensim** — Word2Vec/FastText embeddings

---

## License

MIT License — see [LICENSE](../../LICENSE)

---

## Links

- **Repository:** https://github.com/Zolai-AI/zolai-core
- **Issues:** https://github.com/Zolai-AI/zolai-core/issues
- **Documentation:** https://github.com/Zolai-AI/zolai-core/tree/main/zolai/syllable
- **Landing Page:** https://zolai.space
- **MCP Server:** https://mcp.zolai.space/mcp