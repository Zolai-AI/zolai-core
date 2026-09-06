"""Zolai Toolkit — FastAPI REST API Server."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..analyzer.corpus import CorpusAnalyzer
from ..cleaner.pipeline import CleanPipeline
from ..config import config
from ..crawler.engine import CrawlEngine
from ..dictionary.manager import DictionaryManager
from ..trainer.dataset import DatasetBuilder

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    """A single chat message."""
    role: str
    content: str


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
    "   - Enforce OSV (Object-Subject-Verb) word order.\n"
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
    "   cu/cun → tua, suah → chuak,\n"
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
        logger.info("Zolai API started on %s:%d", config.api_host, config.api_port)
        yield
        logger.info("Zolai API shutting down")

    app = FastAPI(
        title="Zolai Toolkit API",
        description="REST API for Zolai language data pipeline",
        version="1.0.0",
        lifespan=lifespan,
    )

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
            manager = DictionaryManager()
            entries = manager.search(req.query) if req.query else []
            results = []
            for entry in entries[:20]:
                results.append(
                    {
                        "source": entry.get("source", ""),
                        "zolai": entry.get("zolai", ""),
                        "english": entry.get("english", ""),
                        "pos": entry.get("pos", ""),
                        "example": entry.get("example", ""),
                    }
                )
            return DictSearchResponse(status="ok", results=results)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Bible ---

    @app.get("/bible/status")
    async def bible_status():
        """Check Bible data availability."""
        bibles_dir = config.paths.data_knowledge / "bibles"
        files = list(bibles_dir.glob("**/*.xml")) if bibles_dir.exists() else []
        return {"status": "ok", "bible_files": len(files), "path": str(bibles_dir)}

    @app.get("/bible/search")
    async def bible_search(q: str = "", version: str = "tdb77", limit: int = 10):
        """Search bible parallel corpora for matching verses."""
        import json

        parallel_dir = Path("/home/peter/Documents/Projects/zolai-ai/data/parallel")
        version_map = {
            "tdb77": "bible_parallel_tdb77_kjv.jsonl",
            "tbr17": "bible_parallel_tbr17_kjv.jsonl",
            "tedim2010": "bible_parallel_tedim2010_kjv.jsonl",
        }
        filename = version_map.get(version)
        if not filename:
            raise HTTPException(
                400, f"Unknown version: {version}. Use tdb77, tbr17, or tedim2010"
            )

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

    # --- Knowledge Brain (RAG) ---

    class KnowledgeSearchRequest(BaseModel):
        query: str
        top_k: int = 5
        threshold: float = 0.7
        source_type: str | None = None  # filter: "wiki", "pdf"

    class KnowledgeSearchResponse(BaseModel):
        query: str
        results: list[dict]
        context: str  # formatted RAG context for injection

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

    class ZolaiChatRequest(BaseModel):
        message: str
        session_id: str = "default"
        model: str = DEFAULT_MODEL

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

    # === Web UI ===

    @app.get("/")
    async def web_ui():
        """Serve web chat UI."""
        from fastapi.responses import HTMLResponse

        html_path = Path(__file__).parent / "templates" / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Zolai API</h1><p>Go to /docs for API docs</p>")

    return app


# App instance for uvicorn
app = create_app()
