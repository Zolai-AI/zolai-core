# Zolai Second Brain — Open Source Roadmap

> Building a fully capable AI second brain for the Zomi people in Tedim Zolai.
> Active branch: `main` · `master` is the preserved archive.

---

## Current Focus Pillars (this phase)

1. **Deepen the website** (`website/zolai-project/`) — dictionary search UI, translation
   demo, model API integration, contributions/admin.
2. **Model training + Kaggle** — LoRA/QLoRA session cadence, datasets/adapters on HF/Kaggle.
3. **Sentence & word prediction** — predictive features + training corpora from the wiki.
4. **Linguistic improvement** — ZVS 2018 enrichment, grammar refinement, vocabulary reconciliation.
5. **Wiki update/improve** — apply `docs/WIKI_ENRICHMENT_GUIDE.md` (index hygiene, RAG readiness).
6. **GitHub org migration** — promote monorepo → `zolai-ai` org, separate repos per component
   (see `docs/ZOLAI_GITHUB_ORG_PLAN.md` + `docs/REPO_SPLIT_BLUEPRINT.md`), monorepo-first.

---

## Phase 1 — Foundation (Complete ✅)

- [x] Core Python package (`zolai/`) with CLI
- [x] Web crawlers for ZomiDaily, Tongsan, RVAsia, TongDot
- [x] Bible corpus pipeline (TB77, TBR17, Tedim1932, Tedim2010, KJV)
- [x] Unified dictionary (152k entries) with FTS5 SQLite
- [x] Parallel ZO↔EN translation pairs (105k+)
- [x] Instruction synthesis pipeline (v6)
- [x] Training data (v3, ~5.1M deduplicated sentences)
- [x] ZVS grammar rules and wiki knowledge base
- [x] Agent definitions (34 specialized agents)
- [x] Next.js dictionary web app

## Phase 2 — Open Source Release (In Progress 🔄)

- [x] Clean public repository (no secrets, no large binaries)
- [x] Publish datasets to Hugging Face Hub
  - `peterpausianlian/zolai-tedim-v3` — ~5.1M Zolai sentences
  - `peterpausianlian/zolai-llm-training-dataset` — LLM train/val/test splits
  - `peterpausianlian/zolai-adapter-qwen25-3b` — LoRA adapter checkpoints
- [x] Publish fine-tuned adapters to Hugging Face
  - `peterpausianlian/zolai-qwen2.5-3b-lora` — stable QLoRA adapter
  - `peterpausianlian/zolai-qwen-0.5b` — active LoRA FP16 training
- [x] CI/CD pipeline (GitHub Actions: lint, test, build)
- [x] Contributor guide and `CONTRIBUTING.md`
- [ ] Docker setup for local development

## Phase 3 — Model & API

- [ ] Fine-tune Qwen2.5-7B on full Tedim dataset (upgrade from current 0.5B/3B)
- [ ] Evaluate with BLEU/chrF on held-out parallel pairs
- [ ] Public REST API for translation and dictionary lookup
- [ ] Ollama-compatible GGUF model release
- [ ] OpenAI-compatible `/v1/chat/completions` endpoint
- [ ] **AI Tutor Chat** — rebuild chat/tutor UI once model & API are ready
  - Pages: `/chat`, `/tutor`
  - Infrastructure already in `website/zolai-project/lib/ai/`

## Phase 4 — Community & Ecosystem

- [ ] Interactive language learning app (CEFR-tagged lessons)
- [ ] Zolai keyboard / IME support
- [ ] Community contribution portal (crowdsource corrections)
- [ ] Telegram bot (public-facing)
- [ ] Zolai Wikipedia seed corpus
- [ ] OCR pipeline for printed Tedim books

## Phase 5 — Advanced NLP

- [ ] Named entity recognition (NER) for Tedim
- [ ] Part-of-speech tagger trained on ZVS grammar
- [ ] Speech-to-text (ASR) dataset collection
- [ ] Text-to-speech (TTS) voice synthesis
- [ ] Dialect detection (Tedim vs Falam vs Hakha)

---

## Foundation Engine Phases (A-E) — Complete ✅

### Phase A — Core Analysis & Evidence (2026-09-13) ✅
- [x] `zolai/foundation/` module importable with `FoundationAnalyzer`
- [x] `evidence.py` with `Candidate`, `Evidence`, `Confidence` dataclasses + `Verifier` ABC
- [x] `consensus.py` with pure-Python majority-vote consensus
- [x] Gold evaluation set seeded (`data/gold/{words,sentences,paragraphs}.jsonl`)
- [x] `eval/gold_metrics.py` computes accuracy vs gold
- [x] CLI: `zolai foundation analyze` + `zolai foundation gold-eval`
- [x] All new + pre-existing tests pass; ruff clean

### Phase B — Canonical Data Layer (2026-09-14) ✅
- [x] `foundation_*` and `canonical_*` tables created in `data/zolai.db` (13 tables)
- [x] Repository layer for foundation entities (`FoundationRepository`)
- [x] Raw → Staging → Canonical ETL pipeline with provenance tracking
- [x] Versioning + audit log integration for all foundation tables

### Phase C — Adaptive Verification Loop (2026-09-15) ✅
- [x] Batch verification runner (configurable batch size, concurrency)
- [x] Adaptive threshold: confidence gate → human review → auto-accept
- [x] Evidence gating: no LLM output reaches canonical without ≥2 independent sources
- [x] Regression test suite for linguistic errors (ZVS, grammar, syllable, tone)

### Phase D — Human Review UI + Production (2026-09-16) ✅
- [x] Human Review UI with approve/reject workflow
- [x] Cost tracking per model and operation
- [x] Production Docker configuration
- [x] API endpoints for foundation operations

### Phase E — Complete Integration + Documentation (2026-09-17) ✅
- [x] All foundation modules wired into main CLI
- [x] ETL pipeline tested end-to-end with real data
- [x] Verification loop validated with 1,000+ candidates
- [x] Complete API reference and documentation
- [x] Foundation integration guide
- [x] Scripts documentation
- [x] All bug fixes applied (table browser, chat endpoints, proficiency CLI)
- [x] Full pipeline execution completed

---

## Data Release Plan

Large datasets are not stored in this repository. They are released via:

| Dataset | Status | Location |
|---------|--------|----------|
| Parallel pairs (105k+) | ✅ Published | Hugging Face Hub |
| Dictionary (152k entries) | ✅ Published | Hugging Face Hub + API |
| Training set v3 (~5.1M) | ✅ Published | Hugging Face Hub |
| Bible corpus | ✅ Published | Hugging Face Hub |
| zolai-qwen2.5-3b-lora adapter | ✅ Published | Hugging Face Hub |
| zolai-qwen-0.5b adapter | 🔄 In training | Hugging Face Hub |

See [Hugging Face — peterpausianlian](https://huggingface.co/peterpausianlian).

---

## Contributing

Contributions welcome — especially:
- Native Tedim speakers for data validation
- Linguists familiar with Tibeto-Burman languages
- ML engineers with low-resource NLP experience

See `CONTRIBUTING.md` for guidelines.