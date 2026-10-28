"""The learning tool family — study/learner tools plus the two write-back channels.

Every spec wraps a capability that **already exists in-process**; nothing is
re-implemented and nothing shells out to HTTP (the same rule the ``language``
family follows, registry-extension §2):

| tool | calls (verified in source) | REST twin |
|---|---|---|
| ``study_quiz_generate`` | ``ProgressTracker.generate_quiz`` | tool-only |
| ``study_review_schedule`` | ``ProgressTracker.get_due_reviews`` | tool-only |
| ``study_record_review`` | ``ProgressTracker.update_review`` (SM-2) | tool-only |
| ``study_learner_stats`` | ``get_statistics`` + ``get_streak`` + ``get_cefr_level`` | tool-only |
| ``predict_word_completion`` | ``zolai.knowledge.ngram.predict_completion`` | ``GET /api/v1/predictions/completions`` |
| ``suggest_word_corrections`` | ``zolai.knowledge.ngram.suggest_corrections`` | ``GET /api/v1/predictions/corrections`` |
| ``contribute_word`` | ``contributions`` table insert | ``POST /api/v1/contributions`` |
| ``submit_usage_example`` | same table, tagged ``register:<formality>`` | same route |

**No overlap by design.** Register detection and corpus analysis are already
published as ``foundation_analyze_corpus`` (its payload carries ``register``),
adaptive difficulty as ``foundation_get_adaptive_difficulty`` and correction
categorisation as ``foundation_categorize_error`` — the family does not mint
second names for those capabilities.

Tiers: the five **reads** are member and up (mirroring the ``language``
family); the three **write-back** tools are **admin-only**, exactly like
``review_queue_submit`` — the pinned invariant "the member list is read-only"
(``test_public_and_member_are_read_only_sets``) stays intact, and a corpus
write through MCP requires the same credential class as every other durable
write. Widening the anonymous set is a founder decision (registry-extension
§5.7) and was not taken here.
Scopes follow the language-family rule: only where a backing route declares
``require_scope`` does a tool carry one; none of these routes do, so the tier
is the control and every tool's ``scope`` is ``None``.

``learner`` is a study-profile label (the tracker's ``user_id``, default
``"default"``), **not** an identity or credential: it can only choose which
review schedule/quiz pool to read or grow, never whose authority to use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .types import ToolError, ToolErrorCode, ToolSpec

_OBJ = "object"


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": _OBJ, "properties": properties, "required": required or []}


_LEARNER = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "description": "Study profile label (default 'default'). Not a login — a row selector.",
}


def _require_str(inp: dict[str, Any], key: str) -> str:
    value = str(inp.get(key) or "").strip()
    if not value:
        raise ToolError(f"Parameter {key!r} is required and must be a non-empty string.")
    return value


def _opt_str(inp: dict[str, Any], key: str, default: str = "") -> str:
    return str(inp.get(key) or default).strip()


def _opt_int(inp: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    raw = inp.get(key, default)
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _learner(inp: dict[str, Any]) -> str:
    return _opt_str(inp, "learner", "default")[:64]


def _tracker(inp: dict[str, Any]) -> Any:
    from ...learning.progress import ProgressTracker

    return ProgressTracker(user_id=_learner(inp))


# ── Study handlers (reads + the SM-2 write-back) ─────────────────────────────


def _study_quiz_generate(inp: dict[str, Any]) -> Any:
    quiz_type = _opt_str(inp, "quiz_type", "vocabulary")
    if quiz_type not in ("vocabulary", "translation", "reverse"):
        raise ToolError("quiz_type must be 'vocabulary', 'translation' or 'reverse'.")
    count = _opt_int(inp, "count", 10, 1, 20)
    level = _opt_str(inp, "level") or None
    try:
        questions = _tracker(inp).generate_quiz(quiz_type=quiz_type, count=count, level=level)
    except Exception as exc:
        raise ToolError(
            "The quiz generator is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {
        "quiz_type": quiz_type,
        "level": level,
        "questions": questions,
        "count": len(questions),
        "note": (
            "An empty list means the vocabulary pool has no fit for this "
            "type/level — it is a pool miss, not a failure."
        ),
    }


def _study_review_schedule(inp: dict[str, Any]) -> Any:
    limit = _opt_int(inp, "limit", 20, 1, 100)
    try:
        due = _tracker(inp).get_due_reviews(limit=limit)
    except Exception as exc:
        raise ToolError(
            "The review scheduler is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {"learner": _learner(inp), "due": due, "count": len(due)}


def _study_record_review(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    raw_quality = inp.get("quality")
    try:
        quality = int(raw_quality)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ToolError("quality must be an integer 0-5 (SM-2 grading).")
    if not 0 <= quality <= 5:
        raise ToolError("quality must be an integer 0-5 (0-2 failed, 3 hard, 4 good, 5 easy).")
    try:
        result = _tracker(inp).update_review(word=word, quality=quality)
    except Exception as exc:
        raise ToolError(
            "The review record could not be updated right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {"learner": _learner(inp), "word": word, "quality": quality, "result": result}


def _study_learner_stats(inp: dict[str, Any]) -> Any:
    tracker = _tracker(inp)
    try:
        return {
            "learner": _learner(inp),
            "statistics": tracker.get_statistics(),
            "streak": tracker.get_streak(user_id=_learner(inp)),
            "cefr_level": tracker.get_cefr_level(),
        }
    except Exception as exc:
        raise ToolError(
            "Learner statistics are unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc


# ── Prediction handlers (ngram reads) ────────────────────────────────────────


def _load_tables() -> dict:
    from ...knowledge.ngram import load_ngram_tables

    return load_ngram_tables()


_EMPTY_NGRAM_NOTE = (
    "Empty means the ngram tables hold no statistics for this prefix/word — "
    "'nothing recorded', not 'incorrect': an untrained bigram is a corpus gap."
)


def _predict_word_completion(inp: dict[str, Any]) -> Any:
    prefix = _require_str(inp, "prefix")
    top_k = _opt_int(inp, "top_k", 5, 1, 10)
    try:
        from ...knowledge.ngram import predict_completion

        completions = predict_completion(prefix, top_k=top_k, tables=_load_tables())
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            "The completion engine is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {
        "prefix": prefix,
        "completions": [{"completion": c, "score": s} for c, s in completions],
        "count": len(completions),
        "note": _EMPTY_NGRAM_NOTE,
    }


def _suggest_word_corrections(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    top_k = _opt_int(inp, "top_k", 3, 1, 10)
    try:
        from ...knowledge.ngram import suggest_corrections

        suggestions = suggest_corrections(word, top_k=top_k, tables=_load_tables())
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            "The correction engine is unavailable right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {
        "word": word,
        "suggestions": [{"word": w, "count": c} for w, c in suggestions],
        "count": len(suggestions),
        "note": _EMPTY_NGRAM_NOTE,
    }


# ── Write-back handlers (corpus contributions channel) ───────────────────────


def _insert_contribution(
    *,
    ctype: str,
    word: str,
    field: str,
    current_value: str,
    suggested_value: str,
    context: str,
    source: str,
) -> dict[str, Any]:
    """One insert into the same ``contributions`` table + caps the REST twin
    uses — mirrors ``contributions_router.submit_contribution`` byte-for-byte
    on column order and the 'pending' status."""
    from datetime import datetime, timezone

    from ...api.contributions_router import ensure_contributions_table
    from ...data.database import get_manager

    ensure_contributions_table()
    mgr = get_manager()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with mgr.engine.begin() as conn:
        cursor = conn.exec_driver_sql(
            """
            INSERT INTO contributions
            (type, word, field, current_value, suggested_value, context, source, email, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                ctype[:32],
                word[:200],
                field[:64],
                current_value[:2000],
                suggested_value[:2000],
                context[:4000],
                source[:500],
                "",
                created_at,
            ),
        )
        contribution_id = cursor.lastrowid
    return {
        "id": contribution_id,
        "status": "pending",
        "message": "Contribution received and queued for editorial review.",
    }


def _contribute_word(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    suggested = _require_str(inp, "suggested_value")
    ctype = _opt_str(inp, "type", "correction")
    if not ctype or len(ctype) > 32:
        raise ToolError("type must be a non-empty label of at most 32 characters.")
    try:
        return _insert_contribution(
            ctype=ctype,
            word=word,
            field=_opt_str(inp, "field"),
            current_value=_opt_str(inp, "current_value"),
            suggested_value=suggested,
            context=_opt_str(inp, "context"),
            source=_opt_str(inp, "source"),
        )
    except Exception as exc:
        raise ToolError(
            "The contribution could not be queued right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc


_USAGE_TYPES = ("formal", "informal", "neutral")


def _submit_usage_example(inp: dict[str, Any]) -> Any:
    word = _require_str(inp, "word")
    text = _require_str(inp, "text")
    register = _opt_str(inp, "register", "neutral")
    if register not in _USAGE_TYPES:
        raise ToolError("register must be one of: formal, informal, neutral.")
    try:
        out = _insert_contribution(
            ctype="usage_example",
            word=word,
            field=f"register:{register}",
            current_value="",
            suggested_value=text,
            context=_opt_str(inp, "context"),
            source=_opt_str(inp, "source"),
        )
    except Exception as exc:
        raise ToolError(
            "The usage example could not be queued right now. Please try again.",
            ToolErrorCode.UNAVAILABLE,
        ) from exc
    return {**out, "word": word, "register": register}


# ── Specs + export tables (merged into registry.SPECS / executor.HANDLERS) ────

LEARNING_SPECS: dict[str, ToolSpec] = {
    "study_quiz_generate": ToolSpec(
        name="study_quiz_generate",
        description=(
            "Generate a quiz from the vocabulary pool (vocabulary | translation | reverse), "
            "optionally filtered by CEFR level. Returns the questions it actually built; an "
            "empty list is a pool miss, never a fabricated quiz."
        ),
        parameters=_obj(
            {
                "quiz_type": {"type": "string", "enum": ["vocabulary", "translation", "reverse"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
                "level": {"type": "string", "minLength": 1, "maxLength": 8},
                "learner": _LEARNER,
            }
        ),
        title="Generate a study quiz",
        destructive=False,
    ),
    "study_review_schedule": ToolSpec(
        name="study_review_schedule",
        description=(
            "List the SM-2 spaced-repetition reviews due now for a study profile, so an "
            "external tutor app can schedule the same queue the Zolai learner uses."
        ),
        parameters=_obj({"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "learner": _LEARNER}),
        title="Due reviews for a learner",
        destructive=False,
    ),
    "study_record_review": ToolSpec(
        name="study_record_review",
        admin_only=True,
        description=(
            "Record one SM-2 review result (quality 0-5) for a word, updating that profile's "
            "spacing schedule. This is the learning write-back: chats that drill words feed "
            "the same progress the platform tracks."
        ),
        parameters=_obj(
            {
                "word": {"type": "string", "minLength": 1, "maxLength": 200},
                "quality": {"type": "integer", "minimum": 0, "maximum": 5},
                "learner": _LEARNER,
            },
            required=["word", "quality"],
        ),
        title="Record a review result",
        writes=True,
        destructive=True,
    ),
    "study_learner_stats": ToolSpec(
        name="study_learner_stats",
        description=(
            "Progress snapshot for a study profile: SM-2 statistics, current streak and the "
            "derived CEFR level."
        ),
        parameters=_obj({"learner": _LEARNER}),
        title="Learner progress snapshot",
        destructive=False,
    ),
    "predict_word_completion": ToolSpec(
        name="predict_word_completion",
        description=(
            "Corpus ngram word/phrase completion for a prefix (learned from the Zolai "
            "corpus). Empty means the tables recorded no such pattern — not 'wrong'."
        ),
        parameters=_obj(
            {
                "prefix": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            required=["prefix"],
        ),
        title="Predict a word/phrase completion",
        destructive=False,
    ),
    "suggest_word_corrections": ToolSpec(
        name="suggest_word_corrections",
        description=(
            "Corpus-backed spelling/usage corrections for a word that looks off — the "
            "ngram tables suggest what the corpus actually records nearby."
        ),
        parameters=_obj(
            {
                "word": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            required=["word"],
        ),
        title="Suggest corpus corrections",
        destructive=False,
    ),
    "contribute_word": ToolSpec(
        name="contribute_word",
        admin_only=True,
        description=(
            "Queue a dictionary contribution (new word, sense correction, usage note) for "
            "human editorial review — the write-back channel that lets AI chats improve the "
            "corpus. Appends a 'pending' row; nothing takes effect until a reviewer accepts it."
        ),
        parameters=_obj(
            {
                "type": {"type": "string", "minLength": 1, "maxLength": 32},
                "word": {"type": "string", "minLength": 1, "maxLength": 200},
                "suggested_value": {"type": "string", "minLength": 1, "maxLength": 2000},
                "field": {"type": "string", "maxLength": 64},
                "current_value": {"type": "string", "maxLength": 2000},
                "context": {"type": "string", "maxLength": 4000},
                "source": {"type": "string", "maxLength": 500},
            },
            required=["word", "suggested_value"],
        ),
        title="Contribute a dictionary correction",
        writes=True,
        destructive=True,
    ),
    "submit_usage_example": ToolSpec(
        name="submit_usage_example",
        admin_only=True,
        description=(
            "Queue a real-world usage example for a Zolai word, tagged with its register "
            "(formal | informal | neutral), for editorial review. Grows the corpus's "
            "formality/usage evidence from live conversations."
        ),
        parameters=_obj(
            {
                "word": {"type": "string", "minLength": 1, "maxLength": 200},
                "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                "register": {"type": "string", "enum": ["formal", "informal", "neutral"]},
                "context": {"type": "string", "maxLength": 4000},
                "source": {"type": "string", "maxLength": 500},
            },
            required=["word", "text"],
        ),
        title="Submit a tagged usage example",
        writes=True,
        destructive=True,
    ),
}

LEARNING_READ_TOOLS: tuple[str, ...] = (
    "study_quiz_generate",
    "study_review_schedule",
    "study_learner_stats",
    "predict_word_completion",
    "suggest_word_corrections",
)

LEARNING_WRITE_TOOLS: tuple[str, ...] = (
    "study_record_review",
    "contribute_word",
    "submit_usage_example",
)

LEARNING_TOOL_NAMES: tuple[str, ...] = LEARNING_READ_TOOLS + LEARNING_WRITE_TOOLS

LEARNING_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "study_quiz_generate": _study_quiz_generate,
    "study_review_schedule": _study_review_schedule,
    "study_record_review": _study_record_review,
    "study_learner_stats": _study_learner_stats,
    "predict_word_completion": _predict_word_completion,
    "suggest_word_corrections": _suggest_word_corrections,
    "contribute_word": _contribute_word,
    "submit_usage_example": _submit_usage_example,
}
