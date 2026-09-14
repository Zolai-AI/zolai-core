# Foundation Roadmap — Phased Plan A–F

This roadmap maps the 54-section reference ontology onto the existing `zolai/` structure and guides the Foundation module build-out.

## Phase A — Core Analysis & Evidence (Current)
**Definition of Done:**
- `zolai/foundation/` module importable with `FoundationAnalyzer` orchestrating existing engines (tokenizer, syllable, POS tagger, morphology)
- `evidence.py` with `Candidate`, `Evidence`, `Confidence` dataclasses + `Verifier` ABC (default `NullVerifier`)
- `consensus.py` with pure-Python majority-vote consensus (no network calls)
- `data/gold/{words,sentences,paragraphs}.jsonl` seeded with ground-truth samples
- `eval/gold_metrics.py` computes accuracy vs gold
- CLI: `zolai foundation analyze <word|sentence|paragraph>` + `zolai foundation gold-eval`
- All new + pre-existing tests pass; ruff clean

**Mapping to existing structure:**
| Ref Section | Existing Module | Foundation Role |
|-------------|----------------|-----------------|
| 25 (Word Analysis) | `zolai.morphology`, `zolai.syllable` | `WordAnalysis` dataclass |
| 33 (Sentence Analysis) | `zolai.pos_tagger`, `zolai.tokenizer` | `SentenceAnalysis` dataclass |
| 43 (Paragraph Analysis) | `zolai.analyzer` | `ParagraphAnalysis` dataclass |
| Evidence/Verification | — | New `evidence.py`, `consensus.py` |

---

## Phase B — Canonical Data Layer
**Definition of Done:**
- `foundation_*` and `canonical_*` tables created in `data/zolai.db` via migration
- Repository layer for foundation entities (`FoundationRepository`)
- Raw → Staging → Canonical ETL pipeline with provenance tracking
- Versioning + audit log integration for all foundation tables

**Mapping:**
| Ref Section | Target Table |
|-------------|-------------|
| 5–12 (Raw Corpus) | `foundation_raw_corpus` |
| 13–18 (Staging) | `foundation_staging` |
| 19–24 (Canonical) | `canonical_words`, `canonical_sentences`, `canonical_paragraphs` |
| 26–32 (Evidence) | `foundation_evidence`, `foundation_verifications` |
| 34–42 (Consensus) | `foundation_consensus` |

---

## Phase C — Adaptive Verification Loop
**Definition of Done:**
- Batch verification runner (configurable batch size, concurrency)
- Adaptive threshold: confidence gate → human review → auto-accept
- Evidence gating: no LLM output reaches canonical without ≥2 independent sources
- Regression test suite for linguistic errors (ZVS, grammar, syllable, tone)

---

## Phase D — RAG Integration & Knowledge Brain
**Definition of Done:**
- `pcore-brain` API integration: Foundation analyses injected as RAG context
- Dictionary-first → phrase match → grammar pattern → AI fallback pipeline
- ZVS 2018 compliance enforced at generation time
- Bible verse attestation as primary evidence source

---

## Phase E — Offline Desktop & Mobile
**Definition of Done:**
- Tauri app bundles Foundation analyzer + SQLite DB (no network required)
- GGUF model export for offline grammar/translation
- Mobile vocabulary app with syllable engine + spaced repetition

---

## Phase F — Ecosystem Benchmark & Release
**Definition of Done:**
- Zolai NLP Benchmark v1: syllable, grammar, translation, ZVS, tone
- Correction workflow: User → Review → Dataset → Regression test
- MCP server deployed with auth/rate limiting
- Documentation complete: API reference, contributor guide, architecture

---

## Cross-Cutting Principles (from `01-PRINCIPLES.md`)
1. **No direct LLM → Canonical** — Every canonical fact requires ≥2 independent evidence sources
2. **Evidence Gating** — LLM outputs are candidates only; verification is mandatory
3. **Versioning** — All canonical tables carry `version`, `source_hash`, `verified_at`
4. **RAW → Staging → Canonical** — Clear separation; no shortcuts
5. **Batch + Adaptive Verification** — Scale with confidence thresholds, not fixed rules