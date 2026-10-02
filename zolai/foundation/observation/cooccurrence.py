"""Capability 5 — window co-occurrence pairs + PMI (§11).

Pairs from a ``window``-token sweep are accumulated in a **TEMP** SQLite table
(``obs_pair_counts``, canonical order ``w1 <= w2``) through batched upserts, so
memory stays flat no matter how many tokens the build sees (plan risk note: the
full corpus yields millions of pair occurrences).

Two deterministic policy views are derived from it:

- ``neighbors``   — per word, top-K partners by raw window count;
- ``collocations``— per word, top-K by pointwise mutual information
  ``PMI = log2(count · N / (f1 · f2))`` restricted to pairs where both
  unigram frequencies **and** the pair count reach ``min_freq``.

PMI is computed in Python (portable — SQLite math functions are not guaranteed
in every build); ranking ties break on the partner token so output is stable.
No golden numbers are asserted anywhere until speakers validate them (R8) —
window/min-freq are policy defaults, not ground truth.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

__all__ = [
    "COLLOCATION_TOP_K",
    "MIN_FREQ",
    "NEIGHBOR_TOP_K",
    "PAIR_WINDOW",
    "PairCollector",
]

#: Default half-window for pair extraction (policy default — plan §Risks).
PAIR_WINDOW = 2

#: Default minimum unigram/pair frequency for the PMI view (policy default).
MIN_FREQ = 5

#: Default top-K cuts for the two JSON surfaces.
NEIGHBOR_TOP_K = 10
COLLOCATION_TOP_K = 10

#: Rows buffered per executemany upsert — bounds Python-side memory.
_FLUSH_ROWS = 5000

_PAIR_TABLE_DDL = """
CREATE TEMP TABLE IF NOT EXISTS obs_pair_counts (
    w1 TEXT NOT NULL,
    w2 TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (w1, w2)
) WITHOUT ROWID
"""

_PAIR_UPSERT = """
INSERT INTO obs_pair_counts (w1, w2, n) VALUES (:w1, :w2, :n)
ON CONFLICT(w1, w2) DO UPDATE SET n = n + excluded.n
"""


class PairCollector:
    """Accumulate window pairs into a TEMP table, then rank them two ways.

    Must be used with a single ``Connection`` for its whole life: TEMP tables
    are connection-scoped, and the pipeline already keeps one transaction open
    for the entire build.
    """

    def __init__(
        self,
        conn: Connection,
        *,
        window: int = PAIR_WINDOW,
        min_freq: int = MIN_FREQ,
        neighbor_top_k: int = NEIGHBOR_TOP_K,
        collocation_top_k: int = COLLOCATION_TOP_K,
    ) -> None:
        self.conn = conn
        self.window = max(1, window)
        self.min_freq = max(1, min_freq)
        self.neighbor_top_k = max(1, neighbor_top_k)
        self.collocation_top_k = max(1, collocation_top_k)
        self._pending: dict[tuple[str, str], int] = {}
        self._occurrences = 0
        conn.execute(text(_PAIR_TABLE_DDL))

    # -- Accumulation ------------------------------------------------------

    def observe(self, tokens: Sequence[str]) -> None:
        """Add one normalized sentence's within-window pairs."""
        size = len(tokens)
        window = self.window
        for i, left in enumerate(tokens):
            stop = min(size, i + 1 + window)
            for j in range(i + 1, stop):
                right = tokens[j]
                if left == right:
                    continue  # self-pairs carry no information
                key = (left, right) if left <= right else (right, left)
                self._pending[key] = self._pending.get(key, 0) + 1
                self._occurrences += 1
        if len(self._pending) >= _FLUSH_ROWS:
            self.flush()

    def flush(self) -> int:
        """Push buffered pair increments into the TEMP table."""
        if not self._pending:
            return 0
        payload = [
            {"w1": w1, "w2": w2, "n": n} for (w1, w2), n in self._pending.items()
        ]
        self._pending.clear()
        self.conn.execute(text(_PAIR_UPSERT), payload)
        return len(payload)

    @property
    def occurrences(self) -> int:
        """Total pair occurrences seen (self-pairs excluded)."""
        return self._occurrences

    # -- Derived views -----------------------------------------------------

    def neighbors(self) -> dict[str, list[dict[str, int | str]]]:
        """Per word, top-K partners by raw count (count desc, token asc)."""
        self.flush()
        rows = self.conn.execute(
            text(
                """
                WITH directed AS (
                    SELECT w1 AS word, w2 AS partner, n FROM obs_pair_counts
                    UNION ALL
                    SELECT w2 AS word, w1 AS partner, n FROM obs_pair_counts
                ),
                ranked AS (
                    SELECT word, partner, n,
                           ROW_NUMBER() OVER (
                               PARTITION BY word ORDER BY n DESC, partner ASC
                           ) AS rn
                    FROM directed
                )
                SELECT word, partner, n FROM ranked
                WHERE rn <= :k ORDER BY word, rn
                """
            ),
            {"k": self.neighbor_top_k},
        ).fetchall()
        out: dict[str, list[dict[str, int | str]]] = {}
        for word, partner, n in rows:
            out.setdefault(str(word), []).append(
                {"token": str(partner), "count": int(n)}
            )
        return out

    def collocations(
        self, frequencies: Mapping[str, int], *, total_tokens: int
    ) -> dict[str, list[dict[str, int | float | str]]]:
        """Per word, top-K positive-PMI partners (PMI desc, token asc)."""
        self.flush()
        if total_tokens <= 0:
            return {}
        rows = self.conn.execute(
            text(
                "SELECT w1, w2, n FROM obs_pair_counts WHERE n >= :min_freq"
            ),
            {"min_freq": self.min_freq},
        ).fetchall()
        min_freq = self.min_freq
        candidates: dict[str, list[tuple[float, str, int]]] = {}
        for w1, w2, n in rows:
            w1, w2, n = str(w1), str(w2), int(n)
            f1 = frequencies.get(w1, 0)
            f2 = frequencies.get(w2, 0)
            if f1 < min_freq or f2 < min_freq:
                continue
            pmi = math.log2(n * total_tokens / (f1 * f2))
            if pmi <= 0:
                continue
            rounded = round(pmi, 4)
            candidates.setdefault(w1, []).append((rounded, w2, n))
            candidates.setdefault(w2, []).append((rounded, w1, n))
        out: dict[str, list[dict[str, int | float | str]]] = {}
        for word, items in candidates.items():
            items.sort(key=lambda item: (-item[0], item[1]))
            out[word] = [
                {"token": partner, "pmi": pmi, "count": n}
                for pmi, partner, n in items[: self.collocation_top_k]
            ]
        return out
