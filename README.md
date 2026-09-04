# zolai-core — Zolai Python toolkit + RAG Knowledge Brain

Bilingual (Tedim Zolai ⇄ English) AI toolkit for the Zomi people. Python package,
FastAPI services, and the **RAG-first Knowledge Brain** (embeddings over wiki + PDF,
no raw fine-tuning).

## What's here

- `zolai/` — run-time Python package (analyzer, bible, cleaner, cli, crawler,
  dictionary, ingest, knowledge, ocr, api...)
- `zolai/knowledge/` — **Knowledge Brain**: `ingest` (MD/PDF → chunks → embeddings),
  `retrieve` (offline cosine RAG), `ngram` (word/bigram prediction tables)
- `scripts/kg/` — knowledge pipeline tooling + `smoke_test.py`
- `tests/` — pytest suite

## Quick start

```bash
pip install -e .          # or: source .venv/bin/activate
export HF_TOKEN=...       # embeddings sourced from HF Hub (cache)

# Index a wiki sample + a PDF-OCR corpus into the vector store
python -m zolai.knowledge.ingest --limit 20
python -m zolai.knowledge.pdf             # backlog B: PDF-derived knowledge
python scripts/kg/smoke_test.py           # PASS expected

# Retrieval (RAG)
python -c "from zolai.knowledge import load_index, retrieve
idx=load_index(); print(retrieve('Gentehna grammar sentence structure', idx))"
```

## Context

Read `context/` — the **pcore-orchestra six-file set** is ground truth for agents.
See `docs/ZOLAI_AI_ARCHITECTURE.md` (system) and
`docs/ZOLAI_KNOWLEDGE_BRAIN_ARCHITECTURE.md` (RAG-first, no fine-tuning).

## Language standard

ZVS 2018 orthography, SOV order, ergative `-in`. All language output must comply.

## Contribute

See `CONNECT.md` for how `zolai-core` relates to the other `zolai-ai` repos.

---

## Part of the Zolai-AI org

This repo is a component of the **Zolai-AI** organization — see the
[org profile](https://github.com/Zolai-AI) for the full ecosystem and
[`.github/CONTRIBUTING.md`](https://github.com/Zolai-AI/.github/blob/main/CONTRIBUTING.md) to contribute.

---

*Zolai AI · preserving Tedim Zolai (ZVS 2018) with a RAG-first bilingual toolkit for the Zomi people.*



---

## Org context

Full project ecosystem, architecture, design, status & plans: **[Zolai AI Project Brain](https://github.com/Zolai-AI/.github/blob/main/docs/ZOLAI_AI_PROJECT_BRAIN.md)**.
Part of the [Zolai-AI](https://github.com/Zolai-AI) org.
