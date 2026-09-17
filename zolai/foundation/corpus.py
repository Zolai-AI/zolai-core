"""Corpus Linguistics Module — n-gram, collocation, frequency, and register analysis.

Provides:
- CorpusAnalyzer: lazy-init singleton for corpus-level linguistic analysis
- extract_ngrams: n-gram extraction from Bible/translations corpus
- compute_collocations: PMI-based collocation detection from word_collocations + word_usage
- get_frequency_distribution: Zipf-ranked word frequencies
- detect_register: register classification (formal | common | literary)

Uses existing DB tables: bible_verses, translations, vocab, word_collocations,
word_usage. All expensive queries use @lru_cache for performance.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from ..config import config

log = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Collocation:
    """A word pair with PMI score and frequency."""
    word1: str
    word2: str
    pmi: float
    freq: int


@dataclass(frozen=True, slots=True)
class CorpusAnalysis:
    """Result of corpus-level analysis."""
    ngrams: dict[tuple[str, ...], int]
    collocations: list[Collocation]
    freq_distribution: dict[str, int]
    register: str
    total_words_analyzed: int = 0


# ── CorpusAnalyzer ───────────────────────────────────────────────────────────

class CorpusAnalyzer:
    """Lazy-init singleton for corpus-level linguistic analysis.

    Reads from existing DB tables (bible_verses, translations, vocab,
    word_collocations, word_usage). Expensive loads are cached.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── N-gram extraction ─────────────────────────────────────────────────
    def extract_ngrams(self, text: str, n: int = 2) -> Counter[tuple[str, ...]]:
        """Extract character or word n-grams from text.

        Args:
            text: Input text (Zolai or English).
            n: N-gram size (default 2 = bigrams).

        Returns:
            Counter of n-gram tuples.
        """
        words = text.lower().split()
        ngrams: Counter[tuple[str, ...]] = Counter()
        for i in range(len(words) - n + 1):
            gram = tuple(words[i : i + n])
            ngrams[gram] += 1
        return ngrams

    def extract_corpus_ngrams(self, n: int = 2, limit: int = 500) -> Counter[tuple[str, ...]]:
        """Extract n-grams from the entire Bible/translations corpus.

        Args:
            n: N-gram size.
            limit: Max rows to read per table (for performance).

        Returns:
            Counter of n-gram tuples across the corpus.
        """
        corpus_text = self._load_corpus_text(limit)
        all_ngrams: Counter[tuple[str, ...]] = Counter()
        for text_chunk in corpus_text:
            all_ngrams.update(self.extract_ngrams(text_chunk, n))
        return all_ngrams

    def _load_corpus_text(self, limit: int = 500) -> list[str]:
        """Load text from Bible and translations tables."""
        conn = self._get_connection()
        cur = conn.cursor()
        texts: list[str] = []
        try:
            cur.execute(
                "SELECT zolai_text FROM bible_verses WHERE zolai_text IS NOT NULL LIMIT ?",
                (limit,),
            )
            for row in cur.fetchall():
                if row["zolai_text"]:
                    texts.append(row["zolai_text"])

            cur.execute(
                "SELECT zolai FROM translations WHERE zolai IS NOT NULL LIMIT ?",
                (limit,),
            )
            for row in cur.fetchall():
                if row["zolai"]:
                    texts.append(row["zolai"])
        except Exception as e:
            log.debug("Failed to load corpus text: %s", e)
        finally:
            conn.close()
        return texts

    # ── Collocations ──────────────────────────────────────────────────────
    def compute_collocations(
        self, min_pmi: float = 2.0, min_freq: int = 5
    ) -> list[Collocation]:
        """Compute collocations using PMI from word_collocations table.

        Args:
            min_pmi: Minimum PMI score to include.
            min_freq: Minimum co-occurrence frequency.

        Returns:
            List of Collocation objects sorted by PMI descending.
        """
        return self._compute_collocations_cached(min_pmi, min_freq)

    @lru_cache(maxsize=1)
    def _compute_collocations_cached(
        self, min_pmi: float, min_freq: int
    ) -> list[Collocation]:
        conn = self._get_connection()
        cur = conn.cursor()
        collocations: list[Collocation] = []
        try:
            cur.execute(
                """SELECT word1, word2, pmi, frequency
                   FROM word_collocations
                   WHERE frequency >= ? AND pmi >= ?
                   ORDER BY pmi DESC
                   LIMIT 200""",
                (min_freq, min_pmi),
            )
            for row in cur.fetchall():
                collocations.append(
                    Collocation(
                        word1=row["word1"],
                        word2=row["word2"],
                        pmi=float(row["pmi"]),
                        freq=int(row["frequency"]),
                    )
                )
        except Exception as e:
            log.debug("Failed to compute collocations: %s", e)
        finally:
            conn.close()
        return collocations

    # ── Frequency distribution ────────────────────────────────────────────
    def get_frequency_distribution(self, top_k: int = 100) -> dict[str, int]:
        """Get Zipf-ranked word frequencies from Bible, vocab, translations.

        Args:
            top_k: Number of top words to return.

        Returns:
            Dict of word → frequency, sorted by frequency descending.
        """
        return self._get_frequency_distribution_cached(top_k)

    @lru_cache(maxsize=1)
    def _get_frequency_distribution_cached(self, top_k: int) -> dict[str, int]:
        conn = self._get_connection()
        cur = conn.cursor()
        freq: Counter[str] = Counter()
        try:
            # Bible verses
            cur.execute("SELECT zolai_text FROM bible_verses WHERE zolai_text IS NOT NULL LIMIT 5000")
            for row in cur.fetchall():
                for word in row["zolai_text"].lower().split():
                    clean = word.strip(".,;:!?\"'()[]{}")
                    if clean:
                        freq[clean] += 1

            # Translations
            cur.execute("SELECT zolai FROM translations WHERE zolai IS NOT NULL LIMIT 5000")
            for row in cur.fetchall():
                for word in row["zolai"].lower().split():
                    clean = word.strip(".,;:!?\"'()[]{}")
                    if clean:
                        freq[clean] += 1

            # Vocab (use frequency column if available)
            cur.execute(
                "SELECT headword, frequency FROM vocab WHERE frequency > 0 LIMIT 5000"
            )
            for row in cur.fetchall():
                if row["headword"]:
                    freq[row["headword"].lower()] += int(row["frequency"])
        except Exception as e:
            log.debug("Failed to compute frequency distribution: %s", e)
        finally:
            conn.close()

        # Return top_k by frequency (Zipf order)
        return dict(freq.most_common(top_k))

    # ── Register detection ────────────────────────────────────────────────
    def detect_register(self, text: str) -> str:
        """Detect register of text: 'formal' | 'common' | 'literary'.

        Heuristics:
        - Literary: high density of Bible vocabulary, 'lo' negation
        - Formal: complex morphology, passive constructions
        - Common: everyday words, 'kei' negation, simple structures

        Args:
            text: Input Zolai text.

        Returns:
            Register label.
        """
        words = text.lower().split()
        if not words:
            return "common"

        word_set = set(words)
        total = len(words)

        # Check for literary markers
        literary_markers = {"lo", "ciangin", "leh", "tawh", "sungah"}
        literary_count = len(word_set & literary_markers)
        literary_ratio = literary_count / total if total else 0

        # Check for Bible vocabulary density
        bible_words = self._get_bible_word_set()
        bible_overlap = len(word_set & bible_words)
        bible_ratio = bible_overlap / total if total else 0

        # Check for negation type
        has_lo = "lo" in word_set
        has_kei = "kei" in word_set

        # Score
        score = 0.0
        if bible_ratio > 0.3:
            score += 2.0  # Strong Bible vocabulary → literary
        if literary_ratio > 0.1:
            score += 1.5
        if has_lo and not has_kei:
            score += 1.0  # 'lo' is literary/formal
        if has_kei:
            score -= 0.5  # 'kei' is common

        # Morphological complexity indicator
        long_words = sum(1 for w in words if len(w) > 8)
        if long_words / total > 0.2:
            score += 0.5  # Complex morphology → formal/literary

        if score >= 2.0:
            return "literary"
        elif score >= 1.0:
            return "formal"
        return "common"

    @lru_cache(maxsize=1)
    def _get_bible_word_set(self) -> set[str]:
        """Load top Bible vocabulary for register detection."""
        conn = self._get_connection()
        cur = conn.cursor()
        words: set[str] = set()
        try:
            cur.execute(
                """SELECT word, SUM(freq) as total_freq
                   FROM word_alignments
                   GROUP BY word
                   ORDER BY total_freq DESC
                   LIMIT 200"""
            )
            for row in cur.fetchall():
                if row["word"]:
                    words.add(row["word"].lower())
        except Exception:
            # Fallback: use known roots as Bible word proxy
            from zolai.morphology import _KNOWN_ROOTS
            words = {r.lower() for r in _KNOWN_ROOTS}
        finally:
            conn.close()
        return words

    # ── Full analysis ─────────────────────────────────────────────────────
    def analyze(self, text: str) -> CorpusAnalysis:
        """Run full corpus analysis on input text.

        Args:
            text: Input text.

        Returns:
            CorpusAnalysis with ngrams, collocations, freq, register.
        """
        ngrams = self.extract_ngrams(text, n=2)
        register = self.detect_register(text)
        freq_dist = self.get_frequency_distribution(top_k=100)
        collocations = self.compute_collocations()

        return CorpusAnalysis(
            ngrams=dict(ngrams.most_common(100)),
            collocations=collocations[:50],
            freq_distribution=freq_dist,
            register=register,
            total_words_analyzed=len(text.split()),
        )


# ── Module-level singleton ──────────────────────────────────────────────────
_corpus: Optional[CorpusAnalyzer] = None


def get_corpus_analyzer() -> CorpusAnalyzer:
    """Get or create the singleton CorpusAnalyzer."""
    global _corpus  # noqa: PLW0603
    if _corpus is None:
        _corpus = CorpusAnalyzer()
    return _corpus
