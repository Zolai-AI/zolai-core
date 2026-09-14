# Target Schema — Foundation & Canonical Tables

Maps the proposed ~25–35 canonical tables onto existing DB tables + planned `foundation_*`/`canonical_*` additions.

## Existing Canonical Tables (72 tables, ~3.1M rows)

| Table | Rows | Role in Foundation |
|-------|------|-------------------|
| `dictionary` | 103,303 | ZO→EN master (T2 evidence) |
| `dictionary_en_zo` | 113,750 | EN→ZO master (T2 evidence) |
| `bible_verses` | 62,751 | EN/ZO/MY parallel verses (T1 evidence) |
| `grammar_patterns` | 5,547 | SOV/tense/negation patterns (T4 evidence) |
| `phrases` | 5,000 | Multi-word expressions (T2/T3 evidence) |
| `vocab` | 94,458 | Vocabulary index + frequency (T3 evidence) |
| `translations` | 212,754 | EN↔ZO + EN→MY sentence pairs (T3 evidence) |
| `word_usage` | 60,365 | Per-book word profiles (T1/T3 evidence) |
| `training_exercises` | 81,805 | Grammar exercises (derived) |
| `word_alignments` | 385,120 | Word-level ZO↔EN alignment (T1 evidence) |
| `word_collocations` | 5,000 | Word pair frequencies (T3 evidence) |
| `proverbs` | 7,736 | Cultural context (T2 evidence) |
| `syllable_data` | 189,554 | Syllable segmentation (derived) |
| `zolai_vocabulary` | 112,279 | Master vocab (dict + Bible + ref) |
| `zolai_bible_analysis` | 30,758 | Verse + compounds + grammar |
| `zolai_word_usage` | 85,045 | Per-book frequency + meanings |
| `zolai_grammar_patterns` | 13,519 | Grammar from all sources |
| `zolai_tone_sandhi` | 19 | 19 tone sandhi rules |
| `zolai_proverbs_idioms` | 4,984 | Proverbs with cultural context |

---

## New Foundation Tables (Phase B)

### Raw Layer (append-only, immutable)
| Table | Purpose | Source |
|-------|---------|--------|
| `foundation_raw_corpus` | Raw JSONL imports (web, PDF, Bible USX) | Ingest pipeline |
| `foundation_raw_llm` | Raw LLM outputs (candidates) | Batch generation jobs |

### Staging Layer (rebuildable, transient)
| Table | Purpose | Source |
|-------|---------|--------|
| `foundation_staging_words` | Cleaned word candidates + syllable/POS/morphology | `FoundationAnalyzer` |
| `foundation_staging_sentences` | Cleaned sentence analyses + structure | `FoundationAnalyzer` |
| `foundation_staging_paragraphs` | Cleaned paragraph analyses + style profile | `FoundationAnalyzer` |
| `foundation_staging_evidence` | Evidence bundles per candidate | Evidence collection |

### Canonical Layer (serving reads, versioned, evidence-gated)
| Table | Purpose | Promotion Rule |
|-------|---------|----------------|
| `canonical_words` | Verified word entries: form, syllables, POS, morphology, meanings, tone | ≥2 T1/T2 sources OR consensus ≥0.95 |
| `canonical_sentences` | Verified sentences: tokens, POS, dependencies, translation, grammar | ≥2 sources OR consensus ≥0.90 |
| `canonical_paragraphs` | Verified paragraphs: style, structure, multi-style paraphrases | Human review required |
| `foundation_evidence` | Evidence records: source, tier, confidence, provenance_hash, payload | Auto on staging write |
| `foundation_verifications` | Verification results: candidate_id, verifier, passed, score, notes | Auto on verify run |
| `foundation_consensus` | Consensus decisions: fact_key, candidates[], decision, confidence, method | Auto on batch verify |

### Meta / Operational
| Table | Purpose |
|-------|---------|
| `foundation_batches` | Batch job runs: id, type, status, started, completed, stats |
| `foundation_review_queue` | Human review items: candidate_id, priority, assignee, status |
| `foundation_metrics` | Evaluation metrics: run_id, metric, value, baseline, delta |

---

## Schema Details (Key Tables)

### `canonical_words`
```sql
CREATE TABLE canonical_words (
    id INTEGER PRIMARY KEY,
    form TEXT NOT NULL,                    -- Zolai word form (ZVS 2018)
    syllables TEXT NOT NULL,               -- JSON: ["pa", "sian"]
    syllable_count INTEGER NOT NULL,
    pos TEXT NOT NULL,                     -- NOUN, VERB, ADJ, etc.
    morphology TEXT,                       -- JSON: {prefix, root, suffix, compound_parts}
    meanings TEXT NOT NULL,                -- JSON: [{"en": "God", "source": "bible"}, ...]
    tone_profile TEXT,                     -- JSON: {"ambiguous": true, "notes": "T1=lie, T3=thin"}
    zvs_compliant BOOLEAN NOT NULL DEFAULT 1,
    frequency INTEGER DEFAULT 0,           -- Corpus frequency
    version INTEGER NOT NULL DEFAULT 1,
    source_hash TEXT NOT NULL,             -- SHA256 of promoting evidence
    verified_at TIMESTAMP,
    verified_by TEXT,                      -- 'consensus:v1' | 'human:reviewer'
    evidence_ids TEXT NOT NULL,            -- JSON array of foundation_evidence.rowids
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(form, version)
);
CREATE INDEX idx_canonical_words_form ON canonical_words(form);
CREATE INDEX idx_canonical_words_pos ON canonical_words(pos);
```

### `canonical_sentences`
```sql
CREATE TABLE canonical_sentences (
    id INTEGER PRIMARY KEY,
    zolai_text TEXT NOT NULL,              -- Full Zolai sentence
    english_text TEXT,                     -- English translation (if parallel)
    tokens TEXT NOT NULL,                  -- JSON: [{"form": "Pasian", "pos": "N.PROPER", ...}]
    pos_tags TEXT NOT NULL,                -- JSON: ["N.PROPER", "PART.ERG", ...]
    dependencies TEXT,                     -- JSON: [{"head": 2, "dep": "nsubj", ...}]
    grammar_features TEXT,                 -- JSON: {"tense": "past", "negation": "kei", ...}
    bible_ref TEXT,                        -- e.g. "GEN 1:1" if from Bible
    version INTEGER NOT NULL DEFAULT 1,
    source_hash TEXT NOT NULL,
    verified_at TIMESTAMP,
    verified_by TEXT,
    evidence_ids TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_canonical_sentences_bible_ref ON canonical_sentences(bible_ref);
```

### `foundation_evidence`
```sql
CREATE TABLE foundation_evidence (
    id INTEGER PRIMARY KEY,
    fact_type TEXT NOT NULL,               -- 'word' | 'sentence' | 'paragraph' | 'grammar'
    fact_key TEXT NOT NULL,                -- e.g. 'word:pasian' | 'sentence:GEN 1:1'
    source TEXT NOT NULL,                  -- 'bible_verses' | 'dictionary' | 'corpus' | 'grammar_patterns' | 'llm'
    tier INTEGER NOT NULL,                 -- 1=T1(Bible), 2=T2(Dict), 3=T3(Corpus), 4=T4(Grammar), 5=T5(LLM)
    confidence REAL NOT NULL,              -- 0.0–1.0
    provenance_hash TEXT NOT NULL,         -- SHA256 of source record(s)
    payload TEXT NOT NULL,                 -- JSON: source-specific evidence detail
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_foundation_evidence_fact ON foundation_evidence(fact_type, fact_key);
CREATE INDEX idx_foundation_evidence_tier ON foundation_evidence(tier);
```

### `foundation_consensus`
```sql
CREATE TABLE foundation_consensus (
    id INTEGER PRIMARY KEY,
    fact_type TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    candidates TEXT NOT NULL,              -- JSON: [{"evidence_id": 123, "value": {...}, "weight": 0.9}, ...]
    decision TEXT NOT NULL,                -- JSON: the promoted canonical value
    confidence REAL NOT NULL,              -- Aggregated confidence
    method TEXT NOT NULL,                  -- 'majority_vote' | 'weighted_evidence' | 'threshold'
    threshold REAL NOT NULL,               -- Threshold used
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fact_type, fact_key, method)
);
CREATE INDEX idx_foundation_consensus_fact ON foundation_consensus(fact_type, fact_key);
```

---

## Mapping: Reference Ontology Sections → Tables

| Ref Section | Ontology Concept | Target Table(s) |
|-------------|------------------|-----------------|
| 1–4 | Corpus Acquisition | `foundation_raw_corpus` |
| 5–8 | Raw Normalization | `foundation_staging_*` |
| 9–12 | Dictionary Harmonization | `foundation_staging_words` → `canonical_words` |
| 13–16 | Bible Alignment | `bible_verses` (existing) + `word_alignments` (existing) |
| 17–20 | Grammar Extraction | `grammar_patterns` (existing) → `canonical_words.sentences` |
| 21–24 | Syllable/Morphology | `syllable_data` (existing) + `foundation_staging_words` |
| 25 | Word Analysis | `canonical_words` |
| 26–32 | Evidence Collection | `foundation_evidence` |
| 33 | Sentence Analysis | `canonical_sentences` |
| 34–42 | Consensus Building | `foundation_consensus` |
| 43 | Paragraph Analysis | `canonical_paragraphs` |
| 44–48 | Verification Loop | `foundation_verifications`, `foundation_review_queue` |
| 49–54 | Serving / RAG | `canonical_*` (read-only for serving) |

---

## Data Flow (Reflects Reference Doc Final Flow)

```
┌──────────────┐
│  RAW SOURCES │  (Bible USX, Web crawl, PDF, Dict JSONL)
└──────┬───────┘
       │ Ingest (append-only)
       ▼
┌──────────────────┐
│ foundation_raw_* │  (immutable, provenance-tracked)
└──────┬───────────┘
       │ Build (idempotent, no serving reads)
       ▼
┌──────────────────────┐
│ foundation_staging_* │  (cleaned, normalized, analyzed)
└──────┬───────────────┘
       │ Evidence Collection (auto)
       ▼
┌──────────────────────┐
│ foundation_evidence  │  (tiered, provenance_hash, confidence)
└──────┬───────────────┘
       │ Consensus (batch, adaptive threshold)
       ▼
┌──────────────────────┐
│ foundation_consensus │  (decision + method + confidence)
└──────┬───────────────┘
       │ Promote (versioned, audited)
       ▼
┌──────────────────────┐     ┌──────────────────────┐
│ canonical_words      │     │ canonical_sentences  │  ← SERVING LAYER
│ canonical_paragraphs │     │ (read-only for API)  │
└──────────────────────┘     └──────────────────────┘
       │                              │
       └──────────────┬───────────────┘
                      ▼
            ┌──────────────────┐
            │  pcore-brain     │  (RAG context injection)
            │  API (zolai)     │
            └──────────────────┘
```

---

## Migration Strategy

1. **Phase A (current):** No schema changes — Foundation module runs in-memory, writes gold JSONL
2. **Phase B:** Add migration `027_foundation_tables.py` creating all `foundation_*`/`canonical_*` tables
3. **Phase B:** Add `FoundationRepository` in `zolai/data/repositories/foundation.py`
4. **Phase C:** Wire batch verification → consensus → promotion pipeline
5. **Phase D:** Update serving layer (RAG, API) to read from `canonical_*` tables

---

## Constraints & Invariants

- **DB-First:** Serving layer reads ONLY from `canonical_*` / existing canonical tables (enforced by `test_dbfirst_compliance.py`)
- **No JSONL in Serving:** `foundation_staging_*` and `foundation_raw_*` never read by API
- **Versioning:** Every canonical write increments version, records `source_hash`, `evidence_ids`
- **Audit:** Every canonical write appends to `data_audit_log` (existing)
- **ZVS Gate:** `ZVSValidator` runs on all `canonical_words` promotions