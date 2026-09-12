# SylBreak4All — Syllable Engine Design Document

**Module:** `zolai.syllable` | **Status:** Milestone 2 — Design + Skeleton
**Version:** 0.1.0 | **Date:** 2026-09-12

---

## 1. Executive Summary

SylBreak4All provides automatic syllable segmentation for Tedim Zolai
(ZVS 2018 orthography). The engine supports two modes:

- **Rule-based** — deterministic segmentation using Zolai phonotactic
  constraints (onset, nucleus, coda rules, digraph handling).
- **CRF-based** — trained on gold-annotated data using
  `sklearn-crfsuite` for higher accuracy on ambiguous cases.

The module integrates with `zolai-core` as a standalone package
(`zolai.syllable`) with no coupling to other internal modules.

**Design goals:**
1. High boundary precision (target >95% on native-speaker gold data)
2. Language-independent architecture (SylBreak4All framework)
3. Extensible — new phonotactic rules or CRF features without refactor
4. Zero external dependencies beyond `scikit-learn`/`sklearn-crfsuite`
5. Offline-capable — rule mode needs no model files

---

## 2. Zolai Phonology

### 2.1 Consonant Inventory

Tedim Zolai has approximately 23 consonant phonemes:

| Manner | Labial | Alveolar | Palatal | Velar | Glottal |
|--------|--------|----------|---------|-------|---------|
| Plosive | p, b | t, d | | k, g | |
| Fricative | f, v | s, z | | | h |
| Nasal | m | n | | ng | |
| Lateral | | l | | | |
| Approximant | w | | y | | |
| Affricate | | | ch, j | | |

**Digraphs treated as single phonemes:**

| Digraph | IPA | Example |
|---------|-----|---------|
| th | /θ/ | `thla` (flat) |
| kh | /kʰ/ | `kham` (brave) |
| gh | /ɣ/ | `gham` (smell) |
| ng | /ŋ/ | `ngai` (bamboo) |
| ch | /tʃ/ | `chim` (bird) |
| ph | /pʰ/ | `phah` (hit) |
| bh | /bʱ/ | `bhal` (many) |

### 2.2 Vowel Inventory

| Short | Long/Diphthong | Notes |
|-------|----------------|-------|
| a | aw, ai | |
| e | ei | |
| i | | |
| o | ou | |
| u | | |

**Tone marking** — diacritics (á, à, ā, a̋) mark tones but are
**not** part of syllable segmentation. The segmenter operates on the
underlying character sequence, ignoring diacritics for boundary
detection.

### 2.3 Syllable Template

Zolai follows a **(C)(C)V(C)** template:

```
(C)(C) V (C)
 │ │   │  └─ optional coda
 │ │   └──── required nucleus (vowel/diphthong)
 │ └──────── optional second onset consonant
 └────────── optional onset consonant
```

**Examples:**

| Word | Segmentation | Onset | Nucleus | Coda |
|------|-------------|-------|---------|------|
| `mi` | `mi` | ∅ | i | ∅ |
| `gam` | `gam` | g | a | m |
| `pasian` | `pa-sian` | p / s | a / ian | ∅ |
| `vantung` | `van-tung` | v / t | a / u | n / ng |
| `piangsak` | `piang-sak` | p / s | ia / a | ng / k |
| `tapa` | `ta-pa` | t / t | a / a | ∅ |

---

## 3. Syllable Rules

### 3.1 Onset

- **Maximum onset:** 2 consonants
- **Valid clusters:** `pl, kl, bl, gl, fl, sl, tl, pr, kr, tr, br,
  dr, fr, gr, sp, st, sk, sn, sm, sw, sy`
- **Digraphs count as one consonant:** `th, kh, gh, ng, ch, ph, bh`
- **Default onset:** if no valid cluster found, onset = 1 consonant

### 3.2 Nucleus

- **Single vowel:** `a, e, i, o, u`
- **Diphthongs:** `aw, ai, ei, ou`
- The nucleus is the **only required** syllable component
- Scanning: find the leftmost vowel/diphthong from current position

### 3.3 Coda

- **Maximum coda:** 1 consonant
- **Valid codas:** `n, ng, m, l, s, t, k, p`
- **Ng handling:** if `ng` appears after a vowel with no following
  vowel, treat as coda (not onset of next syllable)
- **Coda resolves:** when the next character group forms a valid onset

### 3.4 Tone

- Tone diacritics (á, à, ā) are **stripped** before segmentation
- Tone information is preserved in the output but does not affect
  boundary detection

### 3.5 Edge Cases

| Input | Output | Rule |
|-------|--------|------|
| `aa` | `a-a` | Duplicated vowels split |
| `ngai` | `ngai` | `ng` = onset, not `n` + `gai` |
| `sian` | `sian` | `s` + `ian` (diphthong) |
| `piang` | `piang` | `p` + `ia` + `ng` |
| `tung` | `tung` | `t` + `u` + `ng` |
| `khat` | `khat` | `kh` + `a` + `t` |

---

## 4. CRF Feature Template

The CRF segmenter uses a sliding window over each character to
extract features for boundary prediction (B/I tags).

### 4.1 Character Window Features

For each character at position `i`, extract:

```python
# Unigram features
char[i]           # current character
char[i-1]         # previous character
char[i+1]         # next character
is_vowel[i]       # boolean
is_consonant[i]   # boolean
is_digraph_start[i]  # boolean (e.g., 't' in 'th')

# Bigram features
char[i-1:i+1]     # previous + current
char[i:i+2]       # current + next

# Trigram features
char[i-2:i+1]     # two-back + current
char[i-1:i+2]     # prev + current + next
```

### 4.2 Orthographic Features

```python
uppercase[i]       # is uppercase
digit[i]           # is digit
punctuation[i]     # is punctuation
whitespace[i]      # is whitespace
diacritic[i]       # has tone diacritic
```

### 4.3 Phonotactic Features

```python
valid_onset_1[i]   # char[i] can start a 1-C onset
valid_onset_2[i]   # char[i:i+1] is a valid 2-C cluster
valid_coda[i]      # char[i] can be a coda
is_diphthong[i]    # char[i:i+1] is a diphthong
is_digraph[i]      # char[i:i+1] is a digraph (th, kh, etc.)
```

### 4.4 Position Features

```python
word_start[i]      # i == 0
word_end[i]        # i == len(word) - 1
distance_from_start[i]  # i
distance_from_end[i]    # len(word) - 1 - i
```

### 4.5 Tag Encoding

Standard **BIO** (Begin/Inside/Outside):

```
p  a  s  i  a  n
B  I  I  B  I  I
```

Each `B` marks the start of a new syllable; `I` continues it.

---

## 5. Gold Dataset Format

### 5.1 CoNLL-Style Annotation

Each word is a blank-line-separated block. Characters are
column-aligned with tags:

```
p   a   s   i   a   n
B   I   I   B   I   I

g   a   m
B   I   I

v   a   n   t   u   n   g
B   I   I   B   I   I   I
```

### 5.2 Annotation Guidelines

1. **Syllable boundary** = onset-nucleus-coda cycle restart
2. **Digraphs** (`th, kh, gh, ng, ch, ph, bh`) = one character
   unit for annotation
3. **Diphthongs** (`aw, ai, ei, ou`) = one vowel unit
4. **Tone diacritics** = included in character but do not
   affect boundaries
5. **Loan words** = segment by Zolai phonotactics, not source
6. **Ambiguous cases** = prefer maximally legal onsets (MOP)
7. **Compound words** = segment each root separately

### 5.3 Inter-Annotator Agreement

Use **Cohen's kappa** for boundary-level agreement:

- 2+ native speakers annotate the same 500-word sample
- Compute kappa per boundary position
- Target: κ ≥ 0.85 before training CRF
- Disagreements resolved by majority vote + linguistic review

---

## 6. Integration Points

### 6.1 With zolai-core Modules

| Module | Integration |
|--------|------------|
| `zolai.dictionary` | Syllabify headwords for morphological lookup |
| `zolai.knowledge` | Syllable-aware tokenization for RAG retrieval |
| `zolai.zvs` | Syllable validation for ZVS compliance checks |
| `zolai.cleaner` | Normalize syllable boundaries in corpus cleaning |
| `zolai.trainer` | Syllable features for training data generation |
| `zolai.tokenizer` | Syllable-based tokenization alternative |

### 6.2 Public API

```python
# Quick segmentation
from zolai.syllable import segment
syllables = segment("pasian")  # ["pa", "sian"]

# Full boundary info
from zolai.syllable import segment_with_boundaries
boundaries = segment_with_boundaries("vantung")
# [Boundary(start=0, end=3, syllable="van"),
#  Boundary(start=3, end=7, syllable="tung")]

# CRF mode
from zolai.syllable import ZolaiSyllabifier
syl = ZolaiSyllabifier(mode="crf")
syl.load("models/syllable_crf.pkl")
syllables = syl.segment("piangsak")  # ["piang", "sak"]
```

### 6.3 With External Tools

- **HuggingFace:** Export syllable tokenizer as HF-compatible
- **MCP Server:** Expose syllable API via `segment` tool
- **zolai-web:** Client-side syllable highlighting

---

## 7. Evaluation Metrics

### 7.1 Boundary-Level Metrics

```
Precision = correct_predicted_boundaries / total_predicted_boundaries
Recall    = correct_predicted_boundaries / total_gold_boundaries
F1        = 2 * P * R / (P + R)
```

### 7.2 Syllable-Level Metrics

- **Syllable accuracy:** percentage of words where ALL boundaries
  match gold exactly
- **Boundary type confusion:** count of false positive / false
  negative per boundary position

### 7.3 Word-Level Metrics

- **Word accuracy:** complete match (all boundaries correct)
- **Partial match:** percentage of correct boundaries per word

### 7.4 Baseline Targets

| Metric | Rule-Based | CRF Target |
|--------|-----------|------------|
| Boundary P | 85% | 95% |
| Boundary R | 80% | 93% |
| Boundary F1 | 82% | 94% |
| Word accuracy | 65% | 85% |

---

## 8. Data Flow Diagram

```
Raw Text
    │
    ▼
┌──────────────┐
│ Preprocessor │ ← strip diacritics, normalize
└──────┬───────┘
       │
       ▼
┌──────────────────────────────┐
│    ZolaiSyllabifier         │
│  ┌──────────┐ ┌──────────┐  │
│  │   Rule   │ │   CRF    │  │
│  │ Segmenter│ │ Segmenter│  │
│  └────┬─────┘ └────┬─────┘  │
│       │             │        │
│       └──────┬──────┘        │
│              │               │
│              ▼               │
│     ┌──────────────┐        │
│     │   Boundary   │        │
│     │   Validator  │        │
│     └──────┬───────┘        │
└────────────┼────────────────┘
             │
             ▼
    ┌─────────────────┐
    │  Syllable Output │
    │  (list[str])     │
    └─────────────────┘
```

---

## 9. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Low CRF accuracy on rare words | Medium | Fall back to rule-based; expand gold data |
| Ambiguous onset clusters | High | MOP rule + native speaker adjudication |
| Dialect variation (Tedim vs Hakha) | Medium | Tag dialect in gold data; dialect-specific rules |
| Diphthong ambiguity (`ai` = VV or V+C) | Medium | Phonotactic context features in CRF |
| Gold data scarcity | High | Start with rule-based; bootstrap CRF from rules |
| Diacritics interfering with CRF | Low | Strip before feature extraction |

---

## 10. Implementation Roadmap

### Milestone 2 (Current)
- [x] Design document
- [x] Module skeleton
- [x] Rule-based segmenter skeleton
- [x] CRF segmenter skeleton
- [x] Gold dataset parser
- [x] Evaluation metrics
- [x] pyproject.toml update

### Milestone 3 — Rule-Based Engine
- [ ] Implement full onset/nucleus/coda rules
- [ ] Handle digraphs and diphthongs
- [ ] 100-word manual test suite
- [ ] Achieve >80% boundary F1 on test set

### Milestone 4 — Gold Dataset v1
- [ ] Annotate 500 words from Bible corpus
- [ ] Inter-annotator agreement (2 speakers)
- [ ] Cohen's kappa ≥ 0.85
- [ ] Export to CoNLL format

### Milestone 5 — CRF Training
- [ ] Feature extraction pipeline
- [ ] Train CRF on gold data
- [ ] 5-fold cross-validation
- [ ] Achieve >90% boundary F1

### Milestone 6 — Integration
- [ ] Wire into `zolai.dictionary` morphological lookup
- [ ] Wire into `zolai.knowledge` tokenization
- [ ] MCP server `segment` tool
- [ ] zolai-web syllable highlighting

### Milestone 7 — Scale
- [ ] Annotate 5,000 words
- [ ] Dialect-specific models (Tedim, Hakha, Falam)
- [ ] Online learning from user corrections

### Milestone 8 — Evaluation
- [ ] Full evaluation suite
- [ ] Comparison with SylBreak4All baseline
- [ ] Publication-ready results

### Milestones 9-12
- [ ] Multilingual extension (Hakha, Falam, Paite)
- [ ] Real-time syllable prediction (streaming)
- [ ] Speech alignment integration
- [ ] Community annotation platform

---

## Appendix A: Example Segmentation

```
Input:  "Pasian in vantung leh leitung a piangsak hi."
Output: "Pa-sian in van-tung leh lei-tung a piang-sak hi."

Word breakdown:
  Pa-sian     → [Pa] [sian]
  in          → [in]
  van-tung    → [van] [tung]
  leh         → [leh]
  lei-tung    → [lei] [tung]
  a           → [a]
  piang-sak   → [piang] [sak]
  hi          → [hi]
```

## Appendix B: Valid Onset Clusters

Two-consonant onsets permitted in Zolai:

| Cluster | Example | Meaning |
|---------|---------|---------|
| pl | `plo` | flat surface |
| kl | `kla` | cross |
| bl | `ble` | shine |
| gl | `gla` | measure |
| fl | `fli` | fly |
| sl | `slo` | smooth |
| tl | `tla` | spread |
| pr | `pro` | progress |
| kr | `kri` | create |
| tr | `tre` | travel |
| br | `bre` | break |
| dr | `dre` | direct |
| fr | `fre` | free |
| gr | `gre` | great |
| sp | `spa` | space |
| st | `sta` | start |
| sk | `ska` | scan |
| sn | `sna` | snack |
| sm | `sma` | small |
| sw | `swa` | swap |
| sy | `syl` | syllable |
