"""
JSONL Import/Export API Router for Zolai Desktop.
Provides endpoints for importing/exporting JSONL data with version tracking.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from ..core.jsonl_pipeline_v3 import JSONLPipeline

router = APIRouter(prefix="/desktop/jsonl", tags=["jsonl"])

# Global pipeline instance
_pipeline = JSONLPipeline()


class ImportAllRequest(BaseModel):
    batch_id: Optional[str] = None


class ImportFileRequest(BaseModel):
    file_path: str
    table_name: str
    batch_id: Optional[str] = None
    version: int = 1


class ExportTableRequest(BaseModel):
    table_name: str
    output_path: str
    batch_id: Optional[str] = None
    version: Optional[int] = None
    clean: bool = False
    limit: Optional[int] = None


class ExportAllRequest(BaseModel):
    output_dir: str
    batch_id: Optional[str] = None
    version: Optional[int] = None
    clean: bool = False


@router.post("/import/all")
async def import_all(request: ImportAllRequest, background_tasks: BackgroundTasks):
    """Import all canonical JSONL files into the database."""
    batch_id = request.batch_id or str(uuid.uuid4())

    def run_import():
        try:
            results = _pipeline.import_all(batch_id)
            return {"batch_id": batch_id, "results": results}
        except Exception as e:
            return {"batch_id": batch_id, "error": str(e)}

    # Run in background for large imports
    background_tasks.add_task(run_import)
    return {"batch_id": batch_id, "status": "started", "message": "Import started in background"}


@router.post("/import/file")
async def import_file(request: ImportFileRequest):
    """Import a single JSONL file."""
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    try:
        result = _pipeline.import_file(file_path, request.table_name, request.batch_id, request.version)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/import/status/{batch_id}")
async def import_status(batch_id: str):
    """Check import status by batch ID."""
    logs = _pipeline.get_import_log(100)
    batch_logs = [log for log in logs if log["batch_id"] == batch_id]

    if not batch_logs:
        raise HTTPException(status_code=404, detail="Batch not found")

    total_rows = sum(log["rows_imported"] for log in batch_logs)
    failed = any(log["status"] == "failed" for log in batch_logs)

    return {
        "batch_id": batch_id,
        "tables": len(batch_logs),
        "total_rows": total_rows,
        "status": "failed" if failed else "completed",
        "details": batch_logs
    }


@router.get("/import/log")
async def import_log(limit: int = Query(50, ge=1, le=500)):
    """Get recent import log entries."""
    return _pipeline.get_import_log(limit)


@router.post("/export/table")
async def export_table(request: ExportTableRequest):
    """Export a table to JSONL format."""
    try:
        result = _pipeline.export_table(
            request.table_name,
            Path(request.output_path),
            request.batch_id,
            request.version,
            request.clean,
            request.limit
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export/all")
async def export_all(request: ExportAllRequest):
    """Export all tables to JSONL format."""
    try:
        results = _pipeline.export_all(
            Path(request.output_dir),
            request.batch_id,
            request.version,
            request.clean
        )
        return {"exports": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables")
async def list_tables():
    """List all tables that have import logs."""
    from sqlalchemy import create_engine, text
    from sqlalchemy import inspect as sa_inspect

    from zolai.config import config
    engine = create_engine(f"sqlite:///{config.paths.zolai_db}")
    inspector = sa_inspect(engine)

    tables = inspector.get_table_names()
    import_tables = [t for t in tables if t.endswith("_import") or t == "jsonl_import_log"]

    result = []
    for table in import_tables:
        with engine.connect() as conn:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
        result.append({"table": table, "rows": count})

    return {"tables": result}
