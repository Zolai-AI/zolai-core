"""Tests for Foundation Corpus Linguistics Module."""
from __future__ import annotations

import pytest

from zolai.foundation.corpus import (
    CorpusAnalysis,
    CorpusAnalyzer,
    Collocation,
    get_corpus_analyzer,
)


class TestCorpusAnalyzer:
    """Test CorpusAnalyzer for n-gram, collocation, frequency, register analysis."""

    @pytest.fixture(scope="class")
    def analyzer(self) -> CorpusAnalyzer:
        return get_corpus_analyzer()

    def test_singleton(self) -> None:
        """Test singleton pattern."""
        a1 = get_corpus_analyzer()
        a2 = get_corpus_analyzer()
        assert a1 is a2

    def test_extract_ngrams_bigram(self, analyzer: CorpusAnalyzer) -> None:
        """Test bigram extraction."""
        ngrams = analyzer.extract_ngrams("Pasian in gam a piangsak hi", n=2)
        assert isinstance(ngrams, dict)
        # Should have bigrams from the sentence
        assert len(ngrams) > 0
        # Check that (in, gam) appears as a bigram
        assert ("in", "gam") in ngrams or ("gam", "a") in ngrams

    def test_extract_ngrams_trigram(self, analyzer: CorpusAnalyzer) -> None:
        """Test trigram extraction."""
        ngrams = analyzer.extract_ngrams("Pasian in gam a piangsak hi", n=3)
        assert isinstance(ngrams, dict)
        assert len(ngrams) > 0

    def test_extract_ngrams_empty(self, analyzer: CorpusAnalyzer) -> None:
        """Test n-gram extraction on empty text."""
        ngrams = analyzer.extract_ngrams("", n=2)
        assert len(ngrams) == 0

    def test_extract_ngrams_single_word(self, analyzer: CorpusAnalyzer) -> None:
        """Test n-gram extraction on single word."""
        ngrams = analyzer.extract_ngrams("pasian", n=2)
        assert len(ngrams) == 0  # Not enough words for bigram

    def test_compute_collocations(self, analyzer: CorpusAnalyzer) -> None:
        """Test collocation computation."""
        collocations = analyzer.compute_collocations(min_pmi=0.0, min_freq=1)
        assert isinstance(collocations, list)
        for coll in collocations:
            assert isinstance(coll, Collocation)
            assert hasattr(coll, "word1")
            assert hasattr(coll, "word2")
            assert hasattr(coll, "pmi")
            assert hasattr(coll, "freq")

    def test_get_frequency_distribution(self, analyzer: CorpusAnalyzer) -> None:
        """Test frequency distribution."""
        freq = analyzer.get_frequency_distribution(top_k=10)
        assert isinstance(freq, dict)
        assert len(freq) <= 10
        for word, count in freq.items():
            assert isinstance(word, str)
            assert isinstance(count, int)
            assert count > 0

    def test_detect_register_formal(self, analyzer: CorpusAnalyzer) -> None:
        """Test register detection for formal text."""
        # 'lo' negation is literary/formal
        register = analyzer.detect_register("Pai lo hi. Ci lo hi.")
        assert register in ("formal", "literary")

    def test_detect_register_common(self, analyzer: CorpusAnalyzer) -> None:
        """Test register detection for common text."""
        register = analyzer.detect_register("Ka pai kei hi.")
        assert register in ("common", "formal")

    def test_detect_register_empty(self, analyzer: CorpusAnalyzer) -> None:
        """Test register detection for empty text."""
        register = analyzer.detect_register("")
        assert register == "common"

    def test_analyze_full(self, analyzer: CorpusAnalyzer) -> None:
        """Test full corpus analysis."""
        result = analyzer.analyze("Pasian in vantung leh leitung a piangsak hi.")
        assert isinstance(result, CorpusAnalysis)
        assert isinstance(result.ngrams, dict)
        assert isinstance(result.collocations, list)
        assert isinstance(result.freq_distribution, dict)
        assert result.register in ("formal", "common", "literary")
        assert result.total_words_analyzed > 0

    def test_analyze_short_text(self, analyzer: CorpusAnalyzer) -> None:
        """Test analysis on short text."""
        result = analyzer.analyze("hi")
        assert isinstance(result, CorpusAnalysis)
        assert result.total_words_analyzed == 1
