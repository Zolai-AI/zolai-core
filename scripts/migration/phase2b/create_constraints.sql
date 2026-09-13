-- Phase 2B: Constraints + Indexes for 12 core _v2 tables
-- NON-DESTRUCTIVE: only additive (CREATE INDEX IF NOT EXISTS)
-- Safe for idempotent re-runs

-- ============================================================
-- UNIQUE Constraints (5 safe tables — 0 duplicates verified)
-- ============================================================

-- grammar_patterns_v2: pattern_id is unique
CREATE UNIQUE INDEX IF NOT EXISTS uniq_gp_v2_pattern_id
    ON grammar_patterns_v2 (pattern_id);

-- phrases_v2: zolai column is unique
CREATE UNIQUE INDEX IF NOT EXISTS uniq_phrases_v2_zolai
    ON phrases_v2 (zolai);

-- word_usage_v2: (word, book, meaning_shifts) is unique
CREATE UNIQUE INDEX IF NOT EXISTS uniq_wu_v2_word_book_meaning
    ON word_usage_v2 (word, book, meaning_shifts);

-- syllable_data_v2: word is unique
CREATE UNIQUE INDEX IF NOT EXISTS uniq_syl_v2_word
    ON syllable_data_v2 (word);

-- word_collocations_v2: (word1, word2) is unique
CREATE UNIQUE INDEX IF NOT EXISTS uniq_wc_v2_w1w2
    ON word_collocations_v2 (word1, word2);

-- ============================================================
-- Composite / Lookup Indexes (all 12 core tables)
-- IF NOT EXISTS ensures idempotent re-runs
-- ============================================================

-- dictionary_v2: lookup by zolai headword
CREATE INDEX IF NOT EXISTS idx_dict_zolai ON dictionary_v2 (zolai);

-- dictionary_en_zo_v2: lookup by English headword
CREATE INDEX IF NOT EXISTS idx_dict_en_headword ON dictionary_en_zo_v2 (headword);

-- bible_verses_v2: book/chapter/verse + ref lookup
CREATE INDEX IF NOT EXISTS idx_bible_book_cv ON bible_verses_v2 (book, chapter, verse);
CREATE INDEX IF NOT EXISTS idx_bible_ref ON bible_verses_v2 (ref);

-- grammar_patterns_v2: lookup by pattern_id
CREATE INDEX IF NOT EXISTS idx_grammar_pattern_id ON grammar_patterns_v2 (pattern_id);

-- translations_v2: per-direction lookups
CREATE INDEX IF NOT EXISTS idx_translations_source ON translations_v2 (source);
CREATE INDEX IF NOT EXISTS idx_translations_target ON translations_v2 (target);

-- word_alignments_v2: lookup by ref
CREATE INDEX IF NOT EXISTS idx_alignments_ref ON word_alignments_v2 (ref);

-- vocabulary_v2: lookup by headword
CREATE INDEX IF NOT EXISTS idx_vocab_headword ON vocabulary_v2 (headword);

-- proverbs_v2: lookup by zolai
CREATE INDEX IF NOT EXISTS idx_proverbs_zolai ON proverbs_v2 (zolai);

-- phrases_v2: lookup by zolai
CREATE INDEX IF NOT EXISTS idx_phrases_zolai ON phrases_v2 (zolai);

-- word_usage_v2: lookup by word
CREATE INDEX IF NOT EXISTS idx_word_usage_word ON word_usage_v2 (word);

-- syllable_data_v2: lookup by word
CREATE INDEX IF NOT EXISTS idx_syllable_word ON syllable_data_v2 (word);

-- word_collocations_v2: lookup by word1 and word2
CREATE INDEX IF NOT EXISTS idx_collocations_word1 ON word_collocations_v2 (word1);
CREATE INDEX IF NOT EXISTS idx_collocations_word2 ON word_collocations_v2 (word2);
