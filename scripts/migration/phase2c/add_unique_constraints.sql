-- Phase 2C: Add UNIQUE constraints after deduplication
-- Run AFTER dedup_core.py to enforce business-key uniqueness.
-- Safe for idempotent re-runs (IF NOT EXISTS).

-- 1. dictionary_v2: one entry per Zolai headword
CREATE UNIQUE INDEX IF NOT EXISTS uniq_dict_v2_zolai
    ON dictionary_v2 (zolai);

-- 2. dictionary_en_zo_v2: one entry per English headword
CREATE UNIQUE INDEX IF NOT EXISTS uniq_dict_en_zo_v2_headword
    ON dictionary_en_zo_v2 (headword);

-- 3. bible_verses_v2: one entry per Bible reference
CREATE UNIQUE INDEX IF NOT EXISTS uniq_bible_v2_ref
    ON bible_verses_v2 (ref);

-- 4. translations_v2: one entry per (source, target) pair
CREATE UNIQUE INDEX IF NOT EXISTS uniq_trans_v2_source_target
    ON translations_v2 (source, target);

-- 5. vocabulary_v2: one entry per headword
CREATE UNIQUE INDEX IF NOT EXISTS uniq_vocab_v2_headword
    ON vocabulary_v2 (headword);

-- 6. proverbs_v2: one entry per Zolai proverb
CREATE UNIQUE INDEX IF NOT EXISTS uniq_proverb_v2_zolai
    ON proverbs_v2 (zolai);
