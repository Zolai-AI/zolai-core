# Engine Hardcode Inventory — D2 Audit

**Status:** INVENTORY ONLY (P2) · **Date:** 2026-10-01 · **Scope:** `zolai/` package code (`tests/` excluded)
**Directive:** founder D2 — "works WITH and WITHOUT AI; data-driven — NO hardcoded tags/headwords/sentences".
**No code was changed for this audit.** Every row is a candidate for replacement in **C2** (usage-pattern
mining) / **C3** (flash-train artifacts) / the L-waves — never a Python dict/set literal of words or tags.

## Method

1. AST literal scan over all `zolai/**/*.py`: `dict`/`set`/`list`/`tuple` literals whose keys/items are
   ≥50% lowercase word-like strings (`[a-z][a-z'-]*`) — **201 raw hits** with ≥4 entries, plus a named-assign
   pass for small closed-class sets (≥2 entries).
2. Targeted `rg` patterns: `KNOWN_`, `_KNOWN`, `FORBIDDEN`, `FORBIDDEN_FORMS`, word→tag values
   (`"PART.NEG"`, `"PRON"`, `"nsubj"`, …), closed-class particle sets.
3. Curated into two tiers; non-lexicon literals (response-shape dicts, column whitelists, table whitelists,
   model-size enums, HTML tag lists, URL token lists) were **excluded** — listed under *Excluded* below.

**Hit counts:** Tier A (engine-path) = **79 literals across 22 files** (rows 1–79) + 2 cross-import
consolidation notes (rows 80–81) · Tier B (rest of repo) = **25 rows across 21 files** (rows 82–106) ·
raw AST pass = **201** ≥4-entry hits · ZVS forbidden-form copies = **15** (§3).
**Curated literal total: 104 rows.**

---

## Tier A — engine-path literals (reachable from `zolai/engines.py` registry targets + their helpers)

| # | file:line | symbol | type | entries | proposed replacement (C2/C3) |
|---|-----------|--------|------|---------|------------------------------|
| 1 | `zolai/pos_tagger/__init__.py:31-45` | `_PRONOUNS` | word→tag | 13 | closed-class config artifact (`data/artifacts/closed_classes_v1.json`) + `grammar_patterns` rows |
| 2 | `zolai/pos_tagger/__init__.py:48-76` | `_PARTICLES` | word→tag | 20 | `grammar_patterns` (function column) + closed-class config artifact |
| 3 | `zolai/pos_tagger/__init__.py:79-87` | `_POSTPOSITIONS` | word→tag | 7 | `grammar_patterns` (postposition rows) |
| 4 | `zolai/pos_tagger/__init__.py:90-99` | `_CONJUNCTIONS` | word→tag | 8 | `grammar_patterns` / `dictionary.pos_canonical` |
| 5 | `zolai/pos_tagger/__init__.py:102-104` | `_DETERMINERS` | word list | 4 | closed-class config artifact |
| 6 | `zolai/pos_tagger/__init__.py:107-120` | `_NUMERALS` | word→tag | 12 | `dictionary.pos_canonical` (numerals already in lexicon) |
| 7 | `zolai/pos_tagger/__init__.py:123-127` | `_VERB_SUFFIXES` | word list | 17 | suffix→POS artifact learned from `syllable_data` + dictionary POS |
| 8 | `zolai/pos_tagger/__init__.py:130-133` | `_NOUN_SUFFIXES` | word list | 12 | same suffix→POS artifact |
| 9 | `zolai/pos_tagger/__init__.py:136-142` | `_BIBLE_PROPER` | word list | 24 | artifact mined from `bible_verses` proper nouns (C2) |
| 10 | `zolai/pos_tagger/__init__.py:145-158` | `_DICT_POS_MAP` | word→tag | 12 | merge into `pos_normalize` label map (single versioned config) |
| 11 | `zolai/morphology/__init__.py:31-44` | `_TONE_NOTES` | word→entry | 12 | phonology config artifact (tone notes keyed by tone) |
| 12 | `zolai/morphology/__init__.py:51-57` | `_PREFIXES` | word→entry | 5 | morph-feature config artifact (`morph_affixes_v1.json`) |
| 13 | `zolai/morphology/__init__.py:61-61` | `_PREFIX_LIST` | word list | 5 | derive from #12 (duplicate — load once) |
| 14 | `zolai/morphology/__init__.py:92-154` | `_KNOWN_ROOTS` | word→entry | 55 | DB (`dictionary` roots + `zolai_vocabulary`) + `data/artifacts/morph_roots_v1.json`, CLI-rebuilt |
| 15 | `zolai/morphology/__init__.py:157-176` | `_COMPOUND_PARTS` | word→entry | 18 | `zolai_vocabulary` compound rows / compound-split artifact |
| 16 | `zolai/morphology/__init__.py:179-251` | `_HIGH_FREQ_ROOTS` | word→entry | 65 | frequency-ranked roots derived from `vocabulary.total_freq` → artifact |
| 17 | `zolai/morphology/__init__.py:257-265` | `_KNOWN_PARTICLES` | word→entry | 7 | closed-class config artifact + `grammar_patterns` |
| 18 | `zolai/foundation/morphology.py:31-41` | `_ZVS_FORBIDDEN_MORPHEMES` | word→entry | 9 | import from ZVS canonical (`zolai/zvs/rules_data.py`) |
| 19 | `zolai/foundation/morphology.py:44-50` | `_DIRECTIONALS` | word→entry | 5 | morph-affix config artifact (#12 family) |
| 20 | `zolai/foundation/morphology.py:53-61` | `_ASPECTS` | word→entry | 7 | `grammar_patterns` (tense/aspect rows) |
| 21 | `zolai/foundation/morphology.py:64-70` | `_PARTICLES` | word→entry | 5 | closed-class config artifact |
| 22 | `zolai/foundation/phonology.py:64-70` | `_ZOLAI_ONSETS` | word list | 21 | phoneme-inventory artifact derived from `syllable_data` stats |
| 23 | `zolai/foundation/phonology.py:73-75` | `_ZOLAI_CODAS` | word list | 8 | same phoneme-inventory artifact |
| 24 | `zolai/foundation/phonology.py:78-81` | `_ZOLAI_VOWELS` | word list | 9 | same phoneme-inventory artifact |
| 25 | `zolai/foundation/corpus.py:244` | `literary_markers` | word list | 5 | register signals derived from labeled corpus (C2 register mining) |
| 26 | `zolai/foundation/analysis.py:436-446` | `forbidden` (inline) | word→entry | 9 | ZVS canonical |
| 27 | `zolai/foundation/analysis.py:464-472` | `forbidden` (inline) | word→entry | 7 | ZVS canonical |
| 28 | `zolai/foundation/analysis.py:532-540` | `tense_markers` | word→entry | 7 | `grammar_patterns` (tense/aspect) |
| 29 | `zolai/foundation/verification_runner.py:33-41` | `ZVS_FORBIDDEN` | word→entry | 7 | ZVS canonical |
| 30 | `zolai/data/pos_normalize.py:91-132` | `TOKEN_MAP` | word→tag | 37 | versioned label-map config (`pos_label_map_v1.json`, rebuild from POS_SPEC) |
| 31 | `zolai/data/pos_normalize.py:66-73` | evidence vocabulary | word list | 6 | config enum (low risk; keep versioned) |
| 32 | `zolai/offline/rule_engine.py:15-25` | `ZVS_CORRECTIONS` | word→entry | 9 | ZVS canonical |
| 33 | `zolai/offline/rule_engine.py:165` | `verb_endings` | word list | 8 | morph suffix artifact (#7 family) |
| 34 | `zolai/learning/translation.py:532` | `english_words` | word list | 10 | stopword config artifact |
| 35 | `zolai/learning/grammar_editor.py:24` | `_NEGATION_MARKERS` | word list | 2 | `grammar_patterns` negation rows |
| 36 | `zolai/learning/grammar_editor.py:26` | `_DECLARATIVE_PARTICLES` | word list | 5 | closed-class config artifact |
| 37 | `zolai/learning/grammar_editor.py:27` | `_ASPECT_MARKERS` | word list | 5 | `grammar_patterns` (tense/aspect) |
| 38 | `zolai/learning/grammar_editor.py:28` | `_DIRECTIONAL_PREFIXES` | word list | 5 | morph-affix config artifact |
| 39 | `zolai/learning/grammar_editor.py:252-258` | `markers` (rule categories) | word→entry | 5 | rule-id config (category names, not lexicon — low risk) |
| 40 | `zolai/knowledge/rag_contract.py:104-112` | ZVS map (inline) | word→entry | 7 | ZVS canonical |
| 41 | `zolai/knowledge/rag_contract.py:392-419` | `BOOK_CODES` | word→entry | 66 | Bible book table / dataset metadata config |
| 42 | `zolai/knowledge/rag_contract.py:533` | `valid_endings` | word list | 8 | `grammar_patterns` (sentence-final particles) |
| 43 | `zolai/syllable/rules.py:16` | `VOWELS` | word list | 5 | phoneme-inventory artifact (single source for #22-24, #43-51) |
| 44 | `zolai/syllable/rules.py:18-20` | `DIPHTHONGS` | word list | 10 | phoneme-inventory artifact |
| 45 | `zolai/syllable/rules.py:22-24` | `DIGRAPHS` | word list | 7 | phoneme-inventory artifact |
| 46 | `zolai/syllable/rules.py:26-28` | `VALID_CODAS` | word list | 9 | phoneme-inventory artifact |
| 47 | `zolai/syllable/rules.py:30-34` | `VALID_ONSET_CLUSTERS` | word list | 21 | phoneme-inventory artifact |
| 48 | `zolai/syllable/segmenter.py:48-50` | `CONSONANT_CLUSTERS` | word list | 10 | phoneme-inventory artifact |
| 49 | `zolai/syllable/segmenter.py:53` | `VOWEL_SINGLE` | word list | 5 | phoneme-inventory artifact |
| 50 | `zolai/syllable/segmenter.py:56-58` | `DIPHTHONGS` | word list | 12 | phoneme-inventory artifact |
| 51 | `zolai/syllable/segmenter.py:64` | `FINAL_CONSONANTS` | word list | 10 | phoneme-inventory artifact |
| 52 | `zolai/syllable/segmenter.py:67-72` | `VALID_ONSET_CLUSTERS` | word list | 31 | phoneme-inventory artifact |
| 53 | `zolai/syllable/segmenter.py:311-733` | `compounds` | word→entry | 345 | DB: `syllable_data` compound rows / `compounds_v1.json` artifact (C2) |
| 54 | `zolai/syllable/segmenter.py:544,580` | inline hint lists | word list | 4+4 | compound-suffix hints → same compound artifact |
| 55 | `zolai/syllable/segmenter.py:739-754` | inline word list | word list | 91 | compound/gold list → artifact (rebuildable) |
| 56 | `zolai/syllable/tokenizer_training.py:351-357` | `test_words` | word list | 33 | move to `tests/` gold fixture (dev-only path) |
| 57 | `zolai/zvs/rules_data.py:47-58` | `DIALECT_FORBIDDEN_TO_PREFERRED` | word→entry | 10 | **canonical versioned config** (allowed by D2) — single source of truth |
| 58 | `zolai/zvs/rules_data.py:78-87` | `STEM_FORBIDDEN_TO_PREFERRED` | word→entry | 8 | canonical versioned config |
| 59 | `zolai/zvs/rules_data.py:104-128` | `HISTORICAL_EXCEPTIONS` | word→entry | 3 | canonical versioned config |
| 60 | `zolai/zvs/exceptions.py:52-54` | inline exception list | word list | 7 | import from #59 (dedupe) |
| 61 | `zolai/rules/zolai_rules_reference.py:12-23` | `FORBIDDEN_FORMS` | word→entry | 10 | import from ZVS canonical |
| 62 | `zolai/rules/zolai_rules_reference.py:26-35` | `TENSE_MARKERS` | word→entry | 8 | `grammar_patterns` |
| 63 | `zolai/rules/zolai_rules_reference.py:38-42` | `NEGATION` | word→entry | 3 | `grammar_patterns` |
| 64 | `zolai/rules/zolai_rules_reference.py:45-52` | `PARTICLES` | word→entry | 6 | closed-class config artifact |
| 65 | `zolai/rules/zolai_rules_reference.py:59-68` | greetings (inline) | word→entry | 8 | `phrases` table (greeting rows) |
| 66 | `zolai/rules/zolai_rules_reference.py:90-96` | `PRONOUNS` | word→entry | 5 | closed-class config artifact |
| 67 | `zolai/api/zvs_checker.py:10-21` | `FORBIDDEN_FORMS` | word→entry | 10 | ZVS canonical |
| 68 | `zolai/api/zvs_checker.py:24-27` | `HISTORICAL_EXCEPTIONS` | word list | 7 | ZVS canonical |
| 69 | `zolai/api/answer_validator.py:20` | `SENTENCE_FINAL` | word list | 9 | `grammar_patterns` (sentence-final particles) |
| 70 | `zolai/api/answer_validator.py:101` | English stopwords (inline) | word list | 9 | stopword config artifact |
| 71 | `zolai/dependency/__init__.py:12-29` | `DEP_LABELS` | word list | 16 | UD tagset config (versioned — allowed, but keep in one place) |
| 72 | `zolai/dependency/__init__.py:32-52` | `PARTICLE_ROLES` | word→tag | 19 | `grammar_patterns` + closed-class config |
| 73 | `zolai/dependency/__init__.py:125` | particle list (inline) | word list | 10 | closed-class config artifact |
| 74 | `zolai/ner/__init__.py:17-21` | `KNOWN_PERSONS` | word list | 17 | `ner_gazetteer_v1.json` mined from `bible_verses` proper nouns (C2) |
| 75 | `zolai/ner/__init__.py:24-28` | `KNOWN_LOCATIONS` | word list | 17 | same gazetteer artifact |
| 76 | `zolai/ner/__init__.py:31-33` | `KNOWN_ORGANIZATIONS` | word list | 5 | same gazetteer artifact |
| 77 | `zolai/ner/__init__.py:36-39` | `DATE_PATTERNS` | word list | 11 | same gazetteer artifact |
| 78 | `zolai/ner/__init__.py:42-45` | `NUMBER_WORDS` | word list | 12 | `dictionary.pos_canonical` / gazetteer artifact |
| 79 | `zolai/ner/__init__.py:193` | inline particle list | word list | 8 | closed-class config artifact |
| 80 | `zolai/foundation/morphology.py:11` (reference) | `_PREFIXES`/`_SUFFIXES` cross-import | import | — | consolidate morph affixes into one artifact (with #12-13) |
| 81 | `zolai/foundation/analysis.py:19` | `MORPH_KNOWN_ROOTS` cross-import | import | — | serve from #14 artifact (single loader) |

---

## Tier B — other lexicon-style literals (repo-wide, outside the registry path)

| # | file:line (span) | type | entries | proposed replacement |
|---|------------------|------|---------|----------------------|
| 82 | `zolai/agents/translator.py:72-75` | EN stopword list | 11 | stopword config artifact |
| 83 | `zolai/analyzer/__init__.py:84-86,119` | Zomi function-word lists | 4/7/8/4 | `grammar_patterns` |
| 84 | `zolai/analyzer/corpus.py:133-134,193-195,219` | duplicate of #83 | 4/7/8/4 ×2 | import once from #83's replacement (dedupe) |
| 85 | `zolai/api/dynamic_data_pipeline.py:158` | ZVS forbidden list | 7 | ZVS canonical |
| 86 | `zolai/api/intelligent_analyzer.py:170-184` | POS label map | 13 | `pos_label_map_v1.json` (same as #30) |
| 87 | `zolai/api/intelligent_analyzer.py:226-236` | valency/voice map | 9 | `grammar_patterns` |
| 88 | `zolai/api/rag_context.py:30-34` | EN stopword list | 45 | stopword config artifact |
| 89 | `zolai/api/rag_context_v2.py:26-32` | EN stopword list | 45 | stopword config artifact (dedupe with #88) |
| 90 | `zolai/api/rag_injector.py:15-30` | EN stopword list | 123 | stopword config artifact (dedupe with #88) |
| 91 | `zolai/api/zomidaily_learning_engine.py:189-199` | particle→description | 9 | `grammar_patterns` + closed-class config |
| 92 | `zolai/api/server.py:128` | ZVS list embedded in prompt text | 7 | reference ZVS canonical when rendering the prompt |
| 93 | `zolai/classifier/__init__.py:19-51` | category seed word lists | 8–15 ×6 | `vocabulary` category column / classifier artifact trained on labels (C3) |
| 94 | `zolai/crawler/engine.py:28-32` | language-name list | 18 | config (language metadata) |
| 95 | `zolai/data/services/dictionary.py:26-37` | ZVS map | 10 | ZVS canonical |
| 96 | `zolai/data/services/grammar.py:19-30` | ZVS map | 10 | ZVS canonical |
| 97 | `zolai/data/services/quality.py:91-94` | ZVS list | 10 | ZVS canonical |
| 98 | `zolai/foundation/regression.py:262-265` | ZVS list | 7 | ZVS canonical |
| 99 | `zolai/foundation/regression.py:374-380` | tense markers | 5 | `grammar_patterns` |
| 100 | `zolai/manager/__init__.py:95-98` | seed word list | 9 | vocabulary frequency query |
| 101 | `zolai/shared/utils.py:125-130,133-137,140-142,149-156` | Zomi/EN word lists | 28/27/8/50 | stopword + closed-class config artifacts |
| 102 | `zolai/shared/utils.py:162-166` | foreign stopword list | 26 | stopword config artifact |
| 103 | `zolai/shared/utils.py:223-226` | language-name list | 13 | config (dedupe with #94) |
| 104 | `zolai/summarizer/__init__.py:98-101` | salient-word list | 9 | weights derived from `vocabulary.total_freq` |
| 105 | `zolai/syllable/e2e_test.py:739-787` | inline sentence/word fixtures | 4–12 ×8 | move to `tests/` gold fixtures (file is test-only but ships in `zolai/`) |
| 106 | `zolai/knowledge/ngram.py:19-21` | file-path deps (dict JSON + wiki wordlists) | — | DB-only sources after C1 (paths are not literals but are hard deps) |

**Tier B = 25 numbered rows (82–106), 21 files.** Combined curated literal total: **79 + 25 = 104 rows**
(plus 2 cross-import consolidation notes and 15 ZVS-copy listings in §3).

---

## §3 ZVS forbidden-forms — 15 copies (allowed as versioned config, but LISTED)

D2 permits ZVS forbidden forms **as versioned config**; the defect is *duplication*, which drifts:

| # | location | form |
|---|----------|------|
| 1 | `zolai/zvs/rules_data.py:47,78,104` | **canonical** (`DIALECT_`/`STEM_FORBIDDEN_TO_PREFERRED`, `HISTORICAL_EXCEPTIONS`) |
| 2 | `zolai/zvs/exceptions.py:52-54` | inline exception subset |
| 3 | `zolai/rules/zolai_rules_reference.py:12-23` | `FORBIDDEN_FORMS` class dict |
| 4 | `zolai/api/zvs_checker.py:10-21,24-27` | `FORBIDDEN_FORMS` + `HISTORICAL_EXCEPTIONS` |
| 5 | `zolai/api/dynamic_data_pipeline.py:158` | inline list |
| 6 | `zolai/api/server.py:128` | embedded in system-prompt text |
| 7 | `zolai/offline/rule_engine.py:15-25` | `ZVS_CORRECTIONS` |
| 8 | `zolai/foundation/morphology.py:31-41` | `_ZVS_FORBIDDEN_MORPHEMES` |
| 9 | `zolai/foundation/analysis.py:436-446,464-472` | two inline maps |
| 10 | `zolai/foundation/verification_runner.py:33-41` | `ZVS_FORBIDDEN` |
| 11 | `zolai/foundation/regression.py:262-265` | inline list |
| 12 | `zolai/knowledge/rag_contract.py:104-112` | inline map |
| 13 | `zolai/data/services/dictionary.py:26-37` | inline map |
| 14 | `zolai/data/services/grammar.py:19-30` | inline map |
| 15 | `zolai/data/services/quality.py:91-94` | inline list |

**Proposed (C2):** all consumers import from `zolai/zvs/rules_data.py` (single canonical module), which
itself becomes the loader for a versioned artifact (`data/artifacts/zvs_rules_v1.json`) so community
review can edit without code changes. See also finding **F5** in [`ENGINE_FINDINGS.md`](ENGINE_FINDINGS.md).

---

## Excluded (non-lexicon literals — kept for transparency)

Response-shape dicts (`{"translation", "confidence", …}`), API column whitelists
(`lexicon_router._ZO_EN_LOOKUP`, `records_router` whitelist), table whitelists/export maps,
model/size enums (`context_optimizer`), HTML tag lists (`crawler`), URL token lists (`manager:106`),
output field lists (`zvs/cli`, `llm/gemini/models`), monitoring alert configs. These name *fields and
formats*, not words/tags/sentences, so D2 does not apply to them.

## Notes for C2/C3

- **Nothing here was changed in P2** — inventory only (plan constraint: no behavior changes).
- Replacement rule of thumb: *word→tag* → `grammar_patterns`/`dictionary.pos_canonical` (already in DB);
  *root/compound/participle lexica* → versioned JSON artifacts under `data/artifacts/` with a rebuild CLI;
  *closed-class function words* → one `closed_classes_v1.json`; *phoneme inventories* → one
  `phoneme_inventory_v1.json` derived from `syllable_data`; *ZVS* → canonical `rules_data.py`.
- New literals must not be added anywhere in `zolai/` (D2 enforcement point: C3 code review + this doc).
