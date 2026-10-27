"""Lexicon read API — bilingual word lookup + cursor search.

Mounted under ``/api/v1/lexicon`` (registered **before** the ``server.py``
catch-all).  Both routes are GETs, gated by the frozen ``dataset:read`` scope
(``zolai.api.auth``) and marked cacheable — the lexicon is public linguistic
data that changes rarely:

- ``GET /api/v1/lexicon`` — route index (discovery): the two routes below plus
  the tables behind them, so a bare-prefix probe is content, not a 404.
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
# GET /api/v1/lexicon  — index/discovery for the bare prefix.
# Declared before /{word} like /search: a client that lands on the bare
# route gets this document instead of being read as a word named "" (core#11).
# ---------------------------------------------------------------------------


@router.get("", summary="Lexicon route index (discovery)")
def lexicon_index(response: Response) -> dict[str, Any]:
    """Describe what ``/api/v1/lexicon`` serves.

    Read-only discovery: tells a client about the two real routes and the
    tables behind them, so an index probe is a 200 with content rather than a
    404 or an accidental ``{word}`` lookup.
    """
    # Immutable surface description → safe to cache briefly.
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "name": "lexicon",
        "description": "Bilingual Zolai ↔ English lexicon read API (dataset:read).",
        "routes": [
            {
                "method": "GET",
                "path": "/api/v1/lexicon/search",
                "summary": "Bilingual search with cursor pagination",
                "query": {"q": "string (required)", "limit": "1-500, default 50", "cursor": "opaque '<id>:<table>'"},
            },
            {
                "method": "GET",
                "path": "/api/v1/lexicon/{word}",
                "summary": "Exact lookup in both directions",
                "response_flags": ["found", "zo_en", "en_zo"],
            },
        ],
        "tables": list(_MERGE_TABLES),
        "read_only": True,
    }


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


#: Search columns per merged table — **primary field first**: the primary is the
#: identity column of its table (``zolai`` / ``headword``), the rest are
#: secondary (``english`` / ``translations``).
_SEARCH_FIELDS: dict[str, tuple[str, ...]] = {
    "dictionary": _ZO_EN_SEARCH,
    "dictionary_en_zo": _EN_ZO_SEARCH,
}

#: Graded relevance tiers (core#5). A row's score is the **best** tier any of
#: its search fields reaches, so an exact headword always outranks a prefix
#: hit, which outranks a substring hit. Values are absolute — identical across
#: the two merged tables — which is what lets one cursor compare rows from
#: either side:
#:
#: - ``1.0`` exact on the primary (identity) field
#: - ``0.9`` exact on a secondary field
#: - ``0.8`` prefix on the primary field
#: - ``0.7`` prefix on a secondary field
#: - ``0.5`` contains — the WHERE clause's floor; it matched or it is not here
_TIER_EXACT_PRIMARY = 1.0
_TIER_EXACT_SECONDARY = 0.9
_TIER_PREFIX_PRIMARY = 0.8
_TIER_PREFIX_SECONDARY = 0.7
_TIER_CONTAINS = 0.5


def _score_sql(search_fields: list[str] | tuple[str, ...]) -> str:
    """SQL ``CASE`` grading one row against the query — the single ranking rule.

    Used in three places (SELECT, ORDER BY, and the cursor's resume clause) so
    a row can never be scored one way on the page that returns it and another
    way on the page that follows it. ``:q_exact`` is the raw trimmed query
    (``lower()`` on both sides keeps lookup case-insensitive, like the
    ``LIKE`` match it ranks); ``:q_prefix`` is that query escaped with a
    trailing ``%``.
    """
    primary, *secondary = search_fields
    parts = [
        f"CASE WHEN lower(\"{primary}\") = lower(:q_exact) "
        f"THEN {_TIER_EXACT_PRIMARY}"
    ]
    if secondary:
        ors = " OR ".join(f'lower("{col}") = lower(:q_exact)' for col in secondary)
        parts.append(f"WHEN {ors} THEN {_TIER_EXACT_SECONDARY}")
    parts.append(f'WHEN "{primary}" LIKE :q_prefix ESCAPE \'\\\' THEN {_TIER_PREFIX_PRIMARY}')
    if secondary:
        ors = " OR ".join(f'"{col}" LIKE :q_prefix ESCAPE \'\\\'' for col in secondary)
        parts.append(f"WHEN {ors} THEN {_TIER_PREFIX_SECONDARY}")
    parts.append(f"ELSE {_TIER_CONTAINS} END")
    return " ".join(parts)


def _cursor_score(
    conn: sqlite3.Connection,
    keyset: tuple[int, str],
    cleaned: str,
    prefix: str,
) -> float | None:
    """Score of the row a cursor points at, under the *current* query.

    Relevance order needs a keyset of ``(score, id, table)``; the cursor stays
    the opaque ``"<id>:<table>"`` because the score of that one row is
    recomputed here. ``None`` means the cursor cannot be resolved at all (row
    gone, table absent) — the caller turns that into the same 400 a forged
    cursor gets, rather than resuming from an invented position and repeating
    or skipping rows.
    """
    cid, table = keyset
    available = table_columns(conn, table)
    if not available or "id" not in available:
        return None
    search_fields = _present(_SEARCH_FIELDS.get(table, ()), available)
    if not search_fields:
        return None
    sql = f"SELECT {_score_sql(search_fields)} AS _score FROM {table} WHERE id = :cid"
    row = conn.execute(
        sql, {"cid": cid, "q_exact": cleaned, "q_prefix": prefix}
    ).fetchone()
    if row is None or row["_score"] is None:
        return None
    return float(row["_score"])


@router.get("/search", summary="Bilingual lexicon search (cursor paginated)")
def lexicon_search(
    response: Response,
    q: str = Query(..., min_length=1, description="Free-text query (substring, case-insensitive)"),
    limit: int = Query(50, ge=1, le=500, description="Page size (api-design §4: default 50, cap 500)"),
    cursor: str | None = Query(None, description="Opaque composite cursor '<id>:<table>' from next_cursor"),
) -> dict[str, Any]:
    """Search ``dictionary`` + ``dictionary_en_zo``, ranked by graded relevance.

    **Ranking (core#5).** Every hit carries a ``score`` in ``0.5–1.0``
    (:func:`_score_sql`'s tiers) and the page is ordered by that score, so an
    exact headword match is always the first item — the old order was
    ``ORDER BY id ASC``, i.e. insertion order, which answered ``q=pasian``
    with ``abba`` first and gave the caller nothing to rank on.

    **Merge order** is ``(-score, id, table)``: ``dictionary`` sorts before
    ``dictionary_en_zo`` on an equal score (also their lexicographic order), so
    ties keep the previous, documented order. The returned ``next_cursor`` is
    the opaque composite ``"<id>:<table>"`` of the last item; hand it back as
    ``cursor`` (with the same ``q``) to fetch the next page.

    **Pagination** is a keyset over ``(score, id, table)``: each per-table
    query resumes with
    ``WHERE (score) < :cs OR ((score) = :cs AND (id, '<own-table>') > (:cid, :ctable))``
    — the row value keeps the cross-table id twins (the ``dictionary_en_zo``
    row at id X still qualifies after the ``dictionary`` row at id X), and
    ``:cs`` is the cursor row's score recomputed for this query.
    """
    # Public linguistic data → safe to cache briefly (60 s).
    response.headers["Cache-Control"] = "public, max-age=60"

    cleaned = q.strip()
    keyset = _parse_cursor(cursor)
    pattern = f"%{escape_like(cleaned)}%"
    prefix = f"{escape_like(cleaned)}%"
    conn = connect_db()
    try:
        cursor_score: float | None = None
        if keyset is not None:
            cursor_score = _cursor_score(conn, keyset, cleaned, prefix)
            if cursor_score is None:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "invalid_cursor",
                        "cursor": cursor,
                        "expected": "a next_cursor from this query",
                    },
                )
        merged: list[dict[str, Any]] = []
        for table in _MERGE_TABLES:
            available = table_columns(conn, table)
            if not available:
                continue
            search_fields = _present(_SEARCH_FIELDS[table], available)
            if not search_fields:
                continue
            score_sql = _score_sql(search_fields)
            clauses = [f"{field} LIKE :q_pat ESCAPE '\\'" for field in search_fields]
            where = [f"({' OR '.join(clauses)})"]
            params: dict[str, Any] = {
                "q_pat": pattern,
                "q_exact": cleaned,
                "q_prefix": prefix,
            }
            if keyset is not None:
                # Score first, then the own-table row value: this table keeps
                # (id, <own name>) > (cursor id, cursor table).
                where.append(
                    f"(({score_sql}) < :cs OR (({score_sql}) = :cs "
                    f"AND (id, '{table}') > (:cid, :ctable)))"
                )
                params.update(cs=cursor_score, cid=keyset[0], ctable=keyset[1])
            selected = _present(("id",) + tuple(search_fields) + ("pos", "source"), available)
            sql = (
                f"SELECT {', '.join(f'\"{c}\"' for c in selected)}, {score_sql} AS _score "
                f"FROM {table} WHERE {' AND '.join(where)} "
                f"ORDER BY _score DESC, id ASC LIMIT :lim"
            )
            params["lim"] = limit + 1
            for row in conn.execute(sql, params):
                item = _row_dict(row)
                item["table"] = table
                merged.append(item)
    finally:
        conn.close()

    # Best score first; (id, table) breaks ties — 'dictionary' < 'dictionary_en_zo'.
    merged.sort(key=lambda item: (-item["_score"], item["id"], item["table"]))
    has_more = len(merged) > limit
    items = merged[:limit]
    next_cursor = f"{items[-1]['id']}:{items[-1]['table']}" if (has_more and items) else None
    for item in items:
        item["score"] = round(float(item.pop("_score")), 4)
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
