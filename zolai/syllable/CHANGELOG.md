# Changelog

All notable changes to the Zolai Syllable Engine (SylBreak4All) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-09-13

### Milestone 10: Documentation & Release Preparation

- Complete documentation overhaul (README.md, CHANGELOG.md, RELEASE_NOTES.md)
- Version info added to `__init__.py` (`__version__ = "1.0.0"`)
- All 10 milestones documented with features and usage
- Performance benchmarks published
- Release checklist completed

### Milestone 9: End-to-End Testing & Validation

**Added**
- `E2ETester` class for full pipeline testing (segmentation → tokenization → POS → MT → ZVS)
- Regression testing against saved baselines
- Stress testing with memory profiling (tracemalloc)
- Performance benchmarking with percentile statistics (P50, P95, P99)
- ZVS 2018 compliance verification on corpus
- HTML/JSON report generation for all test types
- Default test cases (15 test sentences with expected outputs)

**Features**
- Pipeline test: validates syllables, tokens, POS, translations, ZVS compliance
- Regression test: compares against baseline JSON, detects drift
- Stress test: processes 100K+ words, measures throughput and memory
- Benchmark: per-component timing (segmenter, tokenizer, POS, MT)
- ZVS compliance: scans corpus for forbidden forms

### Milestone 8: Tokenizer Training (SentencePiece/FastText)

**Added**
- `SyllableTokenizerTrainer` class for training tokenizers
- SentencePiece support: BPE, Unigram, Word, Char models
- FastText support: Skip-gram and CBOW
- Multiple vocab sizes: 1K, 2K, 4K, 8K, 16K, 32K
- Evaluation metrics: compression ratio, OOV rate, avg tokens/word
- FastText similarity evaluation on syllable pairs
- Batch training of all variants with results summary
- JSON results export with numpy type conversion

**Dependencies**
- `sentencepiece` (optional)
- `fasttext` (optional)

### Milestone 7: NLP Pipeline Integration

**Added**
- `ZolaiTokenizer` — HuggingFace-compatible syllable tokenizer
  - `tokenize()`, `tokenize_with_offsets()`, `encode()`, `decode()`
  - `__call__()` for transformer integration (`return_tensors="pt|tf|np"`)
  - Vocabulary building from corpus, save/load JSON
- `SyllableAwarePOS` — POS tagger enhanced with syllable features
  - Syllable structure analysis (CV patterns)
  - Diphthong/digraph/coda detection
  - Onset/nucleus type classification
  - Confidence boosting for known syllable patterns
- `SyllableEmbeddings` — Word2Vec/FastText on syllable sequences
  - `train()`, `train_fasttext()` from corpus
  - `get_embedding()`, `word_embedding()`, `similarity()`, `most_similar()`
  - Save/load pickle format
- FastAPI application with endpoints:
  - `GET /` — Health check
  - `POST /syllable/segment` — Syllable segmentation with offsets
  - `POST /syllable/tokenize` — Tokenization with IDs/offsets
  - `POST /syllable/encode` — Transformer encoding
  - `POST /syllable/decode` — Decode token IDs
  - `GET /syllable/vocab` — Vocabulary dump
  - `GET /health` — Service health

**Dependencies**
- `fastapi`, `uvicorn` (optional, for server)
- `gensim` (optional, for embeddings)
- `torch`/`tensorflow` (optional, for tensor output)

### Milestone 6: Comprehensive Evaluation Framework

**Added**
- `SyllableEvaluator` class for multi-segmenter comparison
- Detailed error analysis: over/under-segmentation, boundary shift
- Per-syllable-count breakdown (1-syl, 2-syl, 3-syl, 4+)
- By-word-frequency analysis (high/medium/low/unseen)
- Common error tracking (top 20)
- McNemar's statistical significance test (continuity correction)
- HTML and Markdown report generation with visualizations
- `SentencePieceBaseline` for comparison
- Progress-aware CLI with rich output

**Metrics**
- Boundary Precision/Recall/F1
- Syllable-level accuracy
- Word-level accuracy (exact match)
- Confusion matrix (B/I tag level)

### Milestone 5: CRF-Based Segmenter

**Added**
- `CRFSyllableSegmenter` using sklearn-crfsuite
- BIO tagging scheme: B-SYL, I-SYL, E-SYL, S-SYL
- Rich feature extraction (20+ features per character):
  - Character n-grams (unigram, bigram, trigram)
  - Orthographic features (case, digit)
  - Phonotactic features (vowel/consonant, digraph, diphthong, coda, onset)
  - Position features (absolute, relative, word boundaries)
  - CV patterns (3-char and 5-char windows)
- Training with dev set evaluation
- Model persistence (pickle)
- Fallback to rule-based for OOV
- Evaluation metrics: word accuracy, boundary P/R/F1, syllable count accuracy
- Per-syllable-count breakdown

### Milestone 4: Gold Standard Dataset Builder

**Added**
- `GoldDatasetBuilder` with three authoritative sources:
  - Bible text (highest priority, ZVS 2018 compliant)
  - Dictionary headwords (verified entries)
  - High-frequency corpus (vocab_by_frequency.jsonl)
- Balanced sampling across syllable counts (1-4+)
- CoNLL-style I/O for inter-annotator agreement
- Cohen's kappa computation
- Train/dev/test splits (80/10/10) with fixed seed (42)
- BIO encoding/decoding utilities
- Feature extraction for CRF training

**Output**
- `data/syllable/gold.jsonl` — 10K balanced entries
- `data/syllable/splits/gold_train.jsonl` — 8K
- `data/syllable/splits/gold_dev.jsonl` — 1K
- `data/syllable/splits/gold_test.jsonl` — 1K

### Milestone 3: Rule-Based Segmenter (Complete)

**Added**
- `SyllableSegmenter` class with full phonotactic engine
- Known compounds dictionary (30+ built-in, extensible via corpus)
- Known roots for segmentation guidance (60+ high-frequency)
- Tone mark handling (strip/preserve via Unicode NFD)
- Corpus loading for compound/root expansion
- Gold dataset loading with duplicate handling
- Batch segmentation, validation against corpus/gold
- Detailed CLI with validation modes
- Backward compatibility: `RuleBasedSegmenter`, `CRFBasedSegmenter` protocols

### Milestones 1-2: Phonotactic Rules & Onset-Nucleus-Coda

**Added**
- `rules.py` — Core phonotactic inventories:
  - Vowels, diphthongs, digraphs, valid codas, onset clusters
  - `segment_word()` — deterministic (C)(C)V(C) segmentation
  - `strip_diacritics()` — Unicode tone mark removal
- Maximum munch algorithm with longest-match-first
- Digraph/diphthong awareness in onset/nucleus/coda parsing
- Compound exception handling

## [0.1.0] - 2025-01-15

### Initial Development

- Project structure established
- Basic syllable segmentation logic
- ZVS 2018 compliance validator integration
- Initial test suite

---

## Release Tags

| Version | Date | Milestone | Git Tag |
|---------|------|-----------|---------|
| 1.0.0 | 2025-09-13 | M10: Documentation & Release | `v1.0.0-sylbreak4all` |
| 0.9.0 | 2025-09-12 | M9: E2E Testing | `v0.9.0-m9` |
| 0.8.0 | 2025-09-11 | M8: Tokenizer Training | `v0.8.0-m8` |
| 0.7.0 | 2025-09-10 | M7: NLP Integration | `v0.7.0-m7` |
| 0.6.0 | 2025-09-09 | M6: Evaluation Framework | `v0.6.0-m6` |
| 0.5.0 | 2025-09-08 | M5: CRF Segmenter | `v0.5.0-m5` |
| 0.4.0 | 2025-09-07 | M4: Gold Dataset | `v0.4.0-m4` |
| 0.3.0 | 2025-09-06 | M3: Rule Segmenter | `v0.3.0-m3` |
| 0.2.0 | 2025-09-05 | M2: Phonotactic Rules | `v0.2.0-m2` |
| 0.1.0 | 2025-01-15 | M1: Project Init | `v0.1.0-init` |

## Upgrade Guide

### 0.9.x → 1.0.0

No breaking changes. Version bump for release.

### 0.8.x → 0.9.0

**New imports available:**
```python
from zolai.syllable import (
    E2ETester,
    PipelineResult,
    RegressionResult,
    StressTestResult,
    BenchmarkResult,
    ZVSComplianceResult,
)
```

### 0.7.x → 0.8.0

**New imports available:**
```python
from zolai.syllable import SyllableTokenizerTrainer
```

### 0.6.x → 0.7.0

**New imports available:**
```python
from zolai.syllable import (
    ZolaiTokenizer,
    SyllableAwarePOS,
    SyllableEmbeddings,
)
```

### 0.5.x → 0.6.0

**New imports available:**
```python
from zolai.syllable import SyllableEvaluator, SentencePieceBaseline
```

### 0.4.x → 0.5.0

**New imports available:**
```python
from zolai.syllable import CRFSyllableSegmenter
```

### 0.3.x → 0.4.0

**New imports available:**
```python
from zolai.syllable import GoldDatasetBuilder
```

### 0.2.x → 0.3.0

**Breaking:** `segment_word()` moved from `rules.py` to `segmenter.py` as `SyllableSegmenter.segment()`.

**Migration:**
```python
# Old (0.2.x)
from zolai.syllable.rules import segment_word
syllables = segment_word("vantung")

# New (0.3.0+)
from zolai.syllable import segment
syllables = segment("vantung")
# Or
from zolai.syllable import ZolaiSyllabifier
syllables = ZolaiSyllabifier().segment("vantung")
```

---

## Contributors

- Zolai-AI Community
- SylBreak4All Project Team

## License

MIT License