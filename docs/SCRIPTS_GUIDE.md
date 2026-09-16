# Scripts Guide

This document provides comprehensive documentation for all scripts in the zolai-core repository.

## Overview

Scripts are organized by category in the `scripts/` directory. They handle data processing, training, migration, and utility tasks.

## Script Categories

### 1. Data Pipeline Scripts (`scripts/data_pipeline/`)

These scripts handle the core data processing pipeline.

#### Build Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `build_corpus_dictionary.py` | Build corpus dictionary from sources | `python scripts/data_pipeline/build_corpus_dictionary.py` |
| `build_master_dataset.py` | Build master training dataset | `python scripts/data_pipeline/build_master_dataset.py` |
| `build_master_dataset_complete.py` | Complete master dataset build | `python scripts/data_pipeline/build_master_dataset_complete.py` |
| `build_llm_dataset_v3.py` | Build LLM training dataset v3 | `python scripts/data_pipeline/build_llm_dataset_v3.py` |
| `build_tedim_train_dataset.py` | Build Tedim training dataset | `python scripts/data_pipeline/build_tedim_train_dataset.py` |
| `build_qwen_dataset.py` | Build Qwen-specific dataset | `python scripts/data_pipeline/build_qwen_dataset.py` |
| `build_master_pipeline.py` | Master pipeline orchestrator | `python scripts/data_pipeline/build_master_pipeline.py` |

#### Clean Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `clean_dict_examples.py` | Clean dictionary examples | `python scripts/data_pipeline/clean_dict_examples.py` |
| `clean_master_dataset.py` | Clean master dataset | `python scripts/data_pipeline/clean_master_dataset.py` |
| `clean_master_pipeline.py` | Master cleaning pipeline | `python scripts/data_pipeline/clean_master_pipeline.py` |
| `clean_training_data.py` | Clean training data | `python scripts/data_pipeline/clean_training_data.py` |
| `deep_clean.py` | Deep cleaning of all data | `python scripts/data_pipeline/deep_clean.py` |

#### Fix Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `fix_batch_9.py` | Fix batch 9 issues | `python scripts/data_pipeline/fix_batch_9.py` |
| `fix_batch_zvs.py` | Fix ZVS compliance in batches | `python scripts/data_pipeline/fix_batch_zvs.py` |
| `fix_multimeaning_words.py` | Fix multi-meaning word handling | `python scripts/data_pipeline/fix_multimeaning_words.py` |
| `fix_sentences_step1.py` | Sentence fixes step 1 | `python scripts/data_pipeline/fix_sentences_step1.py` |
| `fix_sentences_step2.py` | Sentence fixes step 2 | `python scripts/data_pipeline/fix_sentences_step2.py` |
| `fix_sentences_step4.py` | Sentence fixes step 4 | `python scripts/data_pipeline/fix_sentences_step4.py` |
| `fix_sentences_dialect.py` | Fix dialect variations | `python scripts/data_pipeline/fix_sentences_dialect.py` |

#### Enrich Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `enrich_dict_from_bible.py` | Enrich dictionary from Bible | `python scripts/data_pipeline/enrich_dict_from_bible.py` |
| `enrich_dict_examples.py` | Enrich dictionary examples | `python scripts/data_pipeline/enrich_dict_examples.py` |
| `enrich_master_dataset.py` | Enrich master dataset | `python scripts/data_pipeline/enrich_master_dataset.py` |

#### Merge Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `merge_all_resources.py` | Merge all resources | `python scripts/data_pipeline/merge_all_resources.py` |
| `merge_by_index.py` | Merge by index | `python scripts/data_pipeline/merge_by_index.py` |
| `merge_enriched_complete.py` | Merge enriched data | `python scripts/data_pipeline/merge_enriched_complete.py` |
| `merge_final_dataset.py` | Merge final dataset | `python scripts/data_pipeline/merge_final_dataset.py` |

#### Translation Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `translate_contextual.py` | Contextual translation | `python scripts/data_pipeline/translate_contextual.py` |
| `translation_validator.py` | Validate translations | `python scripts/data_pipeline/translation_validator.py` |
| `local_translation_validator.py` | Local translation validation | `python scripts/data_pipeline/local_translation_validator.py` |

#### Utility Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `zo_utils.py` | Zolai utilities | `python scripts/data_pipeline/zo_utils.py` |
| `llm_tools.py` | LLM utilities | `python scripts/data_pipeline/llm_tools.py` |
| `gather_all_sources.py` | Gather all data sources | `python scripts/data_pipeline/gather_all_sources.py` |
| `audit_raw.py` | Audit raw data | `python scripts/data_pipeline/audit_raw.py` |
| `audit_stems.py` | Audit word stems | `python scripts/data_pipeline/audit_stems.py` |
| `doublecheck_master.py` | Double-check master data | `python scripts/data_pipeline/doublecheck_master.py` |
| `automated_system_fixer.py` | Automated system fixes | `python scripts/data_pipeline/automated_system_fixer.py` |

### 2. Training Scripts (`scripts/training/`)

These scripts handle model training and data preparation.

#### Training Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `train_llm.py` | Train LLM model | `python scripts/training/train_llm.py` |
| `train_kaggle_t4x2.py` | Train on Kaggle T4x2 | `python scripts/training/train_kaggle_t4x2.py` |
| `train_kaggle_t4x2_qwen1_5b.py` | Train Qwen 1.5B on Kaggle | `python scripts/training/train_kaggle_t4x2_qwen1_5b.py` |
| `train_local_test.py` | Local training test | `python scripts/training/train_local_test.py` |

#### Data Preparation
| Script | Purpose | Usage |
|--------|---------|-------|
| `prepare_train.py` | Prepare training data | `python scripts/training/prepare_train.py` |
| `create_training_splits.py` | Create train/val/test splits | `python scripts/training/create_training_splits.py` |
| `split_parallel_dataset.py` | Split parallel dataset | `python scripts/training/split_parallel_dataset.py` |
| `convert_training.py` | Convert training format | `python scripts/training/convert_training.py` |
| `build_tedim_train_dataset.py` | Build Tedim training dataset | `python scripts/training/build_tedim_train_dataset.py` |

#### Synthesis Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `synthesize_instructions.py` | Synthesize instructions | `python scripts/training/synthesize_instructions.py` |
| `synthesize_instructions_v3.py` | Synthesize instructions v3 | `python scripts/training/synthesize_instructions_v3.py` |
| `synthesize_instructions_v4.py` | Synthesize instructions v4 | `python scripts/training/synthesize_instructions_v4.py` |
| `synthesize_instructions_v5.py` | Synthesize instructions v5 | `python scripts/training/synthesize_instructions_v5.py` |
| `synthesize_instructions_v6.py` | Synthesize instructions v6 | `python scripts/training/synthesize_instructions_v6.py` |
| `synthesize_instructions_bulk.py` | Bulk instruction synthesis | `python scripts/training/synthesize_instructions_bulk.py` |

#### Export Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `export_pipeline.py` | Export pipeline | `python scripts/training/export_pipeline.py` |
| `merge_adapter.py` | Merge LoRA adapters | `python scripts/training/merge_adapter.py` |

#### Utility Scripts
| Script | Purpose | Usage |
|--------|---------|-------|
| `tag_cefr_levels.py` | Tag CEFR levels | `python scripts/training/tag_cefr_levels.py` |
| `parse_training_logs.py` | Parse training logs | `python scripts/training/parse_training_logs.py` |

### 3. Migration Scripts (`scripts/migration/`)

These scripts handle database migrations.

#### Phase 2a
| Script | Purpose | Usage |
|--------|---------|-------|
| `migrate_data.py` | Migrate data | `python scripts/migration/phase2a/migrate_data.py` |
| `validate.py` | Validate migration | `python scripts/migration/phase2a/validate.py` |

#### Phase 2b
| Script | Purpose | Usage |
|--------|---------|-------|
| `migrate_derived.py` | Migrate derived tables | `python scripts/migration/phase2b/migrate_derived.py` |
| `validate_all.py` | Validate all migrations | `python scripts/migration/phase2b/validate_all.py` |

#### Phase 2c
| Script | Purpose | Usage |
|--------|---------|-------|
| `dedup_core.py` | Deduplicate core tables | `python scripts/migration/phase2c/dedup_core.py` |
| `validate_dedup.py` | Validate deduplication | `python scripts/migration/phase2c/validate_dedup.py` |

#### Phase 2d
| Script | Purpose | Usage |
|--------|---------|-------|
| `validate_cutover.py` | Validate cutover | `python scripts/migration/phase2d/validate_cutover.py` |
| `verify_switch.py` | Verify switch | `python scripts/migration/phase2d/verify_switch.py` |
| `cleanup_old.py` | Cleanup old tables | `python scripts/migration/phase2d/cleanup_old.py` |

### 4. Knowledge Graph Scripts (`scripts/kg/`)

These scripts handle knowledge graph operations.

| Script | Purpose | Usage |
|--------|---------|-------|
| `extract_zsp.py` | Extract ZSP patterns | `python scripts/kg/extract_zsp.py` |
| `extract_pipeline.py` | Extraction pipeline | `python scripts/kg/extract_pipeline.py` |
| `ingest_wiki.py` | Ingest wiki data | `python scripts/kg/ingest_wiki.py` |
| `embed.py` | Generate embeddings | `python scripts/kg/embed.py` |
| `predict_test.py` | Prediction tests | `python scripts/kg/predict_test.py` |
| `smoke_test.py` | Smoke tests | `python scripts/kg/smoke_test.py` |

### 5. Utility Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/dedup_analysis.py` | Deduplication analysis | `python scripts/dedup_analysis.py` |
| `scripts/validate_zolai_auto_import.py` | Validate auto import | `python scripts/validate_zolai_auto_import.py` |
| `scripts/validate_zolai_official_api.py` | Validate official API | `python scripts/validate_zolai_official_api.py` |
| `scripts/validate_zolai_webapi_fixed.py` | Validate web API | `python scripts/validate_zolai_webapi_fixed.py` |
| `scripts/zvs_api.py` | ZVS API script | `python scripts/zvs_api.py` |
| `scripts/local_translation_validator.py` | Local translation validator | `python scripts/local_translation_validator.py` |

## Foundation Engine Scripts

The Foundation Engine has its own set of scripts accessed via CLI:

```bash
# Analysis
zolai foundation analyze <type> <text>

# ETL
zolai foundation etl [--batch-size=N]

# Verification
zolai foundation verify [--batch-size=N] [--concurrency=N]

# Review
zolai foundation review list|approve|reject

# Gold evaluation
zolai foundation gold-eval
```

## Script Configuration

### Environment Variables

Most scripts use environment variables for configuration:

```bash
# Database
ZOLAI_PG_URL=postgresql://user:pass@localhost:5432/zolai
ZOLAI_DB_PATH=/path/to/zolai.db

# API Keys
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key

# Paths
DATA_DIR=/path/to/data
CORPUS_DIR=/path/to/corpus
DICTIONARY_DIR=/path/to/dictionary
```

### Configuration Files

Some scripts use configuration files:

- `config/settings.yaml` - General settings
- `config/training.yaml` - Training configuration
- `config/migration.yaml` - Migration settings

## Running Scripts

### General Pattern

```bash
# From zolai-core directory
python scripts/category/script_name.py [options]

# With environment variables
DATA_DIR=/data python scripts/category/script_name.py

# With configuration
python scripts/category/script_name.py --config config/settings.yaml
```

### Common Options

Most scripts support these options:

```bash
--help              # Show help
--dry-run           # Preview changes without applying
--verbose           # Verbose output
--batch-size N      # Process in batches of N
--limit N           # Process only N items
--offset N          # Start from offset N
```

## Error Handling

Scripts use structured error handling:

1. **Validation errors**: Input validation failures
2. **Connection errors**: Database or API connection issues
3. **Processing errors**: Data processing failures
4. **Resource errors**: Memory or disk space issues

## Logging

Scripts use Python logging:

```bash
# Enable debug logging
ZOLAI_LOG_LEVEL=DEBUG python scripts/category/script_name.py

# Log to file
python scripts/category/script_name.py 2>&1 | tee log.txt
```

## Performance Tips

1. **Batch processing**: Use `--batch-size` for large datasets
2. **Parallel processing**: Use `--concurrency` for independent tasks
3. **Memory management**: Process in chunks for large files
4. **Caching**: Some scripts cache intermediate results

## Troubleshooting

### Common Issues

1. **Import errors**: Ensure `pip install -e .` is run
2. **Database errors**: Check `ZOLAI_DB_PATH` or `ZOLAI_PG_URL`
3. **API errors**: Verify API keys in `.env`
4. **Memory errors**: Reduce batch size or process in chunks

### Debug Mode

```bash
# Enable debug output
python scripts/category/script_name.py --debug

# Check logs
tail -f logs/zolai.log
```

### Validation

```bash
# Validate data
python scripts/validate_zolai_auto_import.py

# Check database
python scripts/migration/phase2b/validate_all.py
```

## Contributing

When adding new scripts:

1. Place in appropriate category directory
2. Add docstring with purpose and usage
3. Support `--help` option
4. Use environment variables for configuration
5. Add error handling and logging
6. Update this documentation