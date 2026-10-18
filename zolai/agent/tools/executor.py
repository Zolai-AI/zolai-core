"""Allow-listed tool executor for the agent runtime (P3 §C).

Every execution returns ``{ok, data|error, latency_ms}`` — an unknown tool or
a tool outside the caller's allow-list produces an **error result and is
never executed**.  Handlers are DB-first (rule-mode deterministic, zero
sockets); only ``web_search`` can touch the network and only behind its
double gate (env flag + non-rule engine mode).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from sqlalchemy import text as sa_text

from ...rag.retrieve import UnifiedRetriever
from .errors import classify_failure
from .family import assert_handlers_cover_specs
from .foundation import FOUNDATION_HANDLERS
from .registry import SPECS, get_tool
from .types import ToolCall, ToolError, ToolErrorCode, ToolResult

logger = logging.getLogger(__name__)


def _retriever() -> UnifiedRetriever:
    from ...data.repositories import get_engine

    return UnifiedRetriever(get_engine())


def _engine() -> Any:
    from ...data.repositories import get_engine

    return get_engine()


def _opt_int(inp: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(inp.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 50))


def _require_str(inp: dict[str, Any], key: str) -> str:
    value = str(inp.get(key) or "").strip()
    if not value:
        raise ToolError(f"{key} is required")
    return value


# ── Public handlers (DB-first) ───────────────────────────────────────────────


def _rag_search(inp: dict[str, Any]) -> Any:
    query = _require_str(inp, "query")
    limit = _opt_int(inp, "limit", 10)
    source_filter = inp.get("source_filter")
    if source_filter is not None:
        # Validate source_filter values
        allowed = {"dictionary", "dictionary_en_zo", "bible_verses", "phrases", "grammar_patterns"}
        source_filter = [s for s in source_filter if s in allowed]
        if not source_filter:
            source_filter = None
    results = _retriever().search(query, limit=limit, source_filter=source_filter)
    # The row's own content travels with the hit. Without it the tool returned
    # only the headword — "dictionary:bang" — and the model, having been handed
    # a list of words with no meanings, correctly reported "no evidence for
    # bang" while `bang = what` sat in the corpus the whole time.
    out: list[dict[str, Any]] = []
    for r in results:
        item: dict[str, Any] = {
            "source": r.source,
            "ref": r.id,
            "text": (r.text or "")[:300],
            "score": r.score,
        }
        for field in ("definition", "context", "pos"):
            value = r.metadata.get(field)
            if value not in (None, "", "unknown"):
                item[field] = str(value)[:300]
        out.append(item)
    return out


def _word_evidence(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    return _retriever().query_word_evidence(word, limit=_opt_int(inp, "limit", 20))


def _word_related(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    return _retriever().related_words(word, limit=_opt_int(inp, "limit", 12))


def _analyze_text(inp: dict[str, Any]) -> Any:
    return _retriever().analyze_sentence(_require_str(inp, "text"))


def _grammar_check(inp: dict[str, Any]) -> Any:
    from ...learning.grammar_editor import GrammarEditor

    return GrammarEditor().validate_text(_require_str(inp, "text"))


def _dictionary_lookup(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    conn_cm = _engine().connect()
    with conn_cm as conn:
        try:
            rows = conn.execute(
                sa_text(
                    "SELECT zolai, english, pos FROM dictionary "
                    "WHERE zolai = :w COLLATE NOCASE LIMIT 5"
                ),
                {"w": word},
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    sa_text(
                        "SELECT zolai, english, pos FROM dictionary "
                        "WHERE zolai LIKE :w LIMIT 5"
                    ),
                    {"w": f"{word}%"},
                ).fetchall()
        except Exception as exc:  # missing table/DB in fresh stores
            logger.warning("dictionary lookup failed", exc_info=exc)
            raise ToolError(
                "The dictionary is unavailable right now. Please try again.",
                ToolErrorCode.UNAVAILABLE,
            ) from exc
    return [
        {"zolai": r[0], "english": r[1], "pos": r[2]}
        for r in rows
    ]


def _verse_lookup(inp: dict[str, Any]) -> Any:
    ref = _require_str(inp, "ref")
    with _engine().connect() as conn:
        try:
            row = conn.execute(
                sa_text(
                    "SELECT ref, en_kJV, zo_tdb77 FROM bible_verses "
                    "WHERE UPPER(ref) = UPPER(:ref) LIMIT 1"
                ),
                {"ref": ref},
            ).fetchone()
        except Exception as exc:
            logger.warning("verse lookup failed", exc_info=exc)
            raise ToolError(
                "The verse index is unavailable right now. Please try again.",
                ToolErrorCode.UNAVAILABLE,
            ) from exc
    if row is None:
        return {"ref": ref, "found": False}
    return {"ref": row[0], "en": row[1], "zo": row[2], "found": True}


# ── Myanmar / Burmese lookups (public) ───────────────────────────────────────
#
# The same three in-process calls the legacy ``/dictionary/search/my``,
# ``/dictionary/translate/zo-my`` and ``/dictionary/translate/my-zo`` routes
# make (``get_manager().lookup_myanmar`` / ``translate_zo_my`` / ``translate_my_zo``).
# Names are frozen to the retired Cloudflare Worker's tool names.  A miss is
# reported as ``found: False`` with a note — never an invented translation.


def _db():
    from ...data.database import get_manager

    return get_manager()


def _search_myanmar_dictionary(inp: dict[str, Any]) -> Any:
    query = _require_str(inp, "query")
    limit = _opt_int(inp, "limit", 10)
    try:
        results = _db().lookup_myanmar(query, limit=limit)
    except Exception as exc:
        logger.warning("myanmar dictionary lookup failed", exc_info=exc)
        raise ToolError(
            "The Myanmar dictionary is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {"query": query, "results": results, "count": len(results)}


def _translate_zo_to_myanmar(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    try:
        result = _db().translate_zo_my(word)
    except Exception as exc:
        logger.warning("myanmar translation failed", exc_info=exc)
        raise ToolError(
            "The Myanmar dictionary is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    if not result:
        return {
            "word": word,
            "found": False,
            "note": f"No Myanmar translation for '{word}' in the dictionary.",
        }
    return {"word": word, "found": True, **result}


def _translate_myanmar_to_zo(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    try:
        result = _db().translate_my_zo(word)
    except Exception as exc:
        logger.warning("myanmar translation failed", exc_info=exc)
        raise ToolError(
            "The Myanmar dictionary is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    if not result:
        return {
            "word": word,
            "found": False,
            "note": f"No Zolai entry for the Myanmar word '{word}' in the dictionary.",
        }
    return {"word": word, "found": True, **result}


# ── Admin handlers (read/proposal surfaces — never a canonical write) ────────


def _knowledge_stats(inp: dict[str, Any]) -> Any:
    return _retriever().get_knowledge_statistics()


def _kb_research(inp: dict[str, Any]) -> Any:
    query = _require_str(inp, "query")
    limit = _opt_int(inp, "limit", 10)
    out: list[dict[str, Any]] = []
    with _engine().connect() as conn:
        for table in ("hypotheses", "knowledge_claims"):
            try:
                rows = conn.execute(
                    sa_text(
                        f"SELECT id, subject, predicate, object, status FROM {table} "
                        "WHERE subject LIKE :q OR predicate LIKE :q OR object LIKE :q "
                        "ORDER BY id DESC LIMIT :lim"
                    ),
                    {"q": f"%{query}%", "lim": limit},
                ).fetchall()
            except Exception:
                continue
            for r in rows:
                out.append(
                    {
                        "table": table,
                        "id": r[0],
                        "subject": r[1],
                        "predicate": r[2],
                        "object": r[3],
                        "status": r[4],
                    }
                )
    return out[:limit]


def _review_queue_submit(inp: dict[str, Any]) -> Any:
    from ...data.repositories.foundation import FoundationReviewQueueRepository

    fact_type = _require_str(inp, "fact_type")
    fact_key = _require_str(inp, "fact_key")
    try:
        priority = int(inp.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0
    repo = FoundationReviewQueueRepository(_engine())
    item_id = repo.add_to_queue(fact_type, fact_key, priority=priority, user="agent")
    return {"queued": True, "id": item_id, "fact_type": fact_type, "fact_key": fact_key}


def _provider_status(inp: dict[str, Any]) -> Any:
    from ...llm import provider_settings as settings

    rows = settings.list_provider_rows()
    return [
        {
            "catalog_id": r.get("catalog_id"),
            "name": r.get("name"),
            "adapter": r.get("adapter"),
            "enabled": bool(r.get("enabled")),
            "is_active": bool(r.get("is_active")),
            "selected_model": r.get("selected_model"),
            "secret": settings.mask_secret_ref(r.get("api_key_ref")),
        }
        for r in rows
    ]


def _web_search(inp: dict[str, Any]) -> Any:
    from ...engines import engine_mode

    if os.environ.get("ZOLAI_AGENT_WEB_SEARCH", "").strip() not in {"1", "true", "on"}:
        raise ToolError("web_search is disabled (set ZOLAI_AGENT_WEB_SEARCH=1 to opt in)")
    if engine_mode() == "rule":
        raise ToolError("web_search needs a non-rule ZOLAI_ENGINE_MODE (hybrid|ai)")
    query = _require_str(inp, "query")
    try:
        from ddgs import DDGS
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ToolError("web_search unavailable: ddgs is not installed") from exc
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=_opt_int(inp, "limit", 5)))
    except Exception as exc:
        logger.warning("web_search failed", exc_info=exc)
        raise ToolError(
            "Web search failed; please try again.", ToolErrorCode.UNAVAILABLE
        ) from exc
    return [
        {"title": h.get("title"), "url": h.get("href") or h.get("url"), "snippet": h.get("body")}
        for h in hits
        if isinstance(h, dict)
    ]


HANDLERS = {
    "rag_search": _rag_search,
    "word_evidence": _word_evidence,
    "word_related": _word_related,
    "analyze_text": _analyze_text,
    "grammar_check": _grammar_check,
    "dictionary_lookup": _dictionary_lookup,
    "verse_lookup": _verse_lookup,
    "search_myanmar_dictionary": _search_myanmar_dictionary,
    "translate_zo_to_myanmar": _translate_zo_to_myanmar,
    "translate_myanmar_to_zo": _translate_myanmar_to_zo,
    "knowledge_stats": _knowledge_stats,
    "kb_research": _kb_research,
    "review_queue_submit": _review_queue_submit,
    "provider_status": _provider_status,
    "web_search": _web_search,
    # The generated ``foundation_*`` family (one handler per typed
    # /api/v1/foundation contract — zolai/agent/tools/foundation.py).
    **FOUNDATION_HANDLERS,
}

# Import-time: a published tool with no handler would be advertised and then
# error on every call, and a handler with no tool is code no client can reach.
# This module is the only place both tables are visible at once.
assert_handlers_cover_specs(SPECS, HANDLERS, subject="tool registry")


def execute_tool(
    call: ToolCall,
    allow: frozenset[str] | set[str] | tuple[str, ...],
    *,
    turn: int | None = None,
) -> ToolResult:
    """Run one tool call against the caller's allow-list.

    Unknown tool or not-allowed tool → error result, **never executed**.
    """
    started = time.monotonic()
    resolved_turn = call.turn if turn is None else turn
    name = str(call.name or "").strip()

    def _done(result: ToolResult) -> ToolResult:
        result.latency_ms = (time.monotonic() - started) * 1000
        return result

    if not name:
        return _done(ToolResult(tool_name="", ok=False, error="empty tool name", turn=resolved_turn))
    if get_tool(name) is None:
        return _done(
            ToolResult(tool_name=name, ok=False, error=f"unknown tool: {name}", turn=resolved_turn)
        )
    if name not in allow:
        return _done(
            ToolResult(
                tool_name=name,
                ok=False,
                error=f"tool {name!r} is not allowed for this caller",
                error_code=ToolErrorCode.FORBIDDEN,
                turn=resolved_turn,
            )
        )
    handler = HANDLERS.get(name)
    if handler is None:
        return _done(
            ToolResult(
                tool_name=name,
                ok=False,
                error=f"tool {name!r} has no handler",
                error_code=ToolErrorCode.INTERNAL,
                turn=resolved_turn,
            )
        )
    try:
        data = handler(call.input or {})
    except ToolError as exc:
        return _done(
            ToolResult(
                tool_name=name,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                turn=resolved_turn,
            )
        )
    except Exception as exc:
        # The raw exception belongs in the log, never on the wire: the caller
        # gets a typed code and a human sentence (zolai/agent/tools/errors.py).
        # ``exc_info`` keeps the traceback an operator needs.
        logger.warning("tool %s failed", name, exc_info=exc)
        failure = classify_failure(exc)
        return _done(
            ToolResult(
                tool_name=name,
                ok=False,
                error=failure.message,
                error_code=failure.code,
                turn=resolved_turn,
            )
        )
    return _done(
        ToolResult(tool_name=name, ok=True, data=data, turn=resolved_turn, call_id=call.call_id)
    )
