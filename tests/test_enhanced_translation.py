"""Tests for Enhanced Translation Engine with 3-tier confidence."""
from __future__ import annotations

import pytest

from zolai.learning.translation import TranslationEngine, _TIER_CONFIDENCE


class TestTranslationEngine:
    """Test TranslationEngine with 3-tier confidence and morphology awareness."""

    @pytest.fixture(scope="class")
    def engine(self) -> TranslationEngine:
        return TranslationEngine()

    def test_detect_direction_english(self, engine: TranslationEngine) -> None:
        """Test auto-detect English direction."""
        direction = engine._detect_direction("the dog is running")
        assert direction == "en-zo"

    def test_detect_direction_zolai(self, engine: TranslationEngine) -> None:
        """Test auto-detect Zolai direction."""
        direction = engine._detect_direction("pasian hi")
        assert direction == "zo-en"

    def test_get_evidence_tier_dictionary(self, engine: TranslationEngine) -> None:
        """Test evidence tier for dictionary source."""
        tier = engine._get_evidence_tier("dictionary")
        assert tier == "dictionary"

    def test_get_evidence_tier_bible(self, engine: TranslationEngine) -> None:
        """Test evidence tier for bible source."""
        tier = engine._get_evidence_tier("bible")
        assert tier == "bible"

    def test_get_evidence_tier_corpus(self, engine: TranslationEngine) -> None:
        """Test evidence tier for corpus source."""
        tier = engine._get_evidence_tier("corpus")
        assert tier == "corpus"

    def test_compute_confidence_dictionary(self, engine: TranslationEngine) -> None:
        """Test confidence computation for dictionary tier."""
        confidence = engine._compute_confidence("dictionary", [{}])
        assert confidence == 0.95

    def test_compute_confidence_bible(self, engine: TranslationEngine) -> None:
        """Test confidence computation for bible tier."""
        confidence = engine._compute_confidence("bible", [{}])
        assert confidence == 0.85

    def test_compute_confidence_corpus(self, engine: TranslationEngine) -> None:
        """Test confidence computation for corpus tier."""
        confidence = engine._compute_confidence("corpus", [{}])
        assert confidence == 0.70

    def test_compute_confidence_multiple_matches(self, engine: TranslationEngine) -> None:
        """Test confidence boost with multiple matches."""
        confidence = engine._compute_confidence("dictionary", [{}, {}, {}])
        assert confidence > 0.95

    def test_tier_confidence_coverage(self) -> None:
        """Test all tiers have defined confidence scores."""
        expected_tiers = {"dictionary", "bible", "corpus", "alignment", "phrase", "morphology"}
        assert expected_tiers == set(_TIER_CONFIDENCE.keys())

    def test_translate_empty(self, engine: TranslationEngine) -> None:
        """Test translation of empty text."""
        result = engine.translate("")
        assert result["translation"] == ""
        assert result["confidence"] == 0
        assert result["tier"] == "none"

    def test_translate_returns_tier(self, engine: TranslationEngine) -> None:
        """Test that translate returns tier in response."""
        result = engine.translate("pasian", direction="zo-en")
        assert "tier" in result
        assert "evidence_chain" in result

    def test_translate_batch(self, engine: TranslationEngine) -> None:
        """Test batch translation."""
        results = engine.translate_batch(["pasian", "gam"])
        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "input" in r
            assert "result" in r

    def test_get_translation_stats(self, engine: TranslationEngine) -> None:
        """Test translation statistics."""
        stats = engine.get_translation_stats()
        assert isinstance(stats, dict)
        assert "total_translations" in stats
        assert "dictionary_entries" in stats
        assert "bible_verses" in stats

    def test_translate_morphology_aware(self, engine: TranslationEngine) -> None:
        """Test morphology-aware translation for unknown words."""
        # This may return None if the word is not decomposable
        result = engine.translate_morphology_aware("nuntakna", direction="zo-en")
        # Result should be None or a dict with morphology_breakdown
        if result is not None:
            assert "morphology_breakdown" in result
            assert result.get("is_partial") is True
