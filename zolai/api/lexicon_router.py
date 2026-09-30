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
  has_more}`` (default 50, cap 500).  Because ``id`` values collide across
  the two merged tables (742 live twins), the cursor is an **opaque
  composite** ``"<id>:<table>"`` and the merge keyset is the row value
  ``(id, <own-table>) > (:cid, :ctable)`` — see :func:`lexicon_search`.

Read-only — no writes, no schema changes.  Errors use the FastAPI
``{"detail": {...}}`` envelope like the rest of the surface.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from . import auth
from .records_router import connect_db, escape_like, table_columns

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


def _present(wanted: tuple[str, ...], available: list[str]) -> list[str]:
    """Intersect ``wanted`` with what the table actually has (L1.3 optional)."""
    return [col for col in wanted if col in available]


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
    available = table_columns(conn, table)
    if not available:
        return []
    selected = _present(("id",) + wanted, available)
    sql = f"SELECT {', '.join(f'\"{c}\"' for c in selected)} FROM {table} WHERE {where_sql}"
    return [_row_dict(row) for row in conn.execute(sql, params)]


# ---------------------------------------------------------------------------
# GET /api/v1/lexicon/search  — MUST be declared before /{word} so the static
# path wins over the path parameter (Starlette matches in registration order).
# ---------------------------------------------------------------------------

#: Tables merged by ``/lexicon/search`` and legal in a composite cursor.
_MERGE_TABLES = ("dictionary", "dictionary_en_zo")


def _parse_cursor(cursor: str | None) -> tuple[int, str] | None:
    """Parse the opaque composite cursor ``"<id>:<table>"`` (strict).

    Returns ``(id, table)`` or ``None`` when no cursor was sent.  Anything
    malformed — bare ints (ambiguous across the merged tables, not lossless),
    unknown table names, non-integer ids, wrong arity — is **400**: the cursor
    is opaque, so a forged one must not silently degrade pagination.
    """
    if cursor is None:
        return None
    parts = cursor.split(":")
    if len(parts) != 2 or parts[1] not in _MERGE_TABLES:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_cursor", "cursor": cursor, "expected": "<id>:<table>"},
        )
    raw_id, table = parts
    if not raw_id.isdecimal():
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_cursor", "cursor": cursor, "expected": "<id>:<table>"},
        )
    return int(raw_id), table


@router.get("/search", summary="Bilingual lexicon search (cursor paginated)")
def lexicon_search(
    response: Response,
    q: str = Query(..., min_length=1, description="Free-text query (substring, case-insensitive)"),
    limit: int = Query(50, ge=1, le=500, description="Page size (api-design §4: default 50, cap 500)"),
    cursor: str | None = Query(None, description="Opaque composite cursor '<id>:<table>' from next_cursor"),
) -> dict[str, Any]:
    """Search ``dictionary`` + ``dictionary_en_zo`` and merge by ``(id, table)``.

    ``id`` values collide across the two tables, so the merge order is the
    pair ``(id, table)`` with a **stable tie-break: ``dictionary`` sorts
    before ``dictionary_en_zo``** (also their lexicographic order).  The
    returned ``next_cursor`` is the opaque composite ``"<id>:<table>"`` of
    the last item; hand it back as ``cursor`` to fetch the next page.

    Each per-table query advances with the SQLite row value
    ``WHERE (id, '<own-table>') > (:cid, :ctable)`` — so after a
    ``dictionary`` row at id X, the ``dictionary_en_zo`` twin at id X still
    qualifies (and vice versa), which plain ``id > ?`` used to drop.
    """
    # Public linguistic data → safe to cache briefly (60 s).
    response.headers["Cache-Control"] = "public, max-age=60"

    keyset = _parse_cursor(cursor)
    pattern = f"%{escape_like(q.strip())}%"
    conn = connect_db()
    try:
        merged: list[dict[str, Any]] = []
        for table, fields in ((_MERGE_TABLES[0], _ZO_EN_SEARCH), (_MERGE_TABLES[1], _EN_ZO_SEARCH)):
            available = table_columns(conn, table)
            if not available:
                continue
            search_fields = _present(fields, available)
            if not search_fields:
                continue
            clauses = [f"{field} LIKE ? ESCAPE '\\'" for field in search_fields]
            where = [f"({' OR '.join(clauses)})"]
            params: list[Any] = [pattern] * len(search_fields)
            if keyset is not None:
                # Row value: own table's name is the second element, so this
                # table keeps (id, <own name>) > (cursor id, cursor table).
                where.insert(0, f"(id, '{table}') > (?, ?)")
                params[:0] = [keyset[0], keyset[1]]
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

    # Stable merge order = (id, table); 'dictionary' < 'dictionary_en_zo'.
    merged.sort(key=lambda item: (item["id"], item["table"]))
    has_more = len(merged) > limit
    items = merged[:limit]
    next_cursor = f"{items[-1]['id']}:{items[-1]['table']}" if (has_more and items) else None
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

    conn = connect_db()
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
