"""SQLAlchemy cursor listeners feeding the database metrics.

Label rules:

- ``operation`` is a bounded **operation class** (``select`` / ``insert`` /
  ``update`` / ``delete`` / ``other``) derived from the statement's leading
  keyword.  SQL text, table names and parameters are **never** used as label
  values, so cardinality stays flat and no schema content leaks out.
- ``error`` is the SQLAlchemy exception class name (a small closed set).

Listeners are installed at most once per engine and never touch the statement
itself.
"""

from __future__ import annotations

import re
import threading
import time
import weakref
from typing import Any

from sqlalchemy import event

from .metrics import DB_LATENCY, DB_QUERY_ERRORS, DB_QUERY_LATENCY

__all__ = ["DatabaseMetrics", "classify_operation", "install_db_metrics"]

#: Leading keyword -> bounded operation class.
_LEADING = {
    "SELECT": "select",
    "INSERT": "insert",
    "UPDATE": "update",
    "DELETE": "delete",
}

#: CTE / compound statements (``WITH ... SELECT``, ``INSERT OR REPLACE`` …).
_DML_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

#: Stack key used on ``connection.info`` for per-statement timings.
_START_KEY = "zolai_query_start"


def classify_operation(statement: str) -> str:
    """Map a SQL statement to a bounded operation class.

    Never returns SQL text — only one of ``select``/``insert``/``update``/
    ``delete``/``other``.
    """
    stripped = statement.lstrip()
    if not stripped:
        return "other"
    first = stripped.split(None, 1)[0].upper()
    if first in _LEADING:
        return _LEADING[first]
    match = _DML_RE.search(stripped)
    if match:
        return _LEADING[match.group(1).upper()]
    return "other"


class DatabaseMetrics:
    """Installs ``before_cursor_execute``/``after_cursor_execute``/``handle_error`` listeners.

    Note the asymmetric signatures: the cursor-execute listeners receive
    ``(connection, cursor, statement, parameters, context, executemany)``
    while ``handle_error`` receives a single ``ExceptionContext``.
    """

    def __init__(self) -> None:
        self._engines: weakref.WeakSet[Any] = weakref.WeakSet()
        self._lock = threading.Lock()

    def install(self, engine: Any) -> bool:
        """Attach the listeners to ``engine`` once.

        Returns:
            ``True`` when this call installed them (``False`` if already done).
        """
        with self._lock:
            if engine in self._engines:
                return False
            event.listen(engine, "before_cursor_execute", self._before)
            event.listen(engine, "after_cursor_execute", self._after)
            event.listen(engine, "handle_error", self._error)
            self._engines.add(engine)
            return True

    # -- listeners -------------------------------------------------------
    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:
        stack = conn.info.setdefault(_START_KEY, [])
        stack.append(time.perf_counter())

    def _after(self, conn, cursor, statement, parameters, context, executemany) -> None:
        started = self._pop(conn)
        if started is None:
            return
        duration = time.perf_counter() - started
        DB_QUERY_LATENCY.labels(operation=classify_operation(statement)).observe(duration)
        DB_LATENCY.observe(duration)

    def _error(self, exception_context) -> None:
        """``handle_error`` listener — SQLAlchemy calls it as ``fn(ctx)``."""
        conn = getattr(exception_context, "connection", None)
        if conn is not None:
            self._pop(conn)
        exception = getattr(exception_context, "exception", None) or getattr(
            exception_context, "original_exception", None
        )
        name = type(exception).__name__ if exception is not None else "unknown"
        DB_QUERY_ERRORS.labels(error=name).inc()

    @staticmethod
    def _pop(conn) -> float | None:
        stack = conn.info.get(_START_KEY)
        if not stack:
            return None
        return stack.pop()


_METRICS = DatabaseMetrics()


def install_db_metrics(engine: Any) -> bool:
    """Install the DB metric listeners on ``engine`` (idempotent)."""
    return _METRICS.install(engine)
