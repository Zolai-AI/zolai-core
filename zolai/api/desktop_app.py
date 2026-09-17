"""Zolai Desktop API — slim FastAPI entrypoint for Tauri sidecar.

Runs with minimal dependencies (no ML/torch). Mounts the same routers
as the full server so the desktop UI works identically.

Usage:
    python -m zolai.api.desktop_app
    python -m zolai.api.desktop_app --port 8001
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("zolai.desktop_app")

# ---------------------------------------------------------------------------
# Lightweight lifespan — skip ML/heavy imports
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown for the desktop sidecar."""
    from ..config import config
    config.paths.ensure_dirs()

    # Run lightweight DB migrations
    try:
        from ..data.database import get_manager
        from ..data.migrations import run_all_migrations

        mgr = get_manager()
        run_all_migrations(mgr)
        logger.info("Database migrations completed")
    except Exception:
        logger.exception("Failed to run database migrations on startup")

    logger.info(
        "Zolai Desktop API started on %s:%s",
        os.environ.get("ZOLAI_HOST", "127.0.0.1"),
        os.environ.get("ZOLAI_PORT", "8000"),
    )
    yield
    logger.info("Zolai Desktop API shutting down")


def create_desktop_app() -> FastAPI:
    """Create a slim FastAPI app for desktop mode."""
    app = FastAPI(
        title="Zolai Desktop API",
        description="Lightweight API for Tauri desktop app",
        version="2.0.0",
        lifespan=lifespan,
    )

    # --- CORS: localhost only ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",      # Tauri dev server
            "http://localhost:8000",      # Same-origin
            "tauri://localhost",          # Tauri protocol
            "https://tauri.localhost",    # Tauri v2 protocol
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Mount existing routers ---
    from .desktop_router import router as desktop_router
    from .foundation_router import router as foundation_router
    from .jsonl_router import router as jsonl_router

    # Desktop routes (prefix: /desktop)
    for route in desktop_router.routes:
        app.router.routes.append(route)

    # JSONL routes (prefix: /desktop/jsonl)
    for route in jsonl_router.routes:
        app.router.routes.append(route)

    # Foundation review queue (prefix: /api/v1/foundation)
    app.include_router(foundation_router, prefix="/api/v1")

    # --- Health endpoint ---

    @app.get("/health")
    async def health():
        from ..config import config
        return {
            "status": "ok",
            "version": "2.0.0",
            "mode": "desktop",
            "data_root": str(config.paths.data),
        }

    # --- API Status endpoint ---

    @app.get("/api/status")
    async def api_status():
        """Detailed status for the Tauri frontend."""
        from ..config import config
        import sqlite3

        db_path = config.paths.zolai_db
        db_exists = db_path.exists()
        db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 1) if db_exists else 0

        table_count = 0
        if db_exists:
            try:
                conn = sqlite3.connect(str(db_path))
                cur = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
                table_count = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass

        # Check plugin status
        plugins_loaded = []
        try:
            from ..plugins import get_plugin_registry
            registry = get_plugin_registry()
            plugins_loaded = [
                {"name": p.name, "available": p.is_available()}
                for p in registry.list_plugins()
            ]
        except Exception:
            pass

        return {
            "status": "ok",
            "version": "2.0.0",
            "mode": "desktop",
            "database": {
                "exists": db_exists,
                "path": str(db_path),
                "size_mb": db_size_mb,
                "tables": table_count,
            },
            "plugins": plugins_loaded,
            "host": os.environ.get("ZOLAI_HOST", "127.0.0.1"),
            "port": int(os.environ.get("ZOLAI_PORT", "8000")),
        }

    # --- Plugin endpoints ---

    @app.get("/plugins")
    async def list_plugins():
        """List available plugins and their status."""
        try:
            from ..plugins import get_plugin_registry
            registry = get_plugin_registry()
            return {
                "plugins": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "available": p.is_available(),
                    }
                    for p in registry.list_plugins()
                ]
            }
        except Exception as e:
            return {"plugins": [], "error": str(e)}

    # --- Provider endpoints ---

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

    # --- Settings endpoints ---

    @app.get("/settings")
    async def get_settings():
        """Get all application settings."""
        from ..core.settings import get_settings_manager
        manager = get_settings_manager()
        return manager.get_all()

    @app.post("/settings")
    async def update_settings(updates: dict):
        """Update application settings."""
        from ..core.settings import get_settings_manager
        manager = get_settings_manager()
        manager.update(updates)
        return {"success": True, "settings": manager.get_all()}

    # --- Learning endpoints ---

    @app.get("/learning/grammar")
    async def list_grammar_patterns(pattern_type: str = None, limit: int = 50):
        """List grammar patterns."""
        from ..learning.grammar_editor import GrammarEditor
        editor = GrammarEditor()
        patterns = editor.search_patterns(pattern_type=pattern_type, limit=limit)
        return {"patterns": patterns, "count": len(patterns)}

    @app.get("/learning/dictionary")
    async def search_dictionary(query: str, limit: int = 20):
        """Search dictionary for learning."""
        from ..learning.dictionary_manager import DictionaryManager
        manager = DictionaryManager()
        results = manager.search(query=query, limit=limit)
        return {"results": results, "count": len(results)}

    @app.post("/learning/translate")
    async def translate_text(text: str, direction: str = "auto"):
        """Translate text between English and Zolai."""
        from ..learning.translation import TranslationEngine
        engine = TranslationEngine()
        result = engine.translate(text=text, direction=direction)
        return result

    @app.get("/learning/progress")
    async def get_progress(user_id: str = "default"):
        """Get learning progress."""
        from ..learning.progress import ProgressTracker
        tracker = ProgressTracker(user_id=user_id)
        cefr = tracker.get_cefr_level()
        return {"cefr": cefr}

    return app


# App instance for uvicorn
app = create_desktop_app()

# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Zolai Desktop API Server")
    parser.add_argument("--host", default=os.environ.get("ZOLAI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ZOLAI_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    uvicorn.run(
        "zolai.api.desktop_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
