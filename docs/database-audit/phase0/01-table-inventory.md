# Phase 0 Database Audit — Table Inventory

**Generated:** 2026-09-13 20:31
**Total tables:** 72

## `articles`
| Property | Value |
|----------|-------|
| Row count | 6,371 |
| Columns | 8 |
| Primary key | id |
| Indexes | 0 |
| Classification | `Wiki/Content` |
| Domain | Wiki/Content |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `title` | TEXT | no | `` |  |
| 2 | `content` | TEXT | no | `` |  |
| 3 | `excerpt` | TEXT | no | `` |  |
| 4 | `categories` | TEXT | no | `` |  |
| 5 | `date` | TEXT | no | `` |  |
| 6 | `link` | TEXT | no | `` |  |
| 7 | `language` | TEXT | no | `'zolai'` |  |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `date` | 4,163 | 65.3% |
| `link` | 4,163 | 65.3% |
| `categories` | 6 | 0.1% |
| `title` | 1 | 0.0% |

## `audit_findings`
| Property | Value |
|----------|-------|
| Row count | 713 |
| Columns | 10 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Audit` |
| Domain | Audit |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `finding_type` | TEXT | YES | `` |  |
| 2 | `word` | TEXT | YES | `` |  |
| 3 | `old_value` | TEXT | no | `` |  |
| 4 | `new_value` | TEXT | no | `` |  |
| 5 | `source` | TEXT | no | `` |  |
| 6 | `confidence` | TEXT | no | `` |  |
| 7 | `created_at` | TEXT | no | `` |  |
| 8 | `verified_by` | TEXT | no | `'system'` |  |
| 9 | `entry_id` | INTEGER | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_audit_type` | no | finding_type |
| `idx_audit_word` | no | word |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `entry_id` | 700 | 98.2% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `verified_by` | 700 | 98.2% |
| `old_value` | 500 | 70.1% |
| `new_value` | 500 | 70.1% |

## `bible_book_analysis_import`
| Property | Value |
|----------|-------|
| Row count | 65 |
| Columns | 13 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `book` | TEXT | no | `` |  |
| 1 | `book_name` | TEXT | no | `` |  |
| 2 | `genre` | TEXT | no | `` |  |
| 3 | `verse_count` | INTEGER | no | `` |  |
| 4 | `unique_words` | INTEGER | no | `` |  |
| 5 | `total_tokens` | INTEGER | no | `` |  |
| 6 | `avg_sentence_length` | REAL | no | `` |  |
| 7 | `top_words` | TEXT | no | `` |  |
| 8 | `book_specific_words` | TEXT | no | `` |  |
| 9 | `import_batch_id` | TEXT | no | `` |  |
| 10 | `source_file` | TEXT | no | `` |  |
| 11 | `version` | INTEGER | no | `1` |  |
| 12 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_bible_book_analysis_import_batch` | no | import_batch_id |

## `bible_chapter_analysis_import`
| Property | Value |
|----------|-------|
| Row count | 1,153 |
| Columns | 11 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `book` | TEXT | no | `` |  |
| 1 | `chapter` | TEXT | no | `` |  |
| 2 | `verse_count` | INTEGER | no | `` |  |
| 3 | `unique_words` | INTEGER | no | `` |  |
| 4 | `avg_length` | REAL | no | `` |  |
| 5 | `top_words` | TEXT | no | `` |  |
| 6 | `theme_words` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_bible_chapter_analysis_import_batch` | no | import_batch_id |

## `bible_context`
| Property | Value |
|----------|-------|
| Row count | 1,228 |
| Columns | 5 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Bible` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `book` | VARCHAR | YES | `` |  |
| 2 | `chapter` | INTEGER | no | `` |  |
| 3 | `analysis_type` | VARCHAR | YES | `` |  |
| 4 | `data` | TEXT | YES | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_bible_context_book` | no | book |
| `ix_bible_context_book` | no | book |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `chapter` | 76 | 6.2% |

## `bible_verses`
| Property | Value |
|----------|-------|
| Row count | 62,751 |
| Columns | 18 |
| Primary key | id |
| Indexes | 4 |
| Classification | `Bible` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `ref` | VARCHAR | YES | `` |  |
| 2 | `book` | VARCHAR | YES | `` |  |
| 3 | `chapter` | INTEGER | YES | `` |  |
| 4 | `verse` | INTEGER | YES | `` |  |
| 5 | `zo_tdb77` | TEXT | no | `` |  |
| 6 | `zo_tedim2010` | TEXT | no | `` |  |
| 7 | `en_kJV` | TEXT | no | `` |  |
| 8 | `myanmar` | TEXT | no | `` |  |
| 9 | `zo_tedim1932` | TEXT | no | `` |  |
| 10 | `zo_hcl06` | TEXT | no | `` |  |
| 11 | `zo_fcl` | TEXT | no | `` |  |
| 12 | `myanmar_judson` | TEXT | no | `` |  |
| 13 | `book_name` | TEXT | no | `` |  |
| 14 | `import_batch_id` | TEXT | no | `` |  |
| 15 | `source_file` | TEXT | no | `` |  |
| 16 | `version` | INTEGER | no | `1` |  |
| 17 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_bible_verses_batch` | no | import_batch_id |
| `ix_bible_verses_ref` | no | ref |
| `ix_bible_verses_book` | no | book |
| `ix_bible_book_chapter_verse` | no | book, chapter, verse |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `zo_hcl06` | 34,570 | 55.1% |
| `myanmar` | 33,966 | 54.1% |
| `zo_fcl` | 33,095 | 52.7% |
| `zo_tedim1932` | 31,963 | 50.9% |
| `myanmar_judson` | 31,696 | 50.5% |
| `import_batch_id` | 31,649 | 50.4% |
| `source_file` | 31,649 | 50.4% |
| `imported_at` | 31,649 | 50.4% |
| `zo_tedim2010` | 1,893 | 3.0% |
| `zo_tdb77` | 1,797 | 2.9% |
| `en_kJV` | 1,463 | 2.3% |

## `bible_verses_enhanced`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 12 |
| Primary key | id |
| Indexes | 0 |
| Classification | `EMPTY_PLANNED` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `book` | TEXT | no | `` |  |
| 2 | `chapter` | INTEGER | no | `` |  |
| 3 | `verse` | INTEGER | no | `` |  |
| 4 | `zolai_text` | TEXT | no | `` |  |
| 5 | `english_text` | TEXT | no | `` |  |
| 6 | `myanmar_text` | TEXT | no | `` |  |
| 7 | `tone_analysis` | TEXT | no | `` |  |
| 8 | `grammar_analysis` | TEXT | no | `` |  |
| 9 | `vocabulary_notes` | TEXT | no | `` |  |
| 10 | `source_version` | TEXT | no | `` |  |
| 11 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

## `bible_verses_import`
| Property | Value |
|----------|-------|
| Row count | 62,204 |
| Columns | 12 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `book` | TEXT | no | `` |  |
| 1 | `book_name` | TEXT | no | `` |  |
| 2 | `chapter` | TEXT | no | `` |  |
| 3 | `verse` | TEXT | no | `` |  |
| 4 | `ref` | TEXT | no | `` |  |
| 5 | `zo_tdb77` | TEXT | no | `` |  |
| 6 | `zo_tedim2010` | TEXT | no | `` |  |
| 7 | `en_kJV` | TEXT | no | `` |  |
| 8 | `import_batch_id` | TEXT | no | `` |  |
| 9 | `source_file` | TEXT | no | `` |  |
| 10 | `version` | INTEGER | no | `1` |  |
| 11 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_bible_verses_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `en_kJV` | 2,888 | 4.6% |
| `zo_tdb77` | 1,812 | 2.9% |
| `zo_tedim2010` | 818 | 1.3% |

## `data_audit_log`
| Property | Value |
|----------|-------|
| Row count | 24,762 |
| Columns | 8 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Audit` |
| Domain | Audit |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `table_name` | VARCHAR | YES | `` |  |
| 2 | `row_id` | INTEGER | YES | `` |  |
| 3 | `field` | VARCHAR | YES | `` |  |
| 4 | `old_value` | TEXT | no | `` |  |
| 5 | `new_value` | TEXT | no | `` |  |
| 6 | `changed_at` | VARCHAR | YES | `` |  |
| 7 | `reason` | TEXT | YES | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_audit_table_row` | no | table_name, row_id |
| `ix_data_audit_log_table_name` | no | table_name |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `new_value` | 533 | 2.2% |
| `old_value` | 16 | 0.1% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `old_value` | 21,847 | 88.2% |
| `new_value` | 30 | 0.1% |

## `dictionary`
| Property | Value |
|----------|-------|
| Row count | 103,303 |
| Columns | 18 |
| Primary key | id |
| Indexes | 4 |
| Classification | `Dictionary` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `zolai` | VARCHAR | YES | `` |  |
| 2 | `english` | TEXT | YES | `` |  |
| 3 | `english_clean` | VARCHAR | no | `` |  |
| 4 | `source` | VARCHAR | YES | `` |  |
| 5 | `pos` | VARCHAR | YES | `` |  |
| 6 | `myanmar` | TEXT | no | `` |  |
| 7 | `entry_version` | TEXT | no | `'v1.0'` |  |
| 8 | `update_remarks` | TEXT | no | `''` |  |
| 9 | `update_description` | TEXT | no | `''` |  |
| 10 | `zvs_compliance_status` | TEXT | no | `'pending'` |  |
| 11 | `updated_at` | TEXT | no | `` |  |
| 12 | `import_batch_id` | TEXT | no | `` |  |
| 13 | `source_file` | TEXT | no | `` |  |
| 14 | `version` | INTEGER | no | `1` |  |
| 15 | `imported_at` | TEXT | no | `` |  |
| 16 | `is_deleted` | INTEGER | no | `0` |  |
| 17 | `deleted_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_batch` | no | import_batch_id |
| `idx_dict_zolai` | no | zolai |
| `ix_dict_zolai_source` | no | zolai, source |
| `ix_dictionary_zolai` | no | zolai |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `import_batch_id` | 103,303 | 100.0% |
| `source_file` | 103,303 | 100.0% |
| `imported_at` | 103,303 | 100.0% |
| `deleted_at` | 103,303 | 100.0% |
| `myanmar` | 97,124 | 94.0% |
| `updated_at` | 80,539 | 78.0% |
| `english_clean` | 22,480 | 21.8% |
| `update_remarks` | 1 | 0.0% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `update_description` | 103,094 | 99.8% |
| `pos` | 98,663 | 95.5% |
| `update_remarks` | 88,464 | 85.6% |
| `english_clean` | 12,571 | 12.2% |

## `dictionary_en_my_import`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 7 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `myanmar` | TEXT | no | `` |  |
| 1 | `zolai` | TEXT | no | `` |  |
| 2 | `source` | TEXT | no | `` |  |
| 3 | `import_batch_id` | TEXT | no | `` |  |
| 4 | `source_file` | TEXT | no | `` |  |
| 5 | `version` | INTEGER | no | `1` |  |
| 6 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_en_my_import_batch` | no | import_batch_id |

## `dictionary_en_zo`
| Property | Value |
|----------|-------|
| Row count | 113,750 |
| Columns | 18 |
| Primary key | id |
| Indexes | 3 |
| Classification | `Dictionary` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `headword` | VARCHAR | YES | `` |  |
| 2 | `translations` | TEXT | YES | `` |  |
| 3 | `translations_clean` | VARCHAR | no | `` |  |
| 4 | `pos` | VARCHAR | no | `` |  |
| 5 | `source` | VARCHAR | YES | `` |  |
| 6 | `myanmar` | TEXT | no | `` |  |
| 7 | `entry_version` | TEXT | no | `'v1.0'` |  |
| 8 | `update_remarks` | TEXT | no | `''` |  |
| 9 | `update_description` | TEXT | no | `''` |  |
| 10 | `zvs_compliance_status` | TEXT | no | `'pending'` |  |
| 11 | `updated_at` | TEXT | no | `NULL` |  |
| 12 | `import_batch_id` | TEXT | no | `` |  |
| 13 | `source_file` | TEXT | no | `` |  |
| 14 | `version` | INTEGER | no | `1` |  |
| 15 | `imported_at` | TEXT | no | `` |  |
| 16 | `is_deleted` | INTEGER | no | `0` |  |
| 17 | `deleted_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_en_zo_batch` | no | import_batch_id |
| `ix_dictionary_en_zo_headword` | no | headword |
| `idx_en_zo_headword` | no | headword |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `updated_at` | 113,750 | 100.0% |
| `import_batch_id` | 113,750 | 100.0% |
| `source_file` | 113,750 | 100.0% |
| `imported_at` | 113,750 | 100.0% |
| `deleted_at` | 113,750 | 100.0% |
| `myanmar` | 113,748 | 100.0% |
| `pos` | 5,964 | 5.2% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `update_description` | 113,750 | 100.0% |
| `translations_clean` | 88,547 | 77.8% |
| `update_remarks` | 62,128 | 54.6% |
| `pos` | 22,445 | 19.7% |

## `dictionary_en_zo_import`
| Property | Value |
|----------|-------|
| Row count | 135,276 |
| Columns | 38 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `headword` | TEXT | no | `` |  |
| 1 | `translations` | TEXT | no | `` |  |
| 2 | `pos` | TEXT | no | `` |  |
| 3 | `explanations` | TEXT | no | `` |  |
| 4 | `sources` | TEXT | no | `` |  |
| 5 | `category` | TEXT | no | `` |  |
| 6 | `translations_clean` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |
| 11 | `english` | TEXT | no | `` |  |
| 12 | `zolai` | TEXT | no | `` |  |
| 13 | `accuracy` | TEXT | no | `` |  |
| 14 | `book_count` | INTEGER | no | `` |  |
| 15 | `source` | TEXT | no | `` |  |
| 16 | `all_books` | TEXT | no | `` |  |
| 17 | `dialect` | TEXT | no | `` |  |
| 18 | `first_book` | TEXT | no | `` |  |
| 19 | `zvs_correction` | TEXT | no | `` |  |
| 20 | `variants` | TEXT | no | `` |  |
| 21 | `examples` | TEXT | no | `` |  |
| 22 | `zvs_correct` | INTEGER | no | `` |  |
| 23 | `same_meaning` | TEXT | no | `` |  |
| 24 | `antonyms` | TEXT | no | `` |  |
| 25 | `reverse` | TEXT | no | `` |  |
| 26 | `related` | TEXT | no | `` |  |
| 27 | `usage_notes` | TEXT | no | `` |  |
| 28 | `synonyms` | TEXT | no | `` |  |
| 29 | `example_en` | TEXT | no | `` |  |
| 30 | `example_zo` | TEXT | no | `` |  |
| 31 | `note` | TEXT | no | `` |  |
| 32 | `original_english` | TEXT | no | `` |  |
| 33 | `cefr` | TEXT | no | `` |  |
| 34 | `example_zo_2` | TEXT | no | `` |  |
| 35 | `example_en_2` | TEXT | no | `` |  |
| 36 | `is_deleted` | INTEGER | no | `0` |  |
| 37 | `deleted_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_en_zo_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `deleted_at` | 135,276 | 100.0% |
| `example_zo_2` | 135,274 | 100.0% |
| `example_en_2` | 135,274 | 100.0% |
| `book_count` | 135,176 | 99.9% |
| `all_books` | 135,176 | 99.9% |
| `first_book` | 135,176 | 99.9% |
| `original_english` | 134,778 | 99.6% |
| `note` | 134,283 | 99.3% |
| `cefr` | 133,275 | 98.5% |
| `example_en` | 132,483 | 97.9% |
| `example_zo` | 132,483 | 97.9% |
| `zvs_correction` | 128,385 | 94.9% |
| `variants` | 128,385 | 94.9% |
| `examples` | 128,385 | 94.9% |
| `zvs_correct` | 128,385 | 94.9% |
| `same_meaning` | 128,385 | 94.9% |
| `antonyms` | 128,385 | 94.9% |
| `reverse` | 128,385 | 94.9% |
| `related` | 128,385 | 94.9% |
| `usage_notes` | 128,385 | 94.9% |
| `accuracy` | 128,285 | 94.8% |
| `synonyms` | 127,373 | 94.2% |
| `english` | 124,252 | 91.9% |
| `zolai` | 124,252 | 91.9% |
| `source` | 124,252 | 91.9% |
| `dialect` | 124,252 | 91.9% |
| `headword` | 11,024 | 8.1% |
| `explanations` | 11,024 | 8.1% |
| `sources` | 11,024 | 8.1% |
| `translations` | 4,033 | 3.0% |
| `pos` | 101 | 0.1% |
| `category` | 100 | 0.1% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `translations_clean` | 12,454 | 9.2% |
| `zvs_correction` | 6,891 | 5.1% |
| `note` | 495 | 0.4% |

## `dictionary_enhanced`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 15 |
| Primary key | id |
| Indexes | 1 |
| Classification | `EMPTY_PLANNED` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `zolai` | TEXT | YES | `` |  |
| 2 | `english` | TEXT | no | `` |  |
| 3 | `english_clean` | TEXT | no | `` |  |
| 4 | `myanmar` | TEXT | no | `` |  |
| 5 | `pos` | TEXT | no | `` |  |
| 6 | `tone_notes` | TEXT | no | `` |  |
| 7 | `source_category` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `confidence` | REAL | no | `1.0` |  |
| 10 | `word_count` | INTEGER | no | `` |  |
| 11 | `is_compound` | INTEGER | no | `0` |  |
| 12 | `compound_parts` | TEXT | no | `` |  |
| 13 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |
| 14 | `updated_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `sqlite_autoindex_dictionary_enhanced_1` | YES | zolai, source_file |

## `dictionary_import`
| Property | Value |
|----------|-------|
| Row count | 156,808 |
| Columns | 30 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `english` | TEXT | no | `` |  |
| 2 | `source` | TEXT | no | `` |  |
| 3 | `pos` | TEXT | no | `` |  |
| 4 | `entry_version` | TEXT | no | `` |  |
| 5 | `update_remarks` | TEXT | no | `` |  |
| 6 | `update_description` | TEXT | no | `` |  |
| 7 | `zvs_compliance_status` | TEXT | no | `` |  |
| 8 | `id` | INTEGER | no | `` |  |
| 9 | `raw_json` | TEXT | no | `` |  |
| 10 | `import_batch_id` | TEXT | no | `` |  |
| 11 | `source_file` | TEXT | no | `` |  |
| 12 | `version` | INTEGER | no | `1` |  |
| 13 | `imported_at` | TEXT | no | `` |  |
| 14 | `existing_match` | INTEGER | no | `` |  |
| 15 | `example` | TEXT | no | `` |  |
| 16 | `sense` | TEXT | no | `` |  |
| 17 | `myanmar_word` | TEXT | no | `` |  |
| 18 | `headword` | TEXT | no | `` |  |
| 19 | `zolai_word` | TEXT | no | `` |  |
| 20 | `zolai_normalized` | TEXT | no | `` |  |
| 21 | `translation` | TEXT | no | `` |  |
| 22 | `first_book` | TEXT | no | `` |  |
| 23 | `all_books` | TEXT | no | `` |  |
| 24 | `accuracy` | TEXT | no | `` |  |
| 25 | `translations` | TEXT | no | `` |  |
| 26 | `dialect` | TEXT | no | `` |  |
| 27 | `book_count` | INTEGER | no | `` |  |
| 28 | `is_deleted` | INTEGER | no | `0` |  |
| 29 | `deleted_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `deleted_at` | 156,808 | 100.0% |
| `first_book` | 154,125 | 98.3% |
| `all_books` | 154,125 | 98.3% |
| `accuracy` | 154,125 | 98.3% |
| `dialect` | 154,125 | 98.3% |
| `book_count` | 154,125 | 98.3% |
| `translations` | 152,735 | 97.4% |
| `existing_match` | 148,967 | 95.0% |
| `example` | 148,967 | 95.0% |
| `sense` | 148,967 | 95.0% |
| `myanmar_word` | 148,967 | 95.0% |
| `zolai_word` | 148,967 | 95.0% |
| `zolai_normalized` | 148,967 | 95.0% |
| `translation` | 148,967 | 95.0% |
| `headword` | 147,577 | 94.1% |
| `entry_version` | 11,914 | 7.6% |
| `update_remarks` | 11,914 | 7.6% |
| `update_description` | 11,914 | 7.6% |
| `zvs_compliance_status` | 11,914 | 7.6% |
| `id` | 11,914 | 7.6% |
| `raw_json` | 11,914 | 7.6% |
| `zolai` | 9,231 | 5.9% |
| `english` | 9,231 | 5.9% |
| `pos` | 4,073 | 2.6% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `pos` | 144,894 | 92.4% |
| `update_remarks` | 144,589 | 92.2% |
| `update_description` | 144,589 | 92.2% |
| `myanmar_word` | 7,841 | 5.0% |

## `dictionary_my_import`
| Property | Value |
|----------|-------|
| Row count | 7,840 |
| Columns | 7 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `myanmar` | TEXT | no | `` |  |
| 2 | `source` | TEXT | no | `` |  |
| 3 | `import_batch_id` | TEXT | no | `` |  |
| 4 | `source_file` | TEXT | no | `` |  |
| 5 | `version` | INTEGER | no | `1` |  |
| 6 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_my_import_batch` | no | import_batch_id |

## `dictionary_trilingual_import`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 8 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Dictionary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `myanmar` | TEXT | no | `` |  |
| 2 | `english` | TEXT | no | `` |  |
| 3 | `source` | TEXT | no | `` |  |
| 4 | `import_batch_id` | TEXT | no | `` |  |
| 5 | `source_file` | TEXT | no | `` |  |
| 6 | `version` | INTEGER | no | `1` |  |
| 7 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_dictionary_trilingual_import_batch` | no | import_batch_id |

## `gemini_model_results`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 9 |
| Primary key | id |
| Indexes | 2 |
| Classification | `EMPTY_PLANNED` |
| Domain | ModelTracking |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `task` | TEXT | YES | `` |  |
| 2 | `model` | TEXT | YES | `` |  |
| 3 | `input_text` | TEXT | YES | `` |  |
| 4 | `output_json` | TEXT | YES | `` |  |
| 5 | `confidence` | REAL | no | `` |  |
| 6 | `ensemble_agreement` | REAL | no | `` |  |
| 7 | `source` | TEXT | no | `` |  |
| 8 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_gemini_model` | no | model |
| `idx_gemini_task` | no | task |

## `grammar_patterns`
| Property | Value |
|----------|-------|
| Row count | 5,547 |
| Columns | 12 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Grammar` |
| Domain | Grammar |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `pattern_id` | VARCHAR | YES | `` |  |
| 2 | `pattern` | VARCHAR | YES | `` |  |
| 3 | `description` | TEXT | no | `` |  |
| 4 | `function` | VARCHAR | YES | `` |  |
| 5 | `examples` | TEXT | YES | `` |  |
| 6 | `frequency` | INTEGER | YES | `` |  |
| 7 | `myanmar` | TEXT | no | `` |  |
| 8 | `import_batch_id` | TEXT | no | `` |  |
| 9 | `source_file` | TEXT | no | `` |  |
| 10 | `version` | INTEGER | no | `1` |  |
| 11 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_grammar_patterns_batch` | no | import_batch_id |
| `ix_grammar_patterns_pattern_id` | no | pattern_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `myanmar` | 5,547 | 100.0% |
| `import_batch_id` | 5,547 | 100.0% |
| `source_file` | 5,547 | 100.0% |
| `imported_at` | 5,547 | 100.0% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `description` | 1,175 | 21.2% |
| `function` | 1,175 | 21.2% |

## `grammar_patterns_enhanced`
| Property | Value |
|----------|-------|
| Row count | 5,597 |
| Columns | 13 |
| Primary key | id |
| Indexes | 0 |
| Classification | `Grammar` |
| Domain | Grammar |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `pattern_id` | TEXT | no | `` |  |
| 2 | `pattern_text` | TEXT | no | `` |  |
| 3 | `zolai_example` | TEXT | no | `` |  |
| 4 | `english_translation` | TEXT | no | `` |  |
| 5 | `pattern_type` | TEXT | no | `` |  |
| 6 | `tone_category` | TEXT | no | `` |  |
| 7 | `source_category` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `page_section` | TEXT | no | `` |  |
| 10 | `frequency` | INTEGER | no | `1` |  |
| 11 | `confidence` | REAL | no | `1.0` |  |
| 12 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `pattern_id` | 5,597 | 100.0% |
| `zolai_example` | 5,597 | 100.0% |
| `english_translation` | 5,597 | 100.0% |
| `tone_category` | 5,597 | 100.0% |
| `page_section` | 5,597 | 100.0% |

## `grammar_patterns_import`
| Property | Value |
|----------|-------|
| Row count | 6,983 |
| Columns | 19 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Grammar |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | TEXT | no | `` |  |
| 1 | `pattern` | TEXT | no | `` |  |
| 2 | `description` | TEXT | no | `` |  |
| 3 | `structure` | TEXT | no | `` |  |
| 4 | `function` | TEXT | no | `` |  |
| 5 | `frequency` | INTEGER | no | `` |  |
| 6 | `confidence` | REAL | no | `` |  |
| 7 | `examples` | TEXT | no | `` |  |
| 8 | `source` | TEXT | no | `` |  |
| 9 | `book` | TEXT | no | `` |  |
| 10 | `import_batch_id` | TEXT | no | `` |  |
| 11 | `source_file` | TEXT | no | `` |  |
| 12 | `version` | INTEGER | no | `1` |  |
| 13 | `imported_at` | TEXT | no | `` |  |
| 14 | `category` | TEXT | no | `` |  |
| 15 | `data` | TEXT | no | `` |  |
| 16 | `zo` | TEXT | no | `` |  |
| 17 | `en` | TEXT | no | `` |  |
| 18 | `ref` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_grammar_patterns_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `data` | 6,982 | 100.0% |
| `zo` | 5,483 | 78.5% |
| `en` | 5,483 | 78.5% |
| `ref` | 5,483 | 78.5% |
| `category` | 5,482 | 78.5% |
| `id` | 1,501 | 21.5% |
| `description` | 1,501 | 21.5% |
| `structure` | 1,501 | 21.5% |
| `function` | 1,501 | 21.5% |
| `frequency` | 1,501 | 21.5% |
| `confidence` | 1,501 | 21.5% |
| `examples` | 1,501 | 21.5% |
| `source` | 1,501 | 21.5% |
| `book` | 1,501 | 21.5% |
| `pattern` | 1 | 0.0% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `book` | 5,482 | 78.5% |
| `description` | 1,175 | 16.8% |
| `structure` | 1,175 | 16.8% |
| `function` | 1,175 | 16.8% |

## `jsonl_import_log`
| Property | Value |
|----------|-------|
| Row count | 92 |
| Columns | 10 |
| Primary key | id |
| Indexes | 2 |
| Classification | `IMPORT_STAGING` |
| Domain | Audit |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `batch_id` | VARCHAR(36) | YES | `` |  |
| 2 | `source_file` | VARCHAR(500) | YES | `` |  |
| 3 | `table_name` | VARCHAR(100) | YES | `` |  |
| 4 | `rows_imported` | INTEGER | YES | `` |  |
| 5 | `sha256` | VARCHAR(64) | no | `` |  |
| 6 | `imported_at` | DATETIME | YES | `` |  |
| 7 | `version` | INTEGER | YES | `` |  |
| 8 | `status` | VARCHAR(20) | YES | `` |  |
| 9 | `error_message` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `ix_jsonl_import_log_table_name` | no | table_name |
| `ix_jsonl_import_log_batch_id` | no | batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `error_message` | 43 | 46.7% |

## `morph_verified`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 8 |
| Primary key | id |
| Indexes | 1 |
| Classification | `EMPTY_PLANNED` |
| Domain | NLP/Experimental |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `word` | TEXT | YES | `` |  |
| 2 | `morphemes` | TEXT | YES | `` |  |
| 3 | `root` | TEXT | no | `` |  |
| 4 | `POS` | TEXT | no | `` |  |
| 5 | `analysis_json` | TEXT | no | `` |  |
| 6 | `source` | TEXT | no | `'gemini'` |  |
| 7 | `verified_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `sqlite_autoindex_morph_verified_1` | YES | word |

## `phrase_context_import`
| Property | Value |
|----------|-------|
| Row count | 45,597 |
| Columns | 10 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Phrase |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `phrase` | TEXT | no | `` |  |
| 1 | `type` | TEXT | no | `` |  |
| 2 | `frequency` | INTEGER | no | `` |  |
| 3 | `locations` | TEXT | no | `` |  |
| 4 | `context_words` | TEXT | no | `` |  |
| 5 | `is_idiomatic` | INTEGER | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_phrase_context_import_batch` | no | import_batch_id |

## `phrases`
| Property | Value |
|----------|-------|
| Row count | 5,000 |
| Columns | 9 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Phrase` |
| Domain | Phrase |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `zo` | VARCHAR | YES | `` |  |
| 2 | `english` | VARCHAR | YES | `` |  |
| 3 | `frequency` | INTEGER | YES | `` |  |
| 4 | `examples` | TEXT | YES | `` |  |
| 5 | `import_batch_id` | TEXT | no | `` |  |
| 6 | `source_file` | TEXT | no | `` |  |
| 7 | `version` | INTEGER | no | `1` |  |
| 8 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_phrases_batch` | no | import_batch_id |
| `ix_phrases_zo` | no | zo |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `import_batch_id` | 5,000 | 100.0% |
| `source_file` | 5,000 | 100.0% |
| `imported_at` | 5,000 | 100.0% |

## `phrases_from_bible_import`
| Property | Value |
|----------|-------|
| Row count | 14,000 |
| Columns | 8 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Phrase |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `phrase` | TEXT | no | `` |  |
| 1 | `frequency` | INTEGER | no | `` |  |
| 2 | `type` | TEXT | no | `` |  |
| 3 | `examples` | TEXT | no | `` |  |
| 4 | `import_batch_id` | TEXT | no | `` |  |
| 5 | `source_file` | TEXT | no | `` |  |
| 6 | `version` | INTEGER | no | `1` |  |
| 7 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_phrases_from_bible_import_batch` | no | import_batch_id |

## `phrases_import`
| Property | Value |
|----------|-------|
| Row count | 15,000 |
| Columns | 12 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Phrase |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zo` | TEXT | no | `` |  |
| 1 | `frequency` | INTEGER | no | `` |  |
| 2 | `examples` | TEXT | no | `` |  |
| 3 | `confidence` | TEXT | no | `` |  |
| 4 | `import_batch_id` | TEXT | no | `` |  |
| 5 | `source_file` | TEXT | no | `` |  |
| 6 | `version` | INTEGER | no | `1` |  |
| 7 | `imported_at` | TEXT | no | `` |  |
| 8 | `phrase` | TEXT | no | `` |  |
| 9 | `verified` | INTEGER | no | `` |  |
| 10 | `source` | TEXT | no | `` |  |
| 11 | `translation` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_phrases_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `phrase` | 10,000 | 66.7% |
| `verified` | 10,000 | 66.7% |
| `source` | 10,000 | 66.7% |
| `translation` | 10,000 | 66.7% |
| `zo` | 5,000 | 33.3% |
| `examples` | 5,000 | 33.3% |
| `confidence` | 5,000 | 33.3% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `translation` | 4,819 | 32.1% |

## `pos_gold`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 7 |
| Primary key | id |
| Indexes | 0 |
| Classification | `EMPTY_PLANNED` |
| Domain | NLP/Experimental |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `sentence` | TEXT | YES | `` |  |
| 2 | `tokens` | TEXT | YES | `` |  |
| 3 | `tags` | TEXT | YES | `` |  |
| 4 | `source_verse` | TEXT | no | `` |  |
| 5 | `source` | TEXT | no | `'gemini'` |  |
| 6 | `created_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

## `pos_verified`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 7 |
| Primary key | id |
| Indexes | 1 |
| Classification | `EMPTY_PLANNED` |
| Domain | NLP/Experimental |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `word` | TEXT | YES | `` |  |
| 2 | `pos_tag` | TEXT | YES | `` |  |
| 3 | `confidence` | REAL | no | `1.0` |  |
| 4 | `reason` | TEXT | no | `` |  |
| 5 | `source` | TEXT | no | `'gemini'` |  |
| 6 | `verified_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `sqlite_autoindex_pos_verified_1` | YES | word |

## `provenance`
| Property | Value |
|----------|-------|
| Row count | 255 |
| Columns | 11 |
| Primary key | id |
| Indexes | 1 |
| Classification | `Provenance` |
| Domain | Provenance |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `filename` | VARCHAR | YES | `` |  |
| 2 | `size_bytes` | INTEGER | YES | `` |  |
| 3 | `sha256` | VARCHAR | YES | `` |  |
| 4 | `row_count` | INTEGER | YES | `` |  |
| 5 | `source` | VARCHAR | YES | `` |  |
| 6 | `generator_script` | VARCHAR | YES | `` |  |
| 7 | `version` | VARCHAR | YES | `` |  |
| 8 | `status` | VARCHAR | YES | `` |  |
| 9 | `updated_at` | VARCHAR | YES | `` |  |
| 10 | `change_log` | TEXT | YES | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `ix_provenance_filename` | no | filename |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `updated_at` | 227 | 89.0% |

## `proverbs`
| Property | Value |
|----------|-------|
| Row count | 7,736 |
| Columns | 9 |
| Primary key | id |
| Indexes | 1 |
| Classification | `Proverbs` |
| Domain | Proverbs |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `zolai` | TEXT | YES | `` |  |
| 2 | `english` | TEXT | no | `` |  |
| 3 | `source` | VARCHAR | YES | `` |  |
| 4 | `category` | VARCHAR | no | `` |  |
| 5 | `import_batch_id` | TEXT | no | `` |  |
| 6 | `source_file` | TEXT | no | `` |  |
| 7 | `version` | INTEGER | no | `1` |  |
| 8 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_proverbs_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `import_batch_id` | 7,736 | 100.0% |
| `source_file` | 7,736 | 100.0% |
| `imported_at` | 7,736 | 100.0% |
| `english` | 29 | 0.4% |

## `proverbs_idioms`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 9 |
| Primary key | id |
| Indexes | 0 |
| Classification | `EMPTY_PLANNED` |
| Domain | Proverbs |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `zolai_text` | TEXT | YES | `` |  |
| 2 | `english_translation` | TEXT | no | `` |  |
| 3 | `literal_translation` | TEXT | no | `` |  |
| 4 | `category` | TEXT | no | `` |  |
| 5 | `source_category` | TEXT | no | `` |  |
| 6 | `source_file` | TEXT | no | `` |  |
| 7 | `cultural_notes` | TEXT | no | `` |  |
| 8 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

## `proverbs_import`
| Property | Value |
|----------|-------|
| Row count | 7,736 |
| Columns | 13 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Proverbs |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zo` | TEXT | no | `` |  |
| 1 | `en` | TEXT | no | `` |  |
| 2 | `book` | TEXT | no | `` |  |
| 3 | `chapter` | TEXT | no | `` |  |
| 4 | `verse` | TEXT | no | `` |  |
| 5 | `ref` | TEXT | no | `` |  |
| 6 | `type` | TEXT | no | `` |  |
| 7 | `is_proverbial_book` | INTEGER | no | `` |  |
| 8 | `has_idiom_pattern` | INTEGER | no | `` |  |
| 9 | `import_batch_id` | TEXT | no | `` |  |
| 10 | `source_file` | TEXT | no | `` |  |
| 11 | `version` | INTEGER | no | `1` |  |
| 12 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_proverbs_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `en` | 29 | 0.4% |

## `sentence_patterns_import`
| Property | Value |
|----------|-------|
| Row count | 65 |
| Columns | 13 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Grammar |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `book` | TEXT | no | `` |  |
| 1 | `book_name` | TEXT | no | `` |  |
| 2 | `genre` | TEXT | no | `` |  |
| 3 | `verse_count` | INTEGER | no | `` |  |
| 4 | `sov_rate` | REAL | no | `` |  |
| 5 | `negation_rate` | REAL | no | `` |  |
| 6 | `question_rate` | REAL | no | `` |  |
| 7 | `tense_distribution` | TEXT | no | `` |  |
| 8 | `avg_complexity` | REAL | no | `` |  |
| 9 | `import_batch_id` | TEXT | no | `` |  |
| 10 | `source_file` | TEXT | no | `` |  |
| 11 | `version` | INTEGER | no | `1` |  |
| 12 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_sentence_patterns_import_batch` | no | import_batch_id |

## `sqlite_sequence`
| Property | Value |
|----------|-------|
| Row count | 15 |
| Columns | 2 |
| Primary key | ❌ **NONE** |
| Indexes | 0 |
| Classification | `SYSTEM` |
| Domain | System |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `name` |  | no | `` |  |
| 1 | `seq` |  | no | `` |  |

## `syllable_data`
| Property | Value |
|----------|-------|
| Row count | 189,554 |
| Columns | 10 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Syllable` |
| Domain | Syllable |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `word` | TEXT | YES | `` |  |
| 2 | `syllables` | TEXT | YES | `` |  |
| 3 | `syllable_count` | INTEGER | YES | `` |  |
| 4 | `engine` | TEXT | YES | `` |  |
| 5 | `confidence` | REAL | no | `1.0` |  |
| 6 | `source_table` | TEXT | no | `` |  |
| 7 | `source_id` | INTEGER | no | `` |  |
| 8 | `created_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |
| 9 | `updated_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_syllable_source` | no | source_table, source_id |
| `idx_syllable_word` | YES | word, engine |

## `tone_patterns`
| Property | Value |
|----------|-------|
| Row count | 118 |
| Columns | 10 |
| Primary key | id |
| Indexes | 0 |
| Classification | `Tone` |
| Domain | Tone |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `word` | TEXT | YES | `` |  |
| 2 | `tone_category` | TEXT | no | `` |  |
| 3 | `meaning_t1` | TEXT | no | `` |  |
| 4 | `meaning_t3` | TEXT | no | `` |  |
| 5 | `meaning_t4` | TEXT | no | `` |  |
| 6 | `sandhi_rules` | TEXT | no | `` |  |
| 7 | `source_category` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `meaning_t1` | 118 | 100.0% |
| `meaning_t3` | 118 | 100.0% |
| `meaning_t4` | 118 | 100.0% |
| `sandhi_rules` | 118 | 100.0% |

## `topic_clusters_import`
| Property | Value |
|----------|-------|
| Row count | 12 |
| Columns | 10 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | NLP/Experimental |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `topic` | TEXT | no | `` |  |
| 1 | `keyword_match_count` | INTEGER | no | `` |  |
| 2 | `characteristic_words` | TEXT | no | `` |  |
| 3 | `chapters` | TEXT | no | `` |  |
| 4 | `chapter_count` | INTEGER | no | `` |  |
| 5 | `usage_examples` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_topic_clusters_import_batch` | no | import_batch_id |

## `training_corpus_qwen3_import`
| Property | Value |
|----------|-------|
| Row count | 9,386 |
| Columns | 8 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Training |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `messages` | TEXT | no | `` |  |
| 1 | `task` | TEXT | no | `` |  |
| 2 | `pattern` | TEXT | no | `` |  |
| 3 | `source` | TEXT | no | `` |  |
| 4 | `import_batch_id` | TEXT | no | `` |  |
| 5 | `source_file` | TEXT | no | `` |  |
| 6 | `version` | INTEGER | no | `1` |  |
| 7 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_training_corpus_qwen3_import_batch` | no | import_batch_id |

## `training_exercises`
| Property | Value |
|----------|-------|
| Row count | 81,805 |
| Columns | 11 |
| Primary key | id |
| Indexes | 3 |
| Classification | `Training` |
| Domain | Training |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `exercise_type` | VARCHAR | YES | `` |  |
| 2 | `zolai` | TEXT | YES | `` |  |
| 3 | `english` | TEXT | YES | `` |  |
| 4 | `source` | VARCHAR | YES | `` |  |
| 5 | `difficulty` | VARCHAR | YES | `` |  |
| 6 | `myanmar` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_training_exercises_batch` | no | import_batch_id |
| `ix_training_exercises_exercise_type` | no | exercise_type |
| `idx_exercise_type` | no | exercise_type |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `myanmar` | 81,805 | 100.0% |
| `import_batch_id` | 81,805 | 100.0% |
| `source_file` | 81,805 | 100.0% |
| `imported_at` | 81,805 | 100.0% |

## `training_exercises_import`
| Property | Value |
|----------|-------|
| Row count | 81,805 |
| Columns | 14 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Training |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `instruction` | TEXT | no | `` |  |
| 1 | `input` | TEXT | no | `` |  |
| 2 | `output` | TEXT | no | `` |  |
| 3 | `negation_type` | TEXT | no | `` |  |
| 4 | `reference` | TEXT | no | `` |  |
| 5 | `confidence` | REAL | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |
| 10 | `question_type` | TEXT | no | `` |  |
| 11 | `word_order` | TEXT | no | `` |  |
| 12 | `grammar_point` | TEXT | no | `` |  |
| 13 | `error_type` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_training_exercises_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `word_order` | 81,602 | 99.8% |
| `error_type` | 72,818 | 89.0% |
| `grammar_point` | 59,786 | 73.1% |
| `question_type` | 57,067 | 69.8% |
| `negation_type` | 55,744 | 68.1% |

## `training_runs`
| Property | Value |
|----------|-------|
| Row count | 2 |
| Columns | 17 |
| Primary key | id |
| Indexes | 0 |
| Classification | `Training` |
| Domain | Training |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `model_name` | TEXT | no | `` |  |
| 2 | `dataset_name` | TEXT | no | `` |  |
| 3 | `entry_count` | INTEGER | no | `` |  |
| 4 | `entry_version` | TEXT | no | `'v1.0'` |  |
| 5 | `update_remarks` | TEXT | no | `''` |  |
| 6 | `created_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |
| 7 | `updated_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |
| 8 | `metrics_json` | TEXT | no | `` |  |
| 9 | `status` | TEXT | no | `"pending"` |  |
| 10 | `completed_at` | TEXT | no | `` |  |
| 11 | `test_type` | TEXT | no | `` |  |
| 12 | `test_date` | TEXT | no | `` |  |
| 13 | `total_tests` | INTEGER | no | `` |  |
| 14 | `passed_tests` | INTEGER | no | `` |  |
| 15 | `score` | REAL | no | `` |  |
| 16 | `details` | TEXT | no | `` |  |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `entry_count` | 1 | 50.0% |
| `metrics_json` | 1 | 50.0% |
| `completed_at` | 1 | 50.0% |
| `test_type` | 1 | 50.0% |
| `test_date` | 1 | 50.0% |
| `total_tests` | 1 | 50.0% |
| `passed_tests` | 1 | 50.0% |
| `score` | 1 | 50.0% |
| `details` | 1 | 50.0% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `update_remarks` | 1 | 50.0% |

## `training_seed_data_import`
| Property | Value |
|----------|-------|
| Row count | 500 |
| Columns | 10 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Training |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `english` | TEXT | no | `` |  |
| 2 | `source` | TEXT | no | `` |  |
| 3 | `type` | TEXT | no | `` |  |
| 4 | `quality` | TEXT | no | `` |  |
| 5 | `context` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_training_seed_data_import_batch` | no | import_batch_id |

## `training_valid_sentences_import`
| Property | Value |
|----------|-------|
| Row count | 4,693 |
| Columns | 13 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Training |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `english` | TEXT | no | `` |  |
| 2 | `pattern` | TEXT | no | `` |  |
| 3 | `source` | TEXT | no | `` |  |
| 4 | `deep_score` | INTEGER | no | `` |  |
| 5 | `checks` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |
| 10 | `context_score` | TEXT | no | `` |  |
| 11 | `corrections` | TEXT | no | `` |  |
| 12 | `corrected` | INTEGER | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_training_valid_sentences_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `corrections` | 4,690 | 99.9% |
| `corrected` | 4,690 | 99.9% |
| `context_score` | 4,279 | 91.2% |

## `translations`
| Property | Value |
|----------|-------|
| Row count | 212,754 |
| Columns | 11 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Translation` |
| Domain | Translation |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `source` | TEXT | YES | `` |  |
| 2 | `target` | TEXT | YES | `` |  |
| 3 | `direction` | VARCHAR | YES | `` |  |
| 4 | `reference` | VARCHAR | YES | `` |  |
| 5 | `confidence` | FLOAT | YES | `` |  |
| 6 | `myanmar` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_translations_batch` | no | import_batch_id |
| `ix_trans_direction_ref` | no | direction, reference |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `myanmar` | 212,754 | 100.0% |
| `import_batch_id` | 212,754 | 100.0% |
| `source_file` | 212,754 | 100.0% |
| `imported_at` | 212,754 | 100.0% |

## `translations_import`
| Property | Value |
|----------|-------|
| Row count | 135,511 |
| Columns | 10 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Translation |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `english` | TEXT | no | `` |  |
| 2 | `dialect` | TEXT | no | `` |  |
| 3 | `source` | TEXT | no | `` |  |
| 4 | `reference` | TEXT | no | `` |  |
| 5 | `category` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_translations_import_batch` | no | import_batch_id |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `reference` | 14 | 0.0% |

## `vocab`
| Property | Value |
|----------|-------|
| Row count | 94,458 |
| Columns | 11 |
| Primary key | id |
| Indexes | 2 |
| Classification | `Vocabulary` |
| Domain | Vocabulary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `headword` | VARCHAR | YES | `` |  |
| 2 | `english` | VARCHAR | YES | `` |  |
| 3 | `frequency` | INTEGER | YES | `` |  |
| 4 | `books` | TEXT | YES | `` |  |
| 5 | `examples` | TEXT | YES | `` |  |
| 6 | `myanmar` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_vocab_batch` | no | import_batch_id |
| `ix_vocab_headword` | no | headword |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `import_batch_id` | 94,458 | 100.0% |
| `source_file` | 94,458 | 100.0% |
| `imported_at` | 94,458 | 100.0% |
| `myanmar` | 88,361 | 93.5% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `english` | 500 | 0.5% |

## `vocab_import`
| Property | Value |
|----------|-------|
| Row count | 180,458 |
| Columns | 14 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Vocabulary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `headword` | TEXT | no | `` |  |
| 1 | `english` | TEXT | no | `` |  |
| 2 | `frequency` | INTEGER | no | `` |  |
| 3 | `books` | TEXT | no | `` |  |
| 4 | `book_count` | INTEGER | no | `` |  |
| 5 | `examples` | TEXT | no | `` |  |
| 6 | `pos` | TEXT | no | `` |  |
| 7 | `notes` | TEXT | no | `` |  |
| 8 | `source` | TEXT | no | `` |  |
| 9 | `import_batch_id` | TEXT | no | `` |  |
| 10 | `source_file` | TEXT | no | `` |  |
| 11 | `version` | INTEGER | no | `1` |  |
| 12 | `imported_at` | TEXT | no | `` |  |
| 13 | `verified` | INTEGER | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_vocab_import_batch` | no | import_batch_id |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `verified` | 117,458 | 65.1% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `notes` | 180,449 | 100.0% |
| `pos` | 180,182 | 99.8% |
| `english` | 843 | 0.5% |

## `vocabulary_enhanced`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 15 |
| Primary key | id |
| Indexes | 0 |
| Classification | `EMPTY_PLANNED` |
| Domain | Vocabulary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `zolai` | TEXT | YES | `` |  |
| 2 | `english_meaning` | TEXT | no | `` |  |
| 3 | `pos` | TEXT | no | `` |  |
| 4 | `tone_category` | TEXT | no | `` |  |
| 5 | `etymology` | TEXT | no | `` |  |
| 6 | `compound_breakdown` | TEXT | no | `` |  |
| 7 | `register` | TEXT | no | `` |  |
| 8 | `frequency` | INTEGER | no | `0` |  |
| 9 | `bible_frequency` | INTEGER | no | `0` |  |
| 10 | `corpus_frequency` | INTEGER | no | `0` |  |
| 11 | `source_category` | TEXT | no | `` |  |
| 12 | `source_file` | TEXT | no | `` |  |
| 13 | `example_sentences` | TEXT | no | `` |  |
| 14 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

## `wiki_content`
| Property | Value |
|----------|-------|
| Row count | 1,688 |
| Columns | 11 |
| Primary key | id |
| Indexes | 3 |
| Classification | `Wiki/Content` |
| Domain | Wiki/Content |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `wiki_category` | TEXT | YES | `` |  |
| 2 | `source_path` | TEXT | YES | `` |  |
| 3 | `title` | TEXT | no | `` |  |
| 4 | `content` | TEXT | YES | `` |  |
| 5 | `word_count` | INTEGER | no | `` |  |
| 6 | `section_count` | INTEGER | no | `` |  |
| 7 | `content_hash` | TEXT | YES | `` |  |
| 8 | `entry_version` | TEXT | no | `'v1.0'` |  |
| 9 | `created_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |
| 10 | `updated_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_wiki_hash` | no | content_hash |
| `idx_wiki_cat` | no | wiki_category |
| `sqlite_autoindex_wiki_content_1` | YES | source_path |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `title` | 32 | 1.9% |

## `wiki_content_fts`
| Property | Value |
|----------|-------|
| Row count | 1,688 |
| Columns | 2 |
| Primary key | ❌ **NONE** |
| Indexes | 0 |
| Classification | `SEARCH_INDEX` |
| Domain | SearchIndex |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `title` |  | no | `` |  |
| 1 | `content` |  | no | `` |  |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `title` | 32 | 1.9% |

## `wiki_content_fts_config`
| Property | Value |
|----------|-------|
| Row count | 1 |
| Columns | 2 |
| Primary key | k |
| Indexes | 1 |
| Classification | `SEARCH_INDEX` |
| Domain | SearchIndex |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `k` |  | YES | `` | ✅ PK(1) |
| 1 | `v` |  | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `sqlite_autoindex_wiki_content_fts_config_1` | YES | k |

## `wiki_content_fts_data`
| Property | Value |
|----------|-------|
| Row count | 4,141 |
| Columns | 2 |
| Primary key | id |
| Indexes | 0 |
| Classification | `SEARCH_INDEX` |
| Domain | SearchIndex |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `block` | BLOB | no | `` |  |

## `wiki_content_fts_docsize`
| Property | Value |
|----------|-------|
| Row count | 1,688 |
| Columns | 2 |
| Primary key | id |
| Indexes | 0 |
| Classification | `SEARCH_INDEX` |
| Domain | SearchIndex |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `sz` | BLOB | no | `` |  |

## `wiki_content_fts_idx`
| Property | Value |
|----------|-------|
| Row count | 3,186 |
| Columns | 3 |
| Primary key | segid, term |
| Indexes | 1 |
| Classification | `SEARCH_INDEX` |
| Domain | SearchIndex |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `segid` |  | YES | `` | ✅ PK(1) |
| 1 | `term` |  | YES | `` | ✅ PK(2) |
| 2 | `pgno` |  | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `sqlite_autoindex_wiki_content_fts_idx_1` | YES | segid, term |

## `wiki_lessons`
| Property | Value |
|----------|-------|
| Row count | 1,688 |
| Columns | 12 |
| Primary key | id |
| Indexes | 1 |
| Classification | `Wiki/Content` |
| Domain | Wiki/Content |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `lesson_type` | TEXT | YES | `` |  |
| 2 | `title` | TEXT | YES | `` |  |
| 3 | `content_summary` | TEXT | no | `` |  |
| 4 | `word_count` | INTEGER | no | `` |  |
| 5 | `grammar_patterns` | TEXT | no | `` |  |
| 6 | `vocabulary_list` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `entry_version` | TEXT | no | `'v1.0'` |  |
| 9 | `update_remarks` | TEXT | no | `''` |  |
| 10 | `created_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |
| 11 | `updated_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_wiki_type` | no | lesson_type |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `update_remarks` | 1,688 | 100.0% |

## `word_alignments`
| Property | Value |
|----------|-------|
| Row count | 385,120 |
| Columns | 10 |
| Primary key | id |
| Indexes | 3 |
| Classification | `WordAlignment` |
| Domain | WordAlignment |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `ref` | VARCHAR | YES | `` |  |
| 2 | `zolai_word` | VARCHAR | YES | `` |  |
| 3 | `english_word` | VARCHAR | YES | `` |  |
| 4 | `position` | INTEGER | YES | `` |  |
| 5 | `myanmar` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_word_alignments_batch` | no | import_batch_id |
| `idx_alignment_ref` | no | ref |
| `ix_word_alignments_ref` | no | ref |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `myanmar` | 385,120 | 100.0% |
| `import_batch_id` | 385,120 | 100.0% |
| `source_file` | 385,120 | 100.0% |
| `imported_at` | 385,120 | 100.0% |

## `word_alignments_import`
| Property | Value |
|----------|-------|
| Row count | 627,000 |
| Columns | 11 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | WordAlignment |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zo_word` | TEXT | no | `` |  |
| 1 | `en_word` | TEXT | no | `` |  |
| 2 | `en_position` | INTEGER | no | `` |  |
| 3 | `confidence` | REAL | no | `` |  |
| 4 | `source` | TEXT | no | `` |  |
| 5 | `ref` | TEXT | no | `` |  |
| 6 | `book` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_word_alignments_import_batch` | no | import_batch_id |

## `word_collocations`
| Property | Value |
|----------|-------|
| Row count | 5,000 |
| Columns | 10 |
| Primary key | id |
| Indexes | 2 |
| Classification | `WordAlignment` |
| Domain | WordAlignment |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `word1` | VARCHAR | YES | `` |  |
| 2 | `word2` | VARCHAR | YES | `` |  |
| 3 | `frequency` | INTEGER | YES | `` |  |
| 4 | `pmiproxy` | FLOAT | YES | `` |  |
| 5 | `myanmar` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_word_collocations_batch` | no | import_batch_id |
| `ix_word_collocations_word1` | no | word1 |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `myanmar` | 5,000 | 100.0% |
| `import_batch_id` | 5,000 | 100.0% |
| `source_file` | 5,000 | 100.0% |
| `imported_at` | 5,000 | 100.0% |

## `word_collocations_import`
| Property | Value |
|----------|-------|
| Row count | 5,000 |
| Columns | 7 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | WordAlignment |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `collocation` | TEXT | no | `` |  |
| 1 | `frequency` | INTEGER | no | `` |  |
| 2 | `examples` | TEXT | no | `` |  |
| 3 | `import_batch_id` | TEXT | no | `` |  |
| 4 | `source_file` | TEXT | no | `` |  |
| 5 | `version` | INTEGER | no | `1` |  |
| 6 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_word_collocations_import_batch` | no | import_batch_id |

## `word_similarity`
| Property | Value |
|----------|-------|
| Row count | 0 |
| Columns | 9 |
| Primary key | id |
| Indexes | 1 |
| Classification | `EMPTY_PLANNED` |
| Domain | NLP/Experimental |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `word1` | TEXT | YES | `` |  |
| 2 | `word2` | TEXT | YES | `` |  |
| 3 | `english1` | TEXT | no | `` |  |
| 4 | `english2` | TEXT | no | `` |  |
| 5 | `similarity` | REAL | YES | `` |  |
| 6 | `reason` | TEXT | no | `` |  |
| 7 | `source` | TEXT | no | `'gemini'` |  |
| 8 | `created_at` | TEXT | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `sqlite_autoindex_word_similarity_1` | YES | word1, word2 |

## `word_usage`
| Property | Value |
|----------|-------|
| Row count | 60,365 |
| Columns | 11 |
| Primary key | id |
| Indexes | 3 |
| Classification | `WordUsage` |
| Domain | WordUsage |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | YES | `` | ✅ PK(1) |
| 1 | `word` | VARCHAR | YES | `` |  |
| 2 | `book` | VARCHAR | YES | `` |  |
| 3 | `total_freq` | INTEGER | YES | `` |  |
| 4 | `meaning_shifts` | TEXT | YES | `` |  |
| 5 | `co_occurring_words` | TEXT | YES | `` |  |
| 6 | `myanmar` | TEXT | no | `` |  |
| 7 | `import_batch_id` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `version` | INTEGER | no | `1` |  |
| 10 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_word_usage_batch` | no | import_batch_id |
| `ix_usage_word_book` | no | word, book |
| `ix_word_usage_word` | no | word |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `myanmar` | 60,365 | 100.0% |
| `import_batch_id` | 60,365 | 100.0% |
| `source_file` | 60,365 | 100.0% |
| `imported_at` | 60,365 | 100.0% |

## `word_usage_profiles_import`
| Property | Value |
|----------|-------|
| Row count | 7,384 |
| Columns | 10 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | WordUsage |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `word` | TEXT | no | `` |  |
| 1 | `total_freq` | INTEGER | no | `` |  |
| 2 | `books_found` | INTEGER | no | `` |  |
| 3 | `per_book_distribution` | TEXT | no | `` |  |
| 4 | `meaning_shifts` | TEXT | no | `` |  |
| 5 | `all_translations` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_word_usage_profiles_import_batch` | no | import_batch_id |

## `zolai_bible_analysis`
| Property | Value |
|----------|-------|
| Row count | 30,758 |
| Columns | 16 |
| Primary key | id |
| Indexes | 1 |
| Classification | `Bible` |
| Domain | Bible |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `book_code` | TEXT | no | `` |  |
| 2 | `book_name` | TEXT | no | `` |  |
| 3 | `chapter` | INTEGER | no | `` |  |
| 4 | `verse` | INTEGER | no | `` |  |
| 5 | `zolai` | TEXT | no | `` |  |
| 6 | `english` | TEXT | no | `` |  |
| 7 | `myanmar_text` | TEXT | no | `` |  |
| 8 | `verse_hash` | TEXT | no | `` |  |
| 9 | `morpheme_analysis` | TEXT | no | `` |  |
| 10 | `grammar_tags` | TEXT | no | `` |  |
| 11 | `tone_analysis` | TEXT | no | `` |  |
| 12 | `compounds_found` | TEXT | no | `` |  |
| 13 | `rare_words` | TEXT | no | `` |  |
| 14 | `source_version` | TEXT | no | `` |  |
| 15 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_bible_ref` | no | book_code, chapter, verse |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `morpheme_analysis` | 30,758 | 100.0% |
| `grammar_tags` | 30,758 | 100.0% |
| `tone_analysis` | 30,758 | 100.0% |
| `rare_words` | 30,758 | 100.0% |
| `myanmar_text` | 581 | 1.9% |
| `english` | 18 | 0.1% |

## `zolai_grammar_patterns`
| Property | Value |
|----------|-------|
| Row count | 13,519 |
| Columns | 22 |
| Primary key | id |
| Indexes | 1 |
| Classification | `Grammar` |
| Domain | Grammar |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `pattern_id` | TEXT | no | `` |  |
| 2 | `pattern_name` | TEXT | no | `` |  |
| 3 | `pattern_text` | TEXT | no | `` |  |
| 4 | `structure` | TEXT | no | `` |  |
| 5 | `zolai_example` | TEXT | no | `` |  |
| 6 | `english_translation` | TEXT | no | `` |  |
| 7 | `morpheme_breakdown` | TEXT | no | `` |  |
| 8 | `tone_pattern` | TEXT | no | `` |  |
| 9 | `pattern_type` | TEXT | no | `` |  |
| 10 | `tense` | TEXT | no | `` |  |
| 11 | `aspect` | TEXT | no | `` |  |
| 12 | `negation_type` | TEXT | no | `` |  |
| 13 | `question_type` | TEXT | no | `` |  |
| 14 | `agreement` | TEXT | no | `` |  |
| 15 | `ergative` | INTEGER | no | `0` |  |
| 16 | `source_category` | TEXT | no | `` |  |
| 17 | `source_file` | TEXT | no | `` |  |
| 18 | `source_line` | INTEGER | no | `` |  |
| 19 | `frequency` | INTEGER | no | `1` |  |
| 20 | `confidence` | REAL | no | `1.0` |  |
| 21 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_grammar_type` | no | pattern_type |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `pattern_id` | 13,519 | 100.0% |
| `pattern_name` | 13,519 | 100.0% |
| `pattern_text` | 13,519 | 100.0% |
| `structure` | 13,519 | 100.0% |
| `morpheme_breakdown` | 13,519 | 100.0% |
| `tone_pattern` | 13,519 | 100.0% |
| `tense` | 13,519 | 100.0% |
| `aspect` | 13,519 | 100.0% |
| `negation_type` | 13,519 | 100.0% |
| `question_type` | 13,519 | 100.0% |
| `agreement` | 13,519 | 100.0% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `pattern_type` | 2,107 | 15.6% |

## `zolai_proverbs_idioms`
| Property | Value |
|----------|-------|
| Row count | 4,984 |
| Columns | 11 |
| Primary key | id |
| Indexes | 0 |
| Classification | `Proverbs` |
| Domain | Proverbs |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `zolai` | TEXT | YES | `` |  |
| 2 | `english_translation` | TEXT | no | `` |  |
| 3 | `literal_translation` | TEXT | no | `` |  |
| 4 | `morpheme_breakdown` | TEXT | no | `` |  |
| 5 | `category` | TEXT | no | `` |  |
| 6 | `theme` | TEXT | no | `` |  |
| 7 | `cultural_context` | TEXT | no | `` |  |
| 8 | `source_category` | TEXT | no | `` |  |
| 9 | `source_file` | TEXT | no | `` |  |
| 10 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `literal_translation` | 4,984 | 100.0% |
| `morpheme_breakdown` | 4,984 | 100.0% |
| `theme` | 4,984 | 100.0% |
| `cultural_context` | 4,984 | 100.0% |

## `zolai_songs`
| Property | Value |
|----------|-------|
| Row count | 1,032 |
| Columns | 6 |
| Primary key | id |
| Indexes | 0 |
| Classification | `Songs` |
| Domain | Songs |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `collection` | TEXT | no | `` |  |
| 2 | `song_number` | INTEGER | no | `` |  |
| 3 | `title` | TEXT | no | `` |  |
| 4 | `text` | TEXT | no | `` |  |
| 5 | `source` | TEXT | no | `` |  |

## `zolai_tone_sandhi`
| Property | Value |
|----------|-------|
| Row count | 19 |
| Columns | 11 |
| Primary key | id |
| Indexes | 1 |
| Classification | `Tone` |
| Domain | Tone |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `rule_number` | TEXT | no | `` |  |
| 2 | `rule_name` | TEXT | no | `` |  |
| 3 | `underlying_pattern` | TEXT | no | `` |  |
| 4 | `surface_pattern` | TEXT | no | `` |  |
| 5 | `condition` | TEXT | no | `` |  |
| 6 | `examples` | TEXT | no | `` |  |
| 7 | `domain` | TEXT | no | `` |  |
| 8 | `source_category` | TEXT | no | `` |  |
| 9 | `source_file` | TEXT | no | `` |  |
| 10 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_tone_word` | no | underlying_pattern |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `examples` | 19 | 100.0% |

## `zolai_vocabulary`
| Property | Value |
|----------|-------|
| Row count | 112,279 |
| Columns | 31 |
| Primary key | id |
| Indexes | 6 |
| Classification | `Vocabulary` |
| Domain | Vocabulary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `zolai` | TEXT | YES | `` |  |
| 2 | `english` | TEXT | no | `` |  |
| 3 | `myanmar` | TEXT | no | `` |  |
| 4 | `pos` | TEXT | no | `` |  |
| 5 | `tone_category` | TEXT | no | `` |  |
| 6 | `meaning_t1` | TEXT | no | `` |  |
| 7 | `meaning_t3` | TEXT | no | `` |  |
| 8 | `meaning_t4` | TEXT | no | `` |  |
| 9 | `is_compound` | INTEGER | no | `0` |  |
| 10 | `compound_parts` | TEXT | no | `` |  |
| 11 | `root_word` | TEXT | no | `` |  |
| 12 | `derivation` | TEXT | no | `` |  |
| 13 | `register` | TEXT | no | `` |  |
| 14 | `frequency_bible` | INTEGER | no | `0` |  |
| 15 | `frequency_corpus` | INTEGER | no | `0` |  |
| 16 | `frequency_songs` | INTEGER | no | `0` |  |
| 17 | `bible_books` | TEXT | no | `` |  |
| 18 | `example_zo` | TEXT | no | `` |  |
| 19 | `example_en` | TEXT | no | `` |  |
| 20 | `source_priority` | INTEGER | no | `999` |  |
| 21 | `source_category` | TEXT | no | `` |  |
| 22 | `source_file` | TEXT | no | `` |  |
| 23 | `confidence` | REAL | no | `1.0` |  |
| 24 | `zvs_compliant` | INTEGER | no | `1` |  |
| 25 | `notes` | TEXT | no | `` |  |
| 26 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |
| 27 | `updated_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |
| 28 | `import_batch_id` | TEXT | no | `` |  |
| 29 | `version` | INTEGER | no | `1` |  |
| 30 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_zolai_vocabulary_batch` | no | import_batch_id |
| `idx_vocab_compound` | no | is_compound |
| `idx_vocab_source` | no | source_category |
| `idx_vocab_english` | no | english |
| `idx_vocab_zolai` | no | zolai |
| `sqlite_autoindex_zolai_vocabulary_1` | YES | zolai, source_category, source_file |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `compound_parts` | 112,279 | 100.0% |
| `root_word` | 112,279 | 100.0% |
| `derivation` | 112,279 | 100.0% |
| `register` | 112,279 | 100.0% |
| `bible_books` | 112,279 | 100.0% |
| `example_zo` | 112,279 | 100.0% |
| `example_en` | 112,279 | 100.0% |
| `notes` | 112,279 | 100.0% |
| `import_batch_id` | 112,279 | 100.0% |
| `imported_at` | 112,279 | 100.0% |
| `meaning_t4` | 112,278 | 100.0% |
| `meaning_t3` | 112,276 | 100.0% |
| `tone_category` | 112,271 | 100.0% |
| `meaning_t1` | 112,271 | 100.0% |
| `myanmar` | 8 | 0.0% |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `myanmar` | 112,271 | 100.0% |
| `pos` | 87,446 | 77.9% |

## `zolai_vocabulary_import`
| Property | Value |
|----------|-------|
| Row count | 12,692 |
| Columns | 9 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | Vocabulary |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `word` | TEXT | no | `` |  |
| 1 | `frequency` | INTEGER | no | `` |  |
| 2 | `definition` | TEXT | no | `` |  |
| 3 | `examples` | TEXT | no | `` |  |
| 4 | `verified` | INTEGER | no | `` |  |
| 5 | `import_batch_id` | TEXT | no | `` |  |
| 6 | `source_file` | TEXT | no | `` |  |
| 7 | `version` | INTEGER | no | `1` |  |
| 8 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_zolai_vocabulary_import_batch` | no | import_batch_id |

### Empty Strings (non-zero only)
| Column | Empty | % |
|--------|-------|---|
| `definition` | 4,982 | 39.3% |

## `zolai_word_usage`
| Property | Value |
|----------|-------|
| Row count | 85,045 |
| Columns | 10 |
| Primary key | id |
| Indexes | 2 |
| Classification | `WordUsage` |
| Domain | WordUsage |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `id` | INTEGER | no | `` | ✅ PK(1) |
| 1 | `word` | TEXT | YES | `` |  |
| 2 | `book_code` | TEXT | no | `` |  |
| 3 | `frequency` | INTEGER | no | `0` |  |
| 4 | `meanings` | TEXT | no | `` |  |
| 5 | `co_occurring` | TEXT | no | `` |  |
| 6 | `contexts` | TEXT | no | `` |  |
| 7 | `source_category` | TEXT | no | `` |  |
| 8 | `source_file` | TEXT | no | `` |  |
| 9 | `created_at` | TIMESTAMP | no | `CURRENT_TIMESTAMP` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_usage_book` | no | book_code |
| `idx_usage_word` | no | word |

### NULL Analysis (non-zero only)
| Column | NULLs | % |
|--------|-------|---|
| `co_occurring` | 85,045 | 100.0% |
| `contexts` | 85,045 | 100.0% |

## `zvs_corrections_import`
| Property | Value |
|----------|-------|
| Row count | 44 |
| Columns | 10 |
| Primary key | ❌ **NONE** |
| Indexes | 1 |
| Classification | `IMPORT_STAGING` |
| Domain | ZVS |

### Columns
| # | Name | Type | NOT NULL | Default | PK |
|---|------|------|----------|---------|-----|
| 0 | `zolai` | TEXT | no | `` |  |
| 1 | `old_english_clean` | TEXT | no | `` |  |
| 2 | `new_english_clean` | TEXT | no | `` |  |
| 3 | `reason` | TEXT | no | `` |  |
| 4 | `bible_examples` | INTEGER | no | `` |  |
| 5 | `sample_ref` | TEXT | no | `` |  |
| 6 | `import_batch_id` | TEXT | no | `` |  |
| 7 | `source_file` | TEXT | no | `` |  |
| 8 | `version` | INTEGER | no | `1` |  |
| 9 | `imported_at` | TEXT | no | `` |  |

### Indexes
| Index | Unique | Columns |
|-------|--------|---------|
| `idx_zvs_corrections_import_batch` | no | import_batch_id |
