"""Linguistics API — POS/syllable endpoints + thin foundation aliases.

Mounted under ``/api/v1/linguistics`` (registered **before** the
``server.py`` catch-all):

- ``GET/POST /api/v1/linguistics/pos`` — rule/lexicon POS tagger
  (:func:`zolai.pos_tagger.get_pos_tagger`, closed-class + dictionary +
  Bible heuristics).  Scope: ``pos:read``.
- ``GET/POST /api/v1/linguistics/syllable`` — rule segmenter
  (:func:`zolai.syllable.segment`, deterministic phonotactic rules).
  Scope: ``dataset:read``.
- ``/api/v1/linguistics/{analyze,search}/*`` — **delegate-mounts** the very
  same callables as ``/api/v1/foundation/{analyze,search}/*`` (api-design
  §1: migrate-not-rename alias layer — no field renames, no logic copies).
  Scope: ``dataset:read``.

Both endpoint families exist so consumers get the planned ``linguistics/*``
paths while the legacy ``foundation/*`` routes keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from . import auth

router = APIRouter(prefix="/api/v1/linguistics", tags=["linguistics"])


class TextInput(BaseModel):
    """POST body for text endpoints (``pos``)."""

    text: str = Field(..., min_length=1, examples=["Pasian in vantung leh leitung a piangsak hi."])


class WordInput(BaseModel):
    """POST body for single-word endpoints (``syllable``)."""

    word: str = Field(..., min_length=1, examples=["pasian"])


# ---------------------------------------------------------------------------
# POS tagging — rule/lexicon tagger (GET + POST)
# ---------------------------------------------------------------------------


@router.get("/pos", dependencies=[Depends(auth.require_scope("pos:read"))], summary="POS-tag a Zolai sentence")
async def pos_tag(text: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Tokenize by whitespace and tag each token (closed classes first)."""
    from zolai.pos_tagger import get_pos_tagger

    tagged = get_pos_tagger().tag(text)
    return {"text": text, "tokens": [{"word": word, "pos": pos} for word, pos in tagged]}


@router.post("/pos", dependencies=[Depends(auth.require_scope("pos:read"))], summary="POS-tag a Zolai sentence")
async def pos_tag_post(body: TextInput) -> dict[str, Any]:
    """POST twin of ``GET /linguistics/pos`` (same handler, same shape)."""
    return await pos_tag(text=body.text)


# ---------------------------------------------------------------------------
# Syllabification — deterministic rule segmenter (GET + POST)
# ---------------------------------------------------------------------------


@router.get(
    "/syllable",
    dependencies=[Depends(auth.require_scope("dataset:read"))],
    summary="Segment a Zolai word into syllables",
)
async def syllabify(word: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Rule-based syllable segmentation (no DB, no network)."""
    from zolai.syllable import segment

    syllables = segment(word.strip().lower())
    return {"word": word, "syllables": syllables, "count": len(syllables)}


@router.post(
    "/syllable",
    dependencies=[Depends(auth.require_scope("dataset:read"))],
    summary="Segment a Zolai word into syllables",
)
async def syllabify_post(body: WordInput) -> dict[str, Any]:
    """POST twin of ``GET /linguistics/syllable`` (same handler, same shape)."""
    return await syllabify(word=body.word)


# ---------------------------------------------------------------------------
# Foundation analysis/search aliases — same callables, new path prefix
# ---------------------------------------------------------------------------


def _mount_foundation_aliases() -> None:
    """Delegate-mount ``/foundation/{analyze,search}/*`` under this router.

    The alias layer is intentionally thin: the endpoint callable, response
    model and declared dependencies are reused verbatim; only the path loses
    the ``/foundation`` segment and gains the ``dataset:read`` scope.
    """
    from .foundation_router import router as foundation_router

    for route in foundation_router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (
            route.path.startswith("/foundation/analyze/")
            or route.path.startswith("/foundation/search/")
        ):
            continue
        router.add_api_route(
            route.path[len("/foundation") :],  # /foundation/analyze/x → /analyze/x
            route.endpoint,
            methods=sorted(route.methods),
            dependencies=[*route.dependencies, Depends(auth.require_scope("dataset:read"))],
            response_model=route.response_model,
            summary=route.summary,
        )


_mount_foundation_aliases()
