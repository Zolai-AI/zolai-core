"""
api/dictionary_api.py
---------------------
FastAPI dictionary lookup server for Zolai dictionaries.

Uses SQLAlchemy database for ZO→EN and EN→ZO lookups.

Endpoints:
  GET /search?q=...&dir=zo-en|en-zo|both   — search by word
  GET /word/{zolai}                          — exact ZO lookup
  GET /english/{english}                     — exact EN lookup
  GET /random                                — random entry
  GET /stats                                 — corpus stats
  GET /health                                — health check

Run:
  uvicorn api.dictionary_api:app --reload --port 8765
"""
from __future__ import annotations

import random
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from zolai.data.database import get_manager

# ── Database manager ────────────────────────────────────────────────────────
mgr = get_manager()
mgr.init_db()

# Cache counts for stats
zo_count = mgr.count("dictionary")
en_count = mgr.count("dictionary_en_zo")
bible_count = mgr.count("bible_verses")

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Zolai Dictionary API",
    description=(
        "Tedim Zolai ↔ English dictionary backed by SQLAlchemy database. "
        "93K ZO→EN entries + 112K EN→ZO entries + 31K Bible verses."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────────────────
def _trim(r: dict, full: bool = True) -> dict:
    """Return full or summary view of a record."""
    if full:
        return r
    return {
        "zolai": r.get("zolai") or r.get("headword"),
        "english": r.get("english") or r.get("translations_clean"),
        "pos": r.get("pos"),
    }


# ── Routes ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "zo_entries": zo_count, "en_entries": en_count}


@app.get("/stats")
def stats():
    return {
        "total_entries": zo_count + en_count,
        "zo_en_entries": zo_count,
        "en_zo_entries": en_count,
        "bible_verses": bible_count,
    }


@app.get("/search")
def search(
    q:     str            = Query(..., description="Search term"),
    dir:   str            = Query("both", description="zo-en | en-zo | both"),
    limit: int            = Query(10, ge=1, le=50),
    full:  bool           = Query(True, description="Return full entry or summary"),
):
    if dir not in ("zo-en", "en-zo", "both"):
        raise HTTPException(400, "dir must be zo-en, en-zo, or both")

    results: list[dict] = []

    if dir in ("zo-en", "both"):
        zo_results = mgr.lookup_word(q)
        for r in zo_results[:limit]:
            results.append(r)

    if dir in ("en-zo", "both"):
        en_results = mgr.lookup_english(q)
        for r in en_results[:limit]:
            results.append(r)

    return {
        "query":   q,
        "dir":     dir,
        "count":   len(results),
        "results": [_trim(r, full) for r in results[:limit]],
    }


@app.get("/word/{zolai}")
def get_by_zolai(zolai: str):
    results = mgr.lookup_word(zolai)
    if not results:
        raise HTTPException(404, f"'{zolai}' not found in Zolai dictionary")
    return results[0]


@app.get("/english/{english}")
def get_by_english(english: str):
    results = mgr.lookup_english(english)
    if not results:
        raise HTTPException(404, f"'{english}' not found in English dictionary")
    return results[0]


@app.get("/random")
def get_random(
    pos: Optional[str] = Query(None, description="Filter by POS: n, v, adj, adv"),
):
    # Use Bible verses for random (smaller table, faster scan)
    bible_table = mgr.metadata.tables.get("bible_verses")
    if bible_table is None:
        raise HTTPException(503, "Bible table not available")

    import sqlalchemy as sa
    with mgr.engine.connect() as conn:
        count = conn.execute(sa.func.count()).select_from(bible_table).scalar_one()
        if count == 0:
            raise HTTPException(404, "No Bible verses available")
        offset = random.randint(0, max(0, count - 1))
        rows = conn.execute(
            bible_table.select().offset(offset).limit(1)
        ).fetchall()
    if not rows:
        raise HTTPException(404, "No entries found")
    return mgr._row_to_dict(rows[0], bible_table)
