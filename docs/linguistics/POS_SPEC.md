# Zomi POS Tagset Specification (v0.1)

## Overview

This document defines the Part-of-Speech (POS) tagset for Tedim Zolai (ZVS 2018).
The tagset is based on Universal Dependencies (UD) v2 with Zomi-specific extensions
for agglutinative morphology.

## Tagset

### Core UD Tags (17)

| Tag | Description | Examples |
|-----|-------------|----------|
| `NOUN` | Noun | `gam` (land), `mi` (person), `lai` (book) |
| `VERB` | Verb | `pai` (go), `ne` (eat), `mu` (see) |
| `ADJ` | Adjective | `hoih` (good), `khaw` (big), `gam` (sweet) |
| `ADV` | Adverb | `ciang` (quickly), `ze` (very) |
| `PRON` | Pronoun | `ka` (I), `na` (you), `a` (he/she) |
| `DET` | Determiner | `hi` (this), `khaw` (that) |
| `ADP` | Adposition | `ah` (at), `in` (ergative marker) |
| `CONJ` | Conjunction | `leh` (and), `a` (but) |
| `PART` | Particle | `hi` (declarative), `hen` (then) |
| `NUM` | Numeral | `khat` (one), `ni` (two) |
| `PUNCT` | Punctuation | `.`, `,`, `?`, `!` |
| `SYM` | Symbol | `$`, `%`, `+` |
| `X` | Other | Foreign words, unanalyzable |
| `INTJ` | Interjection | `o` (oh), `ai` (alas) |
| `AUX` | Auxiliary | `ding` (future), `zo` (completive) |
| `SCONJ` | Subordinating conjunction | `tua` (that), `bang` (why) |
| `PROPN` | Proper noun | `Pasian` (God), `Tapa` (Son) |

### Zomi-Specific Tags (3)

| Tag | Description | Examples |
|-----|-------------|----------|
| `DIR` | Directional prefix | `hong` (hither), `va` (away), `khia` (down), `lut` (enter), `kik` (up) |
| `ASP` | Aspect suffix | `ta` (past), `zo` (completive), `khin` (experiential), `lai` (progressive), `ding` (future) |
| `CLF` | Classifier | `bu` (CLF for flat things), `pung` (CLF for round things) |

## Morphological Features

Features follow UD conventions where applicable, with Zomi-specific additions.

| Feature | Values | Description |
|---------|--------|-------------|
| `Tense` | `Past`, `Pres`, `Fut` | Temporal reference |
| `Aspect` | `Perf`, `Imp`, `Prog`, `Exp` | Aspectual viewpoint |
| `Mood` | `Ind`, `Sub`, `Imp`, `Cnd` | Modality |
| `Number` | `Sing`, `Plur` | Grammatical number |
| `Person` | `1`, `2`, `3` | Grammatical person |
| `Case` | `Nom`, `Acc`, `Erg`, `Gen`, `Dat`, `Loc` | Grammatical case |
| `Polarity` | `Pos`, `Neg` | Negation status |
| `VerbForm` | `Fin`, `Inf`, `Part`, `Conv` | Verb form type |

### Zomi-Specific Feature Values

| Feature | Zomi Values | Description |
|---------|-------------|-------------|
| `Tense` | `Fut` → `ding` | Future marked by `ding` |
| `Aspect` | `Exp` → `khin` | Experiential aspect |
| `Polarity` | `Neg` → `kei`/`lo` | Negation particles |
| `Case` | `Erg` → `in` | Ergative marker |

## Annotation Guidelines

### 1. Tokenization
- Tokens are space-delimited in the source text
- Compound words are single tokens (e.g., `vantung` = heaven)
- Directional + verb + aspect = separate tokens when analyzable
  - `hong pai ta` → `hong` (DIR) `pai` (VERB) `ta` (ASP)
  - But `hongpai` (if lexicalized) → single token

### 2. POS Assignment

#### Verbs (VERB/AUX)
- Main verbs: `VERB`
- Aspect markers (`ta`, `zo`, `khin`, `lai`, `ding`): `ASP` (or `AUX` if auxiliary)
- Future `ding`: `AUX` when auxiliary, `ASP` when aspectual
- Directionals (`hong`, `va`, `khia`, `lut`, `kik`): `DIR`

#### Nouns (NOUN/PROPN)
- Common nouns: `NOUN`
- Proper names (God, places, persons): `PROPN`
- Classifiers: `CLF`

#### Particles (PART)
- Sentence-final particles: `hi`, `hen`, `un`, `vo`
- Question particles: `hiam`, `diam` → `PART` (or `SCONJ` for content questions)

#### Negation (PART/ADV)
- `kei` (standard negation): `PART`
- `lo` (literary negation): `PART`

### 3. Morphological Features

Assign features based on morphological analysis:

```json
{
  "text": "pai",
  "lemma": "pai",
  "pos": "VERB",
  "features": {
    "VerbForm": "Fin"
  }
}
```

```json
{
  "text": "pai",
  "lemma": "pai",
  "pos": "VERB",
  "features": {
    "Tense": "Past",
    "Aspect": "Perf"
  }
}
```

For directional + verb combinations:
```json
[
  {"text": "hong", "lemma": "hong", "pos": "DIR", "features": {}},
  {"text": "pai", "lemma": "pai", "pos": "VERB", "features": {"VerbForm": "Fin"}},
  {"text": "ta", "lemma": "ta", "pos": "ASP", "features": {"Tense": "Past", "Aspect": "Perf"}}
]
```

### 4. Special Cases

#### Ergative Construction
- Ergative marker `in`: `ADP` with `Case=Erg`
- `Pasian in` → `Pasian` (PROPN) `in` (ADP, Case=Erg)

#### Clausal Complementizer
- `tua` (that): `SCONJ`

#### Content Questions
- `bang hang` + V + S + `hiam`: `bang` (SCONJ), `hang` (ADV), `hiam` (PART)

## Data Format

Annotations stored as JSONL (`data/eval/pos_gold_v0.jsonl`):

```json
{
  "sentence_id": "bible_12345",
  "text": "Pasian in vantung leh leitung a piangsak hi",
  "source": "bible",
  "tokens": [
    {"text": "Pasian", "lemma": "Pasian", "pos": "PROPN", "features": {}},
    {"text": "in", "lemma": "in", "pos": "ADP", "features": {"Case": "Erg"}},
    {"text": "vantung", "lemma": "vantung", "pos": "NOUN", "features": {}},
    {"text": "leh", "lemma": "leh", "pos": "CONJ", "features": {}},
    {"text": "leitung", "lemma": "leitung", "pos": "NOUN", "features": {}},
    {"text": "a", "lemma": "a", "pos": "PRON", "features": {"Person": "3", "Number": "Sing"}},
    {"text": "piangsak", "lemma": "piangsak", "pos": "VERB", "features": {"VerbForm": "Fin"}},
    {"text": "hi", "lemma": "hi", "pos": "PART", "features": {}}
  ]
}
```

## Quality Criteria

- **Inter-annotator agreement**: Target κ > 0.8
- **Coverage**: All POS tags should appear in gold set
- **Consistency**: Same word in same context → same POS
- **Documentation**: Edge cases documented in `docs/linguistics/POS_ANNOTATION_GUIDE.md`

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-10-06 | Initial draft based on UD v2 + Zomi corpus evidence |
