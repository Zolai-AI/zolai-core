# Phase 0 Database Audit — Open Questions

**Generated:** 2026-09-13 20:31

These decisions cannot safely be made from evidence alone. They require team input or further investigation.

## Schema Design Questions

### Q1: Should `zolai_vocabulary` replace `vocab` as the primary vocabulary table?
- `vocab` (94K rows, 11 columns) is the current primary
- `zolai_vocabulary` (112K rows, 31 columns) is richer and larger
- **Risk:** Code may depend on `vocab` schema; migration could break queries
- **Decision needed:** Which table does zolai-core actually query?

### Q2: Should `zolai_grammar_patterns` replace `grammar_patterns`?
- `grammar_patterns` (5.5K, 12 cols) is the base
- `zolai_grammar_patterns` (13.5K, 22 cols) has 2.4x more rows AND more columns
- **Question:** Where do the extra 8K rows come from? Are they higher quality?
- **Decision needed:** Are the extra rows valid, or do they introduce noise?

### Q3: Should `zolai_proverbs_idioms` replace `proverbs`?
- `proverbs` (7.7K, 9 cols) and `zolai_proverbs_idioms` (4.9K, 11 cols)
- **Question:** Why does the enriched version have fewer rows? Was filtering applied?
- **Decision needed:** Which is the ground truth?

### Q4: What is `tone_patterns` (118 rows) vs `zolai_tone_sandhi` (19 rows)?
- `zolai_tone_sandhi` has the documented 19 tone sandhi rules
- `tone_patterns` has 118 rows — are these additional patterns?
- **Decision needed:** Should these be merged? Are `tone_patterns` valid?

## Operational Questions

### Q5: Are import tables still used by any pipeline?
- 17 `*_import` tables exist
- `jsonl_import_log` shows 92 import runs
- **Question:** Is the JSONL import pipeline still active, or is it legacy?
- **Decision needed:** Can we safely archive all import tables?

### Q6: Is the empty `gemini_model_results` table still planned?
- Schema exists (9 columns) but 0 rows
- **Question:** Was Gemini integration planned but never implemented?
- **Decision needed:** Drop table or keep for future use?

### Q7: Are `pos_gold`, `pos_verified`, `morph_verified` still planned?
- All empty (0 rows)
- **Question:** Was a POS tagging / morphological analysis pipeline planned?
- **Decision needed:** Drop or keep for future NLP work?

### Q8: Should `training_runs` (2 rows) be expanded or dropped?
- Only 2 rows of training metadata
- **Question:** Is this actively used for training tracking?
- **Decision needed:** Expand schema or drop table?

## Data Quality Questions

### Q9: Why does `translations` (212K) have more rows than `translations_import` (135K)?
- Typically imports have MORE rows than canonical (deduplication reduces)
- **Question:** Were translations added outside the import pipeline?
- **Decision needed:** Is there a manual data entry process?

### Q10: What is the relationship between `word_alignments_import` (627K) and `word_alignments` (385K)?
- 242K rows were removed during import
- **Question:** What filtering criteria were applied? Is the canonical subset correct?

### Q11: Why does `dictionary_import` (156K) have 53K more rows than `dictionary` (103K)?
- Significant data reduction during import
- **Question:** What deduplication/cleaning was applied? Was it correct?

## Architecture Questions

### Q12: Should the database be migrated to PostgreSQL?
- Progress tracker mentions this as a future goal
- **Question:** Is this still the plan? What is the timeline?
- **Evidence:** WAL mode + busy_timeout=30000 suggests concurrent access is needed

### Q13: Should `data/zolai.db` remain a shared workspace DB?
- Currently shared across zolai-core, zolai-web, zolai-tauri
- **Question:** Should each repo have its own DB? Or continue sharing?

### Q14: How should schema versioning be handled?
- No migration framework exists
- **Question:** Alembic? Raw SQL scripts? Something else?

## Next Steps

1. **Audit lead** should review these questions with the team
2. **Priority decisions:** Q1, Q5, Q12 (affect consolidation direction)
3. **Can wait:** Q6, Q7, Q8 (empty tables — low risk)
4. **Needs investigation:** Q9, Q10, Q11 (data quality — run SQL queries)
