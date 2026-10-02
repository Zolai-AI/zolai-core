"""Observation repositories — ``observations``, ``word_observation_stats``,
``attestation_index`` (Phase 2 §36 / capability layer).

Two deliberate differences from :class:`~zolai.data.repositories.base.BaseRepository`:

1. **No per-row audit (plan deviation 5).**  A full build writes ~130k derived
   rows; mirroring each one into ``data_audit_log`` would flood the audit
   trail.  The layer is fully rebuildable from the source tables, and
   idempotency comes from the ``ux_obs_source_ref`` UNIQUE key instead.
2. **Caller-owned transactions.**  Every bulk method takes an optional
   ``conn``.  The pipeline holds one long-lived transaction (SQLite TEMP pair
   table + single-writer discipline), so repos must never open a second
   connection while it runs; without ``conn`` they fall back to their own
   ``engine.begin()`` for convenience in tests/CLI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from .base import BaseRepository

__all__ = [
    "AttestationIndexRepository",
    "ObservationRepository",
    "WordStatsRepository",
]

#: Columns rewritten wholesale on every stats upsert (last-writer-wins).
_STATS_REFRESHED: tuple[str, ...] = (
    "frequency",
    "doc_freq",
    "sent_freq",
    "source_count",
    "diversity",
    "surface_forms",
    "contexts",
    "neighbors",
    "collocations",
    "attestation",
    "pipeline_version",
    "updated_at",
)


class ObservationRepository(BaseRepository):
    """``observations`` — chunked ``INSERT OR IGNORE`` bulk path (idempotent)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "observations")

    def insert_ignore(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        conn: Connection | None = None,
        chunk_size: int = 1000,
    ) -> int:
        """Insert ``records`` with ``INSERT OR IGNORE``.

        Existing ``source_ref`` rows are left untouched (re-run safety).

        Args:
            records: Row dicts keyed by column name.
            conn: Caller-owned connection (transaction stays theirs).
            chunk_size: Rows per executemany statement.

        Returns:
            Number of rows actually inserted (ignored rows excluded).
        """
        if not records:
            return 0
        stmt = self.table.insert().prefix_with("OR IGNORE")

        def _run(c: Connection) -> int:
            inserted = 0
            for start in range(0, len(records), chunk_size):
                chunk = list(records[start : start + chunk_size])
                inserted += int(c.execute(stmt, chunk).rowcount or 0)
            return inserted

        if conn is not None:
            return _run(conn)
        with self._engine.begin() as own:
            return _run(own)

    def count_source_refs(self, source_refs: Iterable[str], *, conn: Connection) -> set[str]:
        """Subset of ``source_refs`` already present (cheap re-run check)."""
        wanted = list(dict.fromkeys(source_refs))
        if not wanted:
            return set()
        found: set[str] = set()
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            rows = conn.execute(
                select(self.table.c.source_ref).where(self.table.c.source_ref.in_(chunk))
            ).fetchall()
            found.update(row[0] for row in rows)
        return found


class WordStatsRepository(BaseRepository):
    """``word_observation_stats`` — upsert of the §8-9 derived roll-up.

    Scalar/JSON surfaces are last-writer-wins (a rebuild recomputes them
    exactly), while ``first_seen``/``last_seen`` merge across runs with
    ``min``/``max`` so the columns keep their cross-run meaning.
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "word_observation_stats")

    @staticmethod
    def _upsert_statement(table):
        base = sqlite_insert(table)
        values: dict[str, Any] = {
            name: getattr(base.excluded, name) for name in _STATS_REFRESHED
        }
        # NULL-safe cross-run merge: coalesce keeps a missing legacy value from
        # swallowing the incoming timestamp (SQLite scalar min/max with 2 args).
        values["first_seen"] = func.min(
            func.coalesce(table.c.first_seen, base.excluded.first_seen),
            base.excluded.first_seen,
        )
        values["last_seen"] = func.max(
            func.coalesce(table.c.last_seen, base.excluded.last_seen),
            base.excluded.last_seen,
        )
        return base.on_conflict_do_update(
            index_elements=["normalized_form"], set_=values
        )

    def upsert_many(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        conn: Connection | None = None,
        chunk_size: int = 500,
    ) -> int:
        """Upsert ``rows`` on ``normalized_form`` (creates or refreshes)."""
        if not rows:
            return 0
        stmt = self._upsert_statement(self.table)

        def _run(c: Connection) -> int:
            touched = 0
            for start in range(0, len(rows), chunk_size):
                chunk = list(rows[start : start + chunk_size])
                touched += int(c.execute(stmt, chunk).rowcount or 0)
            return touched

        if conn is not None:
            return _run(conn)
        with self._engine.begin() as own:
            return _run(own)

    def get_many(self, words: Sequence[str], *, conn: Connection) -> dict[str, dict[str, Any]]:
        """Fetch stats rows for ``words`` (test/parity helper)."""
        if not words:
            return {}
        rows = conn.execute(
            self.table.select().where(self.table.c.normalized_form.in_(list(words)))
        ).mappings()
        return {row["normalized_form"]: dict(row) for row in rows}


class AttestationIndexRepository(BaseRepository):
    """``attestation_index`` — (word, source) pairs for §27 cold-start loads.

    The index is materialized from the *same* loader queries the in-memory
    sets use (``build_attestation_index``), so parity is structural rather than
    a runtime assertion.
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "attestation_index")

    def replace_all(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        conn: Connection | None = None,
        chunk_size: int = 1000,
    ) -> int:
        """Rebuild the index atomically: delete every row, insert ``pairs``."""
        if conn is not None:
            return self._replace(conn, pairs, chunk_size)
        with self._engine.begin() as own:
            return self._replace(own, pairs, chunk_size)

    def _replace(self, conn: Connection, pairs: Sequence[tuple[str, str]], chunk_size: int) -> int:
        conn.execute(self.table.delete())
        if not pairs:
            return 0
        stmt = self.table.insert().prefix_with("OR IGNORE")
        written = 0
        for start in range(0, len(pairs), chunk_size):
            chunk = pairs[start : start + chunk_size]
            payload = [{"word": word, "source": source} for word, source in chunk]
            written += int(conn.execute(stmt, payload).rowcount or 0)
        return written

    def words_for_source(self, source: str, *, conn: Connection) -> set[str]:
        """Every indexed word of ``source`` (uses ``ix_attestation_source``)."""
        rows = conn.execute(
            select(self.table.c.word).where(self.table.c.source == source)
        ).fetchall()
        return {row[0] for row in rows}

    def counts_by_source(self, *, conn: Connection) -> dict[str, int]:
        """Row count per source — quick index-health summary."""
        rows = conn.execute(
            select(self.table.c.source, func.count())
            .group_by(self.table.c.source)
            .order_by(self.table.c.source)
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def clear(self, *, conn: Connection | None = None) -> int:
        """Delete every index row (rebuild entry point)."""
        if conn is not None:
            result = conn.execute(self.table.delete())
            return int(result.rowcount or 0)
        with self._engine.begin() as own:
            result = own.execute(self.table.delete())
            return int(result.rowcount or 0)


def load_index_pairs(conn: Connection, *, limit: int | None = None) -> list[tuple[str, str]]:
    """Raw ``(word, source)`` dump of the index (parity-test helper)."""
    sql = "SELECT word, source FROM attestation_index"
    if limit is not None:
        sql += " LIMIT :limit"
        rows = conn.execute(text(sql), {"limit": limit}).fetchall()
    else:
        rows = conn.execute(text(sql)).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]
