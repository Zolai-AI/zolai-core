"""Capability 4 — left/right window contexts with a first-seen snippet (§10).

Per normalized word the collector keeps:

- ``left`` / ``right`` — the tokens that occur within ``window`` positions of
  the word, with counts, cut to a deterministic top-K;
- ``snippet``        — the first sentence in which the word was observed
  (truncated), so downstream surfaces can show a real example.

Counts use a bounded Space-Saving sketch (``capacity`` entries per side): when
a word sees more distinct neighbours than the capacity, the smallest counter is
evicted and its count is inherited as the replacement's error term.  Below the
capacity — the normal case, and what the fixture tests exercise — the counts are
*exact*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

__all__ = ["CONTEXT_WINDOW", "DEFAULT_TOP_K", "ContextCollector"]

#: Default half-window (tokens kept left + right of the target).
CONTEXT_WINDOW = 2

#: Default top-K cut written into ``word_observation_stats.contexts``.
DEFAULT_TOP_K = 10

#: Default per-side sketch capacity (``top_k * 4`` — headroom before eviction).
CAPACITY_FACTOR = 4

#: Snippet truncation length (chars) — keeps the JSON column bounded.
SNIPPET_LEN = 200


class _BoundedCounts:
    """Space-Saving bounded counter: exact while below ``capacity``."""

    __slots__ = ("_capacity", "_counts")

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, capacity)
        # token -> [count, error]
        self._counts: dict[str, list[int]] = {}

    def add(self, token: str, n: int = 1) -> None:
        slot = self._counts.get(token)
        if slot is not None:
            slot[0] += n
            return
        if len(self._counts) < self._capacity:
            self._counts[token] = [n, 0]
            return
        victim = min(self._counts.items(), key=lambda kv: (kv[1][0], kv[0]))[0]
        floor = self._counts[victim][0]
        del self._counts[victim]
        self._counts[token] = [floor + n, floor]

    def top(self, k: int) -> list[dict[str, int | str]]:
        ranked = sorted(self._counts.items(), key=lambda kv: (-kv[1][0], kv[0]))
        return [{"token": token, "count": count} for token, (count, _err) in ranked[:k]]


class ContextCollector:
    """Streaming window-context aggregation over a sentence stream."""

    def __init__(
        self,
        *,
        window: int = CONTEXT_WINDOW,
        top_k: int = DEFAULT_TOP_K,
        capacity: int | None = None,
        snippet_len: int = SNIPPET_LEN,
    ) -> None:
        self.window = max(0, window)
        self.top_k = max(1, top_k)
        self.capacity = capacity or self.top_k * CAPACITY_FACTOR
        self.snippet_len = snippet_len
        self._left: dict[str, _BoundedCounts] = {}
        self._right: dict[str, _BoundedCounts] = {}
        self._snippets: dict[str, str] = {}

    def observe(self, tokens: Sequence[str], text: str) -> None:
        """Add one already-normalized sentence to the collector."""
        if not tokens or self.window == 0:
            return
        snippet = text if len(text) <= self.snippet_len else text[: self.snippet_len - 1] + "…"
        size = len(tokens)
        for i, target in enumerate(tokens):
            if target not in self._snippets:
                self._snippets[target] = snippet
            left = self._left.get(target)
            if left is None:
                left = self._left[target] = _BoundedCounts(self.capacity)
            for j in range(max(0, i - self.window), i):
                left.add(tokens[j])
            right = self._right.get(target)
            if right is None:
                right = self._right[target] = _BoundedCounts(self.capacity)
            for j in range(i + 1, min(size, i + 1 + self.window)):
                right.add(tokens[j])

    # -- Output ------------------------------------------------------------

    def has_word(self, word: str) -> bool:
        return word in self._snippets

    def as_dict(self, word: str) -> dict[str, object]:
        """``{"left": [...], "right": [...], "snippet": ...}`` for one word.

        Missing words yield an empty structure (deterministic, no KeyError).
        """
        return {
            "left": self._left[word].top(self.top_k) if word in self._left else [],
            "right": self._right[word].top(self.top_k) if word in self._right else [],
            "snippet": self._snippets.get(word, ""),
        }

    def words(self) -> Iterable[str]:
        return self._snippets.keys()

    def __len__(self) -> int:
        return len(self._snippets)
