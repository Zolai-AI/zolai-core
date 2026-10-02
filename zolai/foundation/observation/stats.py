"""Capability 3 — streaming word-frequency aggregation (§8-9 scalars).

The aggregator is fed one sentence at a time and keeps, per normalized word:

- ``frequency``     total token occurrences
- ``doc_freq``      distinct documents containing the word
- ``sent_freq``     sentences containing the word (deduped within a sentence)
- ``source_count``  distinct sources (``{table}:{field}``) containing the word
- ``diversity``     ``doc_freq / total_documents`` — evenness of the spread

plus the pre-normalization surface forms (ZVS variants of the same word).

Memory stays proportional to distinct words, not tokens: a sentence contributes
at most one increment per distinct word for the document/sentence counters.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

__all__ = ["DIVERSITY_DECIMALS", "StatsAggregator", "WordAccumulator"]


@dataclass
class WordAccumulator:
    """Running counters for one normalized word."""

    frequency: int = 0
    sent_freq: int = 0
    docs: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    surfaces: Counter[str] = field(default_factory=Counter)

    @property
    def doc_freq(self) -> int:
        return len(self.docs)

    @property
    def source_count(self) -> int:
        return len(self.sources)


#: Stored precision of the ``diversity`` scalar.
DIVERSITY_DECIMALS = 4


class StatsAggregator:
    """Accumulate §8-9 word statistics over a streamed sentence corpus."""

    def __init__(self) -> None:
        self.words: dict[str, WordAccumulator] = {}
        self.documents: set[str] = set()
        self.sentences_seen = 0
        self.tokens_seen = 0
        self.sources_seen: set[str] = set()

    def observe(
        self,
        tokens: list[str],
        surfaces: list[str],
        *,
        document_id: str,
        source_id: str,
    ) -> None:
        """Record one sentence (``tokens`` already normalized)."""
        if not tokens:
            return
        self.sentences_seen += 1
        self.documents.add(document_id)
        self.sources_seen.add(source_id)
        self.tokens_seen += len(tokens)

        accs: dict[str, WordAccumulator] = {}
        for token, surface in zip(tokens, surfaces, strict=False):
            acc = accs.get(token)
            if acc is None:
                acc = self.words.setdefault(token, WordAccumulator())
                accs[token] = acc
            acc.frequency += 1
            acc.surfaces[surface] += 1

        # One sentence contributes at most 1 to doc/sent counters per word.
        for token, acc in accs.items():
            acc.sent_freq += 1
            acc.docs.add(document_id)
            acc.sources.add(source_id)

    # ── Derived views ────────────────────────────────────────────────────

    @property
    def total_documents(self) -> int:
        return len(self.documents)

    @property
    def total_sentences(self) -> int:
        return self.sentences_seen

    @property
    def total_tokens(self) -> int:
        return self.tokens_seen

    @property
    def distinct_words(self) -> int:
        return len(self.words)

    def scalars(self, word: str) -> dict[str, int | float]:
        """The five §8-9 scalars for ``word`` (diversity rounded, 0 if no docs)."""
        acc = self.words[word]
        total_docs = self.total_documents
        diversity = round(acc.doc_freq / total_docs, DIVERSITY_DECIMALS) if total_docs else 0.0
        return {
            "frequency": acc.frequency,
            "doc_freq": acc.doc_freq,
            "sent_freq": acc.sent_freq,
            "source_count": acc.source_count,
            "diversity": diversity,
        }

    def surface_forms(self, word: str, top_k: int = 10) -> list[dict[str, int | str]]:
        """Top-K pre-normalization surfaces of ``word`` (count desc, form asc)."""
        counter = self.words[word].surfaces
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"surface": form, "count": count} for form, count in ranked[:top_k]]

    def frequencies(self) -> dict[str, int]:
        """Unigram frequencies keyed by normalized form (PMI input)."""
        return {word: acc.frequency for word, acc in self.words.items()}
