# Phase 0 Database Audit — Training Data Audit

**Generated:** 2026-09-13 22:15

## Overview

Analysis of training-related tables in the database, including `training_exercises`, `training_exercises_import`, `training_corpus_qwen3_import`, `training_seed_data_import`, `training_valid_sentences_import`, and `training_runs`.

## Training Tables Inventory

### Canonical Tables

| Table | Rows | Columns | Status | Purpose |
|-------|------|---------|--------|---------|
| `training_exercises` | 81,805 | 11 | ✅ ACTIVE | Main training exercises (5 types) |
| `training_runs` | ? | ? | ✅ ACTIVE | Training run tracking |

### Import Tables

| Table | Rows | Columns | Status | Purpose |
|-------|------|---------|--------|---------|
| `training_exercises_import` | 81,805 | ? | ✅ ACTIVE | Staging for training exercises |
| `training_corpus_qwen3_import` | 9,386 | ? | ✅ ACTIVE | Qwen3 format training data |
| `training_seed_data_import` | 500 | ? | ✅ ACTIVE | Seed data for training |
| `training_valid_sentences_import` | 4,693 | ? | ✅ ACTIVE | Validated sentences |

## Training Exercise Types

The `training_exercises` table contains 5 types of exercises:

| Type | Source JSONL | Description |
|------|--------------|-------------|
| Negation | `bible/negation_exercises.jsonl` | Negation pattern practice |
| Question | `bible/question_exercises.jsonl` | Question formation practice |
| Pronoun | `bible/pronoun_exercises.jsonl` | Pronoun agreement practice |
| Error Correction | `bible/error_correction_exercises.jsonl` | Grammar correction practice |
| Conditional | `bible/conditional_exercises.jsonl` | Conditional sentence practice |

## Data Lineage

```
Bible + Grammar Patterns
    ↓
generate_training_data.py
    ↓
*_exercises.jsonl files
    ↓
training_exercises_import (staging)
    ↓
training_exercises (canonical)
```

## Training Run Tracking

The `training_runs` table tracks:
- Training start/end timestamps
- Model configuration
- Dataset used
- Performance metrics
- Checkpoint locations

## Qwen3 Format Data

The `training_corpus_qwen3_import` table contains:
- 9,386 entries in Qwen3 chat format
- System/user/assistant message structure
- Ready for fine-tuning Qwen3 models

## Seed Data

The `training_seed_data_import` table contains:
- 500 high-quality seed examples
- Used for few-shot learning
- Manually curated for accuracy

## Validated Sentences

The `training_valid_sentences_import` table contains:
- 4,693 sentences validated by grammar checker
- ZVS 2018 compliant
- Ready for training data generation

## Training Pipeline

### Step 1: Exercise Generation
```python
# From Bible verses + grammar patterns
generate_training_data.py --type negation --count 1000
generate_training_data.py --type question --count 1000
generate_training_data.py --type pronoun --count 1000
generate_training_data.py --type error --count 1000
generate_training_data.py --type conditional --count 1000
```

### Step 2: Sentence Validation
```python
# Validate generated sentences
validate_sentences.py --text "sentence"
deep_validate.py --text "sentence"
```

### Step 3: Corpus Building
```python
# Build training corpus
build_training_corpus.py --full
format_training_data.py  # Convert to Qwen3 format
```

### Step 4: Export
```python
# Export for fine-tuning
build_training_corpus.py --export jsonl
```

## Quality Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Total exercises | 81,805 | >50,000 ✅ |
| Exercise types | 5 | 5 ✅ |
| Qwen3 entries | 9,386 | >5,000 ✅ |
| Seed examples | 500 | >200 ✅ |
| Valid sentences | 4,693 | >2,000 ✅ |

## Recommendations

1. **Consolidate import tables** — Consider merging `training_exercises_import` into canonical
2. **Add version tracking** — Track exercise generation versions
3. **Quality scoring** — Add confidence scores to exercises
4. **Deduplication** — Remove duplicate exercises
5. **Balance types** — Ensure equal representation across exercise types

## Training Data Schema

### training_exercises (Canonical)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| type | TEXT | Exercise type (negation, question, etc.) |
| zolai | TEXT | Zolai sentence |
| english | TEXT | English translation |
| pattern | TEXT | Grammar pattern used |
| difficulty | TEXT | CEFR level (A1-C2) |
| source | TEXT | Source (bible, generated, etc.) |
| created_at | TEXT | Creation timestamp |
| updated_at | TEXT | Last update timestamp |
| is_valid | INTEGER | Validation status |
| metadata | TEXT | Additional metadata (JSON) |

### training_runs
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| model_name | TEXT | Model used |
| dataset | TEXT | Training dataset |
| start_time | TEXT | Training start |
| end_time | TEXT | Training end |
| metrics | TEXT | Performance metrics (JSON) |
| checkpoint | TEXT | Checkpoint path |
| status | TEXT | Training status |

## Usage in Code

Training tables are referenced in:
- `scripts/training/` — Training data generation scripts
- `zolai/trainer/` — Training pipeline code
- `zolai/api/desktop_router.py` — Training endpoints
- `tests/test_training*.py` — Training tests

## Future Enhancements

1. **Active learning** — Use model predictions to generate new exercises
2. **Adaptive difficulty** — Adjust exercise difficulty based on learner performance
3. **Spaced repetition** — Integrate with vocabulary learning system
4. **Multi-modal** — Add audio exercises for pronunciation
5. **Collaborative filtering** — Recommend exercises based on learner progress