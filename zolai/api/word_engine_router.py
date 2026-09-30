"""Word-engine prediction routes — the n-gram engine on the main app.

Mounts the **existing** prediction engine
(:mod:`zolai.api.prediction_api`, previously reachable only on the side app
``zolai/api/tools.py`` port 8001) under ``/api/v1/predictions``:

- ``GET/POST /api/v1/predictions/next`` — next-word prediction
- ``GET/POST /api/v1/predictions/complete`` — prefix completion (plan §P1
  name; the handler is shared with the original ``/predictions/completions``
  path, which is kept — migrate-not-rename, no logic duplicated)
- ``GET/POST /api/v1/predictions/corrections`` — spelling suggestions
- ``GET /api/v1/predictions/health`` — n-gram table status

All routes are gated by the frozen ``dataset:read`` scope; the middleware
(`/api/v1` prefix) provides key enforcement, so warn-mode dual-accept is
unchanged.  Registered **before** the ``server.py`` catch-all.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from . import auth
from .prediction_api import (
    CompletionsResponse,
    completions,
)
from .prediction_api import (
    router as prediction_router,
)

router = APIRouter(prefix="/api/v1", tags=["predictions"])

# Reuse the engine handlers as-is (delegation, not duplication): the include
# adds the dataset:read scope to every mounted prediction route.
router.include_router(
    prediction_router,
    dependencies=[Depends(auth.require_scope("dataset:read"))],
)


@router.get(
    "/predictions/complete",
    response_model=CompletionsResponse,
    dependencies=[Depends(auth.require_scope("dataset:read"))],
    summary="Prefix completions (plan §P1 path name)",
)
@router.post(
    "/predictions/complete",
    response_model=CompletionsResponse,
    dependencies=[Depends(auth.require_scope("dataset:read"))],
)
async def predictions_complete(
    prefix: str = Query(...),
    top_k: int = Query(5, ge=1, le=20),
) -> Any:
    """``/api/v1/predictions/complete`` — same handler as ``/completions``."""
    return await completions(prefix=prefix, top_k=top_k)
