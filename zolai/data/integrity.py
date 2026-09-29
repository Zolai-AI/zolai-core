"""Database integrity checks — foreign-key guard and full integrity check.

Two very different costs live here:

``PRAGMA foreign_key_check``
    Fast (scans only rows that participate in an FK).  Safe to run at
    startup — :func:`startup_guard` uses it to decide whether FK
    enforcement may be switched on.

``PRAGMA integrity_check``
    Full b-tree walk of every index/table — **tens of seconds on the 2.3GB
    canonical store**.  On-demand / CLI only (:func:`full_integrity_check`);
    it must NEVER be called from a startup path.

Every run that completes is recorded in ``db_integrity_runs`` (created by
:func:`zolai.data.migrations.create_monitoring_tables`).  The table may not
exist yet on older stores, so recording is best-effort and never raises.

Usage::

    from zolai.data.integrity import foreign_key_check, full_integrity_check

    report = foreign_key_check()        # startup-safe
    report = full_integrity_check()     # explicit, slow
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect as sa_inspect, text

logger = logging.getLogger(__name__)

#: ``check_type`` value for the fast FK scan.
KIND_FOREIGN_KEY = "foreign_key"

#: ``check_type`` value for the expensive full-file scan.
KIND_FULL = "full"

#: Table recording completed integrity runs.
RUNS_TABLE = "db_integrity_runs"

#: FK enforcement policy consumed by ``DatabaseManager``'s connect listener.
#: ``"on"`` = SQLite's default ABORT enforcement; ``"deferred"`` keeps FK
#: enforcement off because a startup check found pre-existing violations
#: (existing data must not be bricked by an additive-only migration).
_FK_POLICY = "on"


def fk_policy() -> str:
    """Return the current FK enforcement policy: ``"on"`` or ``"deferred"``."""
    return _FK_POLICY


def set_fk_policy(policy: str) -> str:
    """Set the FK enforcement policy (``"on"`` / ``"deferred"``).

    Returns:
        The new policy value.
    """
    global _FK_POLICY
    if policy not in ("on", "deferred"):
        raise ValueError(f"unknown FK policy: {policy!r}")
    _FK_POLICY = policy
    return _FK_POLICY


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _has_runs_table(engine) -> bool:
    try:
        return sa_inspect(engine).has_table(RUNS_TABLE)
    except Exception:  # pragma: no cover — inspector failures are non-fatal
        return False


def _record(mgr: Any, report: dict[str, Any]) -> None:
    """Persist a completed run to ``db_integrity_runs`` (best-effort)."""
    if not _has_runs_table(mgr.engine):
        return
    try:
        with mgr.session() as session:
            session.execute(
                text(
                    "INSERT INTO db_integrity_runs"
                    "(check_type, ok, issues, checked_at, duration_ms) "
                    "VALUES (:check_type, :ok, :issues, :checked_at, :duration_ms)"
                ),
                {
                    "check_type": report["kind"],
                    "ok": 1 if report["ok"] else 0,
                    "issues": _issues_json(report["issues"]),
                    "checked_at": report["checked_at"],
                    "duration_ms": report["duration_ms"],
                },
            )
    except Exception:
        logger.exception("failed to record integrity run (ignored)")


def _issues_json(issues: list[Any]) -> str:
    import json

    try:
        return json.dumps(issues, default=str)
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return "[]"


def foreign_key_check(mgr: Any = None, *, write: bool = False) -> dict[str, Any]:
    """Run ``PRAGMA foreign_key_check`` (startup-safe).

    Args:
        mgr: A :class:`~zolai.data.database.DatabaseManager`. Defaults to the
            process singleton.
        write: Record the run in ``db_integrity_runs`` (operator/CLI runs only —
            :func:`startup_guard` records its own entry).

    Returns:
        ``{kind, ok, issues, checked_at, duration_ms}`` where ``issues`` is a
        list of ``{table, rowid, parent, fkid}`` dicts (empty when clean).
    """
    from .database import get_manager

    mgr = mgr if mgr is not None else get_manager()
    started = time.perf_counter()
    issues: list[dict[str, Any]] = []
    with mgr.engine.connect() as conn:
        rows = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
    for row in rows:
        issues.append(
            {
                "table": row[0],
                "rowid": row[1],
                "parent": row[2],
                "fkid": row[3],
            }
        )
    report = {
        "kind": KIND_FOREIGN_KEY,
        "ok": not issues,
        "issues": issues,
        "checked_at": _now(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if write:
        _record(mgr, report)
    return report


def startup_guard(mgr: Any = None) -> dict[str, Any]:
    """Startup FK guard — fast check that decides the FK enforcement policy.

    Clean store → keep ``PRAGMA foreign_keys=ON`` (ABORT).  Violations →
    switch to ``deferred`` (FK enforcement stays off for new connections) and
    log loudly, so pre-existing orphans in the shared 2.3GB store can never
    turn an additive migration into a write failure.

    Never runs ``PRAGMA integrity_check``.

    Returns:
        The :func:`foreign_key_check` report plus a ``fk_policy`` key.
    """
    from .database import get_manager

    mgr = mgr if mgr is not None else get_manager()
    report = foreign_key_check(mgr)
    if report["ok"]:
        policy = set_fk_policy("on")
    else:
        policy = set_fk_policy("deferred")
        logger.error(
            "foreign_key_check found %d violation(s); FK enforcement deferred "
            "(run `zolai db integrity` for details): %s",
            len(report["issues"]),
            report["issues"][:5],
        )
    report = {**report, "fk_policy": policy}
    _record(mgr, report)
    return report


def full_integrity_check(mgr: Any = None, *, write: bool = True) -> dict[str, Any]:
    """Run the full ``PRAGMA integrity_check`` — expensive, explicit only.

    Takes tens of seconds on the 2.3GB canonical store: call from the CLI or
    an operator endpoint, never from a startup path.

    Args:
        mgr: DatabaseManager (defaults to the singleton).
        write: Record the run in ``db_integrity_runs``.

    Returns:
        ``{kind, ok, issues, checked_at, duration_ms}``.
    """
    from .database import get_manager

    mgr = mgr if mgr is not None else get_manager()
    started = time.perf_counter()
    with mgr.engine.connect() as conn:
        rows = conn.execute(text("PRAGMA integrity_check")).fetchall()
    messages = [str(row[0]) for row in rows]
    ok = messages == ["ok"]
    issues: list[Any] = [] if ok else messages
    report = {
        "kind": KIND_FULL,
        "ok": ok,
        "issues": issues,
        "checked_at": _now(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if write:
        _record(mgr, report)
    return report


def check_all(mgr: Any = None) -> dict[str, Any]:
    """Run the fast FK check plus the expensive full check (explicit only)."""
    return {
        "foreign_keys": foreign_key_check(mgr),
        "full": full_integrity_check(mgr),
    }
