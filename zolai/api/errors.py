"""Structured, human-readable API error envelope — no raw exception text.

House style
-----------
Every router in :mod:`zolai.api` already answers an error with a **dict**
``detail`` whose first key is a stable machine code::

    {"error": "unauthorized", "reason": ...}          # zolai/api/auth.py
    {"error": "user_not_found", "username": ...}      # zolai/api/admin_users_router.py
    {"error": "run_not_found", "run_id": ...}         # zolai/api/agent_router.py

:func:`api_error` is that same envelope with one extra key, ``message``, the
human-readable sentence a caller reads:

``{"error": "<code>", "message": "<what went wrong and what to do next>"}``

``message`` (not ``detail``) is used so the body never reads
``{"detail": {"error": ..., "detail": ...}}`` — ``detail`` is FastAPI's own
envelope key, and ``zolai/api/learning_agents_router.py`` already pairs
``error`` with ``message``.

Why a helper
------------
The defect this module exists to prevent is ``detail=str(e)``: the raw
exception text reaching the caller. On a **public** route that hands an
anonymous client SQL, filesystem paths and driver internals. The helper makes
the safe path the easy one — you cannot raise a 500 here without also logging
the raw exception — because the log call and the response construction are the
same statement:

    raise internal_http_error(
        exc,
        logger=logger,
        status_code=500,
        operation="foundation.review_queue.list",
        code="review_queue_unavailable",
        message="The review queue could not be read. Retry in a few seconds.",
    ) from exc

:func:`internal_http_error` **logs** at ``error`` level with ``exc_info=True``
(exception type, message and full traceback, plus structured ``error_code`` /
``operation`` / context fields) and **returns** a 5xx/4xx whose body carries
only the stable code and the human sentence. A converted site therefore still
logs the same failure it always did — with the same operation name — and gains
the traceback; it never loses it.

``tests/test_api_error_envelope.py`` enforces this with an AST scan, so a
future ``detail=str(e)`` fails CI. The scan has one explicit allowlist, held
in that test file, for narrow ``except ValueError`` handlers whose message is
authored by this repository (a literal, or the caller's own identifier).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

__all__ = ["api_error", "internal_error_envelope", "internal_http_error"]

#: LogRecord attributes that ``Logger.error(extra=...)`` refuses to overwrite.
_RESERVED_LOG_KEYS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


def api_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """The house error envelope: ``{"error": <code>, "message": <sentence>}``.

    ``code`` is a stable machine-readable slug; ``message`` is written for the
    caller and must never carry exception text, a traceback, SQL, a filesystem
    path, a table name or a database identifier.
    """
    return {"error": code, "message": message, **extra}


def _log_extra(operation: str, code: str, context: dict[str, Any]) -> dict[str, Any]:
    """Structured fields for the log record, minus anything LogRecord reserves."""
    extra = {"error_code": code, "operation": operation}
    for key, value in context.items():
        if key not in _RESERVED_LOG_KEYS:
            extra[key] = value
    return extra


def internal_error_envelope(
    exc: BaseException,
    *,
    logger: logging.Logger,
    operation: str,
    code: str,
    message: str,
    **log_context: Any,
) -> dict[str, Any]:
    """Log ``exc`` in full server-side; return the safe envelope for a caller.

    ``operation`` names the failed unit of work and appears both in the log line
    and (as ``extra["operation"]``) in the structured fields. ``log_context``
    is server-side only — it is logged, never returned.
    """
    logger.error(
        "%s failed (%s): %s: %s",
        operation,
        code,
        type(exc).__name__,
        exc,
        extra=_log_extra(operation, code, log_context),
        exc_info=True,
    )
    return api_error(code, message)


def internal_http_error(
    exc: BaseException,
    *,
    logger: logging.Logger,
    status_code: int,
    operation: str,
    code: str,
    message: str,
    **log_context: Any,
) -> HTTPException:
    """:func:`internal_error_envelope`, wrapped as a raisable ``HTTPException``.

    Pass the ``status_code`` that is *semantically* right — 500 for an
    unexpected internal failure, 503 when an upstream dependency is
    unavailable, 422 when the caller's own input cannot be accepted — so the
    conversion never flattens a meaningful code into a blanket 500.
    """
    detail = internal_error_envelope(
        exc,
        logger=logger,
        operation=operation,
        code=code,
        message=message,
        **log_context,
    )
    return HTTPException(status_code=status_code, detail=detail)
