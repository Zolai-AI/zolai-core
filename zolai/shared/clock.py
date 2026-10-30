"""One clock for the toolkit: UTC, tz-aware, and one wire format.

Why a module for one line: until 2026-10-27 the package held four different
serialisations of "now" — ``datetime.utcnow().isoformat()`` (naive, deprecated,
and gone in 3.14's successor), ``datetime.now(timezone.utc).isoformat()``
(``+00:00``), ``strftime("%Y-%m-%d %H:%M:%S")`` (space-separated), and
``strftime("%Y-%m-%dT%H:%M:%SZ")``.  They do not sort against each other —
a space is ``0x20`` and a ``T`` is ``0x54``, so ``ORDER BY created_at`` puts
``2026-10-03 23:00`` before ``2026-10-03T01:00`` whatever the clock says — and
any ``WHERE created_at >= :since`` bound written by one lane silently misfilters
rows stored by another.  ``data/zolai.db`` already carries both formats in
``hypotheses.created_at``.

Pick one, and say why: ISO-8601 with an explicit offset.  The knowledge layer
already stores that shape (855 of 865 ``hypotheses`` rows), it round-trips
through :func:`parse` without a per-column guess about the author's local time,
and an offset makes the naive/aware comparison bug raise instead of lying.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["SQL_DEFAULT", "SQL_NOW", "now", "parse", "stamp"]

#: SQLite ``DEFAULT``/inline expression that produces exactly :func:`stamp`'s
#: format.  ``datetime('now')`` — the one every ``CREATE TABLE`` here used until
#: 2026-10-27 — answers ``YYYY-MM-DD HH:MM:SS``, the space-separated shape that
#: does not sort against ``T``-separated stamps in the same column.
SQL_NOW = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
SQL_DEFAULT = f"DEFAULT ({SQL_NOW})"


def now() -> datetime:
    """Timezone-aware UTC *now* — never ``datetime.utcnow()``, which is naive."""
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """``moment`` (default: now) as the canonical storage/wire string.

    Naive input is treated as UTC rather than the machine's local time: a
    timestamp that silently means "wherever this process happened to run" is
    the failure this module exists to prevent.
    """
    moment = moment if moment is not None else now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def parse(value: str) -> datetime:
    """Parse :func:`stamp` output, or a legacy stamp, into an aware datetime.

    Accepts the three shapes already present in the corpus — ``+00:00``,
    trailing ``Z``, and bare ``YYYY-MM-DDTHH:MM:SS`` (read as UTC) — plus the
    space-separated variant, so a caller comparing old rows against new ones
    compares datetimes instead of strings.
    """
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
