# Phase 0 Database Audit — API Audit

**Generated:** 2026-09-13 22:15

## Overview

Analysis of all API endpoints in `zolai/api/server.py` and `zolai/api/desktop_router.py`, including which database tables each endpoint reads/writes.

## API Endpoints Summary

### Server.py Endpoints (31 endpoints)

| Method | Path | Database Tables | Description |
|--------|------|-----------------|-------------|
| GET | `/` | None | Serve frontend HTML |
| GET | `/health` | None | Health check |
| POST | `/crawl` | None | Start web crawl |
| POST | `/clean` | None | Run clean pipeline |
| POST | `/analyze` | None | Analyze corpus |
| GET | `/stats` | None | Quick corpus stats |
| POST | `/train/splits` | None | Build training splits |
| POST | `/dictionary/search` | `dictionary`, `dictionary_en_zo` | Search dictionary (ZO↔EN) |
| GET | `/bible/status` | None | Check Bible data availability |
| GET | `/bible/search` | `bible_verses` | Search Bible verses |
| POST | `/dictionary/add` | `dictionary` | Add dictionary entry |
| PUT | `/dictionary/update` | `dictionary` | Update dictionary entry |
| DELETE | `/dictionary/delete` | `dictionary`, `dictionary_en_zo`, `dictionary_import`, `dictionary_en_zo_import`, `data_audit_log` | Soft delete with audit |
| GET | `/dictionary/search/all` | `dictionary`, `dictionary_en_zo` | Search ZO↔EN↔MY |
| GET | `/monitor/health` | `dictionary`, `bible_verses`, `vocab`, `translations`, `grammar_patterns`, `phrases`, `proverbs`, `word_alignments`, `word_usage`, `data_audit_log` | DB health check |
| GET | `/monitor/coverage` | `dictionary`, `bible_verses`, `vocab`, `translations`, `grammar_patterns`, `phrases`, `proverbs`, `word_alignments`, `word_usage` | Translation coverage |
| GET | `/monitor/audit` | `data_audit_log` | Recent audit log entries |
| GET | `/dictionary/search/my` | `dictionary`, `dictionary_en_zo` | Search Myanmar dictionary |
| GET | `/dictionary/translate/zo-my` | `dictionary` | Translate Zolai → Myanmar |
| GET | `/dictionary/translate/my-zo` | `dictionary_en_zo` | Translate Myanmar → Zolai |
| GET | `/bible/search/my` | `bible_verses` | Search Judson Bible |
| POST | `/knowledge/search` | None (uses vector index) | RAG knowledge search |
| GET | `/knowledge/status` | None (uses vector index) | Knowledge index status |
| POST | `/chat/zolai` | `dictionary`, `bible_verses` | Zolai bilingual chat |
| POST | `/chat/gemini` | None | Gemini chat |
| WEBSOCKET | `/ws` | None | WebSocket connection |
| GET | `/chat/models` | None | List Ollama models |
| POST | `/chat/chat` | None | Chat with Ollama |
| POST | `/chat/chat/stream` | None | Chat with Ollama (streaming) |
| GET | `/chat` | None | Simple GET chat |
| GET | `/{path:path}` | None | Serve static files |

### Desktop Router Endpoints (44 endpoints)

| Method | Path | Database Tables | Description |
|--------|------|-----------------|-------------|
| GET | `/desktop/tables` | All tables | List all tables with row counts |
| GET | `/desktop/query` | Any table (SELECT only) | Run SQL query |
| GET | `/desktop/stats` | All tables | Database statistics dashboard |
| GET | `/desktop/table-data` | Any table | Paginated table data |
| GET | `/desktop/table-schema` | Any table | Table column schema |
| GET | `/desktop/dict/browse` | `dictionary` | Browse dictionary entries |
| GET | `/desktop/dict/stats` | `dictionary` | Dictionary statistics |
| GET | `/desktop/dict/non-zolai` | None (runs script) | Check for non-Zolai words |
| GET | `/desktop/bible/study` | None (runs script) | Bible book study |
| GET | `/desktop/bible/learn` | None (runs script) | Progressive learning |
| GET | `/desktop/bible/context/book` | None (runs script) | Per-book context analysis |
| GET | `/desktop/bible/context/word` | None (runs script) | Word usage profile |
| GET | `/desktop/bible/context/topics` | None (runs script) | Topic clusters |
| GET | `/desktop/bible/books` | `bible_verses` | List Bible books |
| GET | `/desktop/bible/chapters` | `bible_verses` | List chapters for book |
| GET | `/desktop/bible/verses` | `bible_verses` | Get verses for chapter |
| GET | `/desktop/gemini/fill-en` | None (runs script) | Fill missing English |
| GET | `/desktop/gemini/fill-my` | None (runs script) | Fill missing Myanmar |
| GET | `/desktop/gemini/coverage` | `dictionary` | Translation coverage |
| GET | `/desktop/gemini/fill` | None (runs script) | Quick Gemini fill |
| GET | `/desktop/training/generate` | None (runs script) | Generate training data |
| GET | `/desktop/training/generate-sentences` | None (runs script) | Generate Bible sentences |
| GET | `/desktop/training/validate` | None (runs script) | Validate sentences |
| GET | `/desktop/training/deep-validate` | None (runs script) | Deep validate sentences |
| GET | `/desktop/training/build` | None (runs script) | Build training dataset |
| GET | `/desktop/training/build-qwen` | None (runs script) | Build Qwen3 format |
| GET | `/desktop/training/export` | None (runs script) | Export training data |
| GET | `/desktop/training/build-corpus` | None (runs script) | Build corpus |
| GET | `/desktop/training/corpus-stats` | None (runs script) | Corpus statistics |
| GET | `/desktop/test/quiz` | None (runs script) | Proficiency quiz |
| GET | `/desktop/test/stats` | None (runs script) | Test statistics |
| GET | `/desktop/grammar/check` | None (runs script) | Check grammar |
| GET | `/desktop/grammar/negation-rules` | None (runs script) | Negation rules reference |
| GET | `/desktop/paragraph/analyze` | None (runs script) | Analyze paragraph |
| GET | `/desktop/paragraph/style` | None (runs script) | Style transfer |
| GET | `/desktop/paragraph/paraphrase` | None (runs script) | Paraphrase text |
| GET | `/desktop/zvs/validate` | None (runs script) | Validate ZVS compliance |
| GET | `/desktop/zvs/forbidden` | None (runs script) | List forbidden forms |
| GET | `/desktop/pattern/stats` | `grammar_patterns` | Grammar pattern stats |
| GET | `/desktop/pattern/learn` | `grammar_patterns` | Sample grammar patterns |
| GET | `/desktop/export/{data_type}` | `dictionary`, `bible_verses`, `vocab`, `grammar_patterns`, `phrases`, `training_exercises` | Export data to JSONL |
| GET | `/desktop/audit/recent` | `data_audit_log` | Recent audit log entries |
| GET | `/desktop/ai/models` | None | List AI models |

## Database Table Usage by Endpoint

### Direct Database Access

| Table | Read Endpoints | Write Endpoints |
|-------|----------------|-----------------|
| `dictionary` | `/dictionary/search`, `/dictionary/search/all`, `/dictionary/search/my`, `/monitor/*`, `/desktop/dict/*`, `/desktop/gemini/coverage`, `/desktop/export/dictionary` | `/dictionary/add`, `/dictionary/update`, `/dictionary/delete` |
| `dictionary_en_zo` | `/dictionary/search`, `/dictionary/search/all`, `/dictionary/translate/my-zo`, `/dictionary/delete` | `/dictionary/delete` |
| `bible_verses` | `/bible/search`, `/bible/search/my`, `/desktop/bible/books`, `/desktop/bible/chapters`, `/desktop/bible/verses`, `/desktop/export/bible` | None |
| `vocab` | `/monitor/*`, `/desktop/export/vocabulary` | None |
| `translations` | `/monitor/*` | None |
| `grammar_patterns` | `/monitor/*`, `/desktop/pattern/*`, `/desktop/export/grammar` | None |
| `phrases` | `/monitor/*`, `/desktop/export/phrases` | None |
| `proverbs` | `/monitor/*` | None |
| `word_alignments` | `/monitor/*` | None |
| `word_usage` | `/monitor/*` | None |
| `data_audit_log` | `/monitor/audit`, `/desktop/audit/recent`, `/dictionary/delete` | `/dictionary/delete` |
| `training_exercises` | `/desktop/export/exercises` | None |

### Script-Based Endpoints

Most desktop endpoints run external scripts rather than direct database access:

| Script | Endpoint | Description |
|--------|----------|-------------|
| `check_non_zolai.py` | `/desktop/dict/non-zolai` | Dictionary quality check |
| `bible_engine.py` | `/desktop/bible/study`, `/desktop/bible/learn` | Bible analysis |
| `context_deep_learner.py` | `/desktop/bible/context/*` | Context analysis |
| `gemini_translate.py` | `/desktop/gemini/*` | Translation filling |
| `generate_training_data.py` | `/desktop/training/generate` | Training generation |
| `generate_sentences.py` | `/desktop/training/generate-sentences` | Sentence generation |
| `validate_sentences.py` | `/desktop/training/validate` | Validation |
| `deep_validate.py` | `/desktop/training/deep-validate` | Deep validation |
| `build_training_corpus.py` | `/desktop/training/build`, `/desktop/training/export`, `/desktop/training/build-corpus`, `/desktop/training/corpus-stats` | Corpus building |
| `format_training_data.py` | `/desktop/training/build-qwen` | Qwen3 formatting |
| `proficiency_test.py` | `/desktop/test/*` | Proficiency testing |
| `grammar_check.py` | `/desktop/grammar/*` | Grammar checking |
| `paragraph_engine.py` | `/desktop/paragraph/*` | Paragraph analysis |
| `zolai-zvs` | `/desktop/zvs/*` | ZVS compliance |

## Security Considerations

1. **SQL Injection Risk**: `/desktop/query` accepts raw SQL (SELECT only)
2. **Script Execution**: Desktop endpoints run external scripts with user input
3. **No Authentication**: Most endpoints have no auth middleware
4. **CORS**: Allows all origins (`allow_origins=["*"]`)

## Recommendations

1. **Add authentication** — API key or JWT auth for write endpoints
2. **Rate limiting** — Protect against abuse
3. **Input validation** — Sanitize SQL inputs and script arguments
4. **Audit logging** — Log all write operations
5. **Endpoint documentation** — Add OpenAPI descriptions for all endpoints

## Endpoint Statistics

- **Total endpoints:** 75 (31 server.py + 44 desktop_router.py)
- **Database-read endpoints:** 25
- **Database-write endpoints:** 4
- **Script-based endpoints:** 16
- **No-db endpoints:** 30