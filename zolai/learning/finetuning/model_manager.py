"""
Model Manager - Handles model registration, versioning and deployment state.

The registry lives in SQLite (``finetune_model_registry``); no ``registry.json``
is written. Weight files, when a real training backend produces them, stay on
disk — this module only tracks their metadata.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ...data.repositories import get_engine

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages model versions, checkpoints, and deployments (SQLite-backed)."""

    def __init__(self, engine: Engine = None, models_dir: str = "./models"):
        self.engine = engine or get_engine()
        self.models_dir = Path(models_dir)
        self.ensure_schema()

    # ── Schema ─────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create the registry table if missing (additive, idempotent)."""
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS finetune_model_registry (
                    model_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    base_model TEXT NOT NULL,
                    training_method TEXT NOT NULL,
                    model_path TEXT,
                    metrics TEXT NOT NULL DEFAULT '{}',
                    tags TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'registered',
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_finetune_model_registry_version
                ON finetune_model_registry (name, version)
            """))

    # ── CRUD ───────────────────────────────────────────────────────────────

    def register_model(
        self,
        name: str,
        version: str,
        base_model: str,
        training_method: str,
        model_path: str = "",
        metrics: dict = None,
        tags: list = None,
    ) -> str:
        """Register a new model version (idempotent on name+version)."""
        existing = self.find_by_version(name, version)
        if existing:
            return existing["id"]

        model_id = str(uuid.uuid4())[:8]
        now = datetime.utcnow().isoformat()

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO finetune_model_registry
                        (model_id, name, version, base_model, training_method,
                         model_path, metrics, tags, status, is_active, created_at, updated_at)
                    VALUES (:model_id, :name, :version, :base_model, :training_method,
                            :model_path, :metrics, :tags, 'registered', 0, :now, :now)
                """),
                {
                    "model_id": model_id,
                    "name": name,
                    "version": version,
                    "base_model": base_model,
                    "training_method": training_method,
                    "model_path": model_path or "",
                    "metrics": json.dumps(metrics or {}, sort_keys=True, default=str),
                    "tags": json.dumps(tags or []),
                    "now": now,
                },
            )

            # Set as active if this is the first model
            total = conn.execute(
                text("SELECT COUNT(*) FROM finetune_model_registry")
            ).scalar()
            if total == 1:
                conn.execute(
                    text("UPDATE finetune_model_registry SET is_active = 1 WHERE model_id = :mid"),
                    {"mid": model_id},
                )

        logger.info(f"Registered model {name} {version} as {model_id}")
        return model_id

    def _row_to_dict(self, row) -> dict:
        """Normalise a registry row (decoding JSON columns)."""
        model = dict(row._mapping)
        model["metrics"] = json.loads(model.get("metrics") or "{}")
        model["tags"] = json.loads(model.get("tags") or "[]")
        model["is_active"] = bool(model.get("is_active"))
        # ``id`` is the historical alias callers expect from ``list_models``.
        model["id"] = model["model_id"]
        return model

    def get_model(self, model_id: str) -> dict | None:
        """Get model info by ID."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM finetune_model_registry WHERE model_id = :mid"),
                {"mid": model_id},
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_by_version(self, name: str, version: str) -> dict | None:
        """Get a model by name + version."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT * FROM finetune_model_registry
                    WHERE name = :name AND version = :version
                """),
                {"name": name, "version": version},
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_active_model(self) -> dict | None:
        """Get currently active model."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM finetune_model_registry WHERE is_active = 1 LIMIT 1")
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def set_active_model(self, model_id: str) -> bool:
        """Set active model for serving."""
        if self.get_model(model_id) is None:
            return False

        with self.engine.begin() as conn:
            conn.execute(text("UPDATE finetune_model_registry SET is_active = 0"))
            conn.execute(
                text("""
                    UPDATE finetune_model_registry
                    SET is_active = 1, updated_at = :now WHERE model_id = :mid
                """),
                {"mid": model_id, "now": datetime.utcnow().isoformat()},
            )
        return True

    def list_models(self, status: str = None) -> list:
        """List all registered models."""
        sql = "SELECT * FROM finetune_model_registry"
        params: dict = {}
        if status is not None:
            sql += " WHERE status = :status"
            params["status"] = status
        sql += " ORDER BY created_at, model_id"

        with self.engine.connect() as conn:
            return [self._row_to_dict(row) for row in conn.execute(text(sql), params).fetchall()]

    def update_status(self, model_id: str, status: str) -> bool:
        """Update a model's lifecycle status (registered/checkpoint/retired)."""
        if self.get_model(model_id) is None:
            return False
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE finetune_model_registry
                    SET status = :status, updated_at = :now WHERE model_id = :mid
                """),
                {"status": status, "mid": model_id, "now": datetime.utcnow().isoformat()},
            )
        return True

    def delete_model(self, model_id: str, force: bool = False, remove_files: bool = False) -> bool:
        """Delete a model registry entry (weight files are kept by default)."""
        model = self.get_model(model_id)
        if model is None:
            return False

        if model["is_active"] and not force:
            raise ValueError("Cannot delete active model without force=True")

        if remove_files and model.get("model_path"):
            path = Path(model["model_path"])
            if path.is_dir():
                shutil.rmtree(path)

        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM finetune_model_registry WHERE model_id = :mid"),
                {"mid": model_id},
            )
            if model["is_active"]:
                remaining = conn.execute(
                    text("SELECT model_id FROM finetune_model_registry ORDER BY created_at LIMIT 1")
                ).fetchone()
                if remaining:
                    conn.execute(
                        text("""
                            UPDATE finetune_model_registry
                            SET is_active = 1 WHERE model_id = :mid
                        """),
                        {"mid": remaining[0]},
                    )
        return True

    def promote_to_production(self, model_id: str) -> bool:
        """Promote model to production (alias for set_active_model)."""
        return self.set_active_model(model_id)

    # ── Filesystem helpers (weight artifacts only, never JSON) ─────────────

    def create_checkpoint_symlink(self, model_id: str, alias: str = "latest") -> bool:
        """Point an alias at a model's weight directory."""
        model = self.get_model(model_id)
        if not model or not model.get("model_path"):
            return False

        model_path = Path(model["model_path"])
        if not model_path.exists():
            return False

        self.models_dir.mkdir(parents=True, exist_ok=True)
        link_path = self.models_dir / alias

        if link_path.is_symlink() or link_path.exists():
            if link_path.is_dir() and not link_path.is_symlink():
                shutil.rmtree(link_path)
            else:
                link_path.unlink()

        link_path.symlink_to(model_path)
        return True

    def get_model_info(self, model_id: str) -> dict | None:
        """Get detailed model information."""
        return self.get_model(model_id)

    def export_model(self, model_id: str, export_path: str) -> bool:
        """Copy a model's weight directory to ``export_path``."""
        model = self.get_model(model_id)
        if not model or not model.get("model_path"):
            return False

        src = Path(model["model_path"])
        if not src.is_dir():
            return False

        dst = Path(export_path)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True

    def cleanup_old_checkpoints(self, keep: int = 5) -> int:
        """Remove old checkpoint registry rows, keeping the most recent N."""
        checkpoints = [
            model for model in self.list_models(status="checkpoint")
        ]
        checkpoints.sort(key=lambda m: m.get("created_at", ""), reverse=True)

        removed = 0
        for model in checkpoints[max(0, keep):]:
            if self.delete_model(model["id"], force=True):
                removed += 1
        return removed
