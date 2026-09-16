# Foundation Roadmap — Phased Plan A–F

This roadmap maps the 54-section reference ontology onto the existing `zolai/` structure and guides the Foundation module build-out.

## Phase A — Core Analysis & Evidence (Complete ✅)
**Completed:** 2026-09-13

**Definition of Done:**
- [x] `zolai/foundation/` module importable with `FoundationAnalyzer` orchestrating existing engines (tokenizer, syllable, POS tagger, morphology)
- [x] `evidence.py` with `Candidate`, `Evidence`, `Confidence` dataclasses + `Verifier` ABC (default `NullVerifier`)
- [x] `consensus.py` with pure-Python majority-vote consensus (no network calls)
- [x] `data/gold/{words,sentences,paragraphs}.jsonl` seeded with ground-truth samples
- [x] `eval/gold_metrics.py` computes accuracy vs gold
- [x] CLI: `zolai foundation analyze <word|sentence|paragraph>` + `zolai foundation gold-eval`
- [x] All new + pre-existing tests pass; ruff clean

**Mapping to existing structure:**
| Ref Section | Existing Module | Foundation Role |
|-------------|----------------|-----------------|
| 25 (Word Analysis) | `zolai.morphology`, `zolai.syllable` | `WordAnalysis` dataclass |
| 33 (Sentence Analysis) | `zolai.pos_tagger`, `zolai.tokenizer` | `SentenceAnalysis` dataclass |
| 43 (Paragraph Analysis) | `zolai.analyzer` | `ParagraphAnalysis` dataclass |
| Evidence/Verification | — | New `evidence.py`, `consensus.py` |

---

## Phase B — Canonical Data Layer (Complete ✅)
**Completed:** 2026-09-14

**Definition of Done:**
- [x] `foundation_*` and `canonical_*` tables created in `data/zolai.db` via migration
- [x] Repository layer for foundation entities (`FoundationRepository`)
- [x] Raw → Staging → Canonical ETL pipeline with provenance tracking
- [x] Versioning + audit log integration for all foundation tables

**Mapping:**
| Ref Section | Target Table |
|-------------|-------------|
| 5–12 (Raw Corpus) | `foundation_raw_corpus` |
| 13–18 (Staging) | `foundation_staging` |
| 19–24 (Canonical) | `canonical_words`, `canonical_sentences`, `canonical_paragraphs` |
| 26–32 (Evidence) | `foundation_evidence`, `foundation_verifications` |
| 34–42 (Consensus) | `foundation_consensus` |

---

## Phase C — Adaptive Verification Loop (Complete ✅)
**Completed:** 2026-09-15

**Definition of Done:**
- [x] Batch verification runner (configurable batch size, concurrency)
- [x] Adaptive threshold: confidence gate → human review → auto-accept
- [x] Evidence gating: no LLM output reaches canonical without ≥2 independent sources
- [x] Regression test suite for linguistic errors (ZVS, grammar, syllable, tone)

---

## Phase D — Human Review UI + Production (Complete ✅)
**Completed:** 2026-09-16

**Definition of Done:**
- [x] Human Review UI with approve/reject workflow
- [x] Cost tracking per model and operation
- [x] Production Docker configuration
- [x] API endpoints for foundation operations

---

## Phase E — Complete Integration + Documentation (Complete ✅)
**Completed:** 2026-09-17

**Definition of Done:**
- [x] All foundation modules wired into main CLI
- [x] ETL pipeline tested end-to-end with real data
- [x] Verification loop validated with 1,000+ candidates
- [x] Complete API reference and documentation
- [x] Foundation integration guide
- [x] Scripts documentation
- [x] All bug fixes applied (table browser, chat endpoints, proficiency CLI)
- [x] Full pipeline execution completed

---

## Phase F — Ecosystem Benchmark & Release (Planned)
**Target:** TBD

**Definition of Done:**
- [ ] Zolai NLP Benchmark v1: syllable, grammar, translation, ZVS, tone
- [ ] Correction workflow: User → Review → Dataset → Regression test
- [ ] MCP server deployed with auth/rate limiting
- [ ] Documentation complete: API reference, contributor guide, architecture

---

## Cross-Cutting Principles (from `01-PRINCIPLES.md`)
1. **No direct LLM → Canonical** — Every canonical fact requires ≥2 independent evidence sources
2. **Evidence Gating** — LLM outputs are candidates only; verification is mandatory
3. **Versioning** — All canonical tables carry `version`, `source_hash`, `verified_at`
4. **RAW → Staging → Canonical** — Clear separation; no shortcuts
5. **Batch + Adaptive Verification** — Scale with confidence thresholds, not fixed rules