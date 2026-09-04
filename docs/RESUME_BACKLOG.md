# Zolai-AI — Resume & Backlog

Single source of truth for **what's done**, **what remains**, and **how to resume**
this project from any fresh session. Open this file first.

---

## Current state (2026-09-04)

**Workspace:** `/home/peter/Documents/Projects/zolai-ai/` (8 repos, 13GB total)
**Org:** [github.com/Zolai-AI](https://github.com/Zolai-AI)
**Monorepo:** DELETED — data distributed to component repos (freed ~14GB)
**Free disk:** ~18GB

### Repos

| Repo | Role | Key data |
|------|------|----------|
| `zolai-core` | Python toolkit + RAG Knowledge Brain | `zolai/`, `tests/`, `scripts/`, `config/`, `docs/`, `data/` |
| `zolai-web` | Learner platform (Next.js) | `website/` (3.4GB), `components/`, `app/` |
| `zolai-tauri` | Offline desktop app (Tauri 2) | `src-tauri/`, bundled core + GGUF |
| `zolai-datasets` | Bilingual corpora & datasets | `data/` (6.3GB — corpora, dictionaries, models) |
| `zolai-training` | LoRA/QLoRA fine-tuning + GGUF | `kaggle_dataset/`, `notebooks/` |
| `zolai-wiki` | Knowledge base | grammar, vocabulary, curriculum, culture |
| `.github` | Org profile + community | profile README, logo, CONTRIBUTING, SECURITY |
| `zolai-ai.github.io` | GitHub Pages landing | animated landing page |

### Quick resume commands
```bash
cd /home/peter/Documents/Projects/zolai-ai/zolai-core
source .venv/bin/activate  # or create: python -m venv .venv && pip install -e .
python -m pytest tests/ -q
python scripts/kg/smoke_test.py
```

### Seven-file context set (ground truth at session start)
Each code repo (core, web, tauri, datasets, training, wiki) has `context/` with:
- `project-overview.md`, `architecture.md`, `code-standards.md`
- `project-setup.md`, `ui-context.md`, `progress-tracker.md`, `ai-workflow-rules.md`

---

## Completed work

### Org migration (peterlianpi → Zolai-AI)
- Created org `Zolai-AI`; 9 repos created/public
- Org profile, topics on all repos, metadata set
- Pages site live at `https://zolai-ai.github.io/`

### Data distribution (monorepo → component repos)
- `data/` (6.3GB) → `zolai-datasets`
- `website/` (3.4GB) → `zolai-web`
- `kaggle_dataset/` + `notebooks/` → `zolai-training`
- `wiki/` → `zolai-wiki` (already present)
- `zolai/`, `tests/`, `scripts/`, `config/`, `docs/`, `agents/`, `skills/` → `zolai-core`
- Monorepo deleted — freed ~14GB

### Backlog C — Prediction lookup API ✅
- `zolai/api/prediction_api.py` (APIRouter `/predictions`)
- 14 tests, all passing
- Commit: `647e700`

### Versioning
- **2.0.0** across all repos (CHANGELOG.md added)

### P-Core-Orchestra
- Seven-file context set confirmed
- Orchestra scripts added to `zolai-core/scripts/`

---

## Remaining / open backlog

### Backlog D — Dataset export (NOT started)
Expose corpora via `zolai-datasets` to HuggingFace Hub + Kaggle.

### Backlog E — Zolai RAG assistant (NOT started)
Conversational Q&A consuming Knowledge Brain retrieval.

### Backlog F — Monorepo root restructure
DEFERRED — monorepo deleted, not applicable.

### Backlog G — `zolai-web` push unblock
Org-wide GH013 blocked push to `Zolai-AI/zolai-web`. Now resolved (web push succeeded).

### Owner-only (GitHub UI)
| Action | Where |
|--------|-------|
| Upload org avatar | Zolai-AI → Settings → Profile |
| Pin 6 repos | org overview → Customize pins |
| Resolve billing | org billing settings |

---

## Storage state
- Workspace: 13GB (was 27GB)
- Free disk: 18GB (was 5.5GB)
- Monorepo: deleted
- Global caches: cleaned (uv, pip, pre-commit, go-build, huggingface, node-gyp)
