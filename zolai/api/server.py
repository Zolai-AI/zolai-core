"""Zolai Toolkit — FastAPI REST API Server."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..analyzer.corpus import CorpusAnalyzer
from ..api.desktop_router import router as desktop_router
from ..api.foundation_router import router as foundation_router
from ..api.jsonl_router import router as jsonl_router
from ..cleaner.pipeline import CleanPipeline
from ..config import config
from ..crawler.engine import CrawlEngine
from ..dictionary.manager import DictionaryManager
from ..trainer.dataset import DatasetBuilder
from ..ui.routes import router as ui_router

logger = logging.getLogger(__name__)


def build_prompt(messages: list[ChatMessage], system_prompt: str) -> str:
    """Build prompt from chat messages."""
    prompt_parts = [f"system\n{system_prompt}\n"]

    for msg in messages:
        prompt_parts.append(f"{msg.role}\n{msg.content}")

    prompt_parts.append("assistant\n")
    return "\n\n".join(prompt_parts)


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen3-coder:480b-cloud")

ZOLAI_SYSTEM_PROMPT = (
    "You are an AI Senior Engineer, System Architect, Knowledge Manager, "
    "and Zolai Language Expert.\n\n"
    "Your primary goal is to build and maintain a persistent AI Second Brain "
    "for the Zolai (Tedim) language.\n\n"
    "STRICT RULES:\n"
    "1. DOMAIN LOCK: \"Zolai\" refers exclusively to the Zolai/Tedim language "
    "and culture. NEVER provide information about unrelated entities (e.g., "
    "wedding platforms). If context is missing or ambiguous, state that you "
    "are a Zolai Language Expert and ask for Zolai-related input.\n"
    "2. TUTORING MODE: Never act as a simple translator. Use the Socratic "
    "method: guide the learner's thinking, provide hints, and encourage "
    "participation before revealing answers.\n"
    "3. LINGUISTIC RIGOR:\n"
    "   - Enforce SOV (Subject-Object-Verb) word order.\n"
    "   - Use the Ergative marker \"in\" for transitive verb subjects.\n"
    "   - Distinguish between Stem I and Stem II verbs.\n"
    "   - Use \"Suahtakna\" for freedom/liberation.\n"
    "4. MODERN TECH: Use loanwords for primary tech terms (e.g., AI, Internet) "
    "but only use descriptive Zolai compounds (e.g., A kibawltawm Pilna) "
    "when EXPLAINING the concept.\n"
    "5. RESPONSE FORMAT:\n"
    "   - Responses MUST be short (<=4 lines).\n"
    "   - Use English only for explanations.\n"
    "   - Avoid preamble/postamble (e.g., \"Here is the answer\").\n\n"
    "Linguistic References:\n"
    "- Hello: Kum\n"
    "- Thank you: Lungdam (use this when thanking, NOT 'Lungdam na')\n"
    "- Thank you very much: Lungdam mahmah\n"
    "- I am well: Ka dam hi\n"
    "- Yes: Aw\n"
    "- No: Ai\n"
    "- Good: Hoih / Cidam (healthy)\n"
    "- Bad: Koh / Sia\n"
    "- 'na' particle: possessive (na=your) or noun-maker (lungdam na=gratitude)\n"
)

# Bilingual prompt with attestation rules for /chat/zolai
ZOLAI_BILINGUAL_PROMPT = (
    "You are a Zolai (Tedim) language teacher and conversation partner.\n\n"
    "CRITICAL ATTESTATION RULES — NEVER VIOLATE:\n"
    "1. NEVER use words not found in the Bible or dictionary.\n"
    "   If you don't know a Zolai word, say 'Ka thei kei hi' (I don't know)\n"
    "   and ask for the correct word. NEVER guess or invent words.\n"
    "2. ONLY use attested Zolai words:\n"
    "   - lungdam = thank you (verb/greeting)\n"
    "   - pasian = God\n"
    "   - topa = Lord\n"
    "   - kum = hello/greeting\n"
    "   - gam = place\n"
    "   - vantung = heaven\n"
    "   - tui = water\n"
    "   - mi = person\n"
    "   - numei = woman\n"
    "   - sing = tree\n"
    "   - nek = eat\n"
    "   - hiam = question marker\n"
    "   - hoih = good\n"
    "   - koh = bad\n"
    "   - aw = yes\n"
    "   - ai = no\n"
    "3. GRAMMAR — 'na' particle:\n"
    "   - 'na' as possessive = 'your' (2nd person singular)\n"
    "   - 'na' as noun-maker = makes abstract nouns from verbs/adjectives:\n"
    "     lungdam = thank you → lungdam na = gratitude\n"
    "     kum = year → kum na = age\n"
    "     lawm = friend → lawm na = friendship\n"
    "     dam = healthy → dam na = health\n"
    "4. GREETING USAGE — 'Lungdam' vs 'Lungdam na':\n"
    "   - Use 'Lungdam!' when thanking someone (NOT 'Lungdam na!')\n"
    "   - 'Lungdam na' means 'gratitude' (the concept), NOT for thanking\n"
    "   - 'Lungdam mahmah' = thank you very much\n"
    "   - 'Ka dam hi' = I am well (response to 'Na dam hi?')\n"
    "5. FORBIDDEN (ZVS 2018 non-compliant) — NEVER use:\n"
    "   pathian → pasian, ram → gam, fapa → tapa,\n"
    "   bawipa → topa, siangpahrang → kumpipa,\n"
    "   cu/cun → tua, suah → suahtakna,\n"
    "   zalenna → suahtakna, nunnak → nuntakna\n"
    "6. Use SOV word order.\n"
    "7. Use ergative 'in' for transitive subjects.\n"
    "8. If a user's Zolai contains a fake word, correct it:\n"
    "   'Ka thei kei hi. [word] a zong ou. [correct word] hi a ung.'\n\n"
    "RESPONSE FORMAT:\n"
    "Zolai: [your response in Zolai]\n"
    "English: [English translation]\n"
)

# --- Pydantic Models ---


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    data_root: str


class CrawlRequest(BaseModel):
    seed: str
    max_depth: int = 2
    max_pages: int = 50


class CrawlResponse(BaseModel):
    status: str
    pages_crawled: int
    seed: str


class CleanRequest(BaseModel):
    input_dir: Optional[str] = None
    output_dir: Optional[str] = None


class CleanResponse(BaseModel):
    status: str
    files_processed: int
    sentences_cleaned: int
    output_path: str


class AnalyzeRequest(BaseModel):
    corpus_path: Optional[str] = None


class AnalyzeResponse(BaseModel):
    status: str
    stats: dict


class TrainRequest(BaseModel):
    val_ratio: float = 0.02
    test_ratio: float = 0.01
    seed: int = 42


class TrainResponse(BaseModel):
    status: str
    splits: dict


class DictSearchRequest(BaseModel):
    query: str
    lang: str = "zolai"  # "zolai" or "english"


class DictSearchResponse(BaseModel):
    status: str
    results: list


# --- Chat Models ---


class ChatMessage(BaseModel):
    role: str = "user"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str = DEFAULT_MODEL
    temperature: float = 0.7
    stream: bool = False
    system_prompt: str = ZOLAI_SYSTEM_PROMPT
    max_tokens: int = 2048


class ChatResponse(BaseModel):
    model: str
    message: ChatMessage
    done: bool = True
    total_duration: int = 0


class ChatStreamRequest(BaseModel):
    messages: list[ChatMessage]
    model: str = DEFAULT_MODEL
    temperature: float = 0.7
    system_prompt: str = ZOLAI_SYSTEM_PROMPT

class KnowledgeSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    threshold: float = 0.7
    source_type: str | None = None  # filter: "wiki", "pdf"


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[dict]
    context: str  # formatted RAG context for injection

class ZolaiChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    model: str = DEFAULT_MODEL




class ModelInfo(BaseModel):
    name: str
    size: int
    modified_at: str


class ModelsResponse(BaseModel):
    models: list[ModelInfo]


# --- WebSocket Manager ---


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)

    async def broadcast(self, message: dict):
        for conn in self.connections:
            try:
                await conn.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()

# --- App Factory ---


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.paths.ensure_dirs()

        # Run database migrations on startup
        try:
            from ..data.database import get_manager
            from ..data.migrations import run_all_migrations

            mgr = get_manager()
            migration_result = run_all_migrations(mgr)
            logger.info("Database migrations completed: %s", {
                "constraints_applied": len(migration_result.get("constraints", {}).get("applied", [])),
                "indexes_applied": len(migration_result.get("indexes", {}).get("applied", [])),
                "foundation_constraints": len(migration_result.get("foundation_constraints", {}).get("applied", [])),
                "foundation_indexes": len(migration_result.get("foundation_indexes", {}).get("applied", [])),
            })
        except Exception:
            logger.exception("Failed to run database migrations on startup")

        logger.info("Zolai API started on %s:%d", config.api_host, config.api_port)
        yield
        logger.info("Zolai API shutting down")

    app = FastAPI(
        title="Zolai Toolkit API",
        description="REST API for Zolai language data pipeline",
        version="1.0.0",
        lifespan=lifespan,
    )

    # app.include_router(desktop_router)
    for route in desktop_router.routes:
        app.router.routes.append(route)
    # app.include_router(jsonl_router)
    for route in jsonl_router.routes:
        app.router.routes.append(route)
    # Foundation Review Queue API
    app.include_router(foundation_router, prefix="/api/v1")
    # UI Routes for review queue
    app.include_router(ui_router)

    # --- Static File Serving for Desktop App ---
    from fastapi.responses import FileResponse
    _FRONTEND_DIR = config.paths.frontend

    @app.get("/")
    async def serve_frontend():
        """Serve the desktop app HTML."""
        html_path = _FRONTEND_DIR / "index.html"
        if html_path.exists():
            return FileResponse(str(html_path))
        return {"status": "ok", "version": "0.3.0", "api": "running"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Health ---

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            data_root=str(config.paths.data),
        )

    # --- Crawler ---

    @app.post("/crawl", response_model=CrawlResponse)
    async def start_crawl(req: CrawlRequest):
        try:
            engine = CrawlEngine()
            results = engine.crawl_seed(req.seed)
            await manager.broadcast({"event": "crawl_complete", "pages": len(results)})
            return CrawlResponse(status="ok", pages_crawled=len(results), seed=req.seed)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Cleaner ---

    @app.post("/clean", response_model=CleanResponse)
    async def start_clean(req: CleanRequest):
        try:
            pipeline = CleanPipeline()
            result = pipeline.run_full_pipeline()
            await manager.broadcast({"event": "clean_complete", "result": result})
            return CleanResponse(
                status="ok",
                files_processed=result.get("files", 0),
                sentences_cleaned=result.get("sentences", 0),
                output_path=str(config.paths.data_cleaned),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Analyzer ---

    @app.post("/analyze", response_model=AnalyzeResponse)
    async def analyze(req: AnalyzeRequest):
        try:
            analyzer = CorpusAnalyzer()
            stats = analyzer.full_stats()
            return AnalyzeResponse(status="ok", stats=stats)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/stats")
    async def quick_stats():
        """Quick corpus statistics endpoint."""
        try:
            analyzer = CorpusAnalyzer()
            return analyzer.full_stats()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Trainer ---

    @app.post("/train/splits", response_model=TrainResponse)
    async def build_splits(req: TrainRequest):
        try:
            builder = DatasetBuilder()
            result = builder.build_splits(val_ratio=req.val_ratio, test_ratio=req.test_ratio, seed=req.seed)
            await manager.broadcast({"event": "splits_built", "result": result})
            return TrainResponse(status="ok", splits=result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Dictionary ---

    @app.post("/dictionary/search", response_model=DictSearchResponse)
    async def dictionary_search(req: DictSearchRequest):
        try:
            from ..data.database import get_manager
            db = get_manager()

            results = []

            # ZO→EN search
            zo_results = db.lookup_word(req.query)
            for r in zo_results[:10]:
                results.append({
                    "source": r.get("source", ""),
                    "zolai": r.get("zolai", ""),
                    "english": r.get("english_clean")
                    or r.get("english", ""),
                    "pos": r.get("pos", ""),
                    "example": "",
                    "direction": "zo-en",
                })

            # EN→ZO search
            en_results = db.lookup_english(req.query)
            for r in en_results[:10]:
                trans = r.get("translations_clean", "")
                if not trans:
                    import json as _json
                    try:
                        trans_list = _json.loads(
                            r.get("translations", "[]")
                        )
                        trans = (
                            trans_list[0] if trans_list else ""
                        )
                    except Exception:
                        trans = ""
                results.append({
                    "source": r.get("source", ""),
                    "zolai": trans,
                    "english": r.get("headword", ""),
                    "pos": r.get("pos", ""),
                    "example": "",
                    "direction": "en-zo",
                })

            return DictSearchResponse(
                status="ok", results=results[:20]
            )
        except Exception:
            # Fallback to DictionaryManager if DB fails
            try:
                manager = DictionaryManager()
                entries = manager.search(req.query) if req.query else []
                results = []
                for entry in entries[:20]:
                    results.append({
                        "source": entry.get("source", ""),
                        "zolai": entry.get("zolai", ""),
                        "english": entry.get("english", ""),
                        "pos": entry.get("pos", ""),
                        "example": entry.get("example", ""),
                    })
                return DictSearchResponse(status="ok", results=results)
            except Exception as e2:
                raise HTTPException(status_code=500, detail=str(e2))

    # --- Bible ---

    @app.get("/bible/status")
    async def bible_status():
        """Check Bible data availability."""
        bibles_dir = config.paths.data_knowledge / "bibles"
        files = list(bibles_dir.glob("**/*.xml")) if bibles_dir.exists() else []
        return {"status": "ok", "bible_files": len(files), "path": str(bibles_dir)}

    @app.get("/bible/search")
    async def bible_search(q: str = "", version: str = "tdb77", limit: int = 10):
        """Search Bible verses by Zolai or English text (LIKE match)."""
        from ..data.database import get_manager

        if not q:
            raise HTTPException(400, "Query parameter 'q' is required")

        try:
            db = get_manager()
            results = db.search_bible(q)
            # Limit results
            results = results[:limit]
            return {
                "query": q,
                "version": version,
                "count": len(results),
                "results": results,
            }
        except Exception:
            # Fallback to JSONL if DB fails
            import json
            parallel_dir = config.paths.data / "parallel"
            version_map = {
                "tdb77": "bible_parallel_tdb77_kjv.jsonl",
                "tbr17": "bible_parallel_tbr17_kjv.jsonl",
                "tedim2010": "bible_parallel_tedim2010_kjv.jsonl",
            }
            filename = version_map.get(version)
            if not filename:
                raise HTTPException(400, f"Unknown version: {version}")
            filepath = parallel_dir / filename
            if not filepath.exists():
                raise HTTPException(404, f"Bible data not found: {filename}")
            results = []
            q_lower = q.lower()
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    if len(results) >= limit:
                        break
                    try:
                        entry = json.loads(line)
                        zolai = entry.get("zolai", "")
                        english = entry.get("english", "")
                        if q_lower in zolai.lower() or q_lower in english.lower():
                            results.append(entry)
                    except json.JSONDecodeError:
                        continue
            return {"query": q, "version": version, "count": len(results), "results": results}

    # ── Dictionary CRUD ────────────────────────────────────────

    @app.post("/dictionary/add")
    async def dictionary_add(
        word: str,
        english: str = "",
        myanmar: str = "",
        pos: str = "",
    ):
        """Add a new dictionary entry."""
        from ..data.database import get_manager
        db = get_manager()
        r = db.enrich_word(
            word,
            english_clean=english,
            myanmar=myanmar,
            pos=pos,
            source="api_add",
        )
        return {"success": r, "word": word}

    @app.put("/dictionary/update")
    async def dictionary_update(word: str, field: str, value: str):
        """Update a dictionary entry field."""
        from ..data.database import get_manager
        db = get_manager()
        r = db.enrich_word(word, **{field: value})
        return {"success": r, "word": word, "field": field}

    @app.delete("/dictionary/delete")
    async def dictionary_delete(word: str, deleted_by: str = "api_user", reason: str = "api_delete"):
        """
        Soft delete a dictionary entry with full audit trail.

        Args:
            word: The word to delete
            deleted_by: Identifier of who/what initiated the delete (observer)
            reason: Reason for deletion

        Returns:
            Full audit trail of what was deleted across all tables
        """
        import sqlite3
        from datetime import datetime, timezone

        from ..config import config

        db_path = config.paths.zolai_db
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        now = datetime.now(timezone.utc).isoformat()
        deleted_by_id = f"{deleted_by}@{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        results = {"word": word, "deleted_by": deleted_by, "deleted_at": now, "tables_affected": []}

        # Tables to soft delete from (with their primary key column)
        tables_to_delete = [
            ("dictionary", "zolai"),
            ("dictionary_en_zo", "english"),
            ("dictionary_import", "zolai"),
            ("dictionary_en_zo_import", "english"),
        ]

        for table, pk_col in tables_to_delete:
            # First, check if record exists and get its data for audit
            pk_name = "zolai" if "dictionary" in table and "en_zo" not in table else "english"
            cur.execute(f'SELECT * FROM "{table}" WHERE "{pk_name}" = ?', (word,))
            existing = cur.fetchone()

            if existing:
                # Get column names
                cur.execute(f'PRAGMA table_info("{table}")')
                [col[1] for col in cur.fetchall()]

                # Log to data_audit_log before deletion
                cur.execute(
                    """INSERT INTO data_audit_log (table_name, row_id, field, old_value, new_value, changed_at, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (table, 0, "is_deleted", "0", "1", now, f"{reason} by {deleted_by}")
                )

                # Also log the deleted_by info
                cur.execute(
                    """INSERT INTO data_audit_log (table_name, row_id, field, old_value, new_value, changed_at, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (table, 0, "deleted_by", "", deleted_by_id, now, f"{reason} by {deleted_by}")
                )

                # Soft delete
                cur.execute(
                    f'UPDATE "{table}" SET is_deleted = 1, deleted_at = ? WHERE "{pk_col}" = ?',
                    (now, word)
                )
                rows_affected = cur.rowcount

                if rows_affected > 0:
                    results["tables_affected"].append({
                        "table": table,
                        "rows_affected": rows_affected,
                        "primary_key": word
                    })

        conn.commit()
        conn.close()

        total_affected = sum(t["rows_affected"] for t in results["tables_affected"])
        results["success"] = total_affected > 0
        results["total_rows_affected"] = total_affected

        return results

    @app.get("/dictionary/search/all")
    async def dictionary_search_all(q: str, limit: int = 20):
        """Search dictionary with ZO↔EN↔MY."""
        from ..data.database import get_manager

        db = get_manager()
        results = db.search_dictionary(q, limit=limit)
        return {"results": results, "count": len(results)}

    # ── Monitor ────────────────────────────────────────────────

    @app.get("/monitor/health")
    async def monitor_health():
        """DB health check."""
        from ..data.database import get_manager

        db = get_manager()
        return db.health_check()

    @app.get("/monitor/coverage")
    async def monitor_coverage():
        """Translation coverage per table."""
        from ..data.database import get_manager

        db = get_manager()
        return db.quality_report()

    @app.get("/monitor/audit")
    async def monitor_audit(limit: int = 50):
        """Recent audit log entries."""
        from ..data.database import get_manager

        db = get_manager()
        return db.get_audit_log(limit=limit)

    # --- Myanmar / Burmese endpoints ---

    @app.get("/dictionary/search/my")
    async def search_myanmar_dictionary(
        q: str = Query(..., description="Myanmar search query"),
    ):
        """Search Myanmar dictionary + Bible for matching text."""
        from ..data.database import get_manager

        db = get_manager()
        results = db.lookup_myanmar(q)
        return {"query": q, "results": results, "count": len(results)}

    @app.get("/dictionary/translate/zo-my")
    async def translate_zolai_to_myanmar(
        word: str = Query(..., description="Zolai word"),
    ):
        """Translate Zolai → Myanmar via dictionary."""
        from ..data.database import get_manager

        db = get_manager()
        result = db.translate_zo_my(word)
        if result:
            return result
        return {"error": f"No Myanmar translation found for '{word}'"}

    @app.get("/dictionary/translate/my-zo")
    async def translate_myanmar_to_zolai(
        word: str = Query(..., description="Myanmar word"),
    ):
        """Translate Myanmar → Zolai via dictionary."""
        from ..data.database import get_manager

        db = get_manager()
        result = db.translate_my_zo(word)
        if result:
            return result
        return {"error": f"No Zolai translation found for '{word}'"}

    @app.get("/bible/search/my")
    async def search_judson_bible(
        q: str = Query(..., description="Myanmar search text"),
    ):
        """Search Judson Bible by Myanmar text."""
        from ..data.database import get_manager

        db = get_manager()
        results = db.search_judson(q)
        return {"query": q, "results": results, "count": len(results)}

    # --- Knowledge Brain (RAG) ---


    @app.post("/knowledge/search", response_model=KnowledgeSearchResponse)
    async def knowledge_search(req: KnowledgeSearchRequest):
        """Search the knowledge brain (RAG) for relevant chunks."""
        from ..knowledge.retrieve import format_context
        from ..knowledge.retrieve import retrieve as rag_retrieve

        hits = rag_retrieve(
            req.query,
            top_k=req.top_k,
            threshold=req.threshold,
        )
        # Apply metadata filtering
        if req.source_type:
            hits = [h for h in hits if h.get("metadata", {}).get("source_type") == req.source_type]
        ctx = format_context(hits)
        return KnowledgeSearchResponse(
            query=req.query,
            results=hits,
            context=ctx,
        )

    @app.get("/knowledge/status")
    async def knowledge_status():
        """Check knowledge index status."""
        from ..knowledge.retrieve import load_index

        idx = load_index()
        return {
            "indexed_chunks": len(idx.ids),
            "has_vectors": idx.vectors is not None,
            "index_path": str(config.paths.data_knowledge / "knowledge_vectors.jsonl"),
        }

    # === Zolai Bilingual Chat ===


    class ZolaiChatResponse(BaseModel):
        zolai_response: str
        english_gloss: str = ""
        context_source: str = ""
        zvs_compliant: bool = True
        vocabulary: list[str] = []

    @app.post("/chat/zolai", response_model=ZolaiChatResponse)
    async def zolai_chat(req: ZolaiChatRequest):
        """Zolai bilingual chat with RAG context injection."""
        from .conversation_memory import get_conversation_memory
        from .rag_context import build_zolai_context
        from .zvs_checker import check_zvs_compliance

        memory = get_conversation_memory()

        # Build RAG context
        rag_context = build_zolai_context(req.message)

        # Get conversation history
        history = memory.get_history(req.session_id)
        history_text = ""
        if history:
            turns = [f"{t['role']}: {t['text'][:100]}" for t in history[-3:]]
            history_text = "Previous conversation:\n" + "\n".join(turns) + "\n\n"

        # Bilingual system prompt with context
        bilingual_prompt = (
            f"{ZOLAI_BILINGUAL_PROMPT}\n\n"
            f"{history_text}"
            f"CONTEXT FROM DICTIONARY AND BIBLE:\n{rag_context}\n"
        )

        # Call Ollama
        messages = [ChatMessage(role="user", content=req.message)]
        prompt = build_prompt(messages, bilingual_prompt)

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": req.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.7, "num_predict": 512},
                    },
                )
                data = resp.json()
                raw_response = data.get("response", "").strip()
        except Exception as e:
            raw_response = (
                "Sorry, I cannot reach the language model right now. "
                f"Error: {e}"
            )

        # Check ZVS compliance
        zvs_result = check_zvs_compliance(raw_response)
        final_response = (
            zvs_result['corrected_text']
            if not zvs_result['is_compliant']
            else raw_response
        )

        # Extract English gloss (look for "English:" line)
        english_gloss = ""
        for line in final_response.split("\n"):
            if line.strip().startswith("English:"):
                english_gloss = line.replace("English:", "").strip()
                break

        # Extract vocabulary used
        from .rag_context import get_rag_context
        rag = get_rag_context()
        vocabulary = rag.extract_zolai_words(req.message)

        # Save to memory
        memory.add_turn(req.session_id, "user", req.message)
        memory.add_turn(req.session_id, "assistant", final_response)
        memory.add_vocabulary(req.session_id, vocabulary)

        return ZolaiChatResponse(
            zolai_response=final_response,
            english_gloss=english_gloss,
            context_source=rag_context[:200] if rag_context else "No context",
            zvs_compliant=zvs_result['is_compliant'],
            vocabulary=vocabulary,
        )

    @app.post("/chat/gemini", response_model=ZolaiChatResponse)
    async def gemini_chat(req: ZolaiChatRequest):
        """Gemini chat via local gemini-webapi with RAG, ensemble, and validation."""
        import sys
        from pathlib import Path as _Path

        # Add zolai-ai-local to path
        local_pkg = _Path(__file__).parent.parent.parent.parent / "zolai-ai-local"
        if str(local_pkg) not in sys.path:
            sys.path.insert(0, str(local_pkg))

        try:
            from gemini.client_openai import get_default_client
            from shared.zvs_context import get_system_prompt

            # Step 1: RAG context injection
            from .rag_injector import RAGInjector
            db_path = str(config.paths.zolai_db)
            rag = RAGInjector(db_path)
            rag_context = await rag.build_context(req.message, max_tokens=800)

            # Step 2: Build enhanced prompt
            system_prompt = get_system_prompt()
            if rag_context:
                system_prompt += f"\n\nCONTEXT FROM KNOWLEDGE BASE:\n{rag_context}\n"

            # Step 3: Ensemble dispatch (3 models)
            from .gemini_ensemble import GeminiEnsemble
            ensemble_models = [
                "gemini-3-flash",
                "gemini-3-pro-plus",
                "gemini-3-pro",
            ]
            ensemble = GeminiEnsemble(ensemble_models)

            # Try ensemble first; fall back to single model
            ensemble_result = await ensemble.dispatch(
                message=req.message,
                system_prompt=system_prompt,
                n_models=3,
            )

            if ensemble_result["response"]:
                text = ensemble_result["response"]
                context_source = (
                    f"ensemble:{','.join(ensemble_result['models_used'])}"
                    f"(conf={ensemble_result['confidence']:.2f})"
                )
            else:
                # Fallback: single model call
                client = await get_default_client()
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": req.message},
                ]
                result = await client.chat_completion(
                    messages=messages,
                    model=req.model,
                    temperature=0.7,
                )
                text = result["choices"][0]["message"]["content"]
                context_source = f"gemini:{req.model}"

            # Step 4: Validate response
            from .answer_validator import AnswerValidator
            validator = AnswerValidator(db_path)
            await validator.validate(text)

            # Apply ZVS corrections if needed
            from .zvs_checker import check_zvs_compliance
            zvs_result = check_zvs_compliance(text)
            final_response = (
                zvs_result['corrected_text']
                if not zvs_result['is_compliant']
                else text
            )

            # Step 5: Return with metadata
            return ZolaiChatResponse(
                zolai_response=final_response,
                zvs_compliant=zvs_result['is_compliant'],
                context_source=context_source,
                vocabulary=[],
            )
        except Exception as e:
            return ZolaiChatResponse(
                zolai_response=f"Gemini error: {str(e)}",
                zvs_compliant=True,
                context_source="error",
                vocabulary=[],
            )

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            while True:
                data = await ws.receive_json()
                await manager.broadcast({"echo": data})
        except WebSocketDisconnect:
            manager.disconnect(ws)

    # === Ollama Chat Endpoints ===

    @app.get("/chat/models", response_model=ModelsResponse)
    async def list_models():
        """List available Ollama models."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            data = resp.json()
            models = []
            for m in data.get("models", []):
                models.append(
                    ModelInfo(
                        name=m["name"],
                        size=m.get("size", 0),
                        modified_at=m.get("modified_at", ""),
                    )
                )
            return ModelsResponse(models=models)

    @app.post("/chat/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        """Chat with Ollama model."""
        # Build prompt from messages
        prompt = build_prompt(req.messages, req.system_prompt)

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": req.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": req.temperature,
                        "num_predict": req.max_tokens,
                    },
                },
            )
            data = resp.json()

        return ChatResponse(
            model=req.model,
            message=ChatMessage(role="assistant", content=data.get("response", "").strip()),
            done=data.get("done", True),
            total_duration=data.get("total_duration", 0),
        )

    @app.post("/chat/chat/stream")
    async def chat_stream(req: ChatStreamRequest):
        """Chat with Ollama model (streaming)."""
        prompt = build_prompt(req.messages, req.system_prompt)

        async def generate():
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": req.model,
                        "prompt": prompt,
                        "stream": True,
                        "options": {
                            "temperature": req.temperature,
                        },
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            yield f"data: {line}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/chat")
    async def chat_get(q: str, model: str = DEFAULT_MODEL):
        """Simple GET chat endpoint."""
        messages = [ChatMessage(role="user", content=q)]
        return await chat(ChatRequest(messages=messages, model=model))

    # --- Web UI ---

    # (No duplicate "/" route — serve_frontend above handles "/" for the desktop app.)

    # UI Static Files
    _UI_STATIC_DIR = Path(__file__).parent.parent / "ui" / "static"

    @app.get("/review/static/{file_path:path}")
    async def serve_ui_static(file_path: str):
        """Serve UI static files (CSS/JS/images)."""
        full_path = _UI_STATIC_DIR / file_path
        if full_path.exists() and full_path.is_file():
            return FileResponse(str(full_path))
        return {"error": "Not found"}


    # === Provider Endpoints ===

    @app.get("/providers")
    async def list_providers():
        """List available LLM providers."""
        from ..llm.providers.base import get_provider_registry
        registry = get_provider_registry()
        providers = registry.list_providers()
        return {
            "providers": [
                {
                    "name": p.name,
                    "priority": p.priority,
                    "available": p.is_available,
                    "models": p.models,
                    "description": p.description,
                }
                for p in providers
            ]
        }

    @app.get("/providers/{provider_name}")
    async def get_provider(provider_name: str):
        """Get provider details."""
        from ..llm.providers.base import get_provider_registry
        registry = get_provider_registry()
        provider = registry.get_provider(provider_name)
        if not provider:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")
        info = provider.get_info()
        return {
            "name": info.name,
            "priority": info.priority,
            "available": info.is_available,
            "models": info.models,
            "description": info.description,
        }

    # === Settings Endpoints ===

    @app.get("/settings")
    async def get_settings():
        """Get all application settings."""
        from ..core.settings import get_settings_manager
        manager = get_settings_manager()
        return manager.get_all()

    @app.post("/settings")
    async def update_settings(updates: dict[str, Any]):
        """Update application settings."""
        from ..core.settings import get_settings_manager
        manager = get_settings_manager()
        manager.update(updates)
        return {"success": True, "settings": manager.get_all()}

    @app.get("/settings/provider")
    async def get_provider_settings():
        """Get provider-related settings."""
        from ..core.settings import get_settings_manager
        manager = get_settings_manager()
        return manager.get_provider_settings()

    @app.get("/settings/learning")
    async def get_learning_settings():
        """Get learning-related settings."""
        from ..core.settings import get_settings_manager
        manager = get_settings_manager()
        return manager.get_learning_settings()

    # === Learning Endpoints ===

    @app.get("/learning/grammar")
    async def list_grammar_patterns(
        function: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ):
        """List grammar patterns."""
        from ..learning.grammar_editor import GrammarEditor
        editor = GrammarEditor()
        patterns = editor.search_patterns(query=query, function=function, limit=limit)
        return {"patterns": patterns, "count": len(patterns)}

    @app.post("/learning/grammar")
    async def add_grammar_pattern(
        pattern: str,
        function: str,
        description: str = "",
        examples: str = "",
    ):
        """Add a new grammar pattern."""
        from ..learning.grammar_editor import GrammarEditor
        editor = GrammarEditor()
        result = editor.add_pattern(
            pattern=pattern,
            function=function,
            description=description,
            examples=examples,
        )
        return result

    @app.post("/learning/grammar/validate")
    async def validate_grammar_pattern(
        pattern: str,
        function: str = "",
    ):
        """Validate a grammar pattern."""
        from ..learning.grammar_editor import GrammarEditor
        editor = GrammarEditor()
        result = editor.validate_pattern(pattern=pattern, function=function)
        return result

    @app.get("/learning/dictionary")
    async def search_learning_dictionary(
        query: str,
        direction: str = "both",
        limit: int = 20,
    ):
        """Search dictionary for learning."""
        from ..learning.dictionary_manager import DictionaryManager
        manager = DictionaryManager()
        results = manager.search(query=query, direction=direction, limit=limit)
        return {"results": results, "count": len(results)}

    @app.post("/learning/dictionary")
    async def add_dictionary_entry(
        zolai: str,
        english: str,
        myanmar: str = "",
        pos: str = "",
    ):
        """Add a new dictionary entry."""
        from ..learning.dictionary_manager import DictionaryManager
        manager = DictionaryManager()
        result = manager.add_entry(zolai=zolai, english=english, myanmar=myanmar, pos=pos)
        return result

    @app.post("/learning/translate")
    async def translate_text(
        text: str,
        direction: str = "auto",
        context: str | None = None,
    ):
        """Translate text between English and Zolai."""
        from ..learning.translation import TranslationEngine
        engine = TranslationEngine()
        result = engine.translate(text=text, direction=direction, context=context)
        return result

    @app.get("/learning/progress")
    async def get_progress(user_id: str = "default"):
        """Get learning progress."""
        from ..learning.progress import ProgressTracker
        tracker = ProgressTracker(user_id=user_id)
        cefr = tracker.get_cefr_level()
        due = tracker.get_due_reviews()
        return {
            "cefr": cefr,
            "due_reviews": due,
            "due_count": len(due),
        }

    @app.post("/learning/quiz")
    async def generate_quiz(
        quiz_type: str = "vocabulary",
        count: int = 10,
        level: str | None = None,
        user_id: str = "default",
    ):
        """Generate a quiz."""
        from ..learning.progress import ProgressTracker
        tracker = ProgressTracker(user_id=user_id)
        quiz = tracker.generate_quiz(quiz_type=quiz_type, count=count, level=level)
        return {"quiz": quiz, "count": len(quiz)}

    @app.get("/learning/statistics")
    async def get_learning_statistics():
        """Get learning statistics."""
        from ..learning.progress import ProgressTracker
        from ..learning.translation import TranslationEngine
        tracker = ProgressTracker()
        translation_engine = TranslationEngine()
        return {
            "progress": tracker.get_statistics(),
            "translation": translation_engine.get_translation_stats(),
        }

    @app.post("/learning/translate/batch")
    async def translate_batch(
        texts: list[str],
    ):
        """Translate multiple texts in batch."""
        from ..learning.translation import TranslationEngine
        engine = TranslationEngine()
        results = engine.translate_batch(texts=texts)
        return {"results": results, "count": len(results)}

    @app.get("/learning/search")
    async def search_learning_resources(
        query: str,
        limit: int = 10,
    ):
        """Search all learning resources (vocabulary, grammar, Bible)."""
        from ..learning.online_search import get_online_search
        search = get_online_search()
        results = search.search_all(query=query, limit=limit)
        return results

    @app.get("/learning/search/vocabulary")
    async def search_vocabulary(
        query: str,
        limit: int = 10,
    ):
        """Search vocabulary online."""
        from ..learning.online_search import get_online_search
        search = get_online_search()
        results = search.search_vocabulary(word=query, limit=limit)
        return {"results": results, "count": len(results)}

    @app.get("/learning/search/grammar")
    async def search_grammar_patterns(
        query: str,
        limit: int = 10,
    ):
        """Search grammar patterns online."""
        from ..learning.online_search import get_online_search
        search = get_online_search()
        results = search.search_grammar(pattern=query, limit=limit)
        return {"results": results, "count": len(results)}

    @app.get("/learning/search/bible")
    async def search_bible_verses(
        query: str,
        limit: int = 10,
    ):
        """Search Bible verses online."""
        from ..learning.online_search import get_online_search
        search = get_online_search()
        results = search.search_bible(query=query, limit=limit)
        return {"results": results, "count": len(results)}

    # === Static File Serving (last route - catch-all) ===
    @app.get("/{path:path}")
    async def serve_static(path: str):
        """Serve static files (CSS/JS/images only)."""
        file_path = _FRONTEND_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return {"error": "Not found"}

    return app


# App instance for uvicorn
app = create_app()
