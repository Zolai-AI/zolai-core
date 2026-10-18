"""
JSONL Import/Export API Router for Zolai Desktop.
Provides endpoints for importing/exporting JSONL data with version tracking.

Authentication (2026-10-18 security fix)
---------------------------------------
This router used to declare **no** ``require_scope`` dependency at all, while
sitting on the same ``/desktop`` prefix as
:mod:`zolai.api.desktop_router` (which requires ``dataset:read``).  Because
``auth_middleware.is_protected_path`` only gates ``/api/v1``, protection for
``/desktop/*`` rests entirely on each router's own dependencies — so under
``ZOLAI_API_AUTH=enforce`` every route below, including the full-corpus write
(``POST /import/all``) and the full-corpus read (``POST /export/all``), served
**unauthenticated** callers.  Each route now declares a scope explicitly.

Reads and writes deliberately do **not** share one scope, and both scopes come
from the frozen 33-action vocabulary in :data:`zolai.api.auth.VALID_ACTIONS`
(no new scope was invented, and ``VALID_ACTIONS`` is unchanged):

``READ_SCOPE = "dataset:read"``
    the same action every other ``/desktop`` read already needs (table list,
    import log/status) — unchanged posture for a legitimate reader.

``WRITE_SCOPE = "pipeline:run"``
    the four mutating verbs: ``/import/all`` (whole-corpus load),
    ``/import/file`` (row load), ``/export/table`` and ``/export/all`` (the
    latter two dump corpus content to a **caller-chosen path** on the server).
    ``pipeline:run`` is the frozen action for *running* the ingestion/export
    pipeline, so a read-only key can no longer run one — a strictly stronger
    grant than the read scope it sits behind.

Both use the non-strict ``require_scope``: ``ZOLAI_API_AUTH=warn`` still
dual-accepts (logged + rate-limited, unchanged) and ``enforce`` 401s an
anonymous caller, exactly like ``desktop_router``.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from ..core.jsonl_pipeline_v3 import JSONLPipeline
from .auth import require_scope
from .errors import internal_error_envelope, internal_http_error

logger = logging.getLogger(__name__)

#: Frozen vocabulary actions required per verb (see module docstring).
READ_SCOPE = "dataset:read"
WRITE_SCOPE = "pipeline:run"

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


@router.post("/import/all", dependencies=[Depends(require_scope(WRITE_SCOPE))])
async def import_all(request: ImportAllRequest, background_tasks: BackgroundTasks):
    """Import all canonical JSONL files into the database."""
    batch_id = request.batch_id or str(uuid.uuid4())

    def run_import():
        try:
            results = _pipeline.import_all(batch_id)
            return {"batch_id": batch_id, "results": results}
        except Exception as exc:
            return {
                "batch_id": batch_id,
                **internal_error_envelope(
                    exc,
                    logger=logger,
                    operation="desktop.jsonl.import_all",
                    code="import_failed",
                    message=(
                        "The corpus import failed. Check the batch is still on disk, then re-run the import; the "
                        "import log records which table stopped."
                    ),
                    batch_id=batch_id,
                ),
            }

    # Run in background for large imports
    background_tasks.add_task(run_import)
    return {"batch_id": batch_id, "status": "started", "message": "Import started in background"}


@router.post("/import/file", dependencies=[Depends(require_scope(WRITE_SCOPE))])
async def import_file(request: ImportFileRequest):
    """Import a single JSONL file."""
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    try:
        result = _pipeline.import_file(file_path, request.table_name, request.batch_id, request.version)
        return result
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="desktop.jsonl.import_file",
            code="import_failed",
            message=(
                "That JSONL file could not be imported. Check the file is still readable and that table_name names an "
                "import table, then retry."
            ),
            log_context={"table_name": request.table_name, "batch_id": request.batch_id},
        ) from exc

@router.get("/import/status/{batch_id}", dependencies=[Depends(require_scope(READ_SCOPE))])
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


@router.get("/import/log", dependencies=[Depends(require_scope(READ_SCOPE))])
async def import_log(limit: int = Query(50, ge=1, le=500)):
    """Get recent import log entries."""
    return _pipeline.get_import_log(limit)


@router.post("/export/table", dependencies=[Depends(require_scope(WRITE_SCOPE))])
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="desktop.jsonl.export_table",
            code="export_failed",
            message=(
                "That table could not be exported. Check that output_path is a writable location on the server, then "
                "retry."
            ),
            log_context={"table_name": request.table_name},
        ) from exc

@router.post("/export/all", dependencies=[Depends(require_scope(WRITE_SCOPE))])
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
    except Exception as exc:
        raise internal_http_error(
            exc,
            logger=logger,
            status_code=500,
            operation="desktop.jsonl.export_all",
            code="export_failed",
            message=(
                "The corpus could not be exported. Check that output_dir is a writable location on the server, then "
                "retry."
            ),
        ) from exc


@router.get("/tables", dependencies=[Depends(require_scope(READ_SCOPE))])
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
