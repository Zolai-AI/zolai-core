"""Lexicon read API — bilingual word lookup + cursor search.

Mounted under ``/api/v1/lexicon`` (registered **before** the ``server.py``
catch-all).  Both routes are GETs, gated by the frozen ``dataset:read`` scope
(``zolai.api.auth``) and marked cacheable — the lexicon is public linguistic
data that changes rarely:

- ``GET /api/v1/lexicon/{word}`` — exact lookup over ``dictionary`` (ZO→EN)
  and ``dictionary_en_zo`` (EN→ZO).  POS/polysemy come from the L1.3 columns
  **where present** (``pos_canonical`` / ``pos_candidates`` / ``morph_features``
  … — resolved per request via ``PRAGMA table_info``), plus the stored
  ``syllables`` column when the row carries one.
- ``GET /api/v1/lexicon/search?q=&limit=&cursor=`` — bilingual search with
  cursor pagination in the api-design §4 shape: ``{items, next_cursor,
  has_more}`` (default 50, cap 500).

Read-only — no writes, no schema changes.  Errors use the FastAPI
``{"detail": {...}}`` envelope like the rest of the surface.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..config import config
from . import auth

router = APIRouter(
    prefix="/api/v1/lexicon",
    tags=["lexicon"],
    # Read route → frozen dataset:read action (presented key must hold it;
    # absent key keeps the warn-mode dual-accept, strict stays off for GETs).
    dependencies=[Depends(auth.require_scope("dataset:read"))],
)

#: Lookup columns for the ZO→EN side (L1.3 columns filtered by presence).
_ZO_EN_LOOKUP = (
    "zolai",
    "english",
    "pos",
    "source",
    "syllables",
    "syllable_count",
    "pos_canonical",
    "pos_candidates",
    "pos_evidence",
    "morph_features",
    "review_status",
    "confidence",
)

#: Lookup columns for the EN→ZO side (no L1.3 columns on this table).
_EN_ZO_LOOKUP = ("headword", "translations", "pos", "source")

#: Text columns searched by ``/lexicon/search`` (filtered by presence too).
_ZO_EN_SEARCH = ("zolai", "english")
_EN_ZO_SEARCH = ("headword", "translations")

#: Columns stored as JSON text and parsed back to JSON on the way out.
_JSON_COLS = frozenset({"pos_candidates", "morph_features", "translations"})

#: ``translations`` on dictionary_en_zo may hold a JSON list *or* plain text —
#: only parse it when it really is JSON (leave the raw string otherwise).
_JSON_COLS_STRICT = frozenset({"pos_candidates", "morph_features"})


def _connect() -> sqlite3.Connection:
    """Open the canonical SQLite store read-only-ish (short-lived per request)."""
    conn = sqlite3.connect(str(config.paths.db), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Column names of ``table`` (empty when the table does not exist)."""
    return [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _present(wanted: tuple[str, ...], available: list[str]) -> list[str]:
    """Intersect ``wanted`` with what the table actually has (L1.3 optional)."""
    return [col for col in wanted if col in available]


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards in user input (paired with ``ESCAPE '\\'``)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Dict of a row with JSON-text columns decoded where unambiguous."""
    out: dict[str, Any] = dict(row)
    for col in _JSON_COLS:
        value = out.get(col)
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            if col in _JSON_COLS_STRICT:
                out[col] = None
            continue
        if col in _JSON_COLS_STRICT or isinstance(parsed, (list, dict)):
            out[col] = parsed
    return out


def _lookup(
    conn: sqlite3.Connection,
    table: str,
    wanted: tuple[str, ...],
    where_sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    """Exact-match lookup restricted to the columns the table really has."""
    available = _columns(conn, table)
    if not available:
        return []
    selected = _present(("id",) + wanted, available)
    sql = f"SELECT {', '.join(f'\"{c}\"' for c in selected)} FROM {table} WHERE {where_sql}"
    return [_row_dict(row) for row in conn.execute(sql, params)]


# ---------------------------------------------------------------------------
# GET /api/v1/lexicon/search  — MUST be declared before /{word} so the static
# path wins over the path parameter (Starlette matches in registration order).
# ---------------------------------------------------------------------------


@router.get("/search", summary="Bilingual lexicon search (cursor paginated)")
def lexicon_search(
    response: Response,
    q: str = Query(..., min_length=1, description="Free-text query (substring, case-insensitive)"),
    limit: int = Query(50, ge=1, le=500, description="Page size (api-design §4: default 50, cap 500)"),
    cursor: int | None = Query(None, ge=0, description="Opaque cursor = id of the last item seen"),
) -> dict[str, Any]:
    """Search ``dictionary`` + ``dictionary_en_zo`` and merge by ascending id.

    Returns ``{items, next_cursor, has_more}``; hand ``next_cursor`` back as
    ``cursor`` to fetch the next page.
    """
    # Public linguistic data → safe to cache briefly (60 s).
    response.headers["Cache-Control"] = "public, max-age=60"

    pattern = f"%{_escape_like(q.strip())}%"
    conn = _connect()
    try:
        merged: list[dict[str, Any]] = []
        for table, fields in (("dictionary", _ZO_EN_SEARCH), ("dictionary_en_zo", _EN_ZO_SEARCH)):
            available = _columns(conn, table)
            if not available:
                continue
            search_fields = _present(fields, available)
            if not search_fields:
                continue
            clauses = [f"{field} LIKE ? ESCAPE '\\'" for field in search_fields]
            where = [f"({' OR '.join(clauses)})"]
            params: list[Any] = [pattern] * len(search_fields)
            if cursor is not None:
                where.insert(0, "id > ?")
                params.insert(0, cursor)
            selected = _present(("id",) + tuple(search_fields) + ("pos", "source"), available)
            sql = (
                f"SELECT {', '.join(f'\"{c}\"' for c in selected)} FROM {table} "
                f"WHERE {' AND '.join(where)} ORDER BY id ASC LIMIT ?"
            )
            params.append(limit + 1)
            for row in conn.execute(sql, params):
                item = _row_dict(row)
                item["table"] = table
                merged.append(item)
    finally:
        conn.close()

    merged.sort(key=lambda item: item["id"])
    has_more = len(merged) > limit
    items = merged[:limit]
    next_cursor = str(items[-1]["id"]) if (has_more and items) else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


# ---------------------------------------------------------------------------
# GET /api/v1/lexicon/{word}
# ---------------------------------------------------------------------------


@router.get("/{word}", summary="Exact bilingual lexicon lookup")
def lexicon_word(word: str, response: Response) -> dict[str, Any]:
    """Look one word up in both directions.

    ``zo_en`` = dictionary rows whose ``zolai`` equals the word (exact first,
    case-insensitive fallback); ``en_zo`` = ``dictionary_en_zo`` headword hits.
    ``found`` is false (HTTP 200) when the word exists nowhere — consumers
    branch on the flag instead of parsing errors.
    """
    # Immutable linguistic facts → cache a little longer than the search.
    response.headers["Cache-Control"] = "public, max-age=300"

    cleaned = word.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail={"error": "empty_word"})

    conn = _connect()
    try:
        # Exact (indexed) match first; case-insensitive fallback second.
        zo_en = _lookup(conn, "dictionary", _ZO_EN_LOOKUP, "zolai = ?", (cleaned,))
        if not zo_en:
            zo_en = _lookup(conn, "dictionary", _ZO_EN_LOOKUP, "LOWER(zolai) = ?", (cleaned.lower(),))
        en_zo = _lookup(conn, "dictionary_en_zo", _EN_ZO_LOOKUP, "headword = ?", (cleaned,))
        if not en_zo:
            en_zo = _lookup(conn, "dictionary_en_zo", _EN_ZO_LOOKUP, "LOWER(headword) = ?", (cleaned.lower(),))
    finally:
        conn.close()

    return {
        "word": cleaned,
        "found": bool(zo_en or en_zo),
        "zo_en": zo_en,
        "en_zo": en_zo,
    }
